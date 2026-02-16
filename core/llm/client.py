"""LLM 客户端模块"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

from ...common.models import JsonObject

import aiohttp
import logging

from ..config import get_config, load_config_from_file
from pathlib import Path

logger = logging.getLogger(__name__)


def _extract_message_content(message: object) -> str:
    """从兼容多家网关/模型的 message 结构中提取文本内容。

    一些网关会返回：
    - message['content'] 为 None
    - message['content'] 为 list[{'text': ...}, ...]
    - message['content'] 为 {'parts': [{'text': ...}, ...]}

    这里做最大兼容，避免出现 None.strip() 这类错误。
    """
    if not isinstance(message, dict):
        return ""

    # 常见 OpenAI/兼容网关
    content = message.get("content")
    if isinstance(content, str):
        return content
    if content is None:
        # 有些实现会把文本放到 message['text']
        text_fallback = message.get("text")
        return text_fallback if isinstance(text_fallback, str) else ""

    # content 为列表：可能是多段文本/富文本 parts
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
                continue
            if isinstance(p, dict):
                # 常见字段：text / content
                txt = p.get("text")
                if isinstance(txt, str):
                    parts.append(txt)
                    continue
                txt = p.get("content")
                if isinstance(txt, str):
                    parts.append(txt)
                    continue
        return "".join(parts)

    # content 为 dict：可能是 Gemini/proxy 的 parts 结构
    if isinstance(content, dict):
        txt = content.get("text")
        if isinstance(txt, str):
            return txt
        parts_obj = content.get("parts")
        if isinstance(parts_obj, list):
            parts2: list[str] = []
            for p in parts_obj:
                if isinstance(p, str):
                    parts2.append(p)
                elif isinstance(p, dict) and isinstance(p.get("text"), str):
                    parts2.append(p["text"])
            return "".join(parts2)

    return ""


class LLMError(Exception):

    """LLM 调用错误"""
    def __init__(self, message: str, status_code: Optional[int] = None, response: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


@dataclass
class LLMResponse:
    """LLM 响应数据类"""
    content: str
    model: str
    usage: JsonObject
    raw_response: JsonObject

    @property
    def clean_content(self) -> str:
        """获取清理后的内容（移除think标签等）"""
        if not self.content:
            return ""
        
        # 移除 <think> 标签及其内容
        cleaned = re.sub(r'<think>.*?</think>\s*', '', self.content, flags=re.DOTALL)
        cleaned = cleaned.strip()
        
        return cleaned

    def parse_json(self) -> JsonObject:
        """解析LLM返回的JSON响应"""
        step_name = "LLM响应"
        llm_response = self.content
        
        if not llm_response:
            logger.error(f"[规则怪谈] {step_name} LLM返回为空")
            raise LLMError("LLM返回为空")
        
        logger.info(f"[规则怪谈] {step_name} 原始内容长度: {len(llm_response)} 字符")
        
        # 移除 <think> 标签及其内容（某些模型会返回思考过程）
        # 使用非贪婪匹配，并且只匹配完整的标签对
        original_length = len(llm_response)
        llm_response = re.sub(r'<think>.*?</think>\s*', '', llm_response, flags=re.DOTALL)
        llm_response = llm_response.strip()
        
        if len(llm_response) < original_length:
            logger.info(f"[规则怪谈] {step_name} 移除think标签后长度: {original_length} -> {len(llm_response)}")
        
        if not llm_response:
            logger.error(f"[规则怪谈] {step_name} 移除think标签后内容为空")
            raise LLMError("移除think标签后内容为空")
        
        def clean_json_string(json_str: str) -> str:
            """清理JSON字符串中的无效控制字符"""
            json_str = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]', '', json_str)
            return json_str
        
        def fix_json_newlines(json_str: str) -> str:
            """修复JSON字符串值中的未转义换行符
            
            LLM有时会在JSON字符串值中包含原始换行符，这会导致解析失败。
            此函数尝试修复这类问题。
            """
            result = []
            in_string = False
            escape_next = False
            
            for i, char in enumerate(json_str):
                if escape_next:
                    result.append(char)
                    escape_next = False
                    continue
                
                if char == '\\':
                    result.append(char)
                    escape_next = True
                    continue
                
                if char == '"' and not escape_next:
                    in_string = not in_string
                    result.append(char)
                    continue
                
                # 如果在字符串内部遇到换行符，将其替换为\n
                if in_string and char in '\n\r':
                    result.append('\\n')
                    continue
                
                result.append(char)
            
            return ''.join(result)
        
        def fix_json_quotes(json_str: str) -> str:
            """修复JSON字符串值中的未转义引号
            
            LLM有时会在JSON字符串值中包含未转义的双引号，这会导致解析失败。
            此函数尝试修复这类问题（仅处理字符串内部的引号）。
            """
            result = []
            in_string = False
            escape_next = False
            
            for i, char in enumerate(json_str):
                if escape_next:
                    result.append(char)
                    escape_next = False
                    continue
                
                if char == '\\':
                    result.append(char)
                    escape_next = True
                    continue
                
                if char == '"' and not escape_next:
                    # 检查这是否是JSON结构中的引号（键或值的开头/结尾）
                    # 简单启发式：如果前面是 [, {, :, ,, 或空格，可能是结构引号
                    prev_char = json_str[i-1] if i > 0 else ''
                    next_char = json_str[i+1] if i < len(json_str) - 1 else ''
                    
                    # 如果是字符串内部的引号（前面不是结构字符），转义它
                    if in_string and prev_char not in '[{:, \n\r\t':
                        result.append('\\"')
                    else:
                        in_string = not in_string
                        result.append(char)
                    continue
                
                result.append(char)
            
            return ''.join(result)
        
        def try_parse_json(json_str: str) -> JsonObject | None:
            """尝试解析JSON，返回解析结果或 None"""
            try:
                cleaned_str = clean_json_string(json_str)
                loaded = json.loads(cleaned_str)
                return loaded if isinstance(loaded, dict) else None
            except json.JSONDecodeError:
                # 尝试修复未转义的换行符
                try:
                    fixed_str = fix_json_newlines(cleaned_str)
                    loaded = json.loads(fixed_str)
                    return loaded if isinstance(loaded, dict) else None
                except json.JSONDecodeError:
                    pass
                
                # 尝试修复未转义的引号
                try:
                    fixed_str = fix_json_quotes(cleaned_str)
                    loaded = json.loads(fixed_str)
                    return loaded if isinstance(loaded, dict) else None
                except json.JSONDecodeError as e:
                    logger.debug(f"[规则怪谈] JSON解析失败: {e}")
                    return None
        
        # 第一次尝试：直接解析整个响应
        result = try_parse_json(llm_response)
        if result:
            logger.info(f"[规则怪谈] {step_name} JSON解析成功（直接解析）")
            return result
        
        # 第二次尝试：移除Markdown代码块标记后解析
        logger.info(f"[规则怪谈] {step_name} 尝试移除Markdown代码块...")
        
        # 移除 ```json 或 ``` 开头的代码块标记
        cleaned_response = re.sub(r'^```(?:json)?\s*', '', llm_response.strip(), flags=re.IGNORECASE)
        cleaned_response = re.sub(r'\s*```\s*$', '', cleaned_response)
        cleaned_response = cleaned_response.strip()
        
        if cleaned_response != llm_response:
            logger.info(f"[规则怪谈] {step_name} 移除Markdown标记后长度: {len(cleaned_response)} 字符")
            result = try_parse_json(cleaned_response)
            if result:
                logger.info(f"[规则怪谈] {step_name} JSON解析成功（移除Markdown后）")
                return result
        
        # 第三次尝试：提取JSON部分（从第一个 { 到最后一个 }）
        logger.info(f"[规则怪谈] {step_name} 尝试提取JSON部分...")
        
        # 找到第一个 { 和最后一个 }
        first_brace = cleaned_response.find('{')
        last_brace = cleaned_response.rfind('}')
        
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            json_str = cleaned_response[first_brace:last_brace + 1]
            logger.info(f"[规则怪谈] {step_name} 找到JSON，长度: {len(json_str)} 字符")
            logger.info(f"[规则怪谈] {step_name} JSON开头: {json_str[:100]}")
            
            result = try_parse_json(json_str)
            if result:
                logger.info(f"[规则怪谈] {step_name} 成功提取并解析JSON")
                return result
            else:
                # 解析失败，输出详细错误信息
                logger.error(f"[规则怪谈] {step_name} JSON解析失败")
                logger.error(f"[规则怪谈] JSON开头（前200字符）: {json_str[:200]}")
                logger.error(f"[规则怪谈] JSON结尾（后200字符）: {json_str[-200:]}")
                
                # 尝试找出JSON错误位置
                try:
                    json.loads(clean_json_string(json_str))
                except json.JSONDecodeError as e:
                    logger.error(f"[规则怪谈] JSON错误详情: {e}")
                    logger.error(f"[规则怪谈] 错误位置: 行{e.lineno} 列{e.colno}")
                    if e.pos:
                        start = max(0, e.pos - 50)
                        end = min(len(json_str), e.pos + 50)
                        logger.error(f"[规则怪谈] 错误附近内容: ...{json_str[start:end]}...")
                
                raise LLMError("提取JSON后解析失败", response=cleaned_response)
        elif first_brace != -1 and last_brace == -1:
            # JSON被截断，尝试修复（添加缺失的 }）
            logger.warning(f"[规则怪谈] {step_name} JSON不完整（缺少右括号），尝试修复...")
            json_str = cleaned_response[first_brace:]
            
            # 尝试添加缺失的右括号
            # 计算需要添加的 } 数量（简单估计：根据左括号数量）
            open_braces = json_str.count('{')
            close_braces = json_str.count('}')
            missing_braces = open_braces - close_braces
            
            if missing_braces > 0:
                fixed_json = json_str + ('}' * missing_braces)
                logger.info(f"[规则怪谈] {step_name} 尝试修复JSON，添加 {missing_braces} 个右括号")
                
                result = try_parse_json(fixed_json)
                if result:
                    logger.info(f"[规则怪谈] {step_name} JSON修复成功并解析")
                    return result
                else:
                    logger.error(f"[规则怪谈] {step_name} JSON修复后仍无法解析")
            
            # 如果修复失败，尝试解析不完整的JSON（提取已完整的部分）
            logger.warning(f"[规则怪谈] {step_name} 尝试提取部分JSON...")
            # 找到最后一个完整的键值对
            last_comma = json_str.rfind(',')
            if last_comma > 0:
                partial_json = json_str[:last_comma] + '}'
                result = try_parse_json(partial_json)
                if result:
                    logger.info(f"[规则怪谈] {step_name} 成功解析部分JSON")
                    return result
            
            logger.error(f"[规则怪谈] {step_name} 无法修复不完整的JSON")
            logger.error(f"[规则怪谈] JSON内容: {json_str[:500]}")
            raise LLMError("JSON不完整且无法修复", response=cleaned_response)
        else:
            logger.error(f"[规则怪谈] {step_name} 未找到JSON部分")
            logger.error(f"[规则怪谈] first_brace={first_brace}, last_brace={last_brace}")
            logger.error(f"[规则怪谈] LLM返回内容: {cleaned_response}")
            raise LLMError("未找到JSON部分", response=cleaned_response)



def get_default_max_tokens() -> int:
    """获取默认的 max_tokens 值（从配置中读取）"""
    try:
        config = get_config()
        return getattr(config.llm, 'max_tokens', 8000)
    except Exception:
        return 8000


class LLMClient:
    """LLM 客户端"""

    def __init__(self):
        pass

    def _ensure_config_loaded(self):
        """确保配置已加载"""
        config_obj = get_config()
        if not config_obj.llm.api_key:
            try:
                base_dir = Path(__file__).parent.parent.parent
                config_path = base_dir / "config.toml"
                if config_path.exists():
                    logger.info(f"[调试] 尝试从 {config_path} 加载配置")
                    load_config_from_file(str(config_path))
                else:
                    logger.warning(f"[调试] 配置文件不存在: {config_path}")
            except Exception as e:
                logger.error(f"[调试] 加载配置失败: {e}")

    async def call(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """
        调用 LLM API
        """
        # 确保配置已加载
        self._ensure_config_loaded()
        
        config = get_config().llm
        
        api_url = config.api_url
        api_key = config.api_key
        model_list = config.model_list
        current_model_index = config.current_model_index
        temp = temperature if temperature is not None else config.temperature
        tokens = max_tokens if max_tokens is not None else get_default_max_tokens()
        
        if not model_list:
            logger.error("[规则怪谈] 模型列表为空")
            raise LLMError("模型列表为空")
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        
        # 默认 system prompt
        default_system_prompt = """你是一位精通规则怪谈创作的游戏设计师和叙事专家。你的任务是：
1. 生成令人毛骨悚然、逻辑严密的规则怪谈场景
2. 创造具有欺骗性和层次感的规则系统
3. 构建充满细节和氛围的环境描述
4. 提供引人入胜的剧情推进

创作原则：
- 恐怖氛围：通过环境细节、感官描写营造压抑不安的氛围
- 逻辑自洽：所有规则和事件必须有内在逻辑
- 层次递进：规则应该有表里两层，表面规则隐藏深层真相
- 心理暗示：通过细节暗示而非直接揭示真相
- 玩家自主：给玩家足够的探索空间和选择自由

输出要求：
- 使用JSON格式输出结构化数据
- 描述要具体、生动，避免笼统
- 保持中文表达的自然流畅
- 严禁使用emoji表情符号"""
        
        final_system_prompt = system_prompt if system_prompt else default_system_prompt
        
        last_error = None
        
        for i in range(len(model_list)):
            model_index = (current_model_index + i) % len(model_list)
            model = model_list[model_index]
            
            logger.info(f"[规则怪谈] 尝试使用模型 {model} (索引: {model_index})")
            
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": final_system_prompt},
                    {"role": "user", "content": prompt}
                ],
                "temperature": temp,
                "max_tokens": tokens,
                "stream": False
            }

            try:
                # 超时设置 180 秒
                timeout = aiohttp.ClientTimeout(total=180)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    logger.info(f"[规则怪谈] 调用 LLM API: {api_url}")
                    async with session.post(api_url, headers=headers, json=payload) as response:
                        logger.info(f"[规则怪谈] API 响应状态: {response.status}")
                        
                        if response.status == 200:
                            data = await response.json()
                            
                            if isinstance(data, list):
                                logger.warning(f"[规则怪谈] 模型 {model} API返回列表格式: {data}")
                                last_error = f"API返回列表格式"
                                continue
                            
                            if not isinstance(data, dict):
                                logger.warning(f"[规则怪谈] 模型 {model} API返回非字典格式: {type(data)}")
                                last_error = f"API返回非字典格式: {type(data)}"
                                continue
                            
                            choices = data.get("choices", [])
                            if not choices or not isinstance(choices, list):
                                logger.warning(f"[规则怪谈] 模型 {model} choices字段格式错误: {choices}")
                                last_error = f"choices字段格式错误"
                                continue
                            
                            first_choice = choices[0]
                            if not isinstance(first_choice, dict):
                                logger.warning(f"[规则怪谈] 模型 {model} choices[0]格式错误: {first_choice}")
                                last_error = f"choices[0]格式错误"
                                continue
                            
                            message = first_choice.get("message", {})
                            if not isinstance(message, dict):
                                logger.warning(f"[规则怪谈] 模型 {model} message字段格式错误: {message}")
                                last_error = f"message字段格式错误"
                                continue
                            
                            content = _extract_message_content(message).strip()
                            if not content:
                                raw_content = message.get("content") if isinstance(message, dict) else None
                                logger.warning(
                                    f"[规则怪谈] 模型 {model} content为空/不可用: type={type(raw_content)}, value={raw_content!r}"
                                )
                                last_error = "content为空"
                                continue

                            
                            logger.info(f"[规则怪谈] 模型 {model} 调用成功，生成 {len(content)} 字符")
                            
                            usage = data.get("usage", {})
                            
                            return LLMResponse(
                                content=content,
                                model=model,
                                usage=usage,
                                raw_response=data,
                            )
                        else:
                            error_text = await response.text()
                            logger.error(f"[规则怪谈] 模型 {model} API请求失败: Status {response.status}, Body: {error_text}")
                            last_error = f"Status {response.status}: {error_text}"
            except Exception as e:
                logger.error(f"[规则怪谈] 模型 {model} 调用时发生异常: {e}")
                last_error = str(e)
        
        logger.error(f"[规则怪谈] 所有模型都调用失败，最后错误: {last_error}")
        raise LLMError(f"所有模型都调用失败: {last_error}")

    async def call_with_fallback(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """调用 LLM，支持模型故障转移"""
        return await self.call(prompt, system_prompt, temperature, max_tokens)

    async def close(self) -> None:
        """关闭客户端（兼容性方法）"""
        pass
