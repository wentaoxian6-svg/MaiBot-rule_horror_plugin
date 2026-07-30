"""LLM 客户端模块"""
from __future__ import annotations

from dataclasses import dataclass
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from typing import Any, Optional

import aiohttp
import asyncio
import json
import logging
import re

from ...common.models import JsonObject
from ..config import get_config

logger = logging.getLogger(__name__)
_warned_missing_api_key: bool = False


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
                    next_char = json_str[i+1] if i < len(json_str) - 1 else ''

                    # 如果在字符串内部，这个引号需要转义
                    if in_string:
                        # 检查后面是否跟着结构字符（表示这是字符串结尾）
                        if next_char in '},:]\n\r\t ':
                            in_string = False
                            result.append(char)
                        else:
                            # 字符串内部的引号，需要转义
                            result.append('\\"')
                    else:
                        # 字符串开始
                        in_string = True
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



def get_default_max_tokens(config_section: str = "llm") -> int:
    """获取默认的 max_tokens 值（从配置中读取）。"""
    try:
        config = get_config()
        section = getattr(config, config_section, None)
        if section is not None:
            return int(getattr(section, "max_tokens", 8000) or 8000)
        return getattr(config.llm, 'max_tokens', 8000)
    except Exception:
        return 8000


class _RetryableHTTPError(Exception):
    """可重试的 HTTP 错误（429/5xx），触发指数退避重试。"""

    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"HTTP {status_code}: {body}")


class _HTTPError(Exception):
    """不可重试的 HTTP 错误（4xx 等），直接切换下一模型。"""

    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"HTTP {status_code}: {body}")


class LLMClient:
    """LLM 客户端"""

    def __init__(self):
        # 并发上限信号量按配置段惰性创建，从对应配置段的 max_concurrent 读取
        self._concurrency_sems: dict[str, asyncio.Semaphore] = {}
        # 复用的 aiohttp 连接池，惰性创建，跨调用复用
        self._session: Optional[aiohttp.ClientSession] = None

    @staticmethod
    def _get_section_config(config_obj: Any, config_section: str) -> Any:
        section = getattr(config_obj, config_section, None)
        if section is not None:
            return section
        return getattr(config_obj, "llm")

    def _ensure_config_loaded(self, config_section: str = "llm"):
        """确保配置已加载。"""
        global _warned_missing_api_key
        config_obj = get_config()
        section = self._get_section_config(config_obj, config_section)
        fallback_section = getattr(config_obj, "llm")
        has_model_api_key = any(str(model.api_key or "").strip() for model in getattr(section, "models", []))
        has_fallback_model_api_key = any(
            str(model.api_key or "").strip() for model in getattr(fallback_section, "models", [])
        )
        has_api_key = str(getattr(section, "api_key", "") or "").strip()
        fallback_api_key = str(getattr(fallback_section, "api_key", "") or "").strip()
        if (not has_api_key) and (not has_model_api_key) and (not fallback_api_key) and (not has_fallback_model_api_key) and (not _warned_missing_api_key):
            _warned_missing_api_key = True
            logger.warning("[规则怪谈] %s API Key 未配置", config_section)

    @staticmethod
    def _merge_dict(base: JsonObject, override: JsonObject) -> JsonObject:
        merged = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = LLMClient._merge_dict(merged[key], value)
            else:
                merged[key] = value
        return merged

    @staticmethod
    def _normalize_enabled_models(config: Any) -> list[JsonObject]:
        models: list[JsonObject] = []

        for item in getattr(config, "models", []) or []:
            if hasattr(item, "model_dump"):
                data = item.model_dump()
            elif isinstance(item, dict):
                data = dict(item)
            else:
                continue

            if not bool(data.get("enabled", True)):
                continue
            name = str(data.get("name", "") or "").strip()
            if not name:
                continue
            models.append(data)

        if models:
            return models

        for name in list(getattr(config, "model_list", []) or []):
            normalized_name = str(name or "").strip()
            if not normalized_name:
                continue
            models.append(
                {
                    "name": normalized_name,
                    "enabled": True,
                    "api_url": str(getattr(config, "api_url", "") or ""),
                    "api_key": str(getattr(config, "api_key", "") or ""),
                    "temperature": getattr(config, "temperature", None),
                    "max_tokens": getattr(config, "max_tokens", None),
                    "timeout": getattr(config, "timeout", None),
                    "headers": {},
                    "extra_body": {},
                }
            )
        return models

    @staticmethod
    def _resolve_runtime_config(config_obj: Any, config_section: str) -> tuple[Any, str]:
        section = LLMClient._get_section_config(config_obj, config_section)
        if config_section != "llm":
            section_enabled = bool(getattr(section, "enabled", True))
            section_has_models = bool(getattr(section, "models", []) or getattr(section, "model_list", []))
            if (not section_enabled) or (not section_has_models):
                return getattr(config_obj, "llm"), "llm"
        return section, config_section

    def _get_semaphore(self, config_section: str) -> asyncio.Semaphore:
        """获取指定配置段的并发信号量（惰性创建，从对应配置段读取 max_concurrent）。"""
        if config_section not in self._concurrency_sems:
            config_obj = get_config()
            section = self._get_section_config(config_obj, config_section)
            max_concurrent = int(section.max_concurrent)
            self._concurrency_sems[config_section] = asyncio.Semaphore(max_concurrent)
        return self._concurrency_sems[config_section]

    def _get_session(self) -> aiohttp.ClientSession:
        """获取复用的 aiohttp.ClientSession（惰性创建，连接池跨调用复用）。"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _http_post(
        self,
        session: aiohttp.ClientSession,
        api_url: str,
        headers: dict[str, Any],
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> Any:
        """对单个模型发起一次 HTTP POST 请求。

        - 200：返回解析后的 JSON 数据
        - 429/5xx：抛出 _RetryableHTTPError 触发指数退避重试
        - 其它非 200：抛出 _HTTPError 直接切换下一模型
        """
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with session.post(api_url, headers=headers, json=payload, timeout=timeout) as response:
            logger.info("[规则怪谈] API 响应状态: %s", response.status)
            if response.status == 200:
                return await response.json()
            body = await response.text()
            if response.status == 429 or 500 <= response.status < 600:
                logger.warning(
                    "[规则怪谈] 可重试错误 %s，body: %s",
                    response.status,
                    body,
                )
                raise _RetryableHTTPError(response.status, body)
            raise _HTTPError(response.status, body)

    async def _call_internal(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        config_section: str = "llm",
    ) -> LLMResponse:
        """调用指定配置段对应的 LLM。"""
        self._ensure_config_loaded(config_section)

        config_obj = get_config()
        config, resolved_section = self._resolve_runtime_config(config_obj, config_section)
        temp = temperature if temperature is not None else config.temperature
        tokens = max_tokens if max_tokens is not None else get_default_max_tokens(resolved_section)

        model_candidates = self._normalize_enabled_models(config)
        if not model_candidates:
            logger.error("[规则怪谈] %s 模型列表为空", resolved_section)
            raise LLMError("模型列表为空")

        # 默认 system_prompt 留空，强制调用方在业务侧显式传入所需的身份与约束。
        # inline prompt 应当自包含足够上下文，不应隐式依赖任何"规则怪谈设计师"身份。
        default_system_prompt = ""

        final_system_prompt = system_prompt if system_prompt else default_system_prompt
        last_error = None

        # 从配置段读取最大重试次数（语义为"重试次数"，首次调用不计入）
        max_retries = int(config.max_retries)
        # 复用连接池，不在每次调用时新建 ClientSession
        session = self._get_session()

        for model_config in model_candidates:
            model = str(model_config.get("name", "") or "").strip()
            api_url = str(model_config.get("api_url") or config.api_url or config_obj.llm.api_url or "").strip()
            api_key = str(model_config.get("api_key") or config.api_key or config_obj.llm.api_key or "").strip()
            headers = {
                "Content-Type": "application/json",
                **(config.default_headers if isinstance(config.default_headers, dict) else {}),
                **(model_config.get("headers", {}) if isinstance(model_config.get("headers"), dict) else {}),
            }
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            payload = self._merge_dict(
                config.default_body if isinstance(config.default_body, dict) else {},
                model_config.get("extra_body", {}) if isinstance(model_config.get("extra_body"), dict) else {},
            )
            payload.update(
                {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": final_system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": model_config.get("temperature", temp) if model_config.get("temperature") is not None else temp,
                    "max_tokens": (
                        model_config.get("max_tokens", tokens) if model_config.get("max_tokens") is not None else tokens
                    ),
                    "stream": False,
                }
            )

            timeout_seconds = int(model_config.get("timeout") or config.timeout or 180)
            logger.info("[规则怪谈] 尝试使用 %s 配置段模型 %s", resolved_section, model)

            # 429/5xx 及网络错误先按 max_retries 指数退避重试，重试耗尽再切换下一模型
            retrying = AsyncRetrying(
                stop=stop_after_attempt(max_retries + 1),
                wait=wait_exponential(multiplier=1, min=1, max=60),
                retry=retry_if_exception_type(
                    (_RetryableHTTPError, aiohttp.ClientError, asyncio.TimeoutError)
                ),
                reraise=True,
            )
            try:
                logger.info("[规则怪谈] 调用 LLM API: %s", api_url)
                data = await retrying(
                    self._http_post,
                    session,
                    api_url,
                    headers,
                    payload,
                    timeout_seconds,
                )
            except (_RetryableHTTPError, aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.error(
                    "[规则怪谈] 模型 %s 重试 %s 次后仍失败: %s",
                    model,
                    max_retries,
                    e,
                )
                last_error = str(e)
                continue
            except _HTTPError as e:
                logger.error(
                    "[规则怪谈] 模型 %s API请求失败(不可重试): Status %s, Body: %s",
                    model,
                    e.status_code,
                    e.body,
                )
                last_error = str(e)
                continue

            if isinstance(data, list):
                logger.warning("[规则怪谈] 模型 %s API返回列表格式: %s", model, data)
                last_error = "API返回列表格式"
                continue

            if not isinstance(data, dict):
                logger.warning("[规则怪谈] 模型 %s API返回非字典格式: %s", model, type(data))
                last_error = f"API返回非字典格式: {type(data)}"
                continue

            choices = data.get("choices", [])
            if not choices or not isinstance(choices, list):
                logger.warning("[规则怪谈] 模型 %s choices字段格式错误: %s", model, choices)
                last_error = "choices字段格式错误"
                continue

            first_choice = choices[0]
            if not isinstance(first_choice, dict):
                logger.warning("[规则怪谈] 模型 %s choices[0]格式错误: %s", model, first_choice)
                last_error = "choices[0]格式错误"
                continue

            message = first_choice.get("message", {})
            if not isinstance(message, dict):
                logger.warning("[规则怪谈] 模型 %s message字段格式错误: %s", model, message)
                last_error = "message字段格式错误"
                continue

            content = _extract_message_content(message).strip()
            if not content:
                raw_content = message.get("content") if isinstance(message, dict) else None
                logger.warning(
                    "[规则怪谈] 模型 %s content为空/不可用: type=%s, value=%r",
                    model,
                    type(raw_content),
                    raw_content,
                )
                last_error = "content为空"
                continue

            logger.info("[规则怪谈] 模型 %s 调用成功，生成 %s 字符", model, len(content))
            usage = data.get("usage", {})
            return LLMResponse(
                content=content,
                model=model,
                usage=usage if isinstance(usage, dict) else {},
                raw_response=data,
            )

        logger.error(f"[规则怪谈] 所有模型都调用失败，最后错误: {last_error}")
        raise LLMError(f"所有模型都调用失败: {last_error}")

    async def call(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        config_section: str = "llm",
    ) -> LLMResponse:
        """调用指定配置段的 LLM。"""
        # 通过信号量限制全局并发，避免多玩家同时行动打爆 API。
        # 注意：call_main / call_npc_sim / call_with_fallback 等方法内部
        # 均通过 call 进入实际请求，因此只在 call 加 Semaphore 即可，
        # 不在其内部嵌套调用上重复获取，避免 asyncio.Semaphore 不可重入导致的死锁。
        async with self._get_semaphore(config_section):
            return await self._call_internal(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                config_section=config_section,
            )

    async def call_main(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """使用主流程 llm 配置调用模型。"""
        return await self.call(prompt, system_prompt, temperature, max_tokens, config_section="llm")

    async def call_npc_sim(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """使用 npc_sim 配置调用模型。"""
        return await self.call(prompt, system_prompt, temperature, max_tokens, config_section="npc_sim")

    async def call_with_fallback(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        config_section: str = "llm",
    ) -> LLMResponse:
        """调用 LLM，支持模型故障转移"""
        return await self.call(prompt, system_prompt, temperature, max_tokens, config_section=config_section)

    async def close(self) -> None:
        """关闭客户端，释放复用的 aiohttp 连接池。"""
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None
