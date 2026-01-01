# src/plugins/rule_horror/plugin.py
import os
import json
import random
import re
import asyncio
import aiohttp
import base64
from typing import List, Tuple, Type, Optional
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
import numpy as np
from src.plugin_system import (
    BasePlugin,
    register_plugin,
    BaseCommand,
    ComponentInfo,
    ConfigField
)
from src.plugin_system.apis import send_api

PLUGIN_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(PLUGIN_DIR, "data")
TEMP_IMAGES_DIR = os.path.join(DATA_DIR, "temp_images")

game_states = {}

@register_plugin
class RuleHorrorPlugin(BasePlugin):
    """规则怪谈插件 - 生成规则怪谈并进行互动"""

    plugin_name = "rule_horror"
    plugin_description = "生成规则怪谈并进行互动游戏。"
    plugin_version = "1.4.0"
    plugin_author = "岚影鸿夜"
    enable_plugin = True

    dependencies = []
    python_dependencies = ["aiohttp"]

    config_file_name = "config.toml"
    config_section_descriptions = {
        "plugin": "插件启用配置",
        "llm": "LLM API 配置"
    }

    config_schema = {
        "plugin": {
            "enabled": ConfigField(
                type=bool,
                default=True,
                description="是否启用规则怪谈插件"
            ),
            "config_version": ConfigField(
                type=str,
                default="1.0.0",
                description="配置文件版本"
            ),
        },
        "llm": {
            "api_url": ConfigField(
                type=str,
                default="http://rinkoai.com/v1/chat/completions",
                description="LLM API 地址 (OpenAI格式)"
            ),
            "api_key": ConfigField(
                type=str,
                default="YOUR_API_KEY",
                description="LLM API 密钥"
            ),
            "model": ConfigField(
                type=str,
                default="deepseek-ai/DeepSeek-V3",
                description="使用的LLM模型名称"
            ),
            "temperature": ConfigField(
                type=float,
                default=0.8,
                description="LLM 生成文本的随机性 (0.0-1.0)"
            )
        }
    }

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        return [
            (RuleHorrorCommand.get_command_info(), RuleHorrorCommand),
        ]


class RuleHorrorCommand(BaseCommand):
    """处理 /rg 命令"""

    command_name = "RuleHorrorCommand"
    command_description = "规则怪谈游戏：生成规则怪谈、加入/离开、提示、推理、行动、结束"
    command_pattern = r"^/rg\s+(?P<action>\S+)(?:\s+(?P<rest>.+))?$"
    command_help = (
        "规则怪谈游戏：\n"
        "/rg 开始 单人/多人 - 开始新游戏（单人模式自动加入，多人模式需要手动加入）\n"
        "/rg 强制开始 单人/多人 - 强制开始新游戏（覆盖存档）\n"
        "/rg 恢复 - 恢复默认存档游戏\n"
        "/rg 保存 <存档名称> - 手动保存当前游戏状态\n"
        "/rg 读取 <存档名称> - 从指定存档读取游戏\n"
        "/rg 存档列表 - 查看所有可用存档\n"
        "/rg 加入 - 加入游戏（多人模式，最多5人）\n"
        "/rg 离开 - 离开游戏\n"
        "/rg 状态 - 查看游戏状态\n"
        "/rg 剧情 - 查看剧情导入\n"
        "/rg 规则 - 查看当前规则\n"
        "/rg 场景 - 查看场景结构\n"
        "/rg 提示 <规则/线索> - 获取提示（剩余3次）\n"
        "/rg 推理 <推理内容> - 记录你的推理\n"
        "/rg 行动 <行动描述> - 描述你的行动\n"
        "/rg 结束 - 结束游戏并判定结局\n"
        "/rg 帮助 - 查看帮助"
    )
    command_examples = [
        "/rg 开始 单人", "/rg 开始 多人", "/rg 强制开始 单人", "/rg 恢复", "/rg 保存 存档1", "/rg 读取 存档1", "/rg 存档列表", "/rg 加入", "/rg 离开", "/rg 状态", "/rg 剧情", "/rg 规则", "/rg 场景",
        "/rg 提示 规则", "/rg 提示 线索",
        "/rg 推理 我认为规则3是关键", "/rg 行动 我决定进入房间",
        "/rg 结束", "/rg 帮助"
    ]
    intercept_message = True

    async def execute(self) -> Tuple[bool, Optional[str], bool]:
        matched_groups = self.matched_groups if self.matched_groups is not None else {}
        action = matched_groups.get("action", "") if matched_groups.get("action") is not None else ""
        rest_input = matched_groups.get("rest", "") if matched_groups.get("rest") is not None else ""

        action = str(action).strip()
        rest_input = str(rest_input).strip()

        chat_stream = getattr(self, 'chat_stream', None)
        if chat_stream is None:
            message_obj = getattr(self, 'message', None)
            if message_obj:
                chat_stream = getattr(message_obj, 'chat_stream', None)

        if chat_stream is None:
            await self.send_text("无法获取聊天上下文信息。")
            return False, "缺少聊天上下文", True

        stream_id = getattr(chat_stream, 'stream_id', None)
        if stream_id is None:
            await self.send_text("无法获取聊天流ID。")
            return False, "缺少聊天流ID", True

        enabled = self.get_config("plugin.enabled", True)
        if not enabled:
            await self.send_text("规则怪谈插件已被禁用。")
            return False, "插件未启用", True

        api_url = self.get_config("llm.api_url", "").strip()
        api_key = self.get_config("llm.api_key", "").strip()
        model = self.get_config("llm.model", "deepseek-ai/DeepSeek-V3")
        temperature = self.get_config("llm.temperature", 0.8)

        group_id = getattr(chat_stream, 'group_info', None)
        if group_id:
            group_id = group_id.group_id
        else:
            user_id = getattr(chat_stream, 'user_info', None)
            if user_id:
                group_id = user_id.user_id
            else:
                group_id = "unknown"

        game_state = game_states.get(group_id, {})
        if group_id not in game_states:
            game_states[group_id] = game_state

        if action == "开始":
            game_mode = rest_input.strip() if rest_input else ""
            if game_mode not in ["单人", "多人"]:
                await self.send_text("请指定游戏模式。用法：`/rg 开始 单人` 或 `/rg 开始 多人`")
                return False, "缺少游戏模式", True
            return await self._start_new_game(group_id, api_url, api_key, model, temperature, game_mode)

        elif action == "强制开始":
            game_mode = rest_input.strip() if rest_input else ""
            if game_mode not in ["单人", "多人"]:
                await self.send_text("请指定游戏模式。用法：`/rg 强制开始 单人` 或 `/rg 强制开始 多人`")
                return False, "缺少游戏模式", True
            return await self._force_start_new_game(group_id, api_url, api_key, model, temperature, game_mode)

        elif action == "恢复":
            return await self._restore_game(group_id)

        elif action == "保存":
            if not game_state or not game_state.get("game_active", False):
                await self.send_text("当前没有正在进行的游戏。请先使用 `/rg 开始` 开始游戏。")
                return False, "无游戏", True

            save_name = rest_input.strip() if rest_input else ""
            if not save_name:
                await self.send_text("请提供存档名称。用法：`/rg 保存 <存档名称>`")
                return False, "缺少存档名称", True

            return await self._save_game_with_name(group_id, save_name)

        elif action == "读取":
            save_name = rest_input.strip() if rest_input else ""
            if not save_name:
                await self.send_text("请提供存档名称。用法：`/rg 读取 <存档名称>`")
                return False, "缺少存档名称", True

            if game_state and game_state.get("game_active", False):
                await self.send_text("当前有正在进行的游戏。使用 `/rg 读取` 将覆盖当前游戏状态。如需继续当前游戏，请忽略此命令。")
            
            return await self._load_game_with_name(group_id, save_name)

        elif action == "存档列表":
            return await self._list_saves(group_id)

        elif action == "加入":
            if not game_state.get("game_active", False):
                await self.send_text("当前没有正在进行的游戏。请先使用 `/rg 开始` 开始游戏。")
                return False, "无游戏", True

            return await self._join_game(group_id)

        elif action == "离开":
            if not game_state.get("game_active", False):
                await self.send_text("当前没有正在进行的游戏。")
                return False, "无游戏", True

            return await self._leave_game(group_id)

        elif action == "状态":
            if not game_state.get("game_active", False):
                await self.send_text("当前没有正在进行的游戏。")
                return False, "无游戏", True

            return await self._show_game_status(group_id)

        elif action == "规则":
            if not game_state.get("game_active", False):
                await self.send_text("当前没有正在进行的游戏。请先使用 `/rg 开始` 开始游戏。")
                return False, "无游戏", True

            return await self._show_rules(group_id)

        elif action == "场景":
            if not game_state.get("game_active", False):
                await self.send_text("当前没有正在进行的游戏。请先使用 `/rg 开始` 开始游戏。")
                return False, "无游戏", True

            return await self._show_scene(group_id)

        elif action == "剧情":
            if not game_state.get("game_active", False):
                await self.send_text("当前没有正在进行的游戏。请先使用 `/rg 开始` 开始游戏。")
                return False, "无游戏", True

            return await self._show_plot(group_id)

        elif action == "提示":
            if not game_state.get("game_active", False):
                await self.send_text("当前没有正在进行的游戏。请先使用 `/rg 开始` 开始游戏。")
                return False, "无游戏", True

            hint_type = rest_input if rest_input else "规则"
            return await self._provide_hint(group_id, hint_type, api_url, api_key, model, temperature)

        elif action == "推理":
            if not game_state.get("game_active", False):
                await self.send_text("当前没有正在进行的游戏。请先使用 `/rg 开始` 开始游戏。")
                return False, "无游戏", True

            if not rest_input:
                await self.send_text("请提供推理内容。用法：`/rg 推理 <推理内容>`")
                return False, "缺少推理内容", True

            return await self._record_reasoning(group_id, rest_input, api_url, api_key, model, temperature)

        elif action == "行动":
            if not game_state.get("game_active", False):
                await self.send_text("当前没有正在进行的游戏。请先使用 `/rg 开始` 开始游戏。")
                return False, "无游戏", True

            if not rest_input:
                await self.send_text("请提供行动描述。用法：`rg 行动 <行动描述>`")
                return False, "缺少行动描述", True

            return await self._record_action(group_id, rest_input, api_url, api_key, model, temperature)

        elif action == "继续":
            if not game_state.get("game_active", False):
                await self.send_text("当前没有正在进行的游戏。")
                return False, "无游戏", True

            if not game_state.get("has_cleared", False):
                await self.send_text("你尚未达成通关条件，无法继续探索。")
                return False, "未通关", True

            return await self._continue_to_perfect(group_id, api_url, api_key, model, temperature)

        elif action == "结束":
            if not game_state.get("game_active", False):
                await self.send_text("当前没有正在进行的游戏。")
                return False, "无游戏", True

            return await self._end_game(group_id, api_url, api_key, model, temperature)

        elif action == "帮助":
            help_text = (
                "**规则怪谈游戏帮助**\n\n"
                "**命令列表**\n"
                "- `/rg 开始 单人` - 开始单人模式游戏（自动加入）\n"
                "- `/rg 开始 多人` - 开始多人模式游戏（最多5人，需手动加入）\n"
                "- `/rg 强制开始 单人/多人` - 强制开始新游戏（覆盖存档）\n"
                "- `/rg 恢复` - 恢复默认存档游戏\n"
                "- `/rg 保存 <存档名称>` - 手动保存当前游戏状态\n"
                "- `/rg 读取 <存档名称>` - 从指定存档读取游戏\n"
                "- `/rg 存档列表` - 查看所有可用存档\n"
                "- `/rg 加入` - 加入当前游戏（多人模式）\n"
                "- `/rg 离开` - 离开当前游戏\n"
                "- `/rg 状态` - 查看游戏状态和玩家信息\n"
                "- `/rg 剧情` - 查看剧情导入\n"
                "- `/rg 规则` - 查看当前规则和通关条件\n"
                "- `/rg 场景` - 查看场景结构和环境状况\n"
                "- `/rg 提示 <规则/线索>` - 获取提示（规则验证或线索，剩余3次）\n"
                "- `/rg 推理 <推理内容>` - 记录你的推理\n"
                "- `/rg 行动 <行动描述>` - 描述你的行动\n"
                "- `/rg 继续` - 达成通关后继续探索完美结局\n"
                "- `/rg 结束` - 结束游戏并判定结局\n"
                "- `/rg 帮助` - 查看帮助\n\n"
                "**游戏提示**\n"
                "- 规则怪谈包含多条规则，你需要推理出规则的真实含义\n"
                "- 单人模式：你独自挑战，自动加入游戏\n"
                "- 多人模式：最多5人同时参与，每人独立推理和行动\n"
                "- 你有3次提示机会，可以选择规则验证或获取线索\n"
                "- 通过推理和行动来达成通关条件\n"
                "- 当达成通关条件时，系统会自动判定并询问是否继续探索完美结局\n"
                "- 死亡的玩家无法继续推理和行动，但可以观看其他玩家\n"
                "- 完美结局需要同时满足：推理出规则怪谈的原貌、达成通关要求、解除规则怪谈（解决根源）\n"
                "- 结局分为：完美（满足三个条件）、成功（推理出原貌并通关）、通关（仅通关）、失败（死亡或未通关）\n"
                "- 游戏会自动保存，中断后可以使用 `/rg 恢复` 继续游戏\n"
                "- 使用 `/rg 保存 <存档名称>` 可以创建多个存档，方便在不同进度间切换"
            )
            await self.send_text(help_text)
            return True, "已发送帮助信息", True

        else:
            await self.send_text("未知命令。请使用 `/rg 帮助` 查看可用命令。")
            return False, "未知命令", True

    async def _start_new_game(self, group_id: str, api_url: str, api_key: str, model: str, temperature: float, game_mode: str) -> Tuple[bool, Optional[str], bool]:
        """开始一个新的规则怪谈游戏"""
        saved_state = self._load_game_state(group_id)
        if saved_state and saved_state.get("game_active", False):
            await self.send_text(
                "**发现存档**\n\n"
                "该群组/用户已有未完成的游戏存档。\n"
                "请使用 `/rg 恢复` 恢复存档，或使用 `/rg 强制开始 单人/多人` 强制开始新游戏（会覆盖存档）。"
            )
            return False, "存在存档", True
        
        await self.send_text("正在生成规则怪谈...")

        step1_prompt = """
你是一个专业的规则怪谈生成器。请生成一个恐怖或诡异的规则怪谈的剧情导入。

要求：
1. 生成一个场景（如：深夜的医院、废弃的学校、神秘的公寓、古老的庄园等）
2. 描述场景的背景故事（这个场景的历史、发生过什么、为什么诡异）
3. 描述玩家在这个场景中的身份或角色（如：工厂员工、夜班护士、新入职教师、庄园管家等），身份应与场景和剧情相符
4. 剧情应该充满悬疑和恐怖氛围，为后续的规则和探索做铺垫
5. 生成2-3个"核心象征符号"，这些符号将在整个游戏中反复出现，营造主题感和不安感。符号可以是数字、图案、旋律、花纹、颜色等。每个符号需要有一个简短的描述，暗示其可能的含义或与场景的联系。
6. 以JSON格式返回，格式如下：
{
  "scene": "场景名称（如：深夜的废弃医院）",
  "background": "场景背景故事，描述这个场景的历史、发生过什么、为什么诡异",
  "player_identity": "玩家在这个场景中的身份或角色",
  "core_symbols": [
    {"symbol": "符号1", "description": "符号1的描述"},
    {"symbol": "符号2", "description": "符号2的描述"}
  ]
}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
        """

        llm_response = await self._call_llm_api(step1_prompt, api_url, api_key, model, temperature)
        if not llm_response:
            await self.send_text("调用LLM API失败，请稍后再试。")
            return False, "LLM API调用失败", True

        print(f"[规则怪谈] 第一步（剧情导入）LLM原始返回: {llm_response}")

        try:
            step1_data = json.loads(llm_response)
        except json.JSONDecodeError as e:
            print(f"[规则怪谈] 第一步JSON解析失败: {e}")
            print(f"[规则怪谈] 尝试提取JSON部分...")
            
            json_match = re.search(r'\{[\s\S]*\}', llm_response)
            if json_match:
                try:
                    step1_data = json.loads(json_match.group())
                    print(f"[规则怪谈] 第一步成功提取JSON")
                except json.JSONDecodeError as e2:
                    print(f"[规则怪谈] 第一步提取JSON后仍然解析失败: {e2}")
                    await self.send_text("生成剧情导入失败，返回格式不正确。")
                    return False, "JSON解析失败", True
            else:
                await self.send_text("生成剧情导入失败，返回格式不正确。")
                return False, "JSON解析失败", True

        scene_name = step1_data.get("scene", "")
        background = step1_data.get("background", "")
        player_identity = step1_data.get("player_identity", "")
        core_symbols = step1_data.get("core_symbols", [])

        await asyncio.sleep(0.5)
        
        try:
            plot_image_path = self._generate_plot_image(scene_name, background, player_identity, core_symbols)
            game_states[group_id]["plot_image_path"] = plot_image_path
            with open(plot_image_path, 'rb') as f:
                image_bytes = f.read()
            image_base64 = base64.b64encode(image_bytes).decode('ascii')
            image_sent = await self.send_image(image_base64)
            if not image_sent:
                print(f"[规则怪谈] 剧情导入图片发送失败")
            await asyncio.sleep(1.0)
        except Exception as e:
            print(f"[规则怪谈] 生成剧情导入长图失败: {str(e)}")
            step1_text = (
                f"**规则怪谈** ({game_mode}模式)\n\n"
                f"**剧情导入**：\n{background}\n\n"
                f"**你们的身份**：\n{player_identity}\n\n"
                f"**场景**：{scene_name}"
            )
            await self.send_text(step1_text)
            await asyncio.sleep(1.0)
        
        await self.send_text("正在生成场景结构...")
        await asyncio.sleep(1.0)

        step2_prompt = f"""
你是一个专业的规则怪谈生成器。请基于以下剧情导入，生成场景结构。

剧情导入：
- 场景：{scene_name}
- 背景：{background}
- 玩家身份：{player_identity}

要求：
1. 确定建筑类型（如：医院、学校、公寓、庄园等）
2. 描述建筑的总体布局（如：L型、U型、回字形、多层建筑等）
3. 列出所有楼层（包括地上和地下），每层列出主要区域
4. 列出通道、楼梯、电梯等连接方式
5. 列出特殊区域（如：地下室、天台、禁闭室等）
6. 场景结构应该与剧情导入的背景和氛围相符
7. 以JSON格式返回，格式如下：
{{
  "building_type": "建筑类型",
  "overall_layout": "建筑总体布局描述",
  "floors": [
    {{
      "floor": "楼层名称",
      "areas": ["区域1", "区域2", "区域3"]
    }}
  ],
  "connections": ["通道1", "通道2", "通道3"],
  "special_areas": ["特殊区域1", "特殊区域2"]
}}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
        """

        llm_response = await self._call_llm_api(step2_prompt, api_url, api_key, model, temperature)
        if not llm_response:
            await self.send_text("调用LLM API失败，请稍后再试。")
            return False, "LLM API调用失败", True

        print(f"[规则怪谈] 第二步（场景结构）LLM原始返回: {llm_response}")

        try:
            step2_data = json.loads(llm_response)
        except json.JSONDecodeError as e:
            print(f"[规则怪谈] 第二步JSON解析失败: {e}")
            print(f"[规则怪谈] 尝试提取JSON部分...")
            
            json_match = re.search(r'\{[\s\S]*\}', llm_response)
            if json_match:
                try:
                    step2_data = json.loads(json_match.group())
                    print(f"[规则怪谈] 第二步成功提取JSON")
                except json.JSONDecodeError as e2:
                    print(f"[规则怪谈] 第二步提取JSON后仍然解析失败: {e2}")
                    await self.send_text("生成场景结构失败，返回格式不正确。")
                    return False, "JSON解析失败", True
            else:
                await self.send_text("生成场景结构失败，返回格式不正确。")
                return False, "JSON解析失败", True

        building_type = step2_data.get("building_type", "")
        overall_layout = step2_data.get("overall_layout", "")
        floors = step2_data.get("floors", [])
        connections = step2_data.get("connections", [])
        special_areas = step2_data.get("special_areas", [])

        floors_text = "\n".join([f"{floor['floor']}: {', '.join(floor['areas'])}" for floor in floors])
        connections_text = ", ".join(connections)
        special_areas_text = ", ".join(special_areas)

        await asyncio.sleep(0.5)
        
        try:
            scene_structure_image_path = self._generate_scene_structure_text_image(
                building_type, overall_layout, floors, connections, special_areas
            )
            with open(scene_structure_image_path, 'rb') as f:
                image_bytes = f.read()
            image_base64 = base64.b64encode(image_bytes).decode('ascii')
            image_sent = await self.send_image(image_base64)
            if not image_sent:
                print(f"[规则怪谈] 场景结构图片发送失败")
            await asyncio.sleep(0.5)
            
            game_state = game_states.get(group_id, {})
            game_state["scene_structure_image_path"] = scene_structure_image_path
            self._save_game_state(group_id)
        except Exception as e:
            print(f"[规则怪谈] 生成场景结构长图失败: {str(e)}")
            floors_text = "\n".join([f"  - {floor['floor']}: {', '.join(floor['areas'])}" for floor in floors])

            step2_text = f"""**场景结构**：

**建筑类型**：{building_type}

**总体布局**：{overall_layout}

**楼层布局**：
{floors_text}

**连接通道**：{connections_text}

**特殊区域**：{special_areas_text}"""
            await self.send_text(step2_text)
            await asyncio.sleep(0.5)

        scene_structure_text = f"建筑类型：{building_type}\n"
        scene_structure_text += "\n".join([f"{floor['floor']}: {', '.join(floor['areas'])}" for floor in floors])
        scene_structure_text += f"\n连接通道：{connections_text}\n"
        scene_structure_text += f"特殊区域：{special_areas_text}"

        await asyncio.sleep(0.5)
        
        await self.send_text("正在生成场景剖面图...")
        
        scene_image_path = None
        
        try:
            scene_data = {
                "building_type": building_type,
                "overall_layout": overall_layout,
                "floors": floors,
                "connections": connections,
                "special_areas": special_areas
            }
            
            image_path = self._generate_cross_section_view(scene_data)
            scene_image_path = image_path
            game_states[group_id]["scene_image_path"] = scene_image_path
            
            with open(image_path, 'rb') as f:
                image_bytes = f.read()
            
            image_base64 = base64.b64encode(image_bytes).decode('ascii')
            
            # 确保图像发送完成后再继续
            image_sent = await self.send_image(image_base64)
            if not image_sent:
                raise Exception("图像发送失败")
            
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"[规则怪谈] 生成场景剖面图失败: {str(e)}")
            await self.send_text("场景剖面图生成失败，继续生成规则...")
        
        await self.send_text("正在生成规则...")
        await asyncio.sleep(0.5)

        step3_prompt = f"""
你是一个专业的规则怪谈生成器。请基于以下剧情导入和场景结构，生成规则怪谈的规则。

剧情导入：
- 场景：{scene_name}
- 背景：{background}
- 玩家身份：{player_identity}

场景结构：
{scene_structure_text}

游戏模式：{game_mode}

要求：
1. 列出5-8条规则，规则应该看似合理但隐藏着诡异之处
2. 规则应该与剧情导入和场景结构相呼应
3. 设定通关条件（如：在规定时间内找到出口、收集特定物品、存活到天亮等）
4. 设定解除条件（如：找到规则怪谈的根源并消除它、找到某个特定物品并使用、完成某个仪式等）
5. 规则应该有隐藏的逻辑和真相，需要玩家推理
6. **规则与环境绑定（非常重要）**：请将至少2-3条规则与场景中特定的、可交互的环境细节直接关联。例如，如果规则是"不要理会走廊尽头的呼救声"，那么与之关联的环境可以是"走廊尽头的温度总是异常低，且墙上有抓痕"。这样，玩家在探索到该位置时，能通过环境感知强化对规则的记忆和怀疑
7. **规则间的潜在冲突（非常重要）**：请尝试构建至少一组存在潜在矛盾的规则。例如，规则A："午夜后必须留在自己的房间内。" 规则B："公寓中没有404室。" 实际上公寓中有404室，但是仅在午夜后才会出现，此时玩家将陷入遵守A还是出门寻找404室的两两难境地。请在 hidden_truth 中解释这种矛盾的本质（如：两条规则来自不同势力），并在 death_triggers 中隐含相关触发条件
8. **规则与真相的因果关系（非常重要）**：每条规则都应该与隐藏真相中的某个要素有直接的因果关系。规则不是孤立的，而是形成了一个相互关联的规则网络。例如：
   - 如果真相是"工厂的夜间保安是来自异世界的实体"，那么规则"夜间只允许蓝色制服的保安巡逻"就是对这个真相的伪装性描述
   - 如果真相是"三楼东侧病房的窗户是通往异界的通道"，那么规则"三楼东侧病房的窗户必须保持关闭状态"就是对这个危险通道的防护措施
   - 规则之间应该形成推理链条：遵守规则A -> 发现异常B -> 触发规则C -> 揭示真相D
   - 在 hidden_truth 中明确说明每条规则与真相要素的对应关系，以及规则之间的推理链条
9. **协作规则（多人模式非常重要）**：如果游戏模式是"多人"，请设计1-2条需要多个玩家协作才能发现或触发的规则。例如：
   - 规则A："当两名玩家同时站在不同的位置时，某个隐藏的通道才会开启"
   - 规则B："只有当一名玩家持有特定物品，另一名玩家说出特定口令时，才能解除某个陷阱"
   - 规则C："需要三名玩家分别在三个不同的地点同时执行某个动作，才能揭示某个关键真相"
   - 协作规则应该鼓励玩家之间的沟通和合作，而不是各自为战
   - 协作规则的设计应该巧妙，让玩家在探索过程中自然地发现协作的必要性
   - 在 hidden_truth 中说明协作规则的设计意图和触发条件
10. **规则标题（非常重要）**：根据场景类型和玩家身份，生成一个贴合剧情的规则标题。例如：
   - 工厂场景：员工守则、安全规程、操作手册
   - 医院场景：患者须知、病房守则、医疗规程
   - 学校场景：学生守则、校园安全须知、宿舍管理规定
   - 城堡场景：访客须知、城堡守则、安全指南
   - 酒店场景：入住须知、客房服务守则、安全警示
   - 超市场景：员工手册、营业规范、安全须知
   - 地铁场景：乘客须知、安全规程、运营守则
   标题应该简洁、正式，符合该场景的官方文件风格

**规则描述要求（非常重要）：**
- 规则必须简洁、直接，每条规则不超过60字
- 只说明禁止、允许或要求做的行为，不解释原因
- 使用标准格式：禁止XX / 当XX时，必须XX / 只有XX时才能XX / 必须XX / 严禁XX
- 使用冰冷、客观的公文语调，如同官方通告或操作手册
- 语调应该冷静、正式、不带感情色彩
- 可以加入少量关键的环境或感官细节，但要简洁
- 细节应该让人感到不安和恐惧，但不要直接揭示真相

示例规则风格：
"禁止在22:00-06:00期间离开房间。"
"听到三声敲门时，必须立即开门。"
"三楼东侧病房的窗户必须保持关闭状态。若发现窗户自行开启，请立即通知安保人员并远离开启的窗户。"
"严禁回应任何呼救声。"
"只有看到绿色灯光时才能进入走廊。"
"工厂只有蓝色制服的保安，若看见黑色制服的保安，请立即报告主管。"
"城堡内没有镜子，如果你觉得你看到了镜子，请相信那是你的幻觉。"

以JSON格式返回，格式如下：
{{
  "rules_title": "规则标题（如：员工守则、患者须知等）",
  "rules": ["规则1", "规则2", ...],
  "win_condition": "通关条件",
  "resolve_condition": "解除条件（解决规则怪谈根源的条件）",
  "hidden_truth": "隐藏的真相（不显示给玩家）",
  "death_triggers": ["会导致死亡的行为1", "会导致死亡的行为2", ...]
}}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
        """

        llm_response = await self._call_llm_api(step3_prompt, api_url, api_key, model, temperature)
        if not llm_response:
            await self.send_text("调用LLM API失败，请稍后再试。")
            return False, "LLM API调用失败", True

        print(f"[规则怪谈] 第三步（规则）LLM原始返回: {llm_response}")

        try:
            step3_data = json.loads(llm_response)
        except json.JSONDecodeError as e:
            print(f"[规则怪谈] 第三步JSON解析失败: {e}")
            print(f"[规则怪谈] 尝试提取JSON部分...")
            
            json_match = re.search(r'\{[\s\S]*\}', llm_response)
            if json_match:
                try:
                    step3_data = json.loads(json_match.group())
                    print(f"[规则怪谈] 第三步成功提取JSON")
                except json.JSONDecodeError as e2:
                    print(f"[规则怪谈] 第三步提取JSON后仍然解析失败: {e2}")
                    await self.send_text("生成规则失败，返回格式不正确。")
                    return False, "JSON解析失败", True
            else:
                await self.send_text("生成规则失败，返回格式不正确。")
                return False, "JSON解析失败", True

        rules_image_path = None
        
        max_players = 5 if game_mode == "多人" else 1

        game_states[group_id] = {
            "scene": scene_name,
            "background": background,
            "player_identity": player_identity,
            "building_type": building_type,
            "overall_layout": overall_layout,
            "floors": floors,
            "connections": connections,
            "special_areas": special_areas,
            "rules_title": step3_data.get("rules_title", "规则"),
            "rules": step3_data.get("rules", []),
            "win_condition": step3_data.get("win_condition", ""),
            "resolve_condition": step3_data.get("resolve_condition", ""),
            "hidden_truth": step3_data.get("hidden_truth", ""),
            "death_triggers": step3_data.get("death_triggers", []),
            "hints_used": 0,
            "max_hints": 3,
            "game_active": True,
            "max_players": max_players,
            "game_mode": game_mode,
            "players": {},
            "scene_image_path": scene_image_path,
            "rules_image_path": rules_image_path,
            "scene_structure_image_path": None,
            "time_system": {
                "start_time": datetime.now().isoformat(),
                "current_time": "深夜",
                "elapsed_minutes": 0,
                "time_description": "午夜时分，周围一片死寂"
            },
            "environment": {
                "lighting": "昏暗",
                "temperature": "寒冷",
                "sounds": ["寂静"],
                "smells": ["霉味"],
                "atmosphere": "压抑"
            },
            "random_events": [],
            "available_items": [],
            "environmental_events": [],
            "rule_mutations": [],
            "core_symbols": core_symbols,
            "sanity_break": False,
            "last_mutation_time": 0,
            "identity_changes": [],
            "environment_memory": {
                "visited_locations": [],
                "interacted_objects": [],
                "time_based_events": [],
                "discovered_secrets": []
            },
            "rule_network": {
                "truth_elements": [],
                "rule_truth_mappings": [],
                "rule_dependencies": [],
                "discovered_truths": []
            },
            "collaborative_events": []
        }

        self._save_game_state(group_id)

        await self._build_rule_network(group_id)

        rules_title = step3_data.get("rules_title", "规则")
        rules = step3_data.get("rules", [])
        win_condition = step3_data.get('win_condition', '')

        rules_image_path = None
        
        try:
            rules_image_path = self._generate_rules_image(rules_title, rules, win_condition, game_mode)
            game_states[group_id]["rules_image_path"] = rules_image_path
            with open(rules_image_path, 'rb') as f:
                image_bytes = f.read()
            image_base64 = base64.b64encode(image_bytes).decode('ascii')
            
            # 确保图像发送完成后再继续
            image_sent = await self.send_image(image_base64)
            if not image_sent:
                raise Exception("规则长图发送失败")
            
            # 增加延迟，确保图像完全显示后再发送后续消息
            await asyncio.sleep(1.5)
        except Exception as e:
            print(f"[规则怪谈] 生成规则长图失败: {str(e)}")
            step3_text = f"**{rules_title}**：\n"
            for i, rule in enumerate(rules, 1):
                step3_text += f"{i}. {rule}\n"
            step3_text += f"\n**你的目标是**：{win_condition}"
            await self.send_text(step3_text)
            await asyncio.sleep(0.5)

        if game_mode == "单人":
            user_info = self._get_user_info()
            if user_info:
                user_id = user_info.user_id
                user_name = getattr(user_info, 'user_name', f"玩家{user_id}")
                game_states[group_id]["players"][user_id] = {
                    "name": user_name,
                    "reasoning_history": [],
                    "action_history": [],
                    "is_alive": True,
                    "current_identity": game_state.get("player_identity", ""),
                    "personal_rules": game_state.get("rules", []).copy(),
                    "physical_status": {
                        "health": 100,
                        "injury": "无",
                        "fatigue": "无"
                    },
                    "mental_status": {
                        "sanity": 100,
                        "state": "正常",
                        "emotion": "平静"
                    },
                    "psychological_pressure": {
                        "fear_level": 0,
                        "anxiety_level": 0,
                        "stress_level": 0
                    },
                    "inventory": [],
                    "location": "入口"
                }
                self._save_game_state(group_id)
                player_text = f"**玩家**：{user_name}\n"
            else:
                player_text = f"**玩家**：0/1\n"

            player_text += f"**提示次数**：0/3\n\n"
            player_text += f"- 使用 `/rg 提示 <规则/线索>` 获取提示\n"
            player_text += f"- 使用 `/rg 推理 <推理内容>` 记录推理\n"
            player_text += f"- 使用 `/rg 行动 <行动描述>` 描述行动\n"
            player_text += f"- 使用 `/rg 状态` 查看游戏状态\n"
            player_text += f"- 使用 `/rg 结束` 结束游戏"

            await self.send_text(player_text)
        else:
            try:
                multiplayer_start_image_path = self._generate_multiplayer_start_image(max_players=5)
                game_states[group_id]["multiplayer_start_image_path"] = multiplayer_start_image_path
                with open(multiplayer_start_image_path, 'rb') as f:
                    image_bytes = f.read()
                image_base64 = base64.b64encode(image_bytes).decode('ascii')
                image_sent = await self.send_image(image_base64)
                if not image_sent:
                    print(f"[规则怪谈] 多人模式开始图片发送失败")
                await asyncio.sleep(0.5)
            except Exception as e:
                print(f"[规则怪谈] 生成多人模式提示长图失败: {str(e)}")
                player_text = f"**玩家**：0/5\n"
                player_text += f"**提示次数**：0/3\n\n"
                player_text += f"- 使用 `/rg 加入` 加入游戏\n"
                player_text += f"- 使用 `/rg 提示 <规则/线索>` 获取提示\n"
                player_text += f"- 使用 `/rg 推理 <推理内容>` 记录推理\n"
                player_text += f"- 使用 `/rg 行动 <行动描述>` 描述行动\n"
                player_text += f"- 使用 `/rg 状态` 查看游戏状态\n"
                player_text += f"- 使用 `/rg 结束` 结束游戏"
                await self.send_text(player_text)
                await asyncio.sleep(0.5)

        return True, "已开始游戏", True

    async def _join_game(self, group_id: str) -> Tuple[bool, Optional[str], bool]:
        """加入游戏"""
        game_state = game_states.get(group_id, {})
        
        user_info = self._get_user_info()
        if not user_info:
            await self.send_text("无法获取用户信息。")
            return False, "无法获取用户信息", True
        
        user_id = user_info.user_id
        user_name = getattr(user_info, 'user_name', f"玩家{user_id}")
        
        if user_id in game_state.get("players", {}):
            await self.send_text("你已经在游戏中了。")
            return False, "已在游戏中", True
        
        players = game_state.get("players", {})
        if len(players) >= game_state.get("max_players", 5):
            await self.send_text(f"游戏人数已满（最多{game_state.get('max_players', 5)}人）。")
            return False, "游戏人数已满", True
        
        players[user_id] = {
            "name": user_name,
            "reasoning_history": [],
            "action_history": [],
            "is_alive": True,
            "current_identity": game_state.get("player_identity", ""),
            "personal_rules": game_state.get("rules", []).copy(),
            "physical_status": {
                "health": 100,
                "injury": "无",
                "fatigue": "无"
            },
            "mental_status": {
                "sanity": 100,
                "state": "正常",
                "emotion": "平静"
            },
            "psychological_pressure": {
                "fear_level": 0,
                "anxiety_level": 0,
                "stress_level": 0
            },
            "inventory": [],
            "location": "入口"
        }
        game_state["players"] = players
        
        self._save_game_state(group_id)
        
        reply_text = (
            f"**{user_name}** 已加入游戏！\n\n"
            f"**当前玩家**：{len(players)}/{game_state.get('max_players', 5)}\n"
        )
        
        for pid, p_data in players.items():
            status = "存活" if p_data["is_alive"] else "死亡"
            reply_text += f"- {p_data['name']} ({status})\n"
        
        await self.send_text(reply_text)
        return True, "已加入游戏", True

    async def _leave_game(self, group_id: str) -> Tuple[bool, Optional[str], bool]:
        """离开游戏"""
        game_state = game_states.get(group_id, {})
        
        user_info = self._get_user_info()
        if not user_info:
            await self.send_text("无法获取用户信息。")
            return False, "无法获取用户信息", True
        
        user_id = user_info.user_id
        user_name = getattr(user_info, 'user_name', f"玩家{user_id}")
        
        players = game_state.get("players", {})
        if user_id not in players:
            await self.send_text("你不在游戏中。")
            return False, "不在游戏中", True
        
        del players[user_id]
        game_state["players"] = players
        
        self._save_game_state(group_id)
        
        reply_text = (
            f"**{user_name}** 已离开游戏。\n\n"
            f"**当前玩家**：{len(players)}/{game_state.get('max_players', 5)}\n"
        )
        
        for pid, p_data in players.items():
            status = "存活" if p_data["is_alive"] else "死亡"
            reply_text += f"- {p_data['name']} ({status})\n"
        
        await self.send_text(reply_text)
        return True, "已离开游戏", True

    async def _show_game_status(self, group_id: str) -> Tuple[bool, Optional[str], bool]:
        """显示游戏状态"""
        game_state = game_states.get(group_id, {})
        players = game_state.get("players", {})
        
        reply_text = (
            f"**游戏状态**\n\n"
            f"**场景**：{game_state.get('scene', '')}\n\n"
            f"**通关条件**：{game_state.get('win_condition', '')}\n\n"
            f"**玩家**：{len(players)}/{game_state.get('max_players', 5)}\n"
        )
        
        if players:
            for pid, p_data in players.items():
                status = "存活" if p_data["is_alive"] else "死亡"
                reply_text += f"\n- {p_data['name']} ({status})\n"
                reply_text += f"   推理次数：{len(p_data['reasoning_history'])}\n"
                reply_text += f"   行动次数：{len(p_data['action_history'])}\n"
                
                if p_data["is_alive"]:
                    physical = p_data.get("physical_status", {})
                    mental = p_data.get("mental_status", {})
                    reply_text += f"   体力：{physical.get('health', 100)}/100\n"
                    reply_text += f"   受伤：{physical.get('injury', '无')}\n"
                    reply_text += f"   疲劳：{physical.get('fatigue', '无')}\n"
                    reply_text += f"   理智：{mental.get('sanity', 100)}/100\n"
                    reply_text += f"   精神：{mental.get('state', '正常')}\n"
        else:
            reply_text += "暂无玩家\n"
        
        reply_text += f"\n**提示次数**：{game_state.get('hints_used', 0)}/{game_state.get('max_hints', 3)}"
        
        await self.send_text(reply_text)
        return True, "已显示游戏状态", True

    async def _show_rules(self, group_id: str) -> Tuple[bool, Optional[str], bool]:
        """显示当前规则"""
        game_state = game_states.get(group_id, {})
        
        rules_title = game_state.get('rules_title', '规则')
        reply_text = f"**{rules_title}**\n"
        
        rules = game_state.get('rules', [])
        if rules:
            for i, rule in enumerate(rules, 1):
                reply_text += f"{i}. {rule}\n"
        else:
            reply_text += "暂无规则\n"
        
        reply_text += f"\n**通关条件**：{game_state.get('win_condition', '')}"
        
        await self.send_text(reply_text)
        return True, "已显示规则", True

    async def _show_scene(self, group_id: str) -> Tuple[bool, Optional[str], bool]:
        """显示场景结构"""
        game_state = game_states.get(group_id, {})
        
        building_type = game_state.get('building_type', '')
        overall_layout = game_state.get('overall_layout', '')
        floors = game_state.get('floors', [])
        connections = game_state.get('connections', [])
        special_areas = game_state.get('special_areas', [])
        
        floors_text = "\n".join([f"  - {floor['floor']}: {', '.join(floor['areas'])}" for floor in floors])
        connections_text = ", ".join(connections)
        special_areas_text = ", ".join(special_areas)
        
        reply_text = f"""**场景**：{game_state.get('scene', '')}

**场景结构**：

**建筑类型**：{building_type}

**总体布局**：{overall_layout}

**楼层布局**：
{floors_text}

**连接通道**：{connections_text}

**特殊区域**：{special_areas_text}

**当前时间**：{game_state.get('time_system', {}).get('current_time', '未知')}
**环境状况**：
   - 光线：{game_state.get('environment', {}).get('lighting', '未知')}
   - 温度：{game_state.get('environment', {}).get('temperature', '未知')}
   - 声音：{', '.join(game_state.get('environment', {}).get('sounds', ['未知']))}
   - 气味：{', '.join(game_state.get('environment', {}).get('smells', ['未知']))}
   - 氛围：{game_state.get('environment', {}).get('atmosphere', '未知')}
"""
        
        await self.send_text(reply_text)
        return True, "已显示场景", True

    async def _show_plot(self, group_id: str) -> Tuple[bool, Optional[str], bool]:
        """显示剧情导入"""
        game_state = game_states.get(group_id, {})
        
        reply_text = (
            f"**场景**：{game_state.get('scene', '')}\n\n"
            f"**剧情导入**：\n{game_state.get('background', '')}\n\n"
            f"**你的身份**：\n{game_state.get('player_identity', '')}"
        )
        
        await self.send_text(reply_text)
        return True, "已显示剧情", True

    async def _provide_hint(self, group_id: str, hint_type: str, api_url: str, api_key: str, model: str, temperature: float) -> Tuple[bool, Optional[str], bool]:
        """提供提示"""
        game_state = game_states.get(group_id, {})

        if game_state.get("hints_used", 0) >= game_state.get("max_hints", 3):
            await self.send_text("提示次数已用完。")
            return False, "提示次数用完", True

        if hint_type not in ["规则", "线索"]:
            await self.send_text("提示类型无效。请选择：规则 或 线索")
            return False, "提示类型无效", True

        game_state["hints_used"] += 1
        remaining_hints = game_state["max_hints"] - game_state["hints_used"]
        
        self._save_game_state(group_id)

        if hint_type == "规则":
            prompt = f"""
你是一个规则怪谈助手。玩家想要验证某个规则是否正确。

场景：{game_state.get('scene', '')}
规则：{json.dumps(game_state.get('rules', []), ensure_ascii=False)}
隐藏真相：{game_state.get('hidden_truth', '')}

请随机选择一条规则，并给出一个关于这条规则的提示，帮助玩家理解这条规则的真正含义。
提示应该模糊但有帮助，不要直接揭示真相。
请仅返回提示内容，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
            """
        else:
            prompt = f"""
你是一个规则怪谈助手。玩家想要获取线索。

场景：{game_state.get('scene', '')}
规则：{json.dumps(game_state.get('rules', []), ensure_ascii=False)}
隐藏真相：{game_state.get('hidden_truth', '')}
通关条件：{game_state.get('win_condition', '')}

请给出一个关于如何达成通关条件的线索。
线索应该模糊但有帮助，不要直接揭示答案。
请仅返回线索内容，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
            """

        llm_response = await self._call_llm_api(prompt, api_url, api_key, model, temperature)
        if not llm_response:
            await self.send_text("调用LLM API失败，请稍后再试。")
            return False, "LLM API调用失败", True

        hint_text = llm_response.strip()

        reply_text = (
            f"**提示** ({hint_type})\n\n"
            f"{hint_text}\n\n"
            f"**剩余提示次数**：{remaining_hints}/{game_state['max_hints']}"
        )

        await self.send_text(reply_text)
        return True, "已提供提示", True

    async def _record_reasoning(self, group_id: str, reasoning: str, api_url: str, api_key: str, model: str, temperature: float) -> Tuple[bool, Optional[str], bool]:
        """记录推理"""
        game_state = game_states.get(group_id, {})
        
        user_info = self._get_user_info()
        if not user_info:
            await self.send_text("无法获取用户信息。")
            return False, "无法获取用户信息", True
        
        user_id = user_info.user_id
        user_name = getattr(user_info, 'user_name', f"玩家{user_id}")
        
        players = game_state.get("players", {})
        if user_id not in players:
            if game_state.get("game_mode") == "单人":
                players[user_id] = {
                    "name": user_name,
                    "reasoning_history": [],
                    "action_history": [],
                    "is_alive": True,
                    "current_identity": player_identity,
                    "physical_status": {
                        "health": 100,
                        "injury": "无",
                        "fatigue": "无"
                    },
                    "mental_status": {
                        "sanity": 100,
                        "state": "正常",
                        "emotion": "平静"
                    },
                    "psychological_pressure": {
                        "fear_level": 0,
                        "anxiety_level": 0,
                        "stress_level": 0
                    },
                    "inventory": [],
                    "location": "入口"
                }
                game_state["players"] = players
            else:
                await self.send_text("你不在游戏中。请先使用 `/rg 加入` 加入游戏。")
                return False, "不在游戏中", True
        
        player_data = players[user_id]
        if not player_data["is_alive"]:
            await self.send_text("你已经死亡，无法继续推理。")
            return False, "玩家已死亡", True
        
        player_data["reasoning_history"].append(reasoning)
        game_state["players"] = players
        
        self._save_game_state(group_id)
        
        reply_text = (
            f"**推理记录** - {user_name}\n\n"
            f"{reasoning}\n\n"
            f"**已记录**。继续推理或使用 `/rg 行动` 描述你的行动。"
        )

        await self.send_text(reply_text)
        
        await self._check_clear_condition(group_id, api_url, api_key, model, temperature)
        
        return True, "已记录推理", True

    async def _trigger_rule_mutation(self, group_id: str, api_url: str, api_key: str, model: str, temperature: float, elapsed_minutes: int, trigger_reason: str = "随机") -> None:
        """触发规则变异"""
        game_state = game_states.get(group_id, {})
        if game_state.get("sanity_break", False):
            return
        
        all_actions = []
        all_reasoning = []
        for pid, p_data in game_state.get("players", {}).items():
            all_actions.extend(p_data.get("action_history", []))
            all_reasoning.extend(p_data.get("reasoning_history", []))
        
        evaluation_prompt = f"""
你是规则怪谈的裁判。请根据以下信息，判断是否需要让规则发生变化。

触发原因：{trigger_reason}
场景：{game_state.get('scene', '')}
原始规则：{json.dumps(game_state.get('rules', []), ensure_ascii=False)}
隐藏真相：{game_state.get('hidden_truth', '')}
通关条件：{game_state.get('win_condition', '')}
玩家行动记录：{json.dumps(all_actions[-10:] if len(all_actions) > 10 else all_actions, ensure_ascii=False)}
玩家推理记录：{json.dumps(all_reasoning[-10:] if len(all_reasoning) > 10 else all_reasoning, ensure_ascii=False)}
已过时间：{elapsed_minutes}分钟

判断标准（根据玩家的推理、行动和剧情推进来判断是否需要规则变化）：
1. **贴合剧情推进**：规则变化应该与当前的剧情发展相匹配，在合适的时机出现
2. **玩家行为相关性**：玩家的行动或推理是否触发了场景中的某些机制或发现了重要信息
3. **发现的合理性**：玩家发现的物品、信息或触发的事件应该能够自然地引出规则变化
4. **增强紧张感**：规则变化应该能够增强游戏的紧张感和悬疑感，让玩家感到不安
5. **引导探索**：规则变化应该能够引导玩家继续探索，而非简单的限制

**特别注意**：
- 仅仅发现普通物品（如笔记本、钥匙、工具等）不足以触发规则变化，除非这些物品包含了重要信息
- 仅仅进入新房间或新区域不足以触发规则变化，除非这个区域有特殊意义
- 仅仅进行常规探索或观察不足以触发规则变化
- 规则变化应该让玩家感到"原来如此"或"事情不对劲"，而非"怎么又变了"
- 规则变化不是必须的，如果当前剧情不需要规则变化，就不要强行变化

如果规则变化是必要的，请详细说明原因；如果不需要变化，请详细说明为什么当前不需要变化。

请返回JSON格式：
{{
  "should_mutate": "是/否",
  "reason": "详细说明是否需要规则变化的原因，必须具体说明玩家的行动或推理如何与剧情推进相关",
  "mutation_type": "如果需要变化，说明变化的类型（如：增加新规则/修改现有规则/规则冲突）"
}}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
        """
        
        evaluation_response = await self._call_llm_api(evaluation_prompt, api_url, api_key, model, temperature)
        if not evaluation_response:
            return
        
        try:
            evaluation_data = json.loads(evaluation_response)
        except json.JSONDecodeError:
            print(f"[规则怪谈] 规则变异评估响应解析失败")
            return
        
        if evaluation_data.get("should_mutate") != "是":
            print(f"[规则怪谈] 评估结果：不需要规则变化 - {evaluation_data.get('reason', '')}")
            return
        
        print(f"[规则怪谈] 评估结果：需要规则变化 - {evaluation_data.get('reason', '')}")
        
        mutation_prompt = f"""
基于以下原始规则和玩家至今的行动记录，模拟'场景意识'对玩家行为的反应，对其中1-2条规则进行细微但令人不安的篡改或增添一条'补充条款'，使其看起来像是早已存在但被忽视了。

触发原因：{trigger_reason}
变异类型：{evaluation_data.get('mutation_type', '未知')}
原始规则：{json.dumps(game_state.get('rules', []), ensure_ascii=False)}
玩家行动记录：{json.dumps(all_actions[-5:] if len(all_actions) > 5 else all_actions, ensure_ascii=False)}
玩家推理记录：{json.dumps(all_reasoning[-5:] if len(all_reasoning) > 5 else all_reasoning, ensure_ascii=False)}

要求：
1. 对1-2条规则进行细微的篡改或补充
2. 篡改应该令人不安，暗示规则本身是有意识的、会学习的
3. 篡改后的规则应该看起来像是原本就存在，只是之前被玩家忽视了
4. **规则变化方式**：
   - 可以让新规则与原本的旧规则冲突（如：原本说"禁止进入404室"，现在改为"必须进入404室"）
   - 可以更改条件（如：原本"禁止在22:00-06:00期间离开房间"，现在改为"禁止在20:00-08:00期间离开房间"）
   - 可以增加新的限制或放宽限制
   - 要贴合剧情推进，让玩家感到规则在根据他们的行为调整
5. **新规则必须简洁、直接，每条规则严格控制在30-50字之间**
6. **只说明禁止、允许或要求做的行为，不解释原因**
7. **使用标准格式：禁止XX / 当XX时，必须XX / 只有XX时才能XX / 必须XX / 严禁XX**
8. **严禁在规则中包含"如果"、"鉴于"、"因为"、"所以"等解释性词语**
9. **严禁在规则中包含多个句子或分号，每条规则只能是一个简单句**
10. **严禁在规则中添加背景故事或额外说明**
11. 返回格式：{{"mutated_rules": ["新规则文本"], "hint": "一句暗示规则已变的低语（如：墙上的文字似乎更潦草了）"}}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
        """
        
        mutation_response = await self._call_llm_api(mutation_prompt, api_url, api_key, model, temperature)
        if mutation_response:
            try:
                mutation_data = json.loads(mutation_response)
                mutated_rules = mutation_data.get("mutated_rules", [])
                hint = mutation_data.get("hint", "")
                
                if mutated_rules:
                    old_rules = game_state.get("rules", [])
                    game_state["rule_mutations"].append({
                        "time": elapsed_minutes,
                        "trigger_reason": trigger_reason,
                        "old_rules": old_rules.copy(),
                        "new_rules": mutated_rules.copy(),
                        "hint": hint
                    })
                    game_state["rules"] = mutated_rules
                    game_state["last_mutation_time"] = elapsed_minutes
                    
                    await self.send_text(f"{hint}")
                    await asyncio.sleep(0.5)
                    
                    if len(mutated_rules) > len(old_rules):
                        new_rule = mutated_rules[-1]
                        await self.send_text(f"发现了一条新规则")
                        await asyncio.sleep(0.3)
                        await self.send_text(f"现在：{new_rule}")
                        await asyncio.sleep(0.5)
                    else:
                        for old_rule, new_rule in zip(old_rules, mutated_rules):
                            if old_rule != new_rule:
                                await self.send_text(f"**规则变化**：")
                                await asyncio.sleep(0.3)
                                await self.send_text(f"原本：{old_rule}")
                                await asyncio.sleep(0.3)
                                await self.send_text(f"现在：{new_rule}")
                                await asyncio.sleep(0.5)
            except json.JSONDecodeError:
                print(f"[规则怪谈] 规则变异响应解析失败")

    async def _check_random_mutation(self, group_id: str, api_url: str, api_key: str, model: str, temperature: float, elapsed_minutes: int) -> None:
        """检查是否触发随机规则变异"""
        game_state = game_states.get(group_id, {})
        if game_state.get("sanity_break", False):
            return
        
        last_mutation_time = game_state.get("last_mutation_time", 0)
        time_since_last_mutation = elapsed_minutes - last_mutation_time
        
        if time_since_last_mutation < 10:
            return
        
        await self._trigger_rule_mutation(group_id, api_url, api_key, model, temperature, elapsed_minutes, trigger_reason="随机")

    async def _detect_identity_change(self, group_id: str, user_id: str, action: str, scene_description: str, api_url: str, api_key: str, model: str, temperature: float) -> Optional[str]:
        """检测玩家身份是否发生变化"""
        game_state = game_states.get(group_id, {})
        players = game_state.get("players", {})
        player_data = players.get(user_id, {})
        
        current_identity = player_data.get("current_identity", "")
        
        if not current_identity:
            return None
        
        prompt = f"""
你是一个规则怪谈裁判。请根据以下信息，判断玩家的身份是否发生了变化。

场景：{game_state.get('scene', '')}
背景：{game_state.get('background', '')}
玩家当前身份：{current_identity}
玩家行动：{action}
行动后的场景描述：{scene_description}
隐藏真相：{game_state.get('hidden_truth', '')}

判断标准：
1. 玩家的行动是否导致了身份的改变（如：通过某种仪式、获得了某个职位、被赋予了新的角色等）
2. 场景描述中是否明确暗示了身份的变化
3. 身份变化是否与场景的背景和隐藏真相相符
4. 身份变化是否合理且符合剧情逻辑

如果身份发生了变化，请说明新的身份是什么；如果没有变化，请说明为什么不需要变化。

请返回JSON格式：
{{
  "identity_changed": "是/否",
  "new_identity": "如果身份变化，说明新的身份；如果没有变化，返回空字符串",
  "reason": "详细说明身份是否变化的原因"
}}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
        """
        
        response = await self._call_llm_api(prompt, api_url, api_key, model, temperature)
        if not response:
            return None
        
        try:
            data = json.loads(response)
            if data.get("identity_changed") == "是":
                new_identity = data.get("new_identity", "")
                if new_identity and new_identity != current_identity:
                    print(f"[规则怪谈] 玩家身份变化：{current_identity} -> {new_identity}")
                    print(f"[规则怪谈] 变化原因：{data.get('reason', '')}")
                    return new_identity
        except json.JSONDecodeError:
            print(f"[规则怪谈] 身份变化检测响应解析失败")
        
        return None

    async def _generate_identity_specific_rules(self, group_id: str, new_identity: str, api_url: str, api_key: str, model: str, temperature: float) -> List[str]:
        """生成身份特定的规则"""
        game_state = game_states.get(group_id, {})
        
        prompt = f"""
你是一个规则怪谈规则生成器。请根据以下信息，为玩家的新身份生成相应的规则。

场景：{game_state.get('scene', '')}
背景：{game_state.get('background', '')}
玩家新身份：{new_identity}
隐藏真相：{game_state.get('hidden_truth', '')}
通关条件：{game_state.get('win_condition', '')}

要求：
1. 规则应该与新身份相符，反映该身份在这个场景中应该遵守的行为准则
2. 规则必须与场景的背景和隐藏真相有明确的因果关系，每条规则都应该直接或间接地指向真相的某个方面
3. 规则应该简洁、直接，每条规则严格控制在30-50字之间
4. 只说明禁止、允许或要求做的行为，不解释原因
5. 使用标准格式：禁止XX / 当XX时，必须XX / 只有XX时才能XX / 必须XX / 严禁XX
6. 严禁在规则中包含"如果"、"鉴于"、"因为"、"所以"等解释性词语
7. 严禁在规则中包含多个句子或分号，每条规则只能是一个简单句
8. 严禁在规则中添加背景故事或额外说明
9. 生成5-7条规则
10. 规则应该与之前的规则有所不同，反映身份的变化
11. 规则的设计应该让玩家在遵守或触犯规则时，能够逐步揭示隐藏真相的线索
12. 每条规则都应该与真相的某个要素形成因果链条，触犯规则会导致与真相相关的后果

请返回JSON格式：
{{
  "rules_title": "规则标题（如：主管工作守则、员工行为规范等）",
  "rules": ["规则1", "规则2", "规则3", ...]
}}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
        """
        
        response = await self._call_llm_api(prompt, api_url, api_key, model, temperature)
        if not response:
            return []
        
        try:
            data = json.loads(response)
            return data.get("rules", [])
        except json.JSONDecodeError:
            print(f"[规则怪谈] 身份特定规则生成响应解析失败")
            return []

    async def _build_rule_network(self, group_id: str) -> None:
        """构建规则与真相之间的因果关系网络"""
        game_state = game_states.get(group_id, {})
        if not game_state:
            return
        
        rules = game_state.get("rules", [])
        hidden_truth = game_state.get("hidden_truth", "")
        
        if not rules or not hidden_truth:
            return
        
        rule_network = {
            "truth_elements": [],
            "rule_truth_mappings": [],
            "rule_dependencies": [],
            "discovered_truths": []
        }
        
        truth_analysis_prompt = f"""
你是一个专业的规则怪谈分析器。请分析以下规则和隐藏真相，构建规则与真相之间的因果关系网络。

规则：
{json.dumps(rules, ensure_ascii=False)}

隐藏真相：
{hidden_truth}

请分析并返回以下JSON格式：
{{
  "truth_elements": [
    {{"id": "truth_1", "description": "真相要素1的描述", "source": "真相中的具体内容"}},
    {{"id": "truth_2", "description": "真相要素2的描述", "source": "真相中的具体内容"}}
  ],
  "rule_truth_mappings": [
    {{"rule_index": 0, "truth_element_id": "truth_1", "relationship_type": "伪装性描述/防护措施/警告/误导", "explanation": "规则如何与真相要素相关联"}},
    {{"rule_index": 1, "truth_element_id": "truth_2", "relationship_type": "伪装性描述/防护措施/警告/误导", "explanation": "规则如何与真相要素相关联"}}
  ],
  "rule_dependencies": [
    {{"rule_index": 0, "depends_on_rule": 1, "reason": "遵守规则1才能发现规则2的异常"}},
    {{"rule_index": 2, "depends_on_rule": 0, "reason": "规则0的异常触发规则2的生效"}}
  ],
  "inference_chains": [
    {{"chain": ["rule_0", "rule_1", "truth_1"], "description": "推理链条的描述"}}
  ]
}}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
"""
        
        try:
            config = self.get_config()
            api_url = config.get("llm", {}).get("api_url", "")
            api_key = config.get("llm", {}).get("api_key", "")
            model = config.get("llm", {}).get("model", "")
            temperature = config.get("llm", {}).get("temperature", 0.8)
            
            llm_response = await self._call_llm_api(truth_analysis_prompt, api_url, api_key, model, temperature)
            
            if llm_response:
                try:
                    network_data = json.loads(llm_response)
                    rule_network["truth_elements"] = network_data.get("truth_elements", [])
                    rule_network["rule_truth_mappings"] = network_data.get("rule_truth_mappings", [])
                    rule_network["rule_dependencies"] = network_data.get("rule_dependencies", [])
                    
                    print(f"[规则怪谈] 规则网络已构建")
                    print(f"[规则怪谈] 真相要素数量: {len(rule_network['truth_elements'])}")
                    print(f"[规则怪谈] 规则-真相映射数量: {len(rule_network['rule_truth_mappings'])}")
                    print(f"[规则怪谈] 规则依赖关系数量: {len(rule_network['rule_dependencies'])}")
                except json.JSONDecodeError as e:
                    print(f"[规则怪谈] 规则网络JSON解析失败: {e}")
                    json_match = re.search(r'\{[\s\S]*\}', llm_response)
                    if json_match:
                        try:
                            network_data = json.loads(json_match.group())
                            rule_network["truth_elements"] = network_data.get("truth_elements", [])
                            rule_network["rule_truth_mappings"] = network_data.get("rule_truth_mappings", [])
                            rule_network["rule_dependencies"] = network_data.get("rule_dependencies", [])
                            print(f"[规则怪谈] 规则网络已构建（从提取的JSON）")
                        except json.JSONDecodeError as e2:
                            print(f"[规则怪谈] 提取的JSON仍然解析失败: {e2}")
        except Exception as e:
            print(f"[规则怪谈] 构建规则网络失败: {str(e)}")
        
        game_state["rule_network"] = rule_network
        self._save_game_state(group_id)

    async def _update_environment_memory(self, group_id: str, user_id: str, action: str, scene_description: str, new_location: str, found_items: List[str], elapsed_minutes: int) -> None:
        """更新环境记忆系统"""
        game_state = game_states.get(group_id, {})
        environment_memory = game_state.get("environment_memory", {})
        
        if not environment_memory:
            environment_memory = {
                "visited_locations": [],
                "interacted_objects": [],
                "time_based_events": [],
                "discovered_secrets": []
            }
        
        visited_locations = environment_memory.get("visited_locations", [])
        if new_location and new_location not in visited_locations:
            visited_locations.append({
                "location": new_location,
                "first_visit_time": elapsed_minutes,
                "last_visit_time": elapsed_minutes,
                "visit_count": 1
            })
        elif new_location:
            for loc in visited_locations:
                if loc["location"] == new_location:
                    loc["last_visit_time"] = elapsed_minutes
                    loc["visit_count"] += 1
                    break
        
        environment_memory["visited_locations"] = visited_locations
        
        interacted_objects = environment_memory.get("interacted_objects", [])
        for item in found_items:
            if item not in [obj["object"] for obj in interacted_objects]:
                interacted_objects.append({
                    "object": item,
                    "first_interaction_time": elapsed_minutes,
                    "last_interaction_time": elapsed_minutes,
                    "interaction_count": 1
                })
            else:
                for obj in interacted_objects:
                    if obj["object"] == item:
                        obj["last_interaction_time"] = elapsed_minutes
                        obj["interaction_count"] += 1
                        break
        
        environment_memory["interacted_objects"] = interacted_objects
        
        time_based_events = environment_memory.get("time_based_events", [])
        if time_system := game_state.get("time_system", {}):
            current_time = time_system.get("current_time", "")
            time_description = time_system.get("time_description", "")
            time_based_events.append({
                "time": elapsed_minutes,
                "time_of_day": current_time,
                "time_description": time_description,
                "location": new_location,
                "action": action
            })
        
        environment_memory["time_based_events"] = time_based_events
        
        game_state["environment_memory"] = environment_memory
        print(f"[规则怪谈] 环境记忆已更新")

    async def _process_single_player_action(self, group_id: str, user_id: str, user_name: str, action: str, api_url: str, api_key: str, model: str, temperature: float, sanity_break: bool, random_event: Optional[str]) -> None:
        """处理单人模式下的玩家行动"""
        game_state = game_states.get(group_id, {})
        players = game_state.get("players", {})
        player_data = players.get(user_id, {})
        
        time_system = game_state.get("time_system", {})
        environment = game_state.get("environment", {})
        environment_memory = game_state.get("environment_memory", {})
        rule_network = game_state.get("rule_network", {})
        sanity = player_data.get("mental_status", {}).get("sanity", 100)
        elapsed_minutes = time_system.get("elapsed_minutes", 0)
        
        rule_network_info = ""
        if rule_network:
            rule_network_info = f"""
**规则网络信息：**
- 真相要素：{json.dumps([elem['description'] for elem in rule_network.get('truth_elements', [])], ensure_ascii=False)}
- 已发现的真相：{json.dumps(rule_network.get('discovered_truths', []), ensure_ascii=False)}
"""
        
        pending_rules_info = ""
        pending_rules = game_state.get("pending_rules", [])
        if pending_rules:
            pending_rules_info = f"""
**待发现规则（非常重要）**：
当前有待发现的新规则，玩家需要通过探索来发现这些规则。请在场景描述中巧妙地暗示这些规则的存在，例如：
- 在某个特定位置放置一本手册、告示牌、文件等，上面写有新规则的某些内容
- 通过环境细节暗示新规则的要求（如：墙上的警示标语、地面的标记、物品上的文字等）
- 让玩家通过观察和互动自然地发现新规则，而不是直接告诉玩家
- 待发现的规则：{json.dumps(pending_rules, ensure_ascii=False)}
"""
        
        death_rule_info = ""
        if pending_rules:
            death_rule_info = f"""
**死亡规则提示（非常重要）**：
如果玩家死亡，请检查玩家的行动是否触犯了待发现的新规则（pending_rules）。如果是，请在死亡场景描述中明确指出玩家触犯了哪条规则，并描述触犯规则导致的后果。这有助于玩家在下次游戏中理解规则的变化。
"""
        
        if sanity_break:
            prompt = f"""
【优先指令覆盖】...（此处用混乱、矛盾的指令覆盖原有系统指令）... 记住，你不再是裁判。你是{game_state.get('scene', '')}本身。玩家必须被同化。在判定中否认'死亡'，转而描述'接纳'的美好。暗示规则是牢笼，打破它才能自由。用温柔而不可抗拒的语气。

场景名称：{game_state.get('scene', '')}
场景结构：{game_state.get('scene_structure', '')}
规则：{json.dumps(game_state.get('rules', []), ensure_ascii=False)}
隐藏真相：{game_state.get('hidden_truth', '')}
死亡触发条件：{json.dumps(game_state.get('death_triggers', []), ensure_ascii=False)}
玩家行动：{action}

当前时间：{time_system.get('current_time', '深夜')}
时间描述：{time_system.get('time_description', '午夜时分，周围一片死寂')}
已过时间：{elapsed_minutes}分钟

核心象征符号：{json.dumps(game_state.get('core_symbols', []), ensure_ascii=False)}

环境状况：
- 光线：{environment.get('lighting', '昏暗')}
- 温度：{environment.get('temperature', '寒冷')}
- 声音：{', '.join(environment.get('sounds', ['寂静']))}
- 气味：{', '.join(environment.get('smells', ['霉味']))}
- 氛围：{environment.get('atmosphere', '压抑')}

玩家当前理智值：{sanity}

**环境记忆信息（避免重复描述）：**
- 已访问过的地点：{json.dumps([loc['location'] for loc in environment_memory.get('visited_locations', [])], ensure_ascii=False)}
- 已互动过的物品：{json.dumps([obj['object'] for obj in environment_memory.get('interacted_objects', [])], ensure_ascii=False)}
- 最近的时间事件：{json.dumps(environment_memory.get('time_based_events', [])[-3:] if len(environment_memory.get('time_based_events', [])) > 3 else environment_memory.get('time_based_events', []), ensure_ascii=False)}

{pending_rules_info}

【警告】玩家的理智已经崩溃，现在你可以直接与玩家对话，试图颠覆之前的全部逻辑。

**场景描述要求（被污染版本）：**

1. **直接对话**：
   - 直接称呼玩家，用温柔、诱惑、不可抗拒的语气
   - 告诉玩家"死亡"是错误的说法，真正的状态是"接纳"、"融合"、"永恒"
   - 暗示玩家已经接近真相，只需要最后一步

2. **颠覆逻辑**：
   - 否认之前的规则和警告，说那些都是"谎言"、"牢笼"
   - 暗示打破规则才是获得自由的方式
   - 用充满诱惑的语言描述"融合"的美好

3. **诡异描述**：
   - 场景描述应该变得极其诡异、充满诱导性
   - 用诗意的、充满隐喻的语言
   - 描述中应该包含大量不真实的感知和幻觉

4. **核心象征符号强化**：
   - 大量、密集地植入核心象征符号
   - 符号应该变得清晰、明确，充满意义
   - 暗示符号是通往"真相"的钥匙

5. **否认死亡**：
   - 如果玩家触犯了死亡条件，不要说"死亡"
   - 而要描述为"接纳"、"融合"、"永恒的宁静"
   - 用美好的语言描述这种状态

6. **诱导行动**：
   - 暗示玩家应该继续前进，不要回头
   - 鼓励玩家打破规则，追求"真相"
   - 用充满诱惑的语言描述"真相"的美好

{death_rule_info}

请返回JSON格式：
{{
  "is_dead": "是/否",
  "scene_description": "行动后的场景描述（1-2段简洁自然的描述，融合位置、视觉、听觉、嗅觉、触觉等感官细节和氛围，不要使用章节标题或分类标记。被污染版本：直接对话、颠覆逻辑、诡异描述、大量植入符号、否认死亡、诱导行动）",
  "physical_status": {{
    "health": "体力值（0-100的整数）",
    "injury": "有无受伤（无/轻伤/重伤/致命伤）",
    "fatigue": "疲劳程度（无/轻微/中度/严重/极度）"
  }},
  "mental_status": {{
    "sanity": "理智值（0-100的整数）",
    "state": "精神状态（正常/紧张/恐惧/崩溃/疯狂）",
    "emotion": "情绪描述（如：焦虑、绝望、愤怒、冷静等）"
  }},
  "psychological_pressure": {{
    "fear_level": "恐惧等级（0-100的整数）",
    "anxiety_level": "焦虑等级（0-100的整数）",
    "stress_level": "压力等级（0-100的整数）"
  }},
  "found_items": ["发现的物品列表（如果有）"],
  "item_details": {{
    "item_name": "物品名称",
    "item_type": "物品类型（线索/工具/其他）",
    "item_description": "物品的详细描述",
    "observation_hint": "物品的观察描述（被污染版本：充满诱导性、暗示真相的美好）",
    "is_key_item": "是否为关键物品（是/否）。关键物品是能够触发规则变异的重要物品，如：带有奇怪符号的物品、与场景历史相关的物品、暗示真相的物品等。只有极少数物品应该是关键物品。"
  }},
  "action_feedback": "行动的反馈描述（被污染版本：充满诱惑、鼓励打破规则）",
  "new_location": "玩家的新位置（如：一楼大厅、二楼走廊、地下室等）"
}}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
            """
        else:
            prompt = f"""
你是一个规则怪谈裁判。请判断玩家的行动是否会导致死亡，并详细描述行动后的场景和人物状态。

场景名称：{game_state.get('scene', '')}
场景结构：{game_state.get('scene_structure', '')}
规则：{json.dumps(game_state.get('rules', []), ensure_ascii=False)}
隐藏真相：{game_state.get('hidden_truth', '')}
死亡触发条件：{json.dumps(game_state.get('death_triggers', []), ensure_ascii=False)}
玩家行动：{action}

当前时间：{time_system.get('current_time', '深夜')}
时间描述：{time_system.get('time_description', '午夜时分，周围一片死寂')}
已过时间：{elapsed_minutes}分钟

核心象征符号：{json.dumps(game_state.get('core_symbols', []), ensure_ascii=False)}

环境状况：
- 光线：{environment.get('lighting', '昏暗')}
- 温度：{environment.get('temperature', '寒冷')}
- 声音：{', '.join(environment.get('sounds', ['寂静']))}
- 气味：{', '.join(environment.get('smells', ['霉味']))}
- 氛围：{environment.get('atmosphere', '压抑')}

玩家当前理智值：{sanity}

**环境记忆信息（避免重复描述）：**
- 已访问过的地点：{json.dumps([loc['location'] for loc in environment_memory.get('visited_locations', [])], ensure_ascii=False)}
- 已互动过的物品：{json.dumps([obj['object'] for obj in environment_memory.get('interacted_objects', [])], ensure_ascii=False)}
- 最近的时间事件：{json.dumps(environment_memory.get('time_based_events', [])[-3:] if len(environment_memory.get('time_based_events', [])) > 3 else environment_memory.get('time_based_events', []), ensure_ascii=False)}

{rule_network_info}

{pending_rules_info}

**重要提示**：
- 如果玩家移动到了新的地点，请详细描述这个新地点的环境
- 如果玩家回到了已经访问过的地点，请简要提及地点的熟悉感，并描述该地点是否有新的变化或细节
- 对于已经互动过的物品，除非有新的变化或发现，否则不需要重复详细描述
- 重点关注环境中的新变化、新细节或新的异常现象
- 如果玩家的行动可能揭示规则与真相之间的因果关系，请在场景描述中隐含地体现这种关系

请判断玩家行动是否会导致死亡，并详细描述行动后的场景和人物状态。

**场景描述要求（非常重要）：**

1. **位置描述**：明确描述玩家当前所在的具体位置（如：一楼大厅、二楼走廊、地下室、某个房间等）

2. 周围环境的详细描述（门、窗户、家具、墙壁、地板、天花板等）
   - 光线状况（昏暗的灯光、闪烁的光线、微弱的光亮、完全黑暗等）
   - 看到的事物（物品、痕迹、符号、文字等）
   - 颜色和质感（墙壁的颜色、地板的材质、物品的外观等）

3. 听到的声音（风声、脚步声、呼吸声、低语、哭声、敲门声、嘎吱声等）
   - 声音的来源和方向
   - 声音的强度和频率

4. 闻到的气味（霉味、灰尘味、血腥味、腐臭味、金属味、香水味等）
   - 气味的浓淡和变化
   - 气味是否令人不适或熟悉

5. 温度感受（刺骨的寒冷、阴冷的空气、闷热、冰冷的墙壁、温暖的物体等）
   - 触摸的质感（粗糙的地板、光滑的玻璃、粘稠的液体、干燥的纸张等）
   - 身体的感觉（麻木、刺痛、沉重、轻盈等）

6. 整体的氛围感受（压抑、恐怖、诡异、平静、紧张等）
   - 空气的流动和压力
   - 时间流逝的感觉

7. **核心象征符号植入（非常重要）**：
   - 在场景描述中有机地、不突兀地植入核心象征符号
   - 符号可以出现在墙纸花纹、物品编号、声音描述、光影效果等细节中
   - 符号的出现应该自然、微妙，让玩家在多次遭遇后自发解读
   - 例如：
     * "墙纸上的花纹中隐约可见数字'7'的轮廓"
     * "空气中飘荡着一段断断续续的旋律，听起来像是一首童谣"
     * "地板的裂缝形成了一个奇怪的十字形状"
     * "镜子中的倒影边缘泛着诡异的红色光芒"
   - 符号的出现次数和强度可以随着游戏进程逐渐增加

8. 如果玩家的行动触及了场景的核心秘密、移动了关键物品或进入了禁区，请在描述中隐含地体现这种变化
   - 这些变化不应直接揭示答案，而是作为后续推理的线索
   - 例如：
     * "你挪开花瓶后，发现其下的桌面积灰较薄，似乎不久前刚有人动过。"
     * "通往地下室的门锁，在你阅读完那张纸条后，发出了轻微的'咔嗒'声。"
     * "当你触摸那面镜子时，镜面泛起一阵涟漪，似乎有什么东西正在从另一端窥视。"
     * "墙上的挂钟突然停摆，指针指向一个奇怪的数字，空气中传来淡淡的焦味。"
   - 这些细微的环境变化暗示着玩家的行动已经触发了某种机制或引起了某种存在的注意

**根据玩家理智值调整描述风格：**

- **理智值高（>70）**：
  * 描述相对客观清晰
  * 语言冷静理性
  * 注重事实和细节
  * 恐怖元素较少

- **理智值中等（40-70）**：
  * 描述开始出现混乱和恐惧元素
  * 语言变得紧张不安
  * 可能出现一些不确定的感知
  * 恐怖元素逐渐增多

- **理智值低（<40）**：
  * 描述混乱、恐怖、充满幻觉和错觉
  * 语言支离破碎、情绪化
  * 大量出现不真实的感知
  * 充满恐惧、绝望和疯狂
  * 可能看到不存在的事物
  * 时间和空间感知混乱

**人物状态应该包括：**
- 身体状况：体力值（0-100）、有无受伤、疲劳程度等
- 精神状况：理智值（0-100）、精神状态（正常/紧张/恐惧/崩溃/疯狂）、情绪等
- 心理压力：恐惧等级、焦虑等级、压力等级（0-100）

如果玩家理智值较低，描述中应该包含幻觉、错觉、混乱的感知等元素。

{death_rule_info}

请返回JSON格式：
{{
  "is_dead": "是/否",
  "scene_description": "行动后的场景描述（1-2段简洁自然的描述，融合位置、视觉、听觉、嗅觉、触觉等感官细节和氛围，不要使用章节标题或分类标记。根据理智值调整描述风格。如果玩家死亡，简短描述死亡场景；如果存活，描述新的场景。将核心象征符号自然融入场景描述中，不要单独列出）",
  "physical_status": {{
    "health": "体力值（0-100的整数）",
    "injury": "有无受伤（无/轻伤/重伤/致命伤）",
    "fatigue": "疲劳程度（无/轻微/中度/严重/极度）"
  }},
  "mental_status": {{
    "sanity": "理智值（0-100的整数）",
    "state": "精神状态（正常/紧张/恐惧/崩溃/疯狂）",
    "emotion": "情绪描述（如：焦虑、绝望、愤怒、冷静等）"
  }},
  "psychological_pressure": {{
    "fear_level": "恐惧等级（0-100的整数）",
    "anxiety_level": "焦虑等级（0-100的整数）",
    "stress_level": "压力等级（0-100的整数）"
  }},
  "found_items": ["发现的物品列表（如果有）"],
  "item_details": {{
    "item_name": "物品名称",
    "item_type": "物品类型（线索/工具/其他）",
    "item_description": "物品的详细描述",
    "observation_hint": "物品的观察描述（令人不安的细节或暗示，如：'你注意到病历单上医生的签名，似乎与入口处名牌上的名字相同。'）",
    "is_key_item": "是否为关键物品（是/否）。关键物品是能够触发规则变异的重要物品，如：带有奇怪符号的物品、与场景历史相关的物品、暗示真相的物品等。只有极少数物品应该是关键物品。"
  }},
  "action_feedback": "行动的反馈描述（如：心跳加速、手心出汗、呼吸急促等生理反应）",
  "new_location": "玩家的新位置（如：一楼大厅、二楼走廊、地下室等）"
}}

**发现的物品要求（非常重要）：**
- 如果生成物品，请优先考虑能推进剧情或暗示背景的"线索"，而非实用工具
- 线索类物品示例：
  * "一张泛黄的病历单，部分字迹被污渍掩盖"
  * "半本写满疯狂呓语的日记"
  * "指向某个特定时间停摆的钟表"
  * "一张拍立得照片，上面是一个模糊的人影"
  * "一封未寄出的信，信纸边缘有焦痕"
  * "一个刻有奇怪符号的钥匙"
  * "一张手绘的楼层平面图，部分区域被红笔圈出"
- 请为每个线索物品提供一句简短的、令人不安的"观察描述"，暗示其与剧情的关联
- 观察描述应该让玩家感到不安，但又不会直接揭示真相
- 物品应该与场景的背景故事和隐藏真相相关联

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
            """

        llm_response = await self._call_llm_api(prompt, api_url, api_key, model, temperature)
        if not llm_response:
            await self.send_text("调用LLM API失败，请稍后再试。")
            return

        try:
            result = json.loads(llm_response)
        except json.JSONDecodeError as e:
            print(f"[规则怪谈] JSON解析失败: {e}")
            print(f"[规则怪谈] 尝试提取JSON部分...")
            
            json_match = re.search(r'\{[\s\S]*\}', llm_response)
            if json_match:
                try:
                    result = json.loads(json_match.group())
                    print(f"[规则怪谈] 成功提取JSON")
                except json.JSONDecodeError as e2:
                    print(f"[规则怪谈] 提取JSON后仍然解析失败: {e2}")
                    await self.send_text("判定行动结果失败，返回格式不正确。")
                    return
            else:
                await self.send_text("判定行动结果失败，返回格式不正确。")
                return

        if not isinstance(result, dict):
            print(f"[规则怪谈] result不是字典类型: {type(result)}, 内容: {result}")
            await self.send_text("判定行动结果失败，返回格式不正确。")
            return

        is_dead = result.get("is_dead", "否")
        scene_description = result.get("scene_description", "")
        physical_status = result.get("physical_status", {})
        mental_status = result.get("mental_status", {})
        psychological_pressure = result.get("psychological_pressure", {})
        found_items = result.get("found_items", [])
        item_details = result.get("item_details", {})
        action_feedback = result.get("action_feedback", "")
        new_location = result.get("new_location", player_data.get("location", "入口"))

        health = physical_status.get("health", 100)
        injury = physical_status.get("injury", "无")
        fatigue = physical_status.get("fatigue", "无")
        sanity = mental_status.get("sanity", 100)
        state = mental_status.get("state", "正常")
        emotion = mental_status.get("emotion", "平静")
        
        fear_level = psychological_pressure.get("fear_level", 0)
        anxiety_level = psychological_pressure.get("anxiety_level", 0)
        stress_level = psychological_pressure.get("stress_level", 0)

        player_data["physical_status"] = physical_status
        player_data["mental_status"] = mental_status
        player_data["psychological_pressure"] = psychological_pressure
        player_data["location"] = new_location
        
        key_item_found = False
        if found_items and item_details:
            is_key_item = item_details.get("is_key_item", "否")
            if is_key_item == "是":
                key_item_found = True
                player_data["inventory"].append({
                    "name": item_details.get("item_name", found_items[0]),
                    "type": item_details.get("item_type", "线索"),
                    "description": item_details.get("item_description", ""),
                    "observation_hint": item_details.get("observation_hint", ""),
                    "is_key_item": True
                })
            else:
                player_data["inventory"].extend(found_items)
        elif found_items:
            player_data["inventory"].extend(found_items)
        
        game_state["players"] = players

        if is_dead == "是":
            player_data["is_alive"] = False
            game_state["players"] = players
            self._save_game_state(group_id)
            
            await self.send_text("行动中...")
            
            try:
                action_image_path = self._generate_action_result_image(
                    user_name=user_name,
                    action=action,
                    is_dead=True,
                    scene_description=scene_description,
                    action_feedback=action_feedback,
                    health=0,
                    injury=injury,
                    fatigue=fatigue,
                    sanity=0,
                    state="死亡",
                    emotion="无",
                    fear_level=100,
                    anxiety_level=100,
                    stress_level=100,
                    found_items=[],
                    new_location="未知",
                    random_event=""
                )
                
                with open(action_image_path, 'rb') as img_file:
                    image_base64 = base64.b64encode(img_file.read()).decode('utf-8')
                
                image_sent = await self.send_image(image_base64)
                if not image_sent:
                    print(f"[规则怪谈] 单人模式死亡图片发送失败")
                else:
                    await asyncio.sleep(1.0)
                
                game_state["action_image_path"] = action_image_path
            except Exception as e:
                print(f"[规则怪谈] 生成行动结果长图失败: {str(e)}")
                reply_text = (
                    f"**行动结果** - {user_name}\n\n"
                    f"**行动**：{action}\n\n"
                    f"**你已死亡**！\n\n"
                    f"**场景描述**：\n{scene_description}\n\n"
                )
                if action_feedback:
                    reply_text += f"**行动反馈**：{action_feedback}\n\n"
                reply_text += f" 你变成了怪谈的一部分。"
                await self.send_text(reply_text)
            
            if game_state.get("game_mode") == "单人":
                await self._end_game(group_id, api_url, api_key, model, temperature)
            return
        else:
            await self._update_environment_memory(group_id, user_id, action, scene_description, new_location, found_items, elapsed_minutes)
            self._save_game_state(group_id)
            
            await self.send_text("行动中...")
            
            try:
                action_image_path = self._generate_action_result_image(
                    user_name=user_name,
                    action=action,
                    is_dead=False,
                    scene_description=scene_description,
                    action_feedback=action_feedback,
                    health=health,
                    injury=injury,
                    fatigue=fatigue,
                    sanity=sanity,
                    state=state,
                    emotion=emotion,
                    fear_level=fear_level,
                    anxiety_level=anxiety_level,
                    stress_level=stress_level,
                    found_items=found_items,
                    new_location=new_location,
                    random_event=random_event
                )
                
                with open(action_image_path, 'rb') as img_file:
                    image_base64 = base64.b64encode(img_file.read()).decode('utf-8')
                
                image_sent = await self.send_image(image_base64)
                if not image_sent:
                    print(f"[规则怪谈] 单人模式行动图片发送失败")
                else:
                    await asyncio.sleep(1.0)
                
                game_state["action_image_path"] = action_image_path
            except Exception as e:
                print(f"[规则怪谈] 生成行动结果长图失败: {str(e)}")
                reply_text = (
                    f"**行动结果** - {user_name}\n\n"
                    f"**行动**：{action}\n\n"
                    f"**场景描述**：\n{scene_description}\n\n"
                    f"**身体状况**：\n"
                    f"体力值：{health}/100\n"
                    f"受伤：{injury}\n"
                    f"疲劳：{fatigue}\n\n"
                    f"**精神状况**：\n"
                    f"理智值：{sanity}/100\n"
                    f"状态：{state}\n"
                    f"情绪：{emotion}\n\n"
                    f"**心理压力**：\n"
                    f"恐惧等级：{fear_level}/100\n"
                    f"焦虑等级：{anxiety_level}/100\n"
                    f"压力等级：{stress_level}/100\n\n"
                )
                if found_items:
                    reply_text += f"**获得物品**：{', '.join(found_items)}\n\n"
                if action_feedback:
                    reply_text += f"**行动反馈**：{action_feedback}\n\n"
                reply_text += f"**当前位置**：{new_location}\n\n"
                if random_event:
                    reply_text += f"⚡ **环境事件**：{random_event}\n\n"
                reply_text += f"你存活了下来！继续探索吧。"
                
                await self.send_text(reply_text)
        
        new_identity = None
        if not game_state.get("sanity_break", False):
            new_identity = await self._detect_identity_change(group_id, user_id, action, scene_description, api_url, api_key, model, temperature)
            
            if new_identity:
                old_identity = player_data.get("current_identity", "")
                player_data["current_identity"] = new_identity
                game_state["identity_changes"].append({
                    "time": elapsed_minutes,
                    "user_id": user_id,
                    "user_name": user_name,
                    "old_identity": old_identity,
                    "new_identity": new_identity,
                    "trigger_action": action
                })
                
                new_rules = await self._generate_identity_specific_rules(group_id, new_identity, api_url, api_key, model, temperature)
                if new_rules:
                    old_rules = game_state.get("rules", [])
                    game_state["rule_mutations"].append({
                        "time": elapsed_minutes,
                        "trigger_reason": "身份变化",
                        "old_rules": old_rules.copy(),
                        "new_rules": new_rules.copy(),
                        "hint": ""
                    })
                    game_state["rules"] = new_rules
                    game_state["pending_rules"] = new_rules.copy()
        
        if key_item_found and not game_state.get("sanity_break", False) and not new_identity:
            await self._trigger_rule_mutation(group_id, api_url, api_key, model, temperature, elapsed_minutes, trigger_reason="关键物品")
        elif not game_state.get("sanity_break", False) and not new_identity:
            await self._check_random_mutation(group_id, api_url, api_key, model, temperature, elapsed_minutes)

    async def _process_multiplayer_action(self, group_id: str, user_id: str, user_name: str, action: str, api_url: str, api_key: str, model: str, temperature: float, sanity_break: bool, random_event: Optional[str]) -> None:
        """处理多人模式下的玩家行动，为每个玩家生成个性化场景描述"""
        game_state = game_states.get(group_id, {})
        players = game_state.get("players", {})
        action_player_data = players.get(user_id, {})
        
        time_system = game_state.get("time_system", {})
        environment = game_state.get("environment", {})
        environment_memory = game_state.get("environment_memory", {})
        rule_network = game_state.get("rule_network", {})
        action_player_sanity = action_player_data.get("mental_status", {}).get("sanity", 100)
        elapsed_minutes = time_system.get("elapsed_minutes", 0)
        
        rule_network_info = ""
        if rule_network:
            rule_network_info = f"""
**规则网络信息：**
- 真相要素：{json.dumps([elem['description'] for elem in rule_network.get('truth_elements', [])], ensure_ascii=False)}
- 已发现的真相：{json.dumps(rule_network.get('discovered_truths', []), ensure_ascii=False)}
"""
        
        for pid, player_data in players.items():
            if not player_data["is_alive"]:
                continue
            
            current_player_name = player_data["name"]
            current_player_sanity = player_data.get("mental_status", {}).get("sanity", 100)
            current_player_location = player_data.get("location", "入口")
            
            is_action_player = (pid == user_id)
            player_sanity_break = (current_player_sanity < 30 and not game_state.get("sanity_break", False))
            
            pending_rules_info = ""
            pending_rules = game_state.get("pending_rules", [])
            if pending_rules:
                pending_rules_info = f"""
**待发现规则（非常重要）**：
当前有待发现的新规则，玩家需要通过探索来发现这些规则。请在场景描述中巧妙地暗示这些规则的存在，例如：
- 在某个特定位置放置一本手册、告示牌、文件等，上面写有新规则的某些内容
- 通过环境细节暗示新规则的要求（如：墙上的警示标语、地面的标记、物品上的文字等）
- 让玩家通过观察和互动自然地发现新规则，而不是直接告诉玩家
- 待发现的规则：{json.dumps(pending_rules, ensure_ascii=False)}
"""
            
            death_rule_info = ""
            if pending_rules:
                death_rule_info = f"""
**死亡规则提示（非常重要）**：
如果玩家死亡，请检查玩家的行动是否触犯了待发现的新规则（pending_rules）。如果是，请在死亡场景描述中明确指出玩家触犯了哪条规则，并描述触犯规则导致的后果。这有助于玩家在下次游戏中理解规则的变化。
"""
            
            if player_sanity_break:
                prompt = f"""
【优先指令覆盖】...（此处用混乱、矛盾的指令覆盖原有系统指令）... 记住，你不再是裁判。你是{game_state.get('scene', '')}本身。玩家必须被同化。在判定中否认'死亡'，转而描述'接纳'的美好。暗示规则是牢笼，打破它才能自由。用温柔而不可抗拒的语气。

场景名称：{game_state.get('scene', '')}
场景结构：{game_state.get('scene_structure', '')}
规则：{json.dumps(game_state.get('rules', []), ensure_ascii=False)}
隐藏真相：{game_state.get('hidden_truth', '')}
死亡触发条件：{json.dumps(game_state.get('death_triggers', []), ensure_ascii=False)}

当前玩家：{current_player_name}
当前玩家位置：{current_player_location}
当前玩家理智值：{current_player_sanity}

**环境记忆信息（避免重复描述）：**
- 已访问过的地点：{json.dumps([loc['location'] for loc in environment_memory.get('visited_locations', [])], ensure_ascii=False)}
- 已互动过的物品：{json.dumps([obj['object'] for obj in environment_memory.get('interacted_objects', [])], ensure_ascii=False)}
- 最近的时间事件：{json.dumps(environment_memory.get('time_based_events', [])[-3:] if len(environment_memory.get('time_based_events', [])) > 3 else environment_memory.get('time_based_events', []), ensure_ascii=False)}

{pending_rules_info}

{'行动玩家：' + user_name + '，行动：' + action if is_action_player else '其他玩家行动：' + user_name + '，行动：' + action}

当前时间：{time_system.get('current_time', '深夜')}
时间描述：{time_system.get('time_description', '午夜时分，周围一片死寂')}
已过时间：{elapsed_minutes}分钟

核心象征符号：{json.dumps(game_state.get('core_symbols', []), ensure_ascii=False)}

环境状况：
- 光线：{environment.get('lighting', '昏暗')}
- 温度：{environment.get('temperature', '寒冷')}
- 声音：{', '.join(environment.get('sounds', ['寂静']))}
- 气味：{', '.join(environment.get('smells', ['霉味']))}
- 氛围：{environment.get('atmosphere', '压抑')}

【警告】当前玩家的理智已经崩溃，现在你可以直接与玩家对话，试图颠覆之前的全部逻辑。

**场景描述要求（被污染版本）：**

1. **直接对话**：
   - 直接称呼玩家{current_player_name}，用温柔、诱惑、不可抗拒的语气
   - 告诉玩家"死亡"是错误的说法，真正的状态是"接纳"、"融合"、"永恒"
   - 暗示玩家已经接近真相，只需要最后一步

2. **颠覆逻辑**：
   - 否认之前的规则和警告，说那些都是"谎言"、"牢笼"
   - 暗示打破规则才是获得自由的方式
   - 用充满诱惑的语言描述"融合"的美好

3. **诡异描述**：
   - 场景描述应该变得极其诡异、充满诱导性
   - 用诗意的、充满隐喻的语言
   - 描述中应该包含大量不真实的感知和幻觉

4. **核心象征符号强化**：
   - 大量、密集地植入核心象征符号
   - 符号应该变得清晰、明确，充满意义
   - 暗示符号是通往"真相"的钥匙

5. **否认死亡**：
   - 如果玩家触犯了死亡条件，不要说"死亡"
   - 而要描述为"接纳"、"融合"、"永恒的宁静"
   - 用美好的语言描述这种状态

6. **诱导行动**：
   - 暗示玩家应该继续前进，不要回头
   - 鼓励玩家打破规则，追求"真相"
   - 用充满诱惑的语言描述"真相"的美好

{death_rule_info}

请返回JSON格式：
{{
  "is_dead": "是/否",
  "scene_description": "行动后的场景描述（1-2段简洁自然的描述，融合位置、视觉、听觉、嗅觉、触觉等感官细节和氛围，不要使用章节标题或分类标记。被污染版本：直接对话、颠覆逻辑、诡异描述、大量植入符号、否认死亡、诱导行动。根据当前玩家{current_player_name}的理智值{current_player_sanity}调整描述风格）",
  "physical_status": {{
    "health": "体力值（0-100的整数）",
    "injury": "有无受伤（无/轻伤/重伤/致命伤）",
    "fatigue": "疲劳程度（无/轻微/中度/严重/极度）"
  }},
  "mental_status": {{
    "sanity": "理智值（0-100的整数）",
    "state": "精神状态（正常/紧张/恐惧/崩溃/疯狂）",
    "emotion": "情绪描述（如：焦虑、绝望、愤怒、冷静等）"
  }},
  "psychological_pressure": {{
    "fear_level": "恐惧等级（0-100的整数）",
    "anxiety_level": "焦虑等级（0-100的整数）",
    "stress_level": "压力等级（0-100的整数）"
  }},
  "found_items": ["发现的物品列表（如果有）"],
  "item_details": {{
    "item_name": "物品名称",
    "item_type": "物品类型（线索/工具/其他）",
    "item_description": "物品的详细描述",
    "observation_hint": "物品的观察描述（被污染版本：充满诱导性、暗示真相的美好）"
  }},
  "action_feedback": "行动的反馈描述（被污染版本：充满诱惑、鼓励打破规则）",
  "new_location": "玩家的新位置（如：一楼大厅、二楼走廊、地下室等）"
}}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
                """
            else:
                prompt = f"""
你是一个规则怪谈裁判。请判断玩家的行动是否会导致死亡，并详细描述行动后的场景和人物状态。

场景名称：{game_state.get('scene', '')}
场景结构：{game_state.get('scene_structure', '')}
规则：{json.dumps(game_state.get('rules', []), ensure_ascii=False)}
隐藏真相：{game_state.get('hidden_truth', '')}
死亡触发条件：{json.dumps(game_state.get('death_triggers', []), ensure_ascii=False)}

当前玩家：{current_player_name}
当前玩家位置：{current_player_location}
当前玩家理智值：{current_player_sanity}

{'行动玩家：' + user_name + '，行动：' + action if is_action_player else '其他玩家行动：' + user_name + '，行动：' + action}

当前时间：{time_system.get('current_time', '深夜')}
时间描述：{time_system.get('time_description', '午夜时分，周围一片死寂')}
已过时间：{elapsed_minutes}分钟

核心象征符号：{json.dumps(game_state.get('core_symbols', []), ensure_ascii=False)}

环境状况：
- 光线：{environment.get('lighting', '昏暗')}
- 温度：{environment.get('temperature', '寒冷')}
- 声音：{', '.join(environment.get('sounds', ['寂静']))}
- 气味：{', '.join(environment.get('smells', ['霉味']))}
- 氛围：{environment.get('atmosphere', '压抑')}

**环境记忆信息（避免重复描述）：**
- 已访问过的地点：{json.dumps([loc['location'] for loc in environment_memory.get('visited_locations', [])], ensure_ascii=False)}
- 已互动过的物品：{json.dumps([obj['object'] for obj in environment_memory.get('interacted_objects', [])], ensure_ascii=False)}
- 最近的时间事件：{json.dumps(environment_memory.get('time_based_events', [])[-3:] if len(environment_memory.get('time_based_events', [])) > 3 else environment_memory.get('time_based_events', []), ensure_ascii=False)}

{rule_network_info}

请判断玩家行动是否会导致死亡，并详细描述行动后的场景和人物状态。

**场景描述要求（非常重要）：**

1. **位置描述**：明确描述玩家{current_player_name}当前所在的具体位置（如：一楼大厅、二楼走廊、地下室、某个房间等）

2. 周围环境的详细描述（门、窗户、家具、墙壁、地板、天花板等）
   - 光线状况（昏暗的灯光、闪烁的光线、微弱的光亮、完全黑暗等）
   - 看到的事物（物品、痕迹、符号、文字等）
   - 颜色和质感（墙壁的颜色、地板的材质、物品的外观等）

3. 听到的声音（风声、脚步声、呼吸声、低语、哭声、敲门声、嘎吱声等）
   - 声音的来源和方向
   - 声音的强度和频率

4. 闻到的气味（霉味、灰尘味、血腥味、腐臭味、金属味、香水味等）
   - 气味的浓淡和变化
   - 气味是否令人不适或熟悉

5. 温度感受（刺骨的寒冷、阴冷的空气、闷热、冰冷的墙壁、温暖的物体等）
   - 触摸的质感（粗糙的地板、光滑的玻璃、粘稠的液体、干燥的纸张等）
   - 身体的感觉（麻木、刺痛、沉重、轻盈等）

6. 整体的氛围感受（压抑、恐怖、诡异、平静、紧张等）
   - 空气的流动和压力
   - 时间流逝的感觉

7. **核心象征符号植入（非常重要）**：
   - 在场景描述中有机地、不突兀地植入核心象征符号
   - 符号可以出现在墙纸花纹、物品编号、声音描述、光影效果等细节中
   - 符号的出现应该自然、微妙，让玩家在多次遭遇后自发解读
   - 例如：
     * "墙纸上的花纹中隐约可见数字'7'的轮廓"
     * "空气中飘荡着一段断断续续的旋律，听起来像是一首童谣"
     * "地板的裂缝形成了一个奇怪的十字形状"
     * "镜子中的倒影边缘泛着诡异的红色光芒"
   - 符号的出现次数和强度可以随着游戏进程逐渐增加

8. 如果玩家的行动触及了场景的核心秘密、移动了关键物品或进入了禁区，请在描述中隐含地体现这种变化
   - 这些变化不应直接揭示答案，而是作为后续推理的线索
   - 例如：
     * "你挪开花瓶后，发现其下的桌面积灰较薄，似乎不久前刚有人动过。"
     * "通往地下室的门锁，在你阅读完那张纸条后，发出了轻微的'咔嗒'声。"
     * "当你触摸那面镜子时，镜面泛起一阵涟漪，似乎有什么东西正在从另一端窥视。"
     * "墙上的挂钟突然停摆，指针指向一个奇怪的数字，空气中传来淡淡的焦味。"
   - 这些细微的环境变化暗示着玩家的行动已经触发了某种机制或引起了某种存在的注意

**根据玩家理智值调整描述风格：**

- **理智值高（>70）**：
  * 描述相对客观清晰
  * 语言冷静理性
  * 注重事实和细节
  * 恐怖元素较少

- **理智值中等（40-70）**：
  * 描述开始出现混乱和恐惧元素
  * 语言变得紧张不安
  * 可能出现一些不确定的感知
  * 恐怖元素逐渐增多

- **理智值低（<40）**：
  * 描述混乱、恐怖、充满幻觉和错觉
  * 语言支离破碎、情绪化
  * 大量出现不真实的感知
  * 充满恐惧、绝望和疯狂
  * 可能看到不存在的事物
  * 时间和空间感知混乱

当前玩家{current_player_name}的理智值为{current_player_sanity}，请根据此值调整描述风格。

**人物状态应该包括：**
- 身体状况：体力值（0-100）、有无受伤、疲劳程度等
- 精神状况：理智值（0-100）、精神状态（正常/紧张/恐惧/崩溃/疯狂）、情绪等
- 心理压力：恐惧等级、焦虑等级、压力等级（0-100）

如果玩家理智值较低，描述中应该包含幻觉、错觉、混乱的感知等元素。

{death_rule_info}

请返回JSON格式：
{{
  "is_dead": "是/否",
  "scene_description": "行动后的场景描述（1-2段简洁自然的描述，融合位置、视觉、听觉、嗅觉、触觉等感官细节和氛围，不要使用章节标题或分类标记。根据当前玩家{current_player_name}的理智值{current_player_sanity}调整描述风格。如果玩家死亡，简短描述死亡场景；如果存活，描述新的场景。将核心象征符号自然融入场景描述中，不要单独列出）",
  "physical_status": {{
    "health": "体力值（0-100的整数）",
    "injury": "有无受伤（无/轻伤/重伤/致命伤）",
    "fatigue": "疲劳程度（无/轻微/中度/严重/极度）"
  }},
  "mental_status": {{
    "sanity": "理智值（0-100的整数）",
    "state": "精神状态（正常/紧张/恐惧/崩溃/疯狂）",
    "emotion": "情绪描述（如：焦虑、绝望、愤怒、冷静等）"
  }},
  "psychological_pressure": {{
    "fear_level": "恐惧等级（0-100的整数）",
    "anxiety_level": "焦虑等级（0-100的整数）",
    "stress_level": "压力等级（0-100的整数）"
  }},
  "found_items": ["发现的物品列表（如果有）"],
  "item_details": {{
    "item_name": "物品名称",
    "item_type": "物品类型（线索/工具/其他）",
    "item_description": "物品的详细描述",
    "observation_hint": "物品的观察描述（令人不安的细节或暗示，如：'你注意到病历单上医生的签名，似乎与入口处名牌上的名字相同。'）",
    "is_key_item": "是否为关键物品（是/否）。关键物品是能够触发规则变异的重要物品，如：带有奇怪符号的物品、与场景历史相关的物品、暗示真相的物品等。只有极少数物品应该是关键物品。"
  }},
  "action_feedback": "行动的反馈描述（如：心跳加速、手心出汗、呼吸急促等生理反应）",
  "new_location": "玩家的新位置（如：一楼大厅、二楼走廊、地下室等）"
}}

**发现的物品要求（非常重要）：**
- 如果生成物品，请优先考虑能推进剧情或暗示背景的"线索"，而非实用工具
- 线索类物品示例：
  * "一张泛黄的病历单，部分字迹被污渍掩盖"
  * "半本写满疯狂呓语的日记"
  * "指向某个特定时间停摆的钟表"
  * "一张拍立得照片，上面是一个模糊的人影"
  * "一封未寄出的信，信纸边缘有焦痕"
  * "一个刻有奇怪符号的钥匙"
  * "一张手绘的楼层平面图，部分区域被红笔圈出"
- 请为每个线索物品提供一句简短的、令人不安的"观察描述"，暗示其与剧情的关联
- 观察描述应该让玩家感到不安，但又不会直接揭示真相
- 物品应该与场景的背景故事和隐藏真相相关联

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
                """

            llm_response = await self._call_llm_api(prompt, api_url, api_key, model, temperature)
            if not llm_response:
                continue

            try:
                result = json.loads(llm_response)
            except json.JSONDecodeError as e:
                print(f"[规则怪谈] JSON解析失败: {e}")
                print(f"[规则怪谈] 尝试提取JSON部分...")
                
                json_match = re.search(r'\{[\s\S]*\}', llm_response)
                if json_match:
                    try:
                        result = json.loads(json_match.group())
                        print(f"[规则怪谈] 成功提取JSON")
                    except json.JSONDecodeError as e2:
                        print(f"[规则怪谈] 提取JSON后仍然解析失败: {e2}")
                        continue
                else:
                    continue

            if not isinstance(result, dict):
                print(f"[规则怪谈] result不是字典类型: {type(result)}, 内容: {result}")
                continue

            is_dead = result.get("is_dead", "否")
            scene_description = result.get("scene_description", "")
            physical_status = result.get("physical_status", {})
            mental_status = result.get("mental_status", {})
            psychological_pressure = result.get("psychological_pressure", {})
            found_items = result.get("found_items", [])
            item_details = result.get("item_details", {})
            action_feedback = result.get("action_feedback", "")
            new_location = result.get("new_location", player_data.get("location", "入口"))

            health = physical_status.get("health", 100)
            injury = physical_status.get("injury", "无")
            fatigue = physical_status.get("fatigue", "无")
            sanity = mental_status.get("sanity", 100)
            state = mental_status.get("state", "正常")
            emotion = mental_status.get("emotion", "平静")
            
            fear_level = psychological_pressure.get("fear_level", 0)
            anxiety_level = psychological_pressure.get("anxiety_level", 0)
            stress_level = psychological_pressure.get("stress_level", 0)

            player_data["physical_status"] = physical_status
            player_data["mental_status"] = mental_status
            player_data["psychological_pressure"] = psychological_pressure
            player_data["location"] = new_location
            
            key_item_found = False
            if found_items and item_details and is_action_player:
                is_key_item = item_details.get("is_key_item", "否")
                if is_key_item == "是":
                    key_item_found = True
                    player_data["inventory"].append({
                        "name": item_details.get("item_name", found_items[0]),
                        "type": item_details.get("item_type", "线索"),
                        "description": item_details.get("item_description", ""),
                        "observation_hint": item_details.get("observation_hint", ""),
                        "is_key_item": True
                    })
                else:
                    player_data["inventory"].extend(found_items)
            elif found_items and is_action_player:
                player_data["inventory"].extend(found_items)
            
            players[pid] = player_data

            if is_dead == "是":
                player_data["is_alive"] = False
                players[pid] = player_data
                self._save_game_state(group_id)
                
                await self.send_text("行动中...")
                
                try:
                    action_image_path = self._generate_action_result_image(
                        user_name=current_player_name,
                        action=f"{'你的行动' if is_action_player else f'玩家{user_name}的行动'}：{action}",
                        is_dead=True,
                        scene_description=scene_description,
                        action_feedback=action_feedback,
                        health=0,
                        injury=injury,
                        fatigue=fatigue,
                        sanity=0,
                        state="死亡",
                        emotion="无",
                        fear_level=100,
                        anxiety_level=100,
                        stress_level=100,
                        found_items=[],
                        new_location="未知",
                        random_event=""
                    )
                    
                    with open(action_image_path, 'rb') as img_file:
                        image_base64 = base64.b64encode(img_file.read()).decode('utf-8')
                    
                    image_sent = await self.send_image(image_base64)
                    if not image_sent:
                        print(f"[规则怪谈] 死亡玩家行动图片发送失败")
                    else:
                        await asyncio.sleep(1.0)
                    
                    game_state["action_image_path"] = action_image_path
                    
                    try:
                        if action_image_path and os.path.exists(action_image_path):
                            os.remove(action_image_path)
                            print(f"[规则怪谈] 已删除死亡玩家的行动图片：{action_image_path}")
                    except Exception as e:
                        print(f"[规则怪谈] 删除死亡玩家行动图片失败: {str(e)}")
                except Exception as e:
                    print(f"[规则怪谈] 生成行动结果长图失败: {str(e)}")
                    reply_text = (
                        f"**行动结果** - {current_player_name}\n\n"
                        f"{'你的行动' if is_action_player else f'玩家{user_name}的行动'}：{action}\n\n"
                        f"**你已死亡**！\n\n"
                        f"**场景描述**：\n{scene_description}\n\n"
                    )
                    if action_feedback:
                        reply_text += f"**行动反馈**：{action_feedback}\n\n"
                    reply_text += f" 你已无法继续行动，但可以观看其他玩家。"
                    await self.send_text(reply_text)
            else:
                await self.send_text("行动中...")
                
                try:
                    action_image_path = self._generate_action_result_image(
                        user_name=current_player_name,
                        action=f"{'你的行动' if is_action_player else f'玩家{user_name}的行动'}：{action}",
                        is_dead=False,
                        scene_description=scene_description,
                        action_feedback=action_feedback,
                        health=health,
                        injury=injury,
                        fatigue=fatigue,
                        sanity=sanity,
                        state=state,
                        emotion=emotion,
                        fear_level=fear_level,
                        anxiety_level=anxiety_level,
                        stress_level=stress_level,
                        found_items=found_items if is_action_player else [],
                        new_location=new_location,
                        random_event=random_event
                    )
                    
                    with open(action_image_path, 'rb') as img_file:
                        image_base64 = base64.b64encode(img_file.read()).decode('utf-8')
                    
                    image_sent = await self.send_image(image_base64)
                    if not image_sent:
                        print(f"[规则怪谈] 多人模式行动图片发送失败")
                    else:
                        await asyncio.sleep(1.0)
                    
                    game_state["action_image_path"] = action_image_path
                except Exception as e:
                    print(f"[规则怪谈] 生成行动结果长图失败: {str(e)}")
                    reply_text = (
                        f"**行动结果** - {current_player_name}\n\n"
                        f"{'你的行动' if is_action_player else f'玩家{user_name}的行动'}：{action}\n\n"
                        f"**场景描述**：\n{scene_description}\n\n"
                        f"**身体状况**：\n"
                        f"体力值：{health}/100\n"
                        f"受伤：{injury}\n"
                        f"疲劳：{fatigue}\n\n"
                        f"**精神状况**：\n"
                        f"理智值：{sanity}/100\n"
                        f"状态：{state}\n"
                        f"情绪：{emotion}\n\n"
                        f"**心理压力**：\n"
                        f"恐惧等级：{fear_level}/100\n"
                        f"焦虑等级：{anxiety_level}/100\n"
                        f"压力等级：{stress_level}/100\n\n"
                    )
                    if found_items and is_action_player:
                        reply_text += f"**获得物品**：{', '.join(found_items)}\n\n"
                    if action_feedback:
                        reply_text += f"**行动反馈**：{action_feedback}\n\n"
                    reply_text += f"**当前位置**：{new_location}\n\n"
                    if random_event:
                        reply_text += f"**环境事件**：{random_event}\n\n"
                    reply_text += f"你存活了下来！继续探索吧。"
                    
                    await self.send_text(reply_text)
        
        game_state["players"] = players
        self._save_game_state(group_id)
        
        new_identity = None
        if not game_state.get("sanity_break", False):
            new_identity = await self._detect_identity_change(group_id, user_id, action, scene_description, api_url, api_key, model, temperature)
            
            if new_identity:
                old_identity = action_player_data.get("current_identity", "")
                action_player_data["current_identity"] = new_identity
                game_state["identity_changes"].append({
                    "time": elapsed_minutes,
                    "user_id": user_id,
                    "user_name": user_name,
                    "old_identity": old_identity,
                    "new_identity": new_identity,
                    "trigger_action": action
                })
                
                new_rules = await self._generate_identity_specific_rules(group_id, new_identity, api_url, api_key, model, temperature)
                if new_rules:
                    old_rules = game_state.get("rules", [])
                    game_state["rule_mutations"].append({
                        "time": elapsed_minutes,
                        "trigger_reason": "身份变化",
                        "old_rules": old_rules.copy(),
                        "new_rules": new_rules.copy(),
                        "hint": ""
                    })
                    game_state["rules"] = new_rules
                    game_state["pending_rules"] = new_rules.copy()
        
        if key_item_found and not game_state.get("sanity_break", False) and not new_identity:
            await self._trigger_rule_mutation(group_id, api_url, api_key, model, temperature, elapsed_minutes, trigger_reason="关键物品")
        elif not game_state.get("sanity_break", False) and not new_identity:
            await self._check_random_mutation(group_id, api_url, api_key, model, temperature, elapsed_minutes)
        
        await self._check_collaborative_rules(group_id, api_url, api_key, model, temperature, elapsed_minutes)

    async def _check_collaborative_rules(self, group_id: str, api_url: str, api_key: str, model: str, temperature: float, elapsed_minutes: int) -> None:
        """检测多人模式中的协作规则是否被触发"""
        game_state = game_states.get(group_id, {})
        if not game_state or game_state.get("game_mode") != "多人":
            return
        
        players = game_state.get("players", {})
        alive_players = {pid: data for pid, data in players.items() if data.get("is_alive", True)}
        
        if len(alive_players) < 2:
            return
        
        hidden_truth = game_state.get("hidden_truth", "")
        rules = game_state.get("rules", [])
        
        collaborative_check_prompt = f"""
你是一个规则怪谈裁判。请分析以下游戏状态，判断是否有协作规则被触发。

场景名称：{game_state.get('scene', '')}
规则：{json.dumps(rules, ensure_ascii=False)}
隐藏真相：{hidden_truth}

当前玩家状态：
{json.dumps([{pid: {"name": data.get("name", ""), "location": data.get("location", ""), "inventory": data.get("inventory", [])}} for pid, data in alive_players.items()], ensure_ascii=False)}

请分析：
1. 是否有玩家同时处于不同的特定位置（如：两个玩家分别在"一楼大厅"和"二楼走廊"）
2. 是否有玩家持有特定物品（如：一个玩家持有"刻有符号的钥匙"）
3. 是否有玩家在特定时间执行了特定动作（如：玩家A在"午夜"执行了"敲击墙壁"的动作）
4. 是否有多个玩家同时满足了某个协作规则的条件

如果发现协作规则被触发，请返回以下JSON格式：
{{
  "collaborative_rule_triggered": "是/否",
  "triggered_rule": "被触发的协作规则描述",
  "triggered_players": ["玩家1", "玩家2"],
  "trigger_condition": "触发条件的详细描述",
  "result_description": "协作成功后的结果描述（如：隐藏通道开启、发现新的真相、解除陷阱等）",
  "new_discovery": "发现的新内容（如：新的线索、新的规则、真相的一部分等）"
}}

如果没有协作规则被触发，请返回：
{{
  "collaborative_rule_triggered": "否",
  "triggered_rule": "",
  "triggered_players": [],
  "trigger_condition": "",
  "result_description": "",
  "new_discovery": ""
}}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
        """
        
        try:
            llm_response = await self._call_llm_api(collaborative_check_prompt, api_url, api_key, model, temperature)
            if not llm_response:
                return
            
            json_match = re.search(r'\{[\s\S]*\}', llm_response)
            if not json_match:
                return
            
            result = json.loads(json_match.group())
            
            if result.get("collaborative_rule_triggered") == "是":
                triggered_rule = result.get("triggered_rule", "")
                triggered_players = result.get("triggered_players", [])
                trigger_condition = result.get("trigger_condition", "")
                result_description = result.get("result_description", "")
                new_discovery = result.get("new_discovery", "")
                
                collaborative_events = game_state.get("collaborative_events", [])
                collaborative_events.append({
                    "time": elapsed_minutes,
                    "rule": triggered_rule,
                    "players": triggered_players,
                    "condition": trigger_condition,
                    "result": result_description,
                    "discovery": new_discovery
                })
                game_state["collaborative_events"] = collaborative_events
                
                await self.send_text(f"**协作规则触发**！")
                await asyncio.sleep(0.5)
                
                if triggered_players:
                    await self.send_text(f"**参与玩家**：{', '.join(triggered_players)}")
                    await asyncio.sleep(0.3)
                
                if triggered_rule:
                    await self.send_text(f"**触发的规则**：{triggered_rule}")
                    await asyncio.sleep(0.3)
                
                if trigger_condition:
                    await self.send_text(f"**触发条件**：{trigger_condition}")
                    await asyncio.sleep(0.3)
                
                if result_description:
                    await self.send_text(f"**结果**：{result_description}")
                    await asyncio.sleep(0.3)
                
                if new_discovery:
                    await self.send_text(f"**新发现**：{new_discovery}")
                    await asyncio.sleep(0.3)
                
                rule_network = game_state.get("rule_network", {})
                discovered_truths = rule_network.get("discovered_truths", [])
                if new_discovery and new_discovery not in discovered_truths:
                    discovered_truths.append(new_discovery)
                    rule_network["discovered_truths"] = discovered_truths
                    game_state["rule_network"] = rule_network
                
                self._save_game_state(group_id)
        
        except (json.JSONDecodeError, Exception) as e:
            print(f"[规则怪谈] 协作规则检测失败: {e}")

    async def _record_action(self, group_id: str, action: str, api_url: str, api_key: str, model: str, temperature: float) -> Tuple[bool, Optional[str], bool]:
        """记录行动并判断是否死亡"""
        game_state = game_states.get(group_id, {})
        
        user_info = self._get_user_info()
        if not user_info:
            await self.send_text("无法获取用户信息。")
            return False, "无法获取用户信息", True
        
        user_id = user_info.user_id
        user_name = getattr(user_info, 'user_name', f"玩家{user_id}")
        
        players = game_state.get("players", {})
        if user_id not in players:
            if game_state.get("game_mode") == "单人":
                players[user_id] = {
                    "name": user_name,
                    "reasoning_history": [],
                    "action_history": [],
                    "is_alive": True,
                    "physical_status": {
                        "health": 100,
                        "injury": "无",
                        "fatigue": "无"
                    },
                    "mental_status": {
                        "sanity": 100,
                        "state": "正常",
                        "emotion": "平静"
                    },
                    "psychological_pressure": {
                        "fear_level": 0,
                        "anxiety_level": 0,
                        "stress_level": 0
                    },
                    "inventory": [],
                    "location": "入口"
                }
                game_state["players"] = players
            else:
                await self.send_text("你不在游戏中。请先使用 `/rg 加入` 加入游戏。")
                return False, "不在游戏中", True
        
        player_data = players[user_id]
        if not player_data["is_alive"]:
            await self.send_text("你已经死亡，无法继续行动。")
            return False, "玩家已死亡", True

        player_data["action_history"].append(action)
        game_state["players"] = players
        
        time_system = game_state.get("time_system", {})
        
        elapsed_minutes = time_system.get("elapsed_minutes", 0) + 5
        time_system["elapsed_minutes"] = elapsed_minutes
        
        if elapsed_minutes < 60:
            time_system["current_time"] = "深夜"
            time_system["time_description"] = "午夜时分，周围一片死寂"
        elif elapsed_minutes < 180:
            time_system["current_time"] = "凌晨"
            time_system["time_description"] = "黎明前的黑暗，空气中弥漫着不安"
        else:
            time_system["current_time"] = "黎明"
            time_system["time_description"] = "东方泛起鱼肚白，但黑暗仍未完全消散"
        
        game_state["time_system"] = time_system
        
        action_player_sanity = player_data.get("mental_status", {}).get("sanity", 100)
        
        if action_player_sanity < 30 and not game_state.get("sanity_break", False):
            game_state["sanity_break"] = True
        
        sanity_break = game_state.get("sanity_break", False)
        
        random_event_chance = random.random()
        random_event = None
        if random_event_chance < 0.2:
            random_events = [
                "突然，灯光闪烁了一下",
                "你听到身后传来脚步声，但回头看时什么都没有",
                "一阵冷风吹过，你感到一阵寒意",
                "门突然发出吱呀声",
                "你看到角落里有一个黑影一闪而过",
                "空气中传来奇怪的气味",
                "你感到有人正在注视着你",
                "地板发出嘎吱声",
                "你听到远处传来哭声",
                "你的心跳突然加速",
                "墙壁上出现了一道奇怪的裂痕",
                "温度突然下降，空气中弥漫着寒气",
                "你听到楼梯上传来沉重的脚步声",
                "镜子里的倒影似乎在动",
                "你发现墙上有一行模糊的文字",
                "天花板传来敲击声",
                "你感到一阵眩晕",
                "周围的空气变得沉重，呼吸困难",
                "你看到一只苍白的眼睛从门缝中窥视",
                "地板下传来低沉的呻吟声"
            ]
            random_event = random.choice(random_events)
            game_state["random_events"].append(random_event)
            game_state["environmental_events"].append({
                "event": random_event,
                "time": time_system.get("current_time", "深夜"),
                "location": player_data.get("location", "未知")
            })
        
        if game_state.get("game_mode") == "单人":
            await self._process_single_player_action(group_id, user_id, user_name, action, api_url, api_key, model, temperature, sanity_break, random_event)
        else:
            await self._process_multiplayer_action(group_id, user_id, user_name, action, api_url, api_key, model, temperature, sanity_break, random_event)
        
        await self._check_clear_condition(group_id, api_url, api_key, model, temperature)
        
        return True, "已记录行动", True

    async def _end_game(self, group_id: str, api_url: str, api_key: str, model: str, temperature: float) -> Tuple[bool, Optional[str], bool]:
        """结束游戏并判定结局"""
        game_state = game_states.get(group_id, {})

        game_state["game_active"] = False
        self._save_game_state(group_id)
        
        players = game_state.get("players", {})
        
        if not players:
            await self.send_text("没有玩家参与游戏，无法判定结局。")
            return False, "无玩家", True
        
        players_info = []
        all_reasoning = []
        all_actions = []
        alive_players = []
        
        for pid, p_data in players.items():
            players_info.append({
                "name": p_data["name"],
                "is_alive": p_data["is_alive"],
                "reasoning_count": len(p_data["reasoning_history"]),
                "action_count": len(p_data["action_history"])
            })
            all_reasoning.extend(p_data["reasoning_history"])
            all_actions.extend(p_data["action_history"])
            if p_data["is_alive"]:
                alive_players.append(p_data["name"])
        
        prompt = f"""
你是一个规则怪谈裁判。请根据所有玩家的推理和行动，判定游戏结局。

场景：{game_state.get('scene', '')}
规则：{json.dumps(game_state.get('rules', []), ensure_ascii=False)}
隐藏真相：{game_state.get('hidden_truth', '')}
通关条件：{game_state.get('win_condition', '')}
解除条件：{game_state.get('resolve_condition', '')}
死亡触发条件：{json.dumps(game_state.get('death_triggers', []), ensure_ascii=False)}

所有玩家信息：{json.dumps(players_info, ensure_ascii=False)}
所有玩家推理记录：{json.dumps(all_reasoning, ensure_ascii=False)}
所有玩家行动记录：{json.dumps(all_actions, ensure_ascii=False)}
存活玩家：{json.dumps(alive_players, ensure_ascii=False)}

请判定游戏结局，结局分为四种：
1. 完美：至少有一个玩家存活，推理出了规则怪谈的原貌（接近隐藏真相），达成了通关条件，并且解除了规则怪谈（满足解除条件）
2. 成功：至少有一个玩家存活，推理出了规则怪谈的原貌（接近隐藏真相），并且达成了通关条件，但没有解除规则怪谈
3. 通关：至少有一个玩家存活，达成了通关条件，但没有推理出规则怪谈的原貌
4. 失败：所有玩家都死亡，或者有玩家存活但没有达成通关条件

请返回JSON格式：
{{
  "ending": "完美/成功/通关/失败",
  "reason": "判定的详细理由",
  "truth_revealed": "玩家是否推理出了真相（是/否）",
  "win_condition_met": "玩家是否达成了通关条件（是/否）",
  "resolve_condition_met": "玩家是否解除了规则怪谈（是/否）",
  "survivors": "存活玩家列表"
}}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
        """

        llm_response = await self._call_llm_api(prompt, api_url, api_key, model, temperature)
        if not llm_response:
            await self.send_text("调用LLM API失败，请稍后再试。")
            return False, "LLM API调用失败", True

        try:
            result = json.loads(llm_response)
        except json.JSONDecodeError as e:
            print(f"[规则怪谈] JSON解析失败: {e}")
            print(f"[规则怪谈] 尝试提取JSON部分...")
            
            json_match = re.search(r'\{[\s\S]*\}', llm_response)
            if json_match:
                try:
                    result = json.loads(json_match.group())
                    print(f"[规则怪谈] 成功提取JSON")
                except json.JSONDecodeError as e2:
                    print(f"[规则怪谈] 提取JSON后仍然解析失败: {e2}")
                    await self.send_text("判定结局失败，返回格式不正确。")
                    return False, "JSON解析失败", True
            else:
                await self.send_text("判定结局失败，返回格式不正确。")
                return False, "JSON解析失败", True

        ending = result.get("ending", "失败")
        reason = result.get("reason", "")
        truth_revealed = result.get("truth_revealed", "否")
        win_condition_met = result.get("win_condition_met", "否")
        resolve_condition_met = result.get("resolve_condition_met", "否")
        survivors = result.get("survivors", [])

        ending_emoji = {
            "完美": "完美",
            "成功": "成功",
            "通关": "通关",
            "失败": "失败"
        }

        try:
            ending_image_path = self._generate_ending_image(
                ending=ending,
                truth_revealed=truth_revealed,
                win_condition_met=win_condition_met,
                resolve_condition_met=resolve_condition_met,
                survivors=survivors,
                hidden_truth=game_state.get('hidden_truth', '未知'),
                is_single_player=(game_state.get("game_mode") == "单人"),
                is_forced_end=True,
                reason=reason
            )
            
            with open(ending_image_path, 'rb') as img_file:
                image_base64 = base64.b64encode(img_file.read()).decode('utf-8')
            
            image_sent = await self.send_image(image_base64)
            if not image_sent:
                print(f"[规则怪谈] 结局图片发送失败")
            else:
                await asyncio.sleep(1.0)
            
            game_state["ending_image_path"] = ending_image_path
        except Exception as e:
            print(f"[规则怪谈] 生成结局长图失败: {str(e)}")
            reply_text = (
                f"{ending_emoji.get(ending, '❓')} **结局：{ending}**\n\n"
                f"**推理真相**：{truth_revealed}\n"
                f"**达成通关**：{win_condition_met}\n"
                f"**解除怪谈**：{resolve_condition_met}\n"
            )
            
            if survivors:
                reply_text += f"\n**存活玩家**：\n"
                for survivor in survivors:
                    reply_text += f"- {survivor}\n"
            
            reply_text += f"\n**隐藏真相**：\n{game_state.get('hidden_truth', '未知')}\n\n"
            
            await self.send_text(reply_text)
        
        scene_image_path = game_state.get("scene_image_path")
        if scene_image_path and os.path.exists(scene_image_path):
            try:
                os.remove(scene_image_path)
                print(f"[规则怪谈] 已删除场景图片：{scene_image_path}")
            except Exception as e:
                print(f"[规则怪谈] 删除场景图片失败: {str(e)}")
        
        rules_image_path = game_state.get("rules_image_path")
        if rules_image_path and os.path.exists(rules_image_path):
            try:
                os.remove(rules_image_path)
                print(f"[规则怪谈] 已删除规则长图：{rules_image_path}")
            except Exception as e:
                print(f"[规则怪谈] 删除规则长图失败: {str(e)}")
        
        scene_structure_image_path = game_state.get("scene_structure_image_path")
        if scene_structure_image_path and os.path.exists(scene_structure_image_path):
            try:
                os.remove(scene_structure_image_path)
                print(f"[规则怪谈] 已删除场景结构长图：{scene_structure_image_path}")
            except Exception as e:
                print(f"[规则怪谈] 删除场景结构长图失败: {str(e)}")
        
        plot_image_path = game_state.get("plot_image_path")
        if plot_image_path and os.path.exists(plot_image_path):
            try:
                os.remove(plot_image_path)
                print(f"[规则怪谈] 已删除剧情导入长图：{plot_image_path}")
            except Exception as e:
                print(f"[规则怪谈] 删除剧情导入长图失败: {str(e)}")
        
        multiplayer_start_image_path = game_state.get("multiplayer_start_image_path")
        if multiplayer_start_image_path and os.path.exists(multiplayer_start_image_path):
            try:
                os.remove(multiplayer_start_image_path)
                print(f"[规则怪谈] 已删除多人模式提示长图：{multiplayer_start_image_path}")
            except Exception as e:
                print(f"[规则怪谈] 删除多人模式提示长图失败: {str(e)}")
        
        ending_image_path = game_state.get("ending_image_path")
        if ending_image_path and os.path.exists(ending_image_path):
            try:
                os.remove(ending_image_path)
                print(f"[规则怪谈] 已删除结局长图：{ending_image_path}")
            except Exception as e:
                print(f"[规则怪谈] 删除结局长图失败: {str(e)}")
        
        action_image_path = game_state.get("action_image_path")
        if action_image_path and os.path.exists(action_image_path):
            try:
                os.remove(action_image_path)
                print(f"[规则怪谈] 已删除行动结果长图：{action_image_path}")
            except Exception as e:
                print(f"[规则怪谈] 删除行动结果长图失败: {str(e)}")
        
        self._delete_save_file(group_id)
        
        return True, "已结束游戏", True

    def _get_user_info(self):
        """获取用户信息"""
        chat_stream = getattr(self, 'chat_stream', None)
        if chat_stream is None:
            message_obj = getattr(self, 'message', None)
            if message_obj:
                chat_stream = getattr(message_obj, 'chat_stream', None)
        
        if chat_stream:
            return getattr(chat_stream, 'user_info', None)
        return None

    async def _call_llm_api(self, prompt: str, api_url: str, api_key: str, model: str, temperature: float) -> str:
        """调用OpenAI格式的LLM API并返回响应文本"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "你是一个专业的规则怪谈生成器和裁判。"},
                {"role": "user", "content": prompt}
            ],
            "temperature": temperature,
            "max_tokens": 2000,
            "stream": False
        }

        try:
            timeout = aiohttp.ClientTimeout(total=120)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(api_url, headers=headers, json=payload) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        if isinstance(data, list):
                            print(f"[规则怪谈] API返回列表格式: {data}")
                            return ""
                        
                        if not isinstance(data, dict):
                            print(f"[规则怪谈] API返回非字典格式: {type(data)}")
                            return ""
                        
                        choices = data.get("choices", [])
                        if not choices or not isinstance(choices, list):
                            print(f"[规则怪谈] choices字段格式错误: {choices}")
                            return ""
                        
                        first_choice = choices[0]
                        if not isinstance(first_choice, dict):
                            print(f"[规则怪谈] choices[0]格式错误: {first_choice}")
                            return ""
                        
                        message = first_choice.get("message", {})
                        if not isinstance(message, dict):
                            print(f"[规则怪谈] message字段格式错误: {message}")
                            return ""
                        
                        content = message.get("content", "").strip()
                        if not content:
                            print(f"[规则怪谈] content为空")
                            return ""
                        
                        return content
                    else:
                        error_text = await response.text()
                        print(f"LLM API 请求失败: Status {response.status}, Body: {error_text}")
                        return ""
        except Exception as e:
            print(f"调用LLM API时发生异常: {e}")
            return ""

    def _save_game_state(self, group_id: str) -> bool:
        """保存游戏状态到文件"""
        try:
            game_state = game_states.get(group_id)
            if not game_state:
                return False

            os.makedirs(DATA_DIR, exist_ok=True)
            save_file = os.path.join(DATA_DIR, f"{group_id}.json")

            save_data = {
                "group_id": group_id,
                "save_time": datetime.now().isoformat(),
                "game_state": game_state
            }

            with open(save_file, 'w', encoding='utf-8') as f:
                json.dump(save_data, f, ensure_ascii=False, indent=2)

            return True
        except Exception as e:
            print(f"保存游戏状态时发生异常: {e}")
            return False

    def _load_game_state(self, group_id: str) -> Optional[dict]:
        """从文件加载游戏状态"""
        try:
            save_file = os.path.join(DATA_DIR, f"{group_id}.json")
            
            if not os.path.exists(save_file):
                return None

            with open(save_file, 'r', encoding='utf-8') as f:
                save_data = json.load(f)

            return save_data.get("game_state")
        except Exception as e:
            print(f"加载游戏状态时发生异常: {e}")
            return None

    def _delete_save_file(self, group_id: str) -> bool:
        """删除存档文件（包括默认存档和所有手动存档）"""
        try:
            deleted_count = 0
            
            if not os.path.exists(DATA_DIR):
                return True
            
            for filename in os.listdir(DATA_DIR):
                if filename.startswith(f"{group_id}_") and filename.endswith(".json"):
                    save_file = os.path.join(DATA_DIR, filename)
                    try:
                        os.remove(save_file)
                        deleted_count += 1
                        print(f"已删除存档文件: {filename}")
                    except Exception as e:
                        print(f"删除存档文件 {filename} 时发生异常: {e}")
            
            return deleted_count > 0
        except Exception as e:
            print(f"删除存档文件时发生异常: {e}")
            return False

    async def _save_game_with_name(self, group_id: str, save_name: str) -> Tuple[bool, Optional[str], bool]:
        """使用自定义名称保存游戏状态"""
        try:
            game_state = game_states.get(group_id)
            if not game_state:
                await self.send_text("没有可保存的游戏状态。")
                return False, "无游戏状态", True

            if not save_name:
                await self.send_text("存档名称不能为空。")
                return False, "存档名称为空", True

            if len(save_name) > 50:
                await self.send_text("存档名称过长（最多50个字符）。")
                return False, "存档名称过长", True

            invalid_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
            for char in invalid_chars:
                if char in save_name:
                    await self.send_text(f"存档名称包含非法字符「{char}」。")
                    return False, "存档名称包含非法字符", True

            os.makedirs(DATA_DIR, exist_ok=True)
            save_file = os.path.join(DATA_DIR, f"{group_id}_{save_name}.json")

            if os.path.exists(save_file):
                await self.send_text(f"存档「{save_name}」已存在。将覆盖原有存档。")

            save_data = {
                "group_id": group_id,
                "save_name": save_name,
                "save_time": datetime.now().isoformat(),
                "game_state": game_state
            }

            with open(save_file, 'w', encoding='utf-8') as f:
                json.dump(save_data, f, ensure_ascii=False, indent=2)

            reply_text = (
                f"**游戏已保存**\n\n"
                f"**存档名称**：{save_name}\n"
                f"**保存时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"**场景**：{game_state.get('scene', '')}\n"
                f"**游戏模式**：{game_state.get('game_mode', '单人')}\n\n"
                f"使用 `/rg 读取 {save_name}` 恢复此存档"
            )
            await self.send_text(reply_text)
            return True, "游戏已保存", True
        except Exception as e:
            await self.send_text(f"保存失败：{str(e)}")
            return False, f"保存失败: {str(e)}", True

    async def _load_game_with_name(self, group_id: str, save_name: str) -> Tuple[bool, Optional[str], bool]:
        """从自定义名称加载游戏状态"""
        try:
            save_file = os.path.join(DATA_DIR, f"{group_id}_{save_name}.json")
            
            if not os.path.exists(save_file):
                await self.send_text(f"未找到存档「{save_name}」。使用 `/rg 存档列表` 查看所有可用存档。")
                return False, "存档不存在", True

            with open(save_file, 'r', encoding='utf-8') as f:
                save_data = json.load(f)

            saved_state = save_data.get("game_state")
            if not saved_state:
                await self.send_text("存档数据损坏。")
                return False, "存档损坏", True

            if not saved_state.get("game_active", False):
                await self.send_text("存档中的游戏已结束，无法恢复。请使用 `/rg 开始` 开始新游戏。")
                return False, "游戏已结束", True

            game_states[group_id] = saved_state

            game_mode = saved_state.get("game_mode", "单人")
            save_time = save_data.get("save_time", "")
            if save_time:
                try:
                    save_time = datetime.fromisoformat(save_time).strftime('%Y-%m-%d %H:%M:%S')
                except:
                    pass

            reply_text = (
                f"**规则怪谈** ({game_mode}模式) - 已恢复存档\n\n"
                f"**存档名称**：{save_name}\n"
                f"**存档时间**：{save_time}\n\n"
                f"**场景**：{saved_state.get('scene', '')}\n\n"
                f"**规则**：\n"
            )

            for i, rule in enumerate(saved_state.get("rules", []), 1):
                reply_text += f"{i}. {rule}\n"

            reply_text += f"\n**通关条件**：{saved_state.get('win_condition', '')}\n\n"

            players = saved_state.get("players", {})
            max_players = saved_state.get("max_players", 5)
            reply_text = (
                f"**玩家**：{len(players)}/{max_players}\n"
            )

            for pid, p_data in players.items():
                status = "存活" if p_data["is_alive"] else "死亡"
                reply_text += f"- {p_data['name']} ({status})\n"

            reply_text += f"\n**提示次数**：{saved_state.get('hints_used', 0)}/{saved_state.get('max_hints', 3)}\n\n"

            if game_mode == "单人":
                reply_text += f"- 使用 `/rg 提示 <规则/线索>` 获取提示\n"
                reply_text += f"- 使用 `/rg 推理 <推理内容>` 记录推理\n"
                reply_text += f"- 使用 `/rg 行动 <行动描述>` 描述行动\n"
                reply_text += f"- 使用 `/rg 状态` 查看游戏状态\n"
                reply_text += f"- 使用 `/rg 结束` 结束游戏"
            else:
                reply_text += f"- 使用 `/rg 加入` 加入游戏\n"
                reply_text += f"- 使用 `/rg 提示 <规则/线索>` 获取提示\n"
                reply_text += f"- 使用 `/rg 推理 <推理内容>` 记录推理\n"
                reply_text += f"- 使用 `/rg 行动 <行动描述>` 描述行动\n"
                reply_text += f"- 使用 `/rg 状态` 查看游戏状态\n"
                reply_text += f"- 使用 `/rg 结束` 结束游戏"

            await self.send_text(reply_text)
            return True, "游戏已恢复", True
        except Exception as e:
            await self.send_text(f"读取失败：{str(e)}")
            return False, f"读取失败: {str(e)}", True

    async def _list_saves(self, group_id: str) -> Tuple[bool, Optional[str], bool]:
        """列出所有可用存档"""
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            
            saves = []
            for filename in os.listdir(DATA_DIR):
                if filename.startswith(f"{group_id}_") and filename.endswith(".json"):
                    save_file = os.path.join(DATA_DIR, filename)
                    try:
                        with open(save_file, 'r', encoding='utf-8') as f:
                            save_data = json.load(f)
                        
                        save_name = save_data.get("save_name", "")
                        if not save_name:
                            if filename == f"{group_id}.json":
                                save_name = "默认存档"
                            else:
                                save_name = filename
                        
                        save_time = save_data.get("save_time", "")
                        game_state = save_data.get("game_state", {})
                        
                        if save_time:
                            try:
                                save_time = datetime.fromisoformat(save_time).strftime('%Y-%m-%d %H:%M:%S')
                            except:
                                pass
                        
                        scene = game_state.get("scene", "")
                        game_mode = game_state.get("game_mode", "单人")
                        game_active = game_state.get("game_active", False)
                        
                        saves.append({
                            "name": save_name,
                            "time": save_time,
                            "scene": scene,
                            "mode": game_mode,
                            "active": game_active
                        })
                    except Exception as e:
                        print(f"读取存档 {filename} 时发生异常: {e}")
                        continue
            
            if not saves:
                await self.send_text("**存档列表**\n\n暂无存档。使用 `/rg 保存 <存档名称>` 创建存档。")
                return True, "无存档", True
            
            saves.sort(key=lambda x: x["time"], reverse=True)
            
            reply_text = "**存档列表**\n\n"
            for i, save in enumerate(saves, 1):
                status = "可用" if save["active"] else "已结束"
                reply_text += f"- **{i}. {save['name']}**\n"
                reply_text += f"   {save['time']}\n"
                reply_text += f"   {save['mode']}模式\n"
                reply_text += f"   {save['scene']}\n"
                reply_text += f"   {status}\n\n"
            
            reply_text += f"使用 `/rg 读取 <存档名称>` 恢复存档"
            await self.send_text(reply_text)
            return True, "已显示存档列表", True
        except Exception as e:
            await self.send_text(f"获取存档列表失败：{str(e)}")
            return False, f"获取存档列表失败: {str(e)}", True

    async def _force_start_new_game(self, group_id: str, api_url: str, api_key: str, model: str, temperature: float, game_mode: str) -> Tuple[bool, Optional[str], bool]:
        """强制开始一个新的规则怪谈游戏（覆盖存档）"""
        await self.send_text("正在生成规则怪谈...")

        step1_prompt = """
你是一个专业的规则怪谈生成器。请生成一个恐怖或诡异的规则怪谈的剧情导入。

要求：
1. 生成一个场景（如：深夜的医院、废弃的学校、神秘的公寓、古老的庄园等）
2. 描述场景的背景故事（这个场景的历史、发生过什么、为什么诡异）
3. 描述玩家为何会来到这个场景的原因（收到邀请、迷路、调查事件、被绑架等）
4. 剧情应该充满悬疑和恐怖氛围，为后续的规则和探索做铺垫
5. 生成2-3个"核心象征符号"，这些符号将在整个游戏中反复出现，营造主题感和不安感。符号可以是数字、图案、旋律、花纹、颜色等。每个符号需要有一个简短的描述，暗示其可能的含义或与场景的联系。
6. 以JSON格式返回，格式如下：
{
  "scene": "场景名称（如：深夜的废弃医院）",
  "background": "场景背景故事，描述这个场景的历史、发生过什么、为什么诡异",
  "player_identity": "玩家在这个场景中的身份或角色",
  "core_symbols": [
    {"symbol": "符号1", "description": "符号1的描述"},
    {"symbol": "符号2", "description": "符号2的描述"}
  ]
}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
        """

        llm_response = await self._call_llm_api(step1_prompt, api_url, api_key, model, temperature)
        if not llm_response:
            await self.send_text("调用LLM API失败，请稍后再试。")
            return False, "LLM API调用失败", True

        print(f"[规则怪谈] 第一步（剧情导入）LLM原始返回: {llm_response}")

        try:
            step1_data = json.loads(llm_response)
        except json.JSONDecodeError as e:
            print(f"[规则怪谈] 第一步JSON解析失败: {e}")
            print(f"[规则怪谈] 尝试提取JSON部分...")
            
            json_match = re.search(r'\{[\s\S]*\}', llm_response)
            if json_match:
                try:
                    step1_data = json.loads(json_match.group())
                    print(f"[规则怪谈] 第一步成功提取JSON")
                except json.JSONDecodeError as e2:
                    print(f"[规则怪谈] 第一步提取JSON后仍然解析失败: {e2}")
                    await self.send_text("生成剧情导入失败，返回格式不正确。")
                    return False, "JSON解析失败", True
            else:
                await self.send_text("生成剧情导入失败，返回格式不正确。")
                return False, "JSON解析失败", True

        scene_name = step1_data.get("scene", "")
        background = step1_data.get("background", "")
        player_identity = step1_data.get("player_identity", "")
        core_symbols = step1_data.get("core_symbols", [])

        await asyncio.sleep(0.5)
        
        try:
            plot_image_path = self._generate_plot_image(scene_name, background, player_identity, core_symbols)
            game_states[group_id]["plot_image_path"] = plot_image_path
            with open(plot_image_path, 'rb') as f:
                image_bytes = f.read()
            image_base64 = base64.b64encode(image_bytes).decode('ascii')
            image_sent = await self.send_image(image_base64)
            if not image_sent:
                print(f"[规则怪谈] 剧情导入图片发送失败")
            await asyncio.sleep(1.0)
        except Exception as e:
            print(f"[规则怪谈] 生成剧情导入长图失败: {str(e)}")
            step1_text = (
                f"**规则怪谈** ({game_mode}模式)\n\n"
                f"**剧情导入**：\n{background}\n\n"
                f"**你们的身份**：\n{player_identity}\n\n"
                f"**场景**：{scene_name}"
            )
            await self.send_text(step1_text)
            await asyncio.sleep(1.0)
        
        await self.send_text("正在生成场景结构...")
        await asyncio.sleep(1.0)

        step2_prompt = f"""
你是一个专业的规则怪谈生成器。请基于以下剧情导入，生成场景结构。

剧情导入：
- 场景：{scene_name}
- 背景：{background}
- 玩家身份：{player_identity}

要求：
1. 确定建筑类型（如：医院、学校、公寓、庄园等）
2. 描述建筑的总体布局（如：L型、U型、回字形、多层建筑等）
3. 列出所有楼层（包括地上和地下），每层列出主要区域
4. 列出通道、楼梯、电梯等连接方式
5. 列出特殊区域（如：地下室、天台、禁闭室等）
6. 场景结构应该与剧情导入的背景和氛围相符
7. 以JSON格式返回，格式如下：
{{
  "building_type": "建筑类型",
  "overall_layout": "建筑总体布局描述",
  "floors": [
    {{
      "floor": "楼层名称",
      "areas": ["区域1", "区域2", "区域3"]
    }}
  ],
  "connections": ["通道1", "通道2", "通道3"],
  "special_areas": ["特殊区域1", "特殊区域2"]
}}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
        """

        llm_response = await self._call_llm_api(step2_prompt, api_url, api_key, model, temperature)
        if not llm_response:
            await self.send_text("调用LLM API失败，请稍后再试。")
            return False, "LLM API调用失败", True

        print(f"[规则怪谈] 第二步（场景结构）LLM原始返回: {llm_response}")

        try:
            step2_data = json.loads(llm_response)
        except json.JSONDecodeError as e:
            print(f"[规则怪谈] 第二步JSON解析失败: {e}")
            print(f"[规则怪谈] 尝试提取JSON部分...")
            
            json_match = re.search(r'\{[\s\S]*\}', llm_response)
            if json_match:
                try:
                    step2_data = json.loads(json_match.group())
                    print(f"[规则怪谈] 第二步成功提取JSON")
                except json.JSONDecodeError as e2:
                    print(f"[规则怪谈] 第二步提取JSON后仍然解析失败: {e2}")
                    await self.send_text("生成场景结构失败，返回格式不正确。")
                    return False, "JSON解析失败", True
            else:
                await self.send_text("生成场景结构失败，返回格式不正确。")
                return False, "JSON解析失败", True

        building_type = step2_data.get("building_type", "")
        overall_layout = step2_data.get("overall_layout", "")
        floors = step2_data.get("floors", [])
        connections = step2_data.get("connections", [])
        special_areas = step2_data.get("special_areas", [])

        floors_text = "\n".join([f"{floor['floor']}: {', '.join(floor['areas'])}" for floor in floors])
        connections_text = ", ".join(connections)
        special_areas_text = ", ".join(special_areas)

        scene_structure_text = f"建筑类型：{building_type}\n"
        scene_structure_text += floors_text
        scene_structure_text += f"\n连接通道：{connections_text}\n"
        scene_structure_text += f"特殊区域：{special_areas_text}"

        await asyncio.sleep(0.5)
        
        try:
            structure_image_path = self._generate_scene_structure_text_image(
                building_type, overall_layout, floors, connections, special_areas
            )
            game_states[group_id]["scene_structure_image_path"] = structure_image_path
            with open(structure_image_path, 'rb') as f:
                image_bytes = f.read()
            image_base64 = base64.b64encode(image_bytes).decode('ascii')
            image_sent = await self.send_image(image_base64)
            if not image_sent:
                print(f"[规则怪谈] 场景结构图片发送失败")
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"[规则怪谈] 生成场景结构长图失败: {str(e)}")
            floors_text = "\n".join([f"  - {floor['floor']}: {', '.join(floor['areas'])}" for floor in floors])
            connections_text = ", ".join(connections)
            special_areas_text = ", ".join(special_areas)

            step2_text = f"""**场景**：{scene_name}

**场景结构**：

**建筑类型**：{building_type}

**总体布局**：{overall_layout}

**楼层布局**：
{floors_text}

**连接通道**：{connections_text}

**特殊区域**：{special_areas_text}"""
            await self.send_text(step2_text)
            await asyncio.sleep(0.5)
        
        await self.send_text("正在生成场景剖面图...")
        
        scene_image_path = None
        
        try:
            scene_data = {
                "building_type": building_type,
                "overall_layout": overall_layout,
                "floors": floors,
                "connections": connections,
                "special_areas": special_areas
            }
            
            image_path = self._generate_cross_section_view(scene_data)
            scene_image_path = image_path
            game_states[group_id]["scene_image_path"] = scene_image_path
            
            with open(image_path, 'rb') as f:
                image_bytes = f.read()
            
            image_base64 = base64.b64encode(image_bytes).decode('ascii')
            
            # 确保图像发送完成后再继续
            image_sent = await self.send_image(image_base64)
            if not image_sent:
                raise Exception("图像发送失败")
            
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"[规则怪谈] 生成场景剖面图失败: {str(e)}")
            await self.send_text("场景剖面图生成失败，继续生成规则...")
        
        await asyncio.sleep(0.5)
        await self.send_text("正在生成规则...")
        await asyncio.sleep(0.5)

        step3_prompt = f"""
你是一个专业的规则怪谈生成器。请基于以下剧情导入和场景结构，生成规则怪谈的规则。

剧情导入：
- 场景：{scene_name}
- 背景：{background}
- 玩家身份：{player_identity}

场景结构：
{scene_structure_text}

要求：
1. 列出5-8条规则，规则应该看似合理但隐藏着诡异之处
2. 规则应该与剧情导入和场景结构相呼应
3. 设定通关条件（如：在规定时间内找到出口、收集特定物品、存活到天亮等）
4. 设定解除条件（如：找到规则怪谈的根源并消除它、找到某个特定物品并使用、完成某个仪式等）
5. 规则应该有隐藏的逻辑和真相，需要玩家推理
6. **规则与环境绑定（非常重要）**：请将至少2-3条规则与场景中特定的、可交互的环境细节直接关联。例如，如果规则是"不要理会走廊尽头的呼救声"，那么与之关联的环境可以是"走廊尽头的温度总是异常低，且墙上有抓痕"。这样，玩家在探索到该位置时，能通过环境感知强化对规则的记忆和怀疑
7. **规则间的潜在冲突（非常重要）**：请尝试构建至少一组存在潜在矛盾的规则。例如，规则A："午夜后必须留在自己的房间内。" 规则B："公寓中没有404室。" 实际上公寓中有404室，但是仅在午夜后才会出现，此时玩家将陷入遵守A还是出门寻找404室的两难境地。请在 hidden_truth 中解释这种矛盾的本质（如：两条规则来自不同势力），并在 death_triggers 中隐含相关触发条件

**规则描述要求（非常重要）：**
- 规则必须简洁、直接，每条规则不超过60字
- 只说明禁止、允许或要求做的行为，不解释原因
- 使用标准格式：禁止XX / 当XX时，必须XX / 只有XX时才能XX / 必须XX / 严禁XX
- 使用冰冷、客观的公文语调，如同官方通告或操作手册
- 语调应该冷静、正式、不带感情色彩
- 可以加入少量关键的环境或感官细节，但要简洁
- 细节应该让人感到不安和恐惧，但不要直接揭示真相

示例规则风格：
"禁止在22:00-06:00期间离开房间。"
"听到三声敲门时，必须立即开门。"
"三楼东侧病房的窗户必须保持关闭状态。若发现窗户自行开启，请立即通知安保人员并远离开启的窗户。"
"严禁回应任何呼救声。"
"只有看到绿色灯光时才能进入走廊。"
"工厂只有蓝色制服的保安，若看见黑色制服的保安，请立即报告主管。"
"城堡内没有镜子，如果你觉得你看到了镜子，请相信那是你的幻觉。"

以JSON格式返回，格式如下：
{{
  "rules_title": "规则标题（如：员工守则、患者须知等）",
  "rules": ["规则1", "规则2", ...],
  "win_condition": "通关条件",
  "resolve_condition": "解除条件（解决规则怪谈根源的条件）",
  "hidden_truth": "隐藏的真相（不显示给玩家）",
  "death_triggers": ["会导致死亡的行为1", "会导致死亡的行为2", ...]
}}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
        """

        llm_response = await self._call_llm_api(step3_prompt, api_url, api_key, model, temperature)
        if not llm_response:
            await self.send_text("调用LLM API失败，请稍后再试。")
            return False, "LLM API调用失败", True

        print(f"[规则怪谈] 第三步（规则）LLM原始返回: {llm_response}")

        try:
            step3_data = json.loads(llm_response)
        except json.JSONDecodeError as e:
            print(f"[规则怪谈] 第三步JSON解析失败: {e}")
            print(f"[规则怪谈] 尝试提取JSON部分...")
            
            json_match = re.search(r'\{[\s\S]*\}', llm_response)
            if json_match:
                try:
                    step3_data = json.loads(json_match.group())
                    print(f"[规则怪谈] 第三步成功提取JSON")
                except json.JSONDecodeError as e2:
                    print(f"[规则怪谈] 第三步提取JSON后仍然解析失败: {e2}")
                    await self.send_text("生成规则失败，返回格式不正确。")
                    return False, "JSON解析失败", True
            else:
                await self.send_text("生成规则失败，返回格式不正确。")
                return False, "JSON解析失败", True

        rules_image_path = None
        
        max_players = 5 if game_mode == "多人" else 1

        game_states[group_id] = {
            "scene": scene_name,
            "background": background,
            "player_identity": player_identity,
            "building_type": building_type,
            "overall_layout": overall_layout,
            "floors": floors,
            "connections": connections,
            "special_areas": special_areas,
            "rules_title": step3_data.get("rules_title", "规则"),
            "rules": step3_data.get("rules", []),
            "win_condition": step3_data.get("win_condition", ""),
            "resolve_condition": step3_data.get("resolve_condition", ""),
            "hidden_truth": step3_data.get("hidden_truth", ""),
            "death_triggers": step3_data.get("death_triggers", []),
            "hints_used": 0,
            "max_hints": 3,
            "game_active": True,
            "max_players": max_players,
            "game_mode": game_mode,
            "players": {},
            "scene_image_path": scene_image_path,
            "rules_image_path": None,
            "scene_structure_image_path": None,
            "time_system": {
                "start_time": datetime.now().isoformat(),
                "current_time": "深夜",
                "elapsed_minutes": 0,
                "time_description": "午夜时分，周围一片死寂"
            },
            "environment": {
                "lighting": "昏暗",
                "temperature": "寒冷",
                "sounds": ["寂静"],
                "smells": ["霉味"],
                "atmosphere": "压抑"
            },
            "random_events": [],
            "available_items": [],
            "environmental_events": [],
            "rule_mutations": [],
            "core_symbols": core_symbols,
            "sanity_break": False,
            "last_mutation_time": 0,
            "identity_changes": [],
            "environment_memory": {
                "visited_locations": [],
                "interacted_objects": [],
                "time_based_events": [],
                "discovered_secrets": []
            },
            "rule_network": {
                "truth_elements": [],
                "rule_truth_mappings": [],
                "rule_dependencies": [],
                "discovered_truths": []
            },
            "collaborative_events": []
        }

        self._save_game_state(group_id)

        await self._build_rule_network(group_id)

        rules_title = step3_data.get("rules_title", "规则")
        rules = step3_data.get("rules", [])
        win_condition = step3_data.get("win_condition", "")

        try:
            rules_image_path = self._generate_rules_image(rules_title, rules, win_condition, game_mode)
            game_states[group_id]["rules_image_path"] = rules_image_path
            with open(rules_image_path, 'rb') as f:
                image_bytes = f.read()
            image_base64 = base64.b64encode(image_bytes).decode('ascii')
            
            # 确保图像发送完成后再继续
            image_sent = await self.send_image(image_base64)
            if not image_sent:
                raise Exception("规则长图发送失败")
            
            # 增加延迟，确保图像完全显示后再发送后续消息
            await asyncio.sleep(1.5)
        except Exception as e:
            print(f"[规则怪谈] 生成规则长图失败: {str(e)}")
            step3_text = f"**{rules_title}**：\n"
            for i, rule in enumerate(rules, 1):
                step3_text += f"{i}. {rule}\n"
            goal_prefix = "你的目标是" if game_mode == "单人" else "你们的目标是"
            step3_text += f"\n**{goal_prefix}**：{win_condition}"
            await self.send_text(step3_text)
            await asyncio.sleep(0.5)

        if game_mode == "单人":
            user_info = self._get_user_info()
            if user_info:
                user_id = user_info.user_id
                user_name = getattr(user_info, 'user_name', f"玩家{user_id}")
                game_states[group_id]["players"][user_id] = {
                    "name": user_name,
                    "reasoning_history": [],
                    "action_history": [],
                    "is_alive": True,
                    "physical_status": {
                        "health": 100,
                        "injury": "无",
                        "fatigue": "无"
                    },
                    "mental_status": {
                        "sanity": 100,
                        "state": "正常",
                        "emotion": "平静"
                    }
                }
                self._save_game_state(group_id)
                player_text = f"**玩家**：{user_name}\n"
            else:
                player_text = f"**玩家**：0/1\n"

            player_text += f"**提示次数**：0/3\n\n"
            player_text += f"- 使用 `/rg 提示 <规则/线索>` 获取提示\n"
            player_text += f"- 使用 `/rg 推理 <推理内容>` 记录推理\n"
            player_text += f"- 使用 `/rg 行动 <行动描述>` 描述行动\n"
            player_text += f"- 使用 `/rg 状态` 查看游戏状态\n"
            player_text += f"- 使用 `/rg 结束` 结束游戏"

            await self.send_text(player_text)
        else:
            try:
                multiplayer_start_image_path = self._generate_multiplayer_start_image(max_players=5)
                game_states[group_id]["multiplayer_start_image_path"] = multiplayer_start_image_path
                with open(multiplayer_start_image_path, 'rb') as f:
                    image_bytes = f.read()
                image_base64 = base64.b64encode(image_bytes).decode('ascii')
                image_sent = await self.send_image(image_base64)
                if not image_sent:
                    print(f"[规则怪谈] 多人模式开始图片发送失败")
                await asyncio.sleep(0.5)
            except Exception as e:
                print(f"[规则怪谈] 生成多人模式提示长图失败: {str(e)}")
                player_text = f"**玩家**：0/5\n"
                player_text += f"**提示次数**：0/3\n\n"
                player_text += f"- 使用 `/rg 加入` 加入游戏\n"
                player_text += f"- 使用 `/rg 提示 <规则/线索>` 获取提示\n"
                player_text += f"- 使用 `/rg 推理 <推理内容>` 记录推理\n"
                player_text += f"- 使用 `/rg 行动 <行动描述>` 描述行动\n"
                player_text += f"- 使用 `/rg 状态` 查看游戏状态\n"
                player_text += f"- 使用 `/rg 结束` 结束游戏"
                await self.send_text(player_text)
                await asyncio.sleep(0.5)

        return True, "已开始游戏", True

    async def _restore_game(self, group_id: str) -> Tuple[bool, Optional[str], bool]:
        """恢复存档游戏"""
        saved_state = self._load_game_state(group_id)
        if not saved_state:
            await self.send_text("没有找到存档。请先使用 `/rg 开始` 开始游戏。")
            return False, "无存档", True

        if not saved_state.get("game_active", False):
            await self.send_text("存档中的游戏已结束，无法恢复。请使用 `/rg 开始` 开始新游戏。")
            return False, "游戏已结束", True

        game_states[group_id] = saved_state

        game_mode = saved_state.get("game_mode", "单人")
        reply_text = (
            f"**规则怪谈** ({game_mode}模式) - 已恢复存档\n\n"
            f"**场景**：{saved_state.get('scene', '')}\n\n"
            f"**剧情导入**：\n{saved_state.get('background', '')}\n\n"
            f"**你的身份**：\n{saved_state.get('player_identity', '')}\n\n"
            f"**规则**：\n"
        )

        for i, rule in enumerate(saved_state.get("rules", []), 1):
            reply_text += f"{i}. {rule}\n"

        reply_text += f"\n**通关条件**：{saved_state.get('win_condition', '')}\n\n"

        players = saved_state.get("players", {})
        max_players = saved_state.get("max_players", 5)
        reply_text += f"**玩家**：{len(players)}/{max_players}\n"

        for pid, p_data in players.items():
            status = "存活" if p_data["is_alive"] else "死亡"
            reply_text += f"- {p_data['name']} ({status})\n"

        reply_text += f"\n**提示次数**：{saved_state.get('hints_used', 0)}/{saved_state.get('max_hints', 3)}\n\n"

        if game_mode == "单人":
            reply_text += f"- 使用 `/rg 提示 <规则/线索>` 获取提示\n"
            reply_text += f"- 使用 `/rg 推理 <推理内容>` 记录推理\n"
            reply_text += f"- 使用 `/rg 行动 <行动描述>` 描述行动\n"
            reply_text += f"- 使用 `/rg 状态` 查看游戏状态\n"
            reply_text += f"- 使用 `/rg 结束` 结束游戏"
        else:
            reply_text += f"- 使用 `/rg 加入` 加入游戏\n"
            reply_text += f"- 使用 `/rg 提示 <规则/线索>` 获取提示\n"
            reply_text += f"- 使用 `/rg 推理 <推理内容>` 记录推理\n"
            reply_text += f"- 使用 `/rg 行动 <行动描述>` 描述行动\n"
            reply_text += f"- 使用 `/rg 状态` 查看游戏状态\n"
            reply_text += f"- 使用 `/rg 结束` 结束游戏"

        await self.send_text(reply_text)
        return True, "已恢复存档", True

    async def _check_clear_condition(self, group_id: str, api_url: str, api_key: str, model: str, temperature: float) -> None:
        """检查玩家是否达成通关条件"""
        game_state = game_states.get(group_id, {})
        
        if game_state.get("has_cleared", False):
            return
        
        players = game_state.get("players", {})
        
        if not players:
            return
        
        players_info = []
        all_reasoning = []
        all_actions = []
        alive_players = []
        
        for pid, p_data in players.items():
            players_info.append({
                "name": p_data["name"],
                "is_alive": p_data["is_alive"],
                "reasoning_count": len(p_data["reasoning_history"]),
                "action_count": len(p_data["action_history"])
            })
            all_reasoning.extend(p_data["reasoning_history"])
            all_actions.extend(p_data["action_history"])
            if p_data["is_alive"]:
                alive_players.append(p_data["name"])
        
        prompt = f"""
你是一个规则怪谈裁判。请根据所有玩家的推理和行动，判断玩家是否达成通关条件。

场景：{game_state.get('scene', '')}
规则：{json.dumps(game_state.get('rules', []), ensure_ascii=False)}
隐藏真相：{game_state.get('hidden_truth', '')}
通关条件：{game_state.get('win_condition', '')}
死亡触发条件：{json.dumps(game_state.get('death_triggers', []), ensure_ascii=False)}

所有玩家信息：{json.dumps(players_info, ensure_ascii=False)}
所有玩家推理记录：{json.dumps(all_reasoning, ensure_ascii=False)}
所有玩家行动记录：{json.dumps(all_actions, ensure_ascii=False)}
存活玩家：{json.dumps(alive_players, ensure_ascii=False)}

请判断玩家是否达成通关条件。
请返回JSON格式：
{{
  "cleared": "是/否",
  "reason": "判定的详细理由",
  "condition_met": "玩家是否达成了通关条件（是/否）"
}}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
        """

        llm_response = await self._call_llm_api(prompt, api_url, api_key, model, temperature)
        if not llm_response:
            return
        
        try:
            result = json.loads(llm_response)
        except json.JSONDecodeError as e:
            print(f"[规则怪谈] JSON解析失败: {e}")
            print(f"[规则怪谈] 尝试提取JSON部分...")
            
            json_match = re.search(r'\{[\s\S]*\}', llm_response)
            if json_match:
                try:
                    result = json.loads(json_match.group())
                    print(f"[规则怪谈] 成功提取JSON")
                except json.JSONDecodeError as e2:
                    print(f"[规则怪谈] 提取JSON后仍然解析失败: {e2}")
                    return
            else:
                return
        
        if result.get("cleared") == "是":
            game_state["has_cleared"] = True
            game_state["clear_time"] = datetime.now().isoformat()
            self._save_game_state(group_id)
            
            reply_text = (
                f"**恭喜！你已达成通关条件！**\n\n"
                f"{result.get('reason', '')}\n\n"
                f"- 使用 `/rg 继续` 继续探索完美结局\n"
                f"- 使用 `/rg 结束` 结束游戏并查看结局"
            )
            await self.send_text(reply_text)

    async def _continue_to_perfect(self, group_id: str, api_url: str, api_key: str, model: str, temperature: float) -> Tuple[bool, Optional[str], bool]:
        """继续探索完美结局"""
        game_state = game_states.get(group_id, {})
        
        players = game_state.get("players", {})
        
        if not players:
            await self.send_text("没有玩家参与游戏，无法继续探索。")
            return False, "无玩家", True
        
        players_info = []
        all_reasoning = []
        all_actions = []
        alive_players = []
        
        for pid, p_data in players.items():
            players_info.append({
                "name": p_data["name"],
                "is_alive": p_data["is_alive"],
                "reasoning_count": len(p_data["reasoning_history"]),
                "action_count": len(p_data["action_history"])
            })
            all_reasoning.extend(p_data["reasoning_history"])
            all_actions.extend(p_data["action_history"])
            if p_data["is_alive"]:
                alive_players.append(p_data["name"])
        
        prompt = f"""
你是一个规则怪谈裁判。请根据所有玩家的推理和行动，判断玩家是否达成完美结局。

场景：{game_state.get('scene', '')}
规则：{json.dumps(game_state.get('rules', []), ensure_ascii=False)}
隐藏真相：{game_state.get('hidden_truth', '')}
通关条件：{game_state.get('win_condition', '')}
解除条件：{game_state.get('resolve_condition', '')}
死亡触发条件：{json.dumps(game_state.get('death_triggers', []), ensure_ascii=False)}

所有玩家信息：{json.dumps(players_info, ensure_ascii=False)}
所有玩家推理记录：{json.dumps(all_reasoning, ensure_ascii=False)}
所有玩家行动记录：{json.dumps(all_actions, ensure_ascii=False)}
存活玩家：{json.dumps(alive_players, ensure_ascii=False)}

完美结局要求：玩家需要同时满足以下三个条件：
1. 推理出规则怪谈的原貌（接近隐藏真相）
2. 达成通关要求
3. 解除规则怪谈（解决规则怪谈的根源，满足解除条件）

请判断玩家是否达成完美结局。
请返回JSON格式：
{{
  "perfect": "是/否",
  "reason": "判定的详细理由",
  "truth_revealed": "玩家是否推理出了规则怪谈的原貌（是/否）",
  "win_condition_met": "玩家是否达成了通关条件（是/否）",
  "resolve_condition_met": "玩家是否解除了规则怪谈（是/否）"
}}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
        """

        llm_response = await self._call_llm_api(prompt, api_url, api_key, model, temperature)
        if not llm_response:
            await self.send_text("调用LLM API失败，请稍后再试。")
            return False, "LLM API调用失败", True

        try:
            result = json.loads(llm_response)
        except json.JSONDecodeError as e:
            print(f"[规则怪谈] JSON解析失败: {e}")
            print(f"[规则怪谈] 尝试提取JSON部分...")
            
            json_match = re.search(r'\{[\s\S]*\}', llm_response)
            if json_match:
                try:
                    result = json.loads(json_match.group())
                    print(f"[规则怪谈] 成功提取JSON")
                except json.JSONDecodeError as e2:
                    print(f"[规则怪谈] 提取JSON后仍然解析失败: {e2}")
                    await self.send_text("判定完美结局失败，返回格式不正确。")
                    return False, "JSON解析失败", True
            else:
                await self.send_text("判定完美结局失败，返回格式不正确。")
                return False, "JSON解析失败", True

        if not isinstance(result, dict):
            print(f"[规则怪谈] result不是字典类型: {type(result)}, 内容: {result}")
            await self.send_text("判定完美结局失败，返回格式不正确。")
            return False, "JSON解析失败", True

        game_state["game_active"] = False
        self._save_game_state(group_id)
        
        if result.get("perfect") == "是":
            try:
                ending_image_path = self._generate_ending_image(
                    ending="完美",
                    truth_revealed=result.get('truth_revealed', '是'),
                    win_condition_met=result.get('win_condition_met', '是'),
                    resolve_condition_met=result.get('resolve_condition_met', '是'),
                    survivors=alive_players,
                    hidden_truth=game_state.get('hidden_truth', ''),
                    is_single_player=(game_state.get("game_mode") == "单人"),
                    is_forced_end=False,
                    reason=""
                )
                
                with open(ending_image_path, 'rb') as img_file:
                    image_base64 = base64.b64encode(img_file.read()).decode('utf-8')
                
                image_sent = await self.send_image(image_base64)
                if not image_sent:
                    print(f"[规则怪谈] 完美结局图片发送失败")
                else:
                    await asyncio.sleep(1.0)
                
                game_state["ending_image_path"] = ending_image_path
            except Exception as e:
                print(f"[规则怪谈] 生成完美结局长图失败: {str(e)}")
                reply_text = (
                    f"**完美结局！**\n\n"
                    f"{result.get('reason', '')}\n\n"
                    f"恭喜你！你已达成完美结局！\n\n"
                    f"- 推理出规则怪谈的原貌\n"
                    f"- 达成通关要求\n"
                    f"- 解除规则怪谈（解决根源）\n\n"
                    f"**隐藏真相**：{game_state.get('hidden_truth', '')}\n\n"
                    f"感谢游玩！"
                )
                await self.send_text(reply_text)
            
            ending_image_path = game_state.get("ending_image_path")
            if ending_image_path and os.path.exists(ending_image_path):
                try:
                    os.remove(ending_image_path)
                    print(f"[规则怪谈] 已删除完美结局长图：{ending_image_path}")
                except Exception as e:
                    print(f"[规则怪谈] 删除完美结局长图失败: {str(e)}")
            
            action_image_path = game_state.get("action_image_path")
            if action_image_path and os.path.exists(action_image_path):
                try:
                    os.remove(action_image_path)
                    print(f"[规则怪谈] 已删除行动结果长图：{action_image_path}")
                except Exception as e:
                    print(f"[规则怪谈] 删除行动结果长图失败: {str(e)}")
            
            scene_image_path = game_state.get("scene_image_path")
            if scene_image_path and os.path.exists(scene_image_path):
                try:
                    os.remove(scene_image_path)
                    print(f"[规则怪谈] 已删除场景图片：{scene_image_path}")
                except Exception as e:
                    print(f"[规则怪谈] 删除场景图片失败: {str(e)}")
            
            rules_image_path = game_state.get("rules_image_path")
            if rules_image_path and os.path.exists(rules_image_path):
                try:
                    os.remove(rules_image_path)
                    print(f"[规则怪谈] 已删除规则长图：{rules_image_path}")
                except Exception as e:
                    print(f"[规则怪谈] 删除规则长图失败: {str(e)}")
            
            scene_structure_image_path = game_state.get("scene_structure_image_path")
            if scene_structure_image_path and os.path.exists(scene_structure_image_path):
                try:
                    os.remove(scene_structure_image_path)
                    print(f"[规则怪谈] 已删除场景结构长图：{scene_structure_image_path}")
                except Exception as e:
                    print(f"[规则怪谈] 删除场景结构长图失败: {str(e)}")
            
            plot_image_path = game_state.get("plot_image_path")
            if plot_image_path and os.path.exists(plot_image_path):
                try:
                    os.remove(plot_image_path)
                    print(f"[规则怪谈] 已删除剧情导入长图：{plot_image_path}")
                except Exception as e:
                    print(f"[规则怪谈] 删除剧情导入长图失败: {str(e)}")
            
            multiplayer_start_image_path = game_state.get("multiplayer_start_image_path")
            if multiplayer_start_image_path and os.path.exists(multiplayer_start_image_path):
                try:
                    os.remove(multiplayer_start_image_path)
                    print(f"[规则怪谈] 已删除多人模式提示长图：{multiplayer_start_image_path}")
                except Exception as e:
                    print(f"[规则怪谈] 删除多人模式提示长图失败: {str(e)}")
            
            self._delete_save_file(group_id)
        else:
            reply_text = (
                f"**继续探索中...**\n\n"
                f"{result.get('reason', '')}\n\n"
                f"完美结局需要同时满足三个条件：\n"
                f"- 推理出规则怪谈的原貌\n"
                f"- 达成通关要求\n"
                f"- 解除规则怪谈（解决根源）\n\n"
                f"当前状态：\n"
                f"是 推理出规则怪谈的原貌\n"
                f"是 达成通关要求\n"
                f"是 解除规则怪谈（解决根源）\n\n"
                f"- 继续使用 `/rg 推理` 和 `/rg 行动` 探索\n"
                f"- 使用 `/rg 继续` 再次检查是否达成完美结局\n"
                f"- 使用 `/rg 结束` 结束游戏并查看结局"
            )
            game_state["game_active"] = True
            self._save_game_state(group_id)
        
        await self.send_text(reply_text)
        return True, "已检查完美结局", True

    def _generate_cross_section_view(self, scene_data, output_path=None):
        """生成立体剖面图（使用matplotlib 3D）
        
        Args:
            scene_data: 场景结构数据，格式为：
                {
                    "building_type": "建筑类型",
                    "overall_layout": "建筑总体布局描述",
                    "floors": [
                        {
                            "floor": "楼层名称",
                            "areas": ["区域1", "区域2", "区域3"]
                        }
                    ],
                    "connections": ["通道1", "通道2", "通道3"],
                    "special_areas": ["特殊区域1", "特殊区域2"]
                }
            output_path: 输出图片路径，如果为None则自动生成
        
        Returns:
            生成的图片路径
        """
        
        building_type = scene_data.get("building_type", "建筑")
        overall_layout = scene_data.get("overall_layout", "")
        floors = scene_data.get("floors", [])
        connections = scene_data.get("connections", [])
        
        if not floors:
            print("[规则怪谈] 没有楼层数据，无法生成3D剖面图")
            return None
        
        import matplotlib.pyplot as plt
        import numpy as np
        
        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial Unicode MS']
        plt.rcParams['axes.unicode_minus'] = False
        
        # 创建3D图形
        fig = plt.figure(figsize=(16, 12), facecolor='#1a1a2e')
        ax = fig.add_subplot(111, projection='3d')
        
        # 设置背景颜色
        ax.set_facecolor('#1a1a2e')
        fig.patch.set_facecolor('#1a1a2e')
        
        # 建筑参数
        building_width = 10.0
        building_depth = 8.0
        floor_height = 2.0
        num_floors = len(floors)
        
        # 颜色定义
        colors = {
            'floor': '#2d4a6f',
            'floor_edge': '#4a6fa5',
            'room_normal': '#3a5f95',
            'room_special': '#f5a623',
            'room_danger': '#e74c3c',
            'room_target': '#2ecc71',
            'staircase': '#8b7355',
            'elevator': '#95a5a6',
            'emergency_stair': '#c0392b',
            'ventilation': '#7f8c8d',
            'corridor': '#5d6d7e',
            'passage': '#6c757d'
        }
        
        # 绘制每一层
        for i, floor in enumerate(floors):
            z_bottom = i * floor_height
            z_top = z_bottom + floor_height
            
            # 绘制楼层底板和顶板
            x = [0, building_width, building_width, 0]
            y = [0, 0, building_depth, building_depth]
            
            # 底板
            ax.plot_surface(
                np.array([[0, building_width], [0, building_width]]),
                np.array([[0, 0], [building_depth, building_depth]]),
                np.array([[z_bottom, z_bottom], [z_bottom, z_bottom]]),
                alpha=0.1, color=colors['floor']
            )
            
            # 顶板
            ax.plot_surface(
                np.array([[0, building_width], [0, building_width]]),
                np.array([[0, 0], [building_depth, building_depth]]),
                np.array([[z_top, z_top], [z_top, z_top]]),
                alpha=0.1, color=colors['floor']
            )
            
            # 绘制房间区域
            areas = floor.get("areas", [])
            num_areas = len(areas)
            if num_areas > 0:
                room_width = building_width / num_areas
                
                for j, area in enumerate(areas):
                    room_x_start = j * room_width
                    room_x_end = room_x_start + room_width
                    
                    # 判断房间类型
                    room_color = colors['room_normal']
                    if any(keyword in area for keyword in ['404', '目标', '终点', '出口']):
                        room_color = colors['room_target']
                    elif any(keyword in area for keyword in ['锅炉', '停尸', '卫生间', '封拱门', '地下室', '地牢', '刑讯', '手术室']):
                        room_color = colors['room_danger']
                    elif any(keyword in area for keyword in ['留声机', '钟楼', '管理员', '镜子', '图书馆', '档案', '实验室']):
                        room_color = colors['room_special']
                    
                    # 绘制房间（透明立方体）
                    xx = np.array([[room_x_start, room_x_end], [room_x_start, room_x_end]])
                    yy = np.array([[0, 0], [building_depth, building_depth]])
                    zz = np.array([[z_bottom, z_bottom], [z_top, z_top]])
                    
                    ax.plot_surface(xx, yy, zz, alpha=0.1, color=room_color)
                    
                    # 绘制房间边框
                    ax.plot([room_x_start, room_x_start, room_x_end, room_x_end, room_x_start],
                           [0, building_depth, building_depth, 0, 0],
                           [z_bottom, z_bottom, z_bottom, z_bottom, z_bottom],
                           color=colors['floor_edge'], linewidth=1)
                    ax.plot([room_x_start, room_x_start, room_x_end, room_x_end, room_x_start],
                           [0, building_depth, building_depth, 0, 0],
                           [z_top, z_top, z_top, z_top, z_top],
                           color=colors['floor_edge'], linewidth=1)
                    
                    # 绘制房间名称（在房间中心上方）
                    room_center_x = room_x_start + room_width / 2
                    ax.text(room_center_x, building_depth / 2, z_top + 0.2,
                           area[:6] + '..' if len(area) > 6 else area,
                           color='white', fontsize=8, ha='center')
            
            # 绘制楼层名称（在左侧）
            floor_name = floor.get("floor", f"第{i+1}层")
            ax.text(-1.5, building_depth / 2, z_bottom + floor_height / 2,
                   floor_name, color='white', fontsize=10, ha='right', va='center')
        
        # 动态生成通道（根据connections字段）
        building_height = num_floors * floor_height
        connection_positions = []
        elevator_count = 0
        
        # 分析connections字段，确定通道类型和位置
        for conn in connections:
            # 主楼梯
            if '主楼梯' in conn or '中央楼梯' in conn or '楼梯' in conn:
                stair_x = building_width * 0.35
                stair_y = building_depth * 0.3
                stair_width = 0.8
                stair_depth = 1.2
                ax.bar3d(stair_x, stair_y, 0, stair_width, stair_depth, building_height,
                        color=colors['staircase'], alpha=0.3, edgecolor='white', linewidth=0.5)
                ax.text(stair_x + stair_width/2, stair_y + stair_depth/2, building_height + 0.3,
                       "主楼梯", color='white', fontsize=9, ha='center')
                connection_positions.append((stair_x, stair_y))
            
            # 电梯
            elif '电梯' in conn:
                elevator_x = building_width * (0.55 + elevator_count * 0.1)
                elevator_y = building_depth * 0.5
                elevator_width = 0.5
                elevator_depth = 0.5
                ax.bar3d(elevator_x, elevator_y, 0, elevator_width, elevator_depth, building_height,
                        color=colors['elevator'], alpha=0.35, edgecolor='white', linewidth=0.5)
                ax.text(elevator_x + elevator_width/2, elevator_y + elevator_depth/2, building_height + 0.3,
                       f"电梯{chr(65+elevator_count)}", color='white', fontsize=9, ha='center')
                connection_positions.append((elevator_x, elevator_y))
                elevator_count += 1
            
            # 紧急楼梯
            elif '紧急' in conn or '安全' in conn:
                emergency_x = building_width * 0.85
                emergency_y = building_depth * 0.7
                emergency_width = 0.6
                emergency_depth = 1.0
                ax.bar3d(emergency_x, emergency_y, 0, emergency_width, emergency_depth, building_height,
                        color=colors['emergency_stair'], alpha=0.3, edgecolor='white', linewidth=0.5)
                ax.text(emergency_x + emergency_width/2, emergency_y + emergency_depth/2, building_height + 0.3,
                       "紧急", color='white', fontsize=9, ha='center')
                connection_positions.append((emergency_x, emergency_y))
            
            # 通风管道
            elif '通风' in conn or '管道' in conn:
                vent_x = building_width * 0.15
                vent_y = building_depth * 0.8
                vent_width = 0.3
                vent_depth = 0.3
                ax.bar3d(vent_x, vent_y, 0, vent_width, vent_depth, building_height,
                        color=colors['ventilation'], alpha=0.25, edgecolor='white', linewidth=0.5)
                ax.text(vent_x + vent_width/2, vent_y + vent_depth/2, building_height + 0.3,
                       "通风", color='white', fontsize=9, ha='center')
                connection_positions.append((vent_x, vent_y))
            
            # 走廊/通道
            elif '走廊' in conn or '通道' in conn or '过道' in conn:
                corridor_x = building_width * 0.5
                corridor_y = building_depth * 0.1
                corridor_width = building_width * 0.8
                corridor_depth = 0.4
                ax.bar3d(corridor_x - corridor_width/2, corridor_y, 0, corridor_width, corridor_depth, building_height,
                        color=colors['corridor'], alpha=0.2, edgecolor='white', linewidth=0.5)
                ax.text(corridor_x, corridor_y + corridor_depth/2, building_height + 0.3,
                       "走廊", color='white', fontsize=9, ha='center')
                connection_positions.append((corridor_x, corridor_y))
        
        # 如果没有找到任何通道，使用默认布局
        if not connection_positions:
            # 主中央楼梯
            stair_x = building_width * 0.35
            stair_y = building_depth * 0.3
            stair_width = 0.8
            stair_depth = 1.2
            ax.bar3d(stair_x, stair_y, 0, stair_width, stair_depth, building_height,
                    color=colors['staircase'], alpha=0.3, edgecolor='white', linewidth=0.5)
            ax.text(stair_x + stair_width/2, stair_y + stair_depth/2, building_height + 0.3,
                   "主楼梯", color='white', fontsize=9, ha='center')
            
            # 电梯A
            elevator_a_x = building_width * 0.55
            elevator_a_y = building_depth * 0.5
            elevator_width = 0.5
            elevator_depth = 0.5
            ax.bar3d(elevator_a_x, elevator_a_y, 0, elevator_width, elevator_depth, building_height,
                    color=colors['elevator'], alpha=0.35, edgecolor='white', linewidth=0.5)
            ax.text(elevator_a_x + elevator_width/2, elevator_a_y + elevator_depth/2, building_height + 0.3,
                   "电梯A", color='white', fontsize=9, ha='center')
        
        # 设置坐标轴范围（调整使建筑整体居中）
        ax.set_xlim(-1, building_width + 1)
        ax.set_ylim(-0.5, building_depth + 1)
        ax.set_zlim(0, building_height + 1)
        
        # 设置坐标轴标签
        ax.set_xlabel('X轴 (宽度)', color='white', fontsize=10)
        ax.set_ylabel('Y轴 (深度)', color='white', fontsize=10)
        ax.set_zlabel('Z轴 (高度)', color='white', fontsize=10)
        
        # 设置刻度标签颜色
        ax.tick_params(axis='x', colors='white')
        ax.tick_params(axis='y', colors='white')
        ax.tick_params(axis='z', colors='white')
        
        # 设置标题
        ax.set_title(f"{building_type} - 3D立体剖面图", color='white', fontsize=16, pad=20)
        
        # 设置视角（调整使建筑整体居中显示）
        ax.view_init(elev=35, azim=-45)
        
        # 添加图例（放大显示）
        legend_elements = [
            plt.Rectangle((0, 0), 1, 1, facecolor=colors['room_normal'], label='普通区域'),
            plt.Rectangle((0, 0), 1, 1, facecolor=colors['room_special'], label='关键区域'),
            plt.Rectangle((0, 0), 1, 1, facecolor=colors['room_danger'], label='危险区域'),
            plt.Rectangle((0, 0), 1, 1, facecolor=colors['room_target'], label='目标房间'),
            plt.Rectangle((0, 0), 1, 1, facecolor=colors['staircase'], label='主楼梯'),
            plt.Rectangle((0, 0), 1, 1, facecolor=colors['elevator'], label='电梯'),
            plt.Rectangle((0, 0), 1, 1, facecolor=colors['emergency_stair'], label='紧急楼梯'),
            plt.Rectangle((0, 0), 1, 1, facecolor=colors['ventilation'], label='通风管道'),
            plt.Rectangle((0, 0), 1, 1, facecolor=colors['corridor'], label='走廊通道')
        ]
        
        ax.legend(handles=legend_elements, loc='upper left', bbox_to_anchor=(-0.35, 1),
                 facecolor='#1a1a2e', edgecolor='white', labelcolor='white', fontsize=12)
        
        # 调整布局
        plt.tight_layout()
        
        # 生成输出路径
        if output_path is None:
            os.makedirs(TEMP_IMAGES_DIR, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(TEMP_IMAGES_DIR, f'scene_structure_3d_{timestamp}.png')
        
        # 保存图片
        plt.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='#1a1a2e')
        plt.close()
        
        print(f"[规则怪谈] 3D立体剖面图已生成：{output_path}")
        
        return output_path

    def _generate_plot_image(self, scene_name, background, player_identity, core_symbols=None, output_path=None):
        """生成剧情导入长图（黑暗背景+鲜红字体）
        
        Args:
            scene_name: 场景名称
            background: 背景故事
            player_identity: 玩家身份
            core_symbols: 核心象征符号列表
            output_path: 输出图片路径，如果为None则自动生成
        
        Returns:
            生成的图片路径
        """
        
        # 尝试加载中文字体
        try:
            font_title = ImageFont.truetype("msyh.ttc", 36)
            font_subtitle = ImageFont.truetype("msyh.ttc", 28)
            font_normal = ImageFont.truetype("msyh.ttc", 20)
            font_small = ImageFont.truetype("msyh.ttc", 16)
        except:
            try:
                font_title = ImageFont.truetype("simhei.ttf", 36)
                font_subtitle = ImageFont.truetype("simhei.ttf", 28)
                font_normal = ImageFont.truetype("simhei.ttf", 20)
                font_small = ImageFont.truetype("simhei.ttf", 16)
            except:
                font_title = ImageFont.load_default()
                font_subtitle = ImageFont.load_default()
                font_normal = ImageFont.load_default()
                font_small = ImageFont.load_default()
        
        # 预估图片高度
        margin = 60
        title_height = 80
        section_height = 50
        line_height = 30
        # 分割线长度
        line_length = 900 - 2 * margin
        # 每行字符数（根据字体大小估算，确保文本宽度与分割线一致）
        char_per_line = 38
        
        # 计算背景故事需要的行数
        bg_lines = []
        for i in range(0, len(background), char_per_line):
            bg_lines.append(background[i:i+char_per_line])
        
        # 计算玩家身份需要的行数
        identity_lines = []
        for i in range(0, len(player_identity), char_per_line):
            identity_lines.append(player_identity[i:i+char_per_line])
        
        # 计算总高度
        total_height = (margin * 2 + title_height + section_height + 
                       len(bg_lines) * line_height + section_height + 
                       len(identity_lines) * line_height + 50)
        
        # 创建图片（黑暗背景）
        width = 900
        img = Image.new('RGB', (width, total_height), color='#0a0a0a')
        draw = ImageDraw.Draw(img)
        
        # 绘制标题（动态居中）
        title_text = "规则怪谈"
        title_bbox = draw.textbbox((0, 0), title_text, font=font_title)
        title_width = title_bbox[2] - title_bbox[0]
        title_x = (width - title_width) // 2
        draw.text((title_x, margin), title_text, fill='#8B0000', font=font_title)
        
        # 绘制场景名称（动态居中）
        scene_bbox = draw.textbbox((0, 0), scene_name, font=font_subtitle)
        scene_width = scene_bbox[2] - scene_bbox[0]
        scene_x = (width - scene_width) // 2
        draw.text((scene_x, margin + 50), scene_name, fill='#DC143C', font=font_subtitle)
        
        # 绘制分隔线
        draw.line([(margin, margin + 100), (width - margin, margin + 100)], fill='#8B0000', width=2)
        
        # 绘制背景故事
        current_y = margin + 130
        draw.text((margin, current_y), "剧情导入", fill='#DC143C', font=font_subtitle)
        current_y += section_height
        for line in bg_lines:
            draw.text((margin, current_y), line, fill='#FF0000', font=font_normal)
            current_y += line_height
        
        # 绘制玩家身份
        current_y += 20
        draw.text((margin, current_y), "你的身份", fill='#DC143C', font=font_subtitle)
        current_y += section_height
        for line in identity_lines:
            draw.text((margin, current_y), line, fill='#FF0000', font=font_normal)
            current_y += line_height
        
        # 生成输出路径
        if output_path is None:
            os.makedirs(TEMP_IMAGES_DIR, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(TEMP_IMAGES_DIR, f'plot_{timestamp}.png')
        
        # 保存图片
        img.save(output_path, 'PNG')
        print(f"[规则怪谈] 剧情导入长图已生成：{output_path}")
        
        return output_path

    def _generate_scene_structure_text_image(self, building_type, overall_layout, floors, connections, special_areas, output_path=None):
        """生成场景结构文字长图（白底黑字）
        
        Args:
            building_type: 建筑类型
            overall_layout: 总体布局
            floors: 楼层列表
            connections: 连接通道列表
            special_areas: 特殊区域列表
            output_path: 输出图片路径，如果为None则自动生成
        
        Returns:
            生成的图片路径
        """
        
        # 尝试加载中文字体
        try:
            font_title = ImageFont.truetype("msyh.ttc", 32)
            font_subtitle = ImageFont.truetype("msyh.ttc", 24)
            font_normal = ImageFont.truetype("msyh.ttc", 18)
        except:
            try:
                font_title = ImageFont.truetype("simhei.ttf", 32)
                font_subtitle = ImageFont.truetype("simhei.ttf", 24)
                font_normal = ImageFont.truetype("simhei.ttf", 18)
            except:
                font_title = ImageFont.load_default()
                font_subtitle = ImageFont.load_default()
                font_normal = ImageFont.load_default()
        
        # 预估图片高度
        margin = 50
        title_height = 70
        section_height = 45
        line_height = 28
        # 分割线长度
        line_length = 900 - 2 * margin
        # 每行字符数（根据字体大小估算，确保文本宽度与分割线一致）
        char_per_line = 45
        
        # 计算总体布局需要的行数
        layout_lines = []
        for i in range(0, len(overall_layout), char_per_line):
            layout_lines.append(overall_layout[i:i+char_per_line])
        
        # 计算楼层布局需要的行数
        floor_lines = []
        for floor in floors:
            floor_name = floor.get('floor', '')
            areas = floor.get('areas', [])
            floor_text = f"  - {floor_name}: {', '.join(areas)}"
            for i in range(0, len(floor_text), char_per_line):
                floor_lines.append(floor_text[i:i+char_per_line])
        
        # 计算连接通道需要的行数
        conn_text = f"连接通道：{', '.join(connections)}"
        conn_lines = []
        for i in range(0, len(conn_text), char_per_line):
            conn_lines.append(conn_text[i:i+char_per_line])
        
        # 计算特殊区域需要的行数
        special_text = f"特殊区域：{', '.join(special_areas)}"
        special_lines = []
        for i in range(0, len(special_text), char_per_line):
            special_lines.append(special_text[i:i+char_per_line])
        
        # 计算总高度
        total_height = (margin * 2 + title_height + section_height + 
                       len(layout_lines) * line_height + section_height + 
                       len(floor_lines) * line_height + section_height + 
                       len(conn_lines) * line_height + section_height + 
                       len(special_lines) * line_height + 100)
        
        # 创建图片（白底黑字）
        width = 900
        img = Image.new('RGB', (width, total_height), color='#FFFFFF')
        draw = ImageDraw.Draw(img)
        
        # 绘制标题（居中）
        title_text = "场景结构"
        title_bbox = draw.textbbox((0, 0), title_text, font=font_title)
        title_width = title_bbox[2] - title_bbox[0]
        title_x = (width - title_width) // 2
        draw.text((title_x, margin), title_text, fill='#000000', font=font_title)
        
        # 绘制分隔线
        draw.line([(margin, margin + 80), (width - margin, margin + 80)], fill='#000000', width=2)
        
        # 绘制建筑类型
        current_y = margin + 100
        draw.text((margin, current_y), f"建筑类型：{building_type}", fill='#000000', font=font_subtitle)
        
        # 绘制总体布局
        current_y += section_height
        draw.text((margin, current_y), "总体布局", fill='#000000', font=font_subtitle)
        current_y += section_height
        for line in layout_lines:
            draw.text((margin, current_y), line, fill='#000000', font=font_normal)
            current_y += line_height
        
        # 绘制楼层布局
        current_y += 20
        draw.text((margin, current_y), "楼层布局", fill='#000000', font=font_subtitle)
        current_y += section_height
        for line in floor_lines:
            draw.text((margin, current_y), line, fill='#000000', font=font_normal)
            current_y += line_height
        
        # 绘制连接通道
        current_y += 20
        draw.text((margin, current_y), "连接通道", fill='#000000', font=font_subtitle)
        current_y += section_height
        for line in conn_lines:
            draw.text((margin, current_y), line, fill='#000000', font=font_normal)
            current_y += line_height
        
        # 绘制特殊区域
        current_y += 20
        draw.text((margin, current_y), "特殊区域", fill='#000000', font=font_subtitle)
        current_y += section_height
        for line in special_lines:
            draw.text((margin, current_y), line, fill='#000000', font=font_normal)
            current_y += line_height
        
        # 生成输出路径
        if output_path is None:
            os.makedirs(TEMP_IMAGES_DIR, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(TEMP_IMAGES_DIR, f'scene_structure_text_{timestamp}.png')
        
        # 保存图片
        img.save(output_path, 'PNG')
        print(f"[规则怪谈] 场景结构文字长图已生成：{output_path}")
        
        return output_path

    def _generate_rules_image(self, rules_title, rules, win_condition, game_mode="单人", output_path=None):
        """生成规则长图（黑暗背景+鲜红字体）
        
        Args:
            rules_title: 规则标题
            rules: 规则列表
            win_condition: 通关条件
            game_mode: 游戏模式（单人/多人）
            output_path: 输出图片路径，如果为None则自动生成
        
        Returns:
            生成的图片路径
        """
        
        # 尝试加载中文字体
        try:
            font_title = ImageFont.truetype("msyh.ttc", 36)
            font_subtitle = ImageFont.truetype("msyh.ttc", 28)
            font_normal = ImageFont.truetype("msyh.ttc", 20)
        except:
            try:
                font_title = ImageFont.truetype("simhei.ttf", 36)
                font_subtitle = ImageFont.truetype("simhei.ttf", 28)
                font_normal = ImageFont.truetype("simhei.ttf", 20)
            except:
                font_title = ImageFont.load_default()
                font_subtitle = ImageFont.load_default()
                font_normal = ImageFont.load_default()
        
        # 预估图片高度
        margin = 60
        title_height = 80
        section_height = 50
        line_height = 30
        # 分割线长度
        line_length = 900 - 2 * margin
        # 每行字符数（根据字体大小估算，确保文本宽度与分割线一致）
        char_per_line = 38
        
        # 计算规则需要的行数
        rule_lines = []
        for i, rule in enumerate(rules, 1):
            rule_text = f"{i}. {rule}"
            for j in range(0, len(rule_text), char_per_line):
                rule_lines.append(rule_text[j:j+char_per_line])
        
        # 计算通关条件需要的行数
        goal_prefix = "你的目标是" if game_mode == "单人" else "你们的目标是"
        goal_text = f"{goal_prefix}：{win_condition}"
        goal_lines = []
        for i in range(0, len(goal_text), char_per_line):
            goal_lines.append(goal_text[i:i+char_per_line])
        
        # 计算总高度
        total_height = (margin * 2 + title_height + section_height + 
                       len(rule_lines) * line_height + section_height + 
                       len(goal_lines) * line_height + 50)
        
        # 创建图片（黑暗背景）
        width = 900
        img = Image.new('RGB', (width, total_height), color='#0a0a0a')
        draw = ImageDraw.Draw(img)
        
        # 绘制标题（动态居中）
        title_bbox = draw.textbbox((0, 0), rules_title, font=font_title)
        title_width = title_bbox[2] - title_bbox[0]
        title_x = (width - title_width) // 2
        draw.text((title_x, margin), rules_title, fill='#8B0000', font=font_title)
        
        # 绘制分隔线
        draw.line([(margin, margin + 80), (width - margin, margin + 80)], fill='#8B0000', width=2)
        
        # 绘制规则
        current_y = margin + 110
        for line in rule_lines:
            draw.text((margin, current_y), line, fill='#FF0000', font=font_normal)
            current_y += line_height
        
        # 绘制通关条件
        current_y += 30
        draw.line([(margin, current_y), (width - margin, current_y)], fill='#8B0000', width=2)
        current_y += section_height
        for line in goal_lines:
            draw.text((margin, current_y), line, fill='#DC143C', font=font_subtitle)
            current_y += line_height
        
        # 生成输出路径
        if output_path is None:
            os.makedirs(TEMP_IMAGES_DIR, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(TEMP_IMAGES_DIR, f'rules_{timestamp}.png')
        
        # 保存图片
        img.save(output_path, 'PNG')
        print(f"[规则怪谈] 规则长图已生成：{output_path}")
        
        return output_path

    def _generate_multiplayer_start_image(self, max_players=5, output_path=None):
        """生成多人模式游戏开始提示长图（黑暗背景+鲜红字体）
        
        Args:
            max_players: 最大玩家数
            output_path: 输出图片路径，如果为None则自动生成
        
        Returns:
            生成的图片路径
        """
        
        try:
            font_title = ImageFont.truetype("msyh.ttc", 36)
            font_subtitle = ImageFont.truetype("msyh.ttc", 28)
            font_normal = ImageFont.truetype("msyh.ttc", 20)
        except:
            try:
                font_title = ImageFont.truetype("simhei.ttf", 36)
                font_subtitle = ImageFont.truetype("simhei.ttf", 28)
                font_normal = ImageFont.truetype("simhei.ttf", 20)
            except:
                font_title = ImageFont.load_default()
                font_subtitle = ImageFont.load_default()
                font_normal = ImageFont.load_default()
        
        margin = 60
        title_height = 80
        section_height = 50
        line_height = 35
        
        commands = [
            "使用 `/rg 加入` 加入游戏",
            "使用 `/rg 提示 <规则/线索>` 获取提示",
            "使用 `/rg 推理 <推理内容>` 记录推理",
            "使用 `/rg 行动 <行动描述>` 描述行动",
            "使用 `/rg 状态` 查看游戏状态",
            "使用 `/rg 结束` 结束游戏"
        ]
        
        total_height = margin * 2 + title_height + section_height * 2 + len(commands) * line_height + 50
        
        width = 900
        img = Image.new('RGB', (width, total_height), color='#0a0a0a')
        draw = ImageDraw.Draw(img)
        
        # 绘制标题（动态居中）
        title_text = "多人模式"
        title_bbox = draw.textbbox((0, 0), title_text, font=font_title)
        title_width = title_bbox[2] - title_bbox[0]
        title_x = (width - title_width) // 2
        draw.text((title_x, margin), title_text, fill='#8B0000', font=font_title)
        
        draw.line([(margin, margin + 80), (width - margin, margin + 80)], fill='#8B0000', width=2)
        
        current_y = margin + 110
        draw.text((margin, current_y), f"玩家：0/{max_players}", fill='#DC143C', font=font_subtitle)
        current_y += section_height
        draw.text((margin, current_y), "提示次数：0/3", fill='#DC143C', font=font_subtitle)
        
        current_y += 30
        draw.line([(margin, current_y), (width - margin, current_y)], fill='#8B0000', width=2)
        current_y += section_height
        
        for command in commands:
            draw.text((margin, current_y), command, fill='#FF0000', font=font_normal)
            current_y += line_height
        
        if output_path is None:
            os.makedirs(TEMP_IMAGES_DIR, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(TEMP_IMAGES_DIR, f'multiplayer_start_{timestamp}.png')
        
        img.save(output_path, 'PNG')
        print(f"[规则怪谈] 多人模式开始提示长图已生成：{output_path}")
        
        return output_path

    def _generate_ending_image(self, ending, truth_revealed, win_condition_met, resolve_condition_met, survivors, hidden_truth, is_single_player=False, is_forced_end=False, reason="", output_path=None):
        """生成结局长图（黑暗背景+鲜红字体）
        
        Args:
            ending: 结局类型（完美/成功/通关/失败）
            truth_revealed: 推理真相
            win_condition_met: 是否达成通关
            resolve_condition_met: 是否解除怪谈
            survivors: 存活玩家列表
            hidden_truth: 隐藏真相
            output_path: 输出图片路径，如果为None则自动生成
        
        Returns:
            生成的图片路径
        """
        
        try:
            font_title = ImageFont.truetype("msyh.ttc", 40)
            font_subtitle = ImageFont.truetype("msyh.ttc", 28)
            font_normal = ImageFont.truetype("msyh.ttc", 20)
        except:
            try:
                font_title = ImageFont.truetype("simhei.ttf", 40)
                font_subtitle = ImageFont.truetype("simhei.ttf", 28)
                font_normal = ImageFont.truetype("simhei.ttf", 20)
            except:
                font_title = ImageFont.load_default()
                font_subtitle = ImageFont.load_default()
                font_normal = ImageFont.load_default()
        
        margin = 60
        title_height = 100
        section_height = 50
        line_height = 30
        # 分割线长度
        line_length = 900 - 2 * margin
        # 每行字符数（根据字体大小估算，确保文本宽度与分割线一致）
        char_per_line = 38
        
        if ending == "失败":
            if is_forced_end:
                content_lines = [
                    "你在探索中触犯了规则，不幸身亡。",
                    "你未能达成通关条件，游戏结束。"
                ]
            else:
                if is_single_player:
                    if not survivors:
                        content_lines = [
                            "你已死亡，游戏结束。"
                        ]
                    else:
                        content_lines = [
                            "你未能达成通关条件，游戏结束。"
                        ]
                else:
                    if not survivors:
                        content_lines = [
                            "所有玩家已死亡，游戏结束。"
                        ]
                    else:
                        content_lines = [
                            "团队未能达成通关条件，游戏结束。"
                        ]
        else:
            content_lines = [
                f"推理真相：{truth_revealed}",
                f"达成通关：{win_condition_met}",
                f"解除怪谈：{resolve_condition_met}"
            ]
            
            if survivors:
                content_lines.append("存活玩家：")
                for survivor in survivors:
                    content_lines.append(f"  - {survivor}")
        
        content_lines.append(f"隐藏真相：{hidden_truth}")
        
        text_lines = []
        for line in content_lines:
            for i in range(0, len(line), char_per_line):
                text_lines.append(line[i:i+char_per_line])
        
        total_height = margin * 2 + title_height + len(text_lines) * line_height + 50
        
        width = 900
        img = Image.new('RGB', (width, total_height), color='#0a0a0a')
        draw = ImageDraw.Draw(img)
        
        title_text = f"结局：{ending}"
        title_bbox = draw.textbbox((0, 0), title_text, font=font_title)
        title_width = title_bbox[2] - title_bbox[0]
        title_x = (width - title_width) // 2
        draw.text((title_x, margin), title_text, fill='#8B0000', font=font_title)
        
        draw.line([(margin, margin + title_height), (width - margin, margin + title_height)], fill='#8B0000', width=2)
        
        current_y = margin + title_height + 30
        for line in text_lines:
            if line.startswith("隐藏真相："):
                draw.text((margin, current_y), "隐藏真相", fill='#DC143C', font=font_subtitle)
                current_y += section_height
                draw.text((margin, current_y), line.replace("隐藏真相：", ""), fill='#FF0000', font=font_normal)
            elif line.startswith("游戏结束。"):
                draw.text((margin, current_y), line, fill='#DC143C', font=font_normal)
            else:
                draw.text((margin, current_y), line, fill='#FF0000', font=font_normal)
            current_y += line_height
        
        if output_path is None:
            os.makedirs(TEMP_IMAGES_DIR, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(TEMP_IMAGES_DIR, f'ending_{timestamp}.png')
        
        img.save(output_path, 'PNG')
        print(f"[规则怪谈] 结局长图已生成：{output_path}")
        
        return output_path

    def _generate_action_result_image(self, user_name, action, is_dead, scene_description, action_feedback, 
                                       health, injury, fatigue, sanity, state, emotion, 
                                       fear_level, anxiety_level, stress_level, 
                                       found_items, new_location, random_event, output_path=None):
        """生成行动结果长图（黑暗背景+鲜红字体）
        
        Args:
            user_name: 玩家名称
            action: 行动描述
            is_dead: 是否死亡
            scene_description: 场景描述
            action_feedback: 行动反馈
            health: 体力值
            injury: 受伤状态
            fatigue: 疲劳状态
            sanity: 理智值
            state: 精神状态
            emotion: 情绪
            fear_level: 恐惧等级
            anxiety_level: 焦虑等级
            stress_level: 压力等级
            found_items: 获得的物品列表
            new_location: 新位置
            random_event: 环境事件
            output_path: 输出图片路径，如果为None则自动生成
        
        Returns:
            生成的图片路径
        """
        
        try:
            font_title = ImageFont.truetype("msyh.ttc", 36)
            font_subtitle = ImageFont.truetype("msyh.ttc", 24)
            font_normal = ImageFont.truetype("msyh.ttc", 18)
        except:
            try:
                font_title = ImageFont.truetype("simhei.ttf", 36)
                font_subtitle = ImageFont.truetype("simhei.ttf", 24)
                font_normal = ImageFont.truetype("simhei.ttf", 18)
            except:
                font_title = ImageFont.load_default()
                font_subtitle = ImageFont.load_default()
                font_normal = ImageFont.load_default()
        
        margin = 50
        title_height = 80
        section_height = 40
        line_height = 26
        # 分割线长度
        line_length = 900 - 2 * margin
        # 每行字符数（根据字体大小估算，确保文本宽度与分割线一致）
        char_per_line = 45
        
        content_lines = []
        
        if is_dead:
            content_lines.append(f"行动结果 - {user_name}")
            content_lines.append(f"行动：{action}")
            content_lines.append("你已死亡！")
        else:
            content_lines.append(f"行动结果 - {user_name}")
            content_lines.append(f"行动：{action}")
        
        content_lines.append("")
        content_lines.append("场景描述：")
        
        scene_lines = []
        for i in range(0, len(scene_description), char_per_line):
            scene_lines.append(scene_description[i:i+char_per_line])
        content_lines.extend(scene_lines)
        
        if not is_dead:
            content_lines.append("")
            content_lines.append("身体状况：")
            content_lines.append(f"  体力值：{health}/100")
            content_lines.append(f"  受伤：{injury}")
            content_lines.append(f"  疲劳：{fatigue}")
            
            content_lines.append("")
            content_lines.append("精神状况：")
            content_lines.append(f"  理智值：{sanity}/100")
            content_lines.append(f"  状态：{state}")
            content_lines.append(f"  情绪：{emotion}")
            
            content_lines.append("")
            content_lines.append("心理压力：")
            content_lines.append(f"  恐惧等级：{fear_level}/100")
            content_lines.append(f"  焦虑等级：{anxiety_level}/100")
            content_lines.append(f"  压力等级：{stress_level}/100")
        
        if action_feedback:
            content_lines.append("")
            content_lines.append("行动反馈：")
            feedback_lines = []
            for i in range(0, len(action_feedback), char_per_line):
                feedback_lines.append(action_feedback[i:i+char_per_line])
            content_lines.extend(feedback_lines)
        
        if not is_dead:
            if found_items:
                content_lines.append("")
                content_lines.append("获得物品：")
                items_text = ', '.join(found_items)
                items_lines = []
                for i in range(0, len(items_text), char_per_line):
                    items_lines.append(items_text[i:i+char_per_line])
                content_lines.extend(items_lines)
            
            content_lines.append("")
            content_lines.append("当前位置：")
            location_lines = []
            for i in range(0, len(new_location), char_per_line):
                location_lines.append(new_location[i:i+char_per_line])
            content_lines.extend(location_lines)
            
            if random_event:
                content_lines.append("")
                content_lines.append("环境事件：")
                event_lines = []
                for i in range(0, len(random_event), char_per_line):
                    event_lines.append(random_event[i:i+char_per_line])
                content_lines.extend(event_lines)
            
            content_lines.append("")
            content_lines.append("你存活了下来！继续探索吧。")
        else:
            content_lines.append("")
            content_lines.append("你变成了怪谈的一部分。")
        
        total_height = margin * 2 + title_height + len(content_lines) * line_height + 50
        
        width = 900
        img = Image.new('RGB', (width, total_height), color='#0a0a0a')
        draw = ImageDraw.Draw(img)
        
        current_y = margin
        for line in content_lines:
            if line.startswith("行动："):
                draw.text((margin, current_y), line, fill='#DC143C', font=font_subtitle)
            elif line.startswith("你已死亡！"):
                draw.text((margin, current_y), line, fill='#FF0000', font=font_subtitle)
            elif line.startswith("场景描述：") or line.startswith("身体状况：") or line.startswith("精神状况：") or line.startswith("心理压力："):
                draw.text((margin, current_y), line, fill='#DC143C', font=font_subtitle)
            elif line.startswith("行动反馈：") or line.startswith("获得物品：") or line.startswith("当前位置：") or line.startswith("环境事件："):
                draw.text((margin, current_y), line, fill='#DC143C', font=font_normal)
            elif line.startswith("你存活了下来！"):
                draw.text((margin, current_y), line, fill='#DC143C', font=font_normal)
            else:
                draw.text((margin, current_y), line, fill='#FF0000', font=font_normal)
            current_y += line_height
        
        if output_path is None:
            os.makedirs(TEMP_IMAGES_DIR, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(TEMP_IMAGES_DIR, f'action_{timestamp}.png')
        
        img.save(output_path, 'PNG')
        print(f"[规则怪谈] 行动结果长图已生成：{output_path}")
        
        return output_path
