# src/plugins/rule_horror/plugin.py
import os
import json
import random
import re
import aiohttp
from typing import List, Tuple, Type, Optional
from datetime import datetime
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

game_states = {}

@register_plugin
class RuleHorrorPlugin(BasePlugin):
    """规则怪谈插件 - 生成规则怪谈并进行互动"""

    plugin_name = "rule_horror"
    plugin_description = "生成规则怪谈并进行互动游戏。"
    plugin_version = "1.0.0"
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
        "/rg 恢复 - 恢复存档游戏\n"
        "/rg 加入 - 加入游戏（多人模式，最多5人）\n"
        "/rg 离开 - 离开游戏\n"
        "/rg 状态 - 查看游戏状态\n"
        "/rg 规则 - 查看当前规则\n"
        "/rg 场景 - 查看场景结构\n"
        "/rg 提示 <规则/线索> - 获取提示（剩余3次）\n"
        "/rg 推理 <推理内容> - 记录你的推理\n"
        "/rg 行动 <行动描述> - 描述你的行动\n"
        "/rg 结束 - 结束游戏并判定结局\n"
        "/rg 帮助 - 查看帮助"
    )
    command_examples = [
        "/rg 开始 单人", "/rg 开始 多人", "/rg 强制开始 单人", "/rg 恢复", "/rg 加入", "/rg 离开", "/rg 状态", "/rg 规则", "/rg 场景",
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
            await self.send_text("❌ 无法获取聊天上下文信息。")
            return False, "缺少聊天上下文", True

        stream_id = getattr(chat_stream, 'stream_id', None)
        if stream_id is None:
            await self.send_text("❌ 无法获取聊天流ID。")
            return False, "缺少聊天流ID", True

        enabled = self.get_config("plugin.enabled", True)
        if not enabled:
            await self.send_text("❌ 规则怪谈插件已被禁用。")
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
                await self.send_text("❌ 请指定游戏模式。用法：`/rg 开始 单人` 或 `/rg 开始 多人`")
                return False, "缺少游戏模式", True
            return await self._start_new_game(group_id, api_url, api_key, model, temperature, game_mode)

        elif action == "强制开始":
            game_mode = rest_input.strip() if rest_input else ""
            if game_mode not in ["单人", "多人"]:
                await self.send_text("❌ 请指定游戏模式。用法：`/rg 强制开始 单人` 或 `/rg 强制开始 多人`")
                return False, "缺少游戏模式", True
            return await self._force_start_new_game(group_id, api_url, api_key, model, temperature, game_mode)

        elif action == "恢复":
            return await self._restore_game(group_id)

        elif action == "加入":
            if not game_state.get("game_active", False):
                await self.send_text("❌ 当前没有正在进行的游戏。请先使用 `/rg 开始` 开始游戏。")
                return False, "无游戏", True

            return await self._join_game(group_id)

        elif action == "离开":
            if not game_state.get("game_active", False):
                await self.send_text("❌ 当前没有正在进行的游戏。")
                return False, "无游戏", True

            return await self._leave_game(group_id)

        elif action == "状态":
            if not game_state.get("game_active", False):
                await self.send_text("❌ 当前没有正在进行的游戏。")
                return False, "无游戏", True

            return await self._show_game_status(group_id)

        elif action == "规则":
            if not game_state.get("game_active", False):
                await self.send_text("❌ 当前没有正在进行的游戏。请先使用 `/rg 开始` 开始游戏。")
                return False, "无游戏", True

            return await self._show_rules(group_id)

        elif action == "场景":
            if not game_state.get("game_active", False):
                await self.send_text("❌ 当前没有正在进行的游戏。请先使用 `/rg 开始` 开始游戏。")
                return False, "无游戏", True

            return await self._show_scene(group_id)

        elif action == "提示":
            if not game_state.get("game_active", False):
                await self.send_text("❌ 当前没有正在进行的游戏。请先使用 `/rg 开始` 开始游戏。")
                return False, "无游戏", True

            hint_type = rest_input if rest_input else "规则"
            return await self._provide_hint(group_id, hint_type, api_url, api_key, model, temperature)

        elif action == "推理":
            if not game_state.get("game_active", False):
                await self.send_text("❌ 当前没有正在进行的游戏。请先使用 `/rg 开始` 开始游戏。")
                return False, "无游戏", True

            if not rest_input:
                await self.send_text("❌ 请提供推理内容。用法：`/rg 推理 <推理内容>`")
                return False, "缺少推理内容", True

            return await self._record_reasoning(group_id, rest_input, api_url, api_key, model, temperature)

        elif action == "行动":
            if not game_state.get("game_active", False):
                await self.send_text("❌ 当前没有正在进行的游戏。请先使用 `/rg 开始` 开始游戏。")
                return False, "无游戏", True

            if not rest_input:
                await self.send_text("❌ 请提供行动描述。用法：`/rg 行动 <行动描述>`")
                return False, "缺少行动描述", True

            return await self._record_action(group_id, rest_input, api_url, api_key, model, temperature)

        elif action == "继续":
            if not game_state.get("game_active", False):
                await self.send_text("❌ 当前没有正在进行的游戏。")
                return False, "无游戏", True

            if not game_state.get("has_cleared", False):
                await self.send_text("❌ 你尚未达成通关条件，无法继续探索。")
                return False, "未通关", True

            return await self._continue_to_perfect(group_id, api_url, api_key, model, temperature)

        elif action == "结束":
            if not game_state.get("game_active", False):
                await self.send_text("❌ 当前没有正在进行的游戏。")
                return False, "无游戏", True

            return await self._end_game(group_id, api_url, api_key, model, temperature)

        elif action == "帮助":
            help_text = (
                "🎮 **规则怪谈游戏帮助**\n\n"
                "📌 **命令列表**\n"
                "🔸 `/rg 开始 单人` - 开始单人模式游戏（自动加入）\n"
                "🔸 `/rg 开始 多人` - 开始多人模式游戏（最多5人，需手动加入）\n"
                "🔸 `/rg 强制开始 单人/多人` - 强制开始新游戏（覆盖存档）\n"
                "🔸 `/rg 恢复` - 恢复存档游戏\n"
                "🔸 `/rg 加入` - 加入当前游戏（多人模式）\n"
                "🔸 `/rg 离开` - 离开当前游戏\n"
                "🔸 `/rg 状态` - 查看游戏状态和玩家信息\n"
                "🔸 `/rg 规则` - 查看当前规则和通关条件\n"
                "🔸 `/rg 场景` - 查看场景结构和环境状况\n"
                "🔸 `/rg 提示 <规则/线索>` - 获取提示（规则验证或线索，剩余3次）\n"
                "🔸 `/rg 推理 <推理内容>` - 记录你的推理\n"
                "🔸 `/rg 行动 <行动描述>` - 描述你的行动\n"
                "🔸 `/rg 继续` - 达成通关后继续探索完美结局\n"
                "🔸 `/rg 结束` - 结束游戏并判定结局\n"
                "🔸 `/rg 帮助` - 查看帮助\n\n"
                "💡 **游戏提示**\n"
                "🔹 规则怪谈包含多条规则，你需要推理出规则的真实含义\n"
                "🔹 单人模式：你独自挑战，自动加入游戏\n"
                "🔹 多人模式：最多5人同时参与，每人独立推理和行动\n"
                "🔹 你有3次提示机会，可以选择规则验证或获取线索\n"
                "🔹 通过推理和行动来达成通关条件\n"
                "🔹 当达成通关条件时，系统会自动判定并询问是否继续探索完美结局\n"
                "🔹 死亡的玩家无法继续推理和行动，但可以观看其他玩家\n"
                "🔹 完美结局需要同时满足：推理出规则怪谈的原貌、达成通关要求、解除规则怪谈（解决根源）\n"
                "🔹 结局分为：完美（满足三个条件）、成功（推理出原貌并通关）、通关（仅通关）、失败（死亡或未通关）\n"
                "🔹 游戏会自动保存，中断后可以使用 `/rg 恢复` 继续游戏"
            )
            await self.send_text(help_text)
            return True, "已发送帮助信息", True

        else:
            await self.send_text("❌ 未知命令。请使用 `/rg 帮助` 查看可用命令。")
            return False, "未知命令", True

    async def _start_new_game(self, group_id: str, api_url: str, api_key: str, model: str, temperature: float, game_mode: str) -> Tuple[bool, Optional[str], bool]:
        """开始一个新的规则怪谈游戏"""
        saved_state = self._load_game_state(group_id)
        if saved_state and saved_state.get("game_active", False):
            await self.send_text(
                "⚠️ **发现存档**\n\n"
                "该群组/用户已有未完成的游戏存档。\n"
                "请使用 `/rg 恢复` 恢复存档，或使用 `/rg 强制开始 单人/多人` 强制开始新游戏（会覆盖存档）。"
            )
            return False, "存在存档", True
        
        prompt = """
你是一个专业的规则怪谈生成器。请生成一个恐怖或诡异的规则怪谈。

要求：
1. 生成一个场景（如：深夜的医院、废弃的学校、神秘的公寓等）
2. 详细描述场景的完整结构，包括：
   - 建筑类型和总体布局
   - 楼层数量（如：地上5层，地下2层）
   - 每层的主要房间和区域
   - 通道、楼梯、电梯等连接方式
   - 特殊区域（如：地下室、天台、禁闭室等）
3. 列出5-8条规则，规则应该看似合理但隐藏着诡异之处
4. 设定通关条件（如：在规定时间内找到出口、收集特定物品、存活到天亮等）
5. 设定解除条件（如：找到规则怪谈的根源并消除它、找到某个特定物品并使用、完成某个仪式等）
6. 规则应该有隐藏的逻辑和真相，需要玩家推理
7. 以JSON格式返回，格式如下：
{
  "scene": "场景名称（如：深夜的废弃医院）",
  "scene_structure": "详细的场景结构描述，包括建筑类型、楼层数、每层的房间布局、通道、特殊区域等",
  "rules": ["规则1", "规则2", ...],
  "win_condition": "通关条件",
  "resolve_condition": "解除条件（解决规则怪谈根源的条件）",
  "hidden_truth": "隐藏的真相（不显示给玩家）",
  "death_triggers": ["会导致死亡的行为1", "会导致死亡的行为2", ...]
}

请仅返回JSON，不要包含任何其他文字。
        """

        await self.send_text("正在生成规则怪谈...")

        llm_response = await self._call_llm_api(prompt, api_url, api_key, model, temperature)
        if not llm_response:
            await self.send_text("❌ 调用LLM API失败，请稍后再试。")
            return False, "LLM API调用失败", True

        print(f"[规则怪谈] LLM原始返回: {llm_response}")

        try:
            game_data = json.loads(llm_response)
        except json.JSONDecodeError as e:
            print(f"[规则怪谈] JSON解析失败: {e}")
            print(f"[规则怪谈] 尝试提取JSON部分...")
            
            json_match = re.search(r'\{[\s\S]*\}', llm_response)
            if json_match:
                try:
                    game_data = json.loads(json_match.group())
                    print(f"[规则怪谈] 成功提取JSON")
                except json.JSONDecodeError as e2:
                    print(f"[规则怪谈] 提取JSON后仍然解析失败: {e2}")
                    await self.send_text("❌ 生成规则怪谈失败，返回格式不正确。")
                    return False, "JSON解析失败", True
            else:
                await self.send_text("❌ 生成规则怪谈失败，返回格式不正确。")
                return False, "JSON解析失败", True

        max_players = 5 if game_mode == "多人" else 1

        game_states[group_id] = {
            "scene": game_data.get("scene", ""),
            "scene_structure": game_data.get("scene_structure", ""),
            "rules": game_data.get("rules", []),
            "win_condition": game_data.get("win_condition", ""),
            "resolve_condition": game_data.get("resolve_condition", ""),
            "hidden_truth": game_data.get("hidden_truth", ""),
            "death_triggers": game_data.get("death_triggers", []),
            "hints_used": 0,
            "max_hints": 3,
            "game_active": True,
            "max_players": max_players,
            "game_mode": game_mode,
            "players": {},
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
            "environmental_events": []
        }

        self._save_game_state(group_id)

        reply_text = (
            f"🎭 **规则怪谈** ({game_mode}模式)\n\n"
            f"📍 **场景**：{game_data.get('scene', '')}\n\n"
            f"🏗️ **场景结构**：\n{game_data.get('scene_structure', '')}\n\n"
            f"📜 **规则**：\n"
        )

        for i, rule in enumerate(game_data.get("rules", []), 1):
            reply_text += f"{i}. {rule}\n"

        reply_text += f"\n🎯 **通关条件**：{game_data.get('win_condition', '')}\n\n"

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
                reply_text += f"👤 **玩家**：{user_name}\n"
            else:
                reply_text += f"👤 **玩家**：0/1\n"
        else:
            reply_text += f"👥 **玩家**：0/5\n"

        reply_text += f"💡 **提示次数**：0/3\n\n"

        if game_mode == "单人":
            reply_text += f"🔸 使用 `/rg 提示 <规则/线索>` 获取提示\n"
            reply_text += f"🔸 使用 `/rg 推理 <推理内容>` 记录推理\n"
            reply_text += f"🔸 使用 `/rg 行动 <行动描述>` 描述行动\n"
            reply_text += f"🔸 使用 `/rg 状态` 查看游戏状态\n"
            reply_text += f"🔸 使用 `/rg 结束` 结束游戏"
        else:
            reply_text += f"🔸 使用 `/rg 加入` 加入游戏\n"
            reply_text += f"🔸 使用 `/rg 提示 <规则/线索>` 获取提示\n"
            reply_text += f"🔸 使用 `/rg 推理 <推理内容>` 记录推理\n"
            reply_text += f"🔸 使用 `/rg 行动 <行动描述>` 描述行动\n"
            reply_text += f"🔸 使用 `/rg 状态` 查看游戏状态\n"
            reply_text += f"🔸 使用 `/rg 结束` 结束游戏"

        await self.send_text(reply_text)
        return True, "已开始游戏", True

    async def _join_game(self, group_id: str) -> Tuple[bool, Optional[str], bool]:
        """加入游戏"""
        game_state = game_states.get(group_id, {})
        
        user_info = self._get_user_info()
        if not user_info:
            await self.send_text("❌ 无法获取用户信息。")
            return False, "无法获取用户信息", True
        
        user_id = user_info.user_id
        user_name = getattr(user_info, 'user_name', f"玩家{user_id}")
        
        if user_id in game_state.get("players", {}):
            await self.send_text("❌ 你已经在游戏中了。")
            return False, "已在游戏中", True
        
        players = game_state.get("players", {})
        if len(players) >= game_state.get("max_players", 5):
            await self.send_text(f"❌ 游戏人数已满（最多{game_state.get('max_players', 5)}人）。")
            return False, "游戏人数已满", True
        
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
        
        self._save_game_state(group_id)
        
        reply_text = (
            f"✅ **{user_name}** 已加入游戏！\n\n"
            f"👥 **当前玩家**：{len(players)}/{game_state.get('max_players', 5)}\n"
        )
        
        for pid, p_data in players.items():
            status = "存活" if p_data["is_alive"] else "死亡"
            reply_text += f"🔸 {p_data['name']} ({status})\n"
        
        await self.send_text(reply_text)
        return True, "已加入游戏", True

    async def _leave_game(self, group_id: str) -> Tuple[bool, Optional[str], bool]:
        """离开游戏"""
        game_state = game_states.get(group_id, {})
        
        user_info = self._get_user_info()
        if not user_info:
            await self.send_text("❌ 无法获取用户信息。")
            return False, "无法获取用户信息", True
        
        user_id = user_info.user_id
        user_name = getattr(user_info, 'user_name', f"玩家{user_id}")
        
        players = game_state.get("players", {})
        if user_id not in players:
            await self.send_text("❌ 你不在游戏中。")
            return False, "不在游戏中", True
        
        del players[user_id]
        game_state["players"] = players
        
        self._save_game_state(group_id)
        
        reply_text = (
            f"👋 **{user_name}** 已离开游戏。\n\n"
            f"👥 **当前玩家**：{len(players)}/{game_state.get('max_players', 5)}\n"
        )
        
        for pid, p_data in players.items():
            status = "存活" if p_data["is_alive"] else "死亡"
            reply_text += f"🔸 {p_data['name']} ({status})\n"
        
        await self.send_text(reply_text)
        return True, "已离开游戏", True

    async def _show_game_status(self, group_id: str) -> Tuple[bool, Optional[str], bool]:
        """显示游戏状态"""
        game_state = game_states.get(group_id, {})
        players = game_state.get("players", {})
        
        reply_text = (
            f"📊 **游戏状态**\n\n"
            f"📍 **场景**：{game_state.get('scene', '')}\n\n"
            f"🎯 **通关条件**：{game_state.get('win_condition', '')}\n\n"
            f"👥 **玩家**：{len(players)}/{game_state.get('max_players', 5)}\n"
        )
        
        if players:
            for pid, p_data in players.items():
                status = "存活" if p_data["is_alive"] else "死亡"
                reply_text += f"\n🔸 {p_data['name']} ({status})\n"
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
        
        reply_text += f"\n💡 **提示次数**：{game_state.get('hints_used', 0)}/{game_state.get('max_hints', 3)}"
        
        await self.send_text(reply_text)
        return True, "已显示游戏状态", True

    async def _show_rules(self, group_id: str) -> Tuple[bool, Optional[str], bool]:
        """显示当前规则"""
        game_state = game_states.get(group_id, {})
        
        reply_text = "📜 **规则**\n"
        
        rules = game_state.get('rules', [])
        if rules:
            for i, rule in enumerate(rules, 1):
                reply_text += f"{i}. {rule}\n"
        else:
            reply_text += "暂无规则\n"
        
        reply_text += f"\n🎯 **通关条件**：{game_state.get('win_condition', '')}"
        
        await self.send_text(reply_text)
        return True, "已显示规则", True

    async def _show_scene(self, group_id: str) -> Tuple[bool, Optional[str], bool]:
        """显示场景结构"""
        game_state = game_states.get(group_id, {})
        
        reply_text = (
            f"📍 **场景**：{game_state.get('scene', '')}\n\n"
            f"🏗️ **场景结构**：\n{game_state.get('scene_structure', '')}\n\n"
            f"⏰ **当前时间**：{game_state.get('time_system', {}).get('current_time', '未知')}\n"
            f"🌡️ **环境状况**：\n"
            f"   - 光线：{game_state.get('environment', {}).get('lighting', '未知')}\n"
            f"   - 温度：{game_state.get('environment', {}).get('temperature', '未知')}\n"
            f"   - 声音：{', '.join(game_state.get('environment', {}).get('sounds', ['未知']))}\n"
            f"   - 气味：{', '.join(game_state.get('environment', {}).get('smells', ['未知']))}\n"
            f"   - 氛围：{game_state.get('environment', {}).get('atmosphere', '未知')}\n"
        )
        
        await self.send_text(reply_text)
        return True, "已显示场景", True

    async def _provide_hint(self, group_id: str, hint_type: str, api_url: str, api_key: str, model: str, temperature: float) -> Tuple[bool, Optional[str], bool]:
        """提供提示"""
        game_state = game_states.get(group_id, {})

        if game_state.get("hints_used", 0) >= game_state.get("max_hints", 3):
            await self.send_text("❌ 提示次数已用完。")
            return False, "提示次数用完", True

        if hint_type not in ["规则", "线索"]:
            await self.send_text("❌ 提示类型无效。请选择：规则 或 线索")
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
请仅返回提示内容，不要包含任何其他文字。
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
请仅返回线索内容，不要包含任何其他文字。
            """

        llm_response = await self._call_llm_api(prompt, api_url, api_key, model, temperature)
        if not llm_response:
            await self.send_text("❌ 调用LLM API失败，请稍后再试。")
            return False, "LLM API调用失败", True

        hint_text = llm_response.strip()

        reply_text = (
            f"💡 **提示** ({hint_type})\n\n"
            f"{hint_text}\n\n"
            f"📊 **剩余提示次数**：{remaining_hints}/{game_state['max_hints']}"
        )

        await self.send_text(reply_text)
        return True, "已提供提示", True

    async def _record_reasoning(self, group_id: str, reasoning: str, api_url: str, api_key: str, model: str, temperature: float) -> Tuple[bool, Optional[str], bool]:
        """记录推理"""
        game_state = game_states.get(group_id, {})
        
        user_info = self._get_user_info()
        if not user_info:
            await self.send_text("❌ 无法获取用户信息。")
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
                await self.send_text("❌ 你不在游戏中。请先使用 `/rg 加入` 加入游戏。")
                return False, "不在游戏中", True
        
        player_data = players[user_id]
        if not player_data["is_alive"]:
            await self.send_text("❌ 你已经死亡，无法继续推理。")
            return False, "玩家已死亡", True
        
        player_data["reasoning_history"].append(reasoning)
        game_state["players"] = players
        
        self._save_game_state(group_id)
        
        reply_text = (
            f"🧠 **推理记录** - {user_name}\n\n"
            f"{reasoning}\n\n"
            f"📝 **已记录**。继续推理或使用 `/rg 行动` 描述你的行动。"
        )

        await self.send_text(reply_text)
        
        await self._check_clear_condition(group_id, api_url, api_key, model, temperature)
        
        return True, "已记录推理", True

    async def _record_action(self, group_id: str, action: str, api_url: str, api_key: str, model: str, temperature: float) -> Tuple[bool, Optional[str], bool]:
        """记录行动并判断是否死亡"""
        game_state = game_states.get(group_id, {})
        
        user_info = self._get_user_info()
        if not user_info:
            await self.send_text("❌ 无法获取用户信息。")
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
                await self.send_text("❌ 你不在游戏中。请先使用 `/rg 加入` 加入游戏。")
                return False, "不在游戏中", True
        
        player_data = players[user_id]
        if not player_data["is_alive"]:
            await self.send_text("❌ 你已经死亡，无法继续行动。")
            return False, "玩家已死亡", True

        player_data["action_history"].append(action)
        game_state["players"] = players
        
        time_system = game_state.get("time_system", {})
        environment = game_state.get("environment", {})
        
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
        
        sanity = player_data.get("mental_status", {}).get("sanity", 100)
        
        if sanity < 30:
            environment["lighting"] = "极度昏暗"
            environment["temperature"] = "刺骨寒冷"
            environment["sounds"] = ["诡异的声音", "低语", "心跳声"]
            environment["smells"] = ["血腥味", "腐臭味"]
            environment["atmosphere"] = "极度恐怖"
        elif sanity < 60:
            environment["lighting"] = "昏暗"
            environment["temperature"] = "寒冷"
            environment["sounds"] = ["风声", "脚步声", "呼吸声"]
            environment["smells"] = ["霉味", "灰尘味"]
            environment["atmosphere"] = "压抑"
        else:
            environment["lighting"] = "微弱光亮"
            environment["temperature"] = "阴冷"
            environment["sounds"] = ["寂静", "远处的声音"]
            environment["smells"] = ["轻微霉味"]
            environment["atmosphere"] = "紧张"
        
        game_state["time_system"] = time_system
        game_state["environment"] = environment
        
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

环境状况：
- 光线：{environment.get('lighting', '昏暗')}
- 温度：{environment.get('temperature', '寒冷')}
- 声音：{', '.join(environment.get('sounds', ['寂静']))}
- 气味：{', '.join(environment.get('smells', ['霉味']))}
- 氛围：{environment.get('atmosphere', '压抑')}

玩家当前理智值：{sanity}

请判断玩家行动是否会导致死亡，并详细描述行动后的场景和人物状态。

**场景描述要求（非常重要）：**

1. **位置描述**：明确描述玩家当前所在的具体位置（如：一楼大厅、二楼走廊、地下室、某个房间等）

2. **视觉细节**：
   - 周围环境的详细描述（门、窗户、家具、墙壁、地板、天花板等）
   - 光线状况（昏暗的灯光、闪烁的光线、微弱的光亮、完全黑暗等）
   - 看到的事物（物品、痕迹、符号、文字等）
   - 颜色和质感（墙壁的颜色、地板的材质、物品的外观等）

3. **听觉描述**：
   - 听到的声音（风声、脚步声、呼吸声、低语、哭声、敲门声、嘎吱声等）
   - 声音的来源和方向
   - 声音的强度和频率

4. **嗅觉描述**：
   - 闻到的气味（霉味、灰尘味、血腥味、腐臭味、金属味、香水味等）
   - 气味的浓淡和变化
   - 气味是否令人不适或熟悉

5. **触觉描述**：
   - 温度感受（刺骨的寒冷、阴冷的空气、闷热、冰冷的墙壁、温暖的物体等）
   - 触摸的质感（粗糙的地板、光滑的玻璃、粘稠的液体、干燥的纸张等）
   - 身体的感觉（麻木、刺痛、沉重、轻盈等）

6. **氛围营造**：
   - 整体的氛围感受（压抑、恐怖、诡异、平静、紧张等）
   - 空气的流动和压力
   - 时间流逝的感觉

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

请返回JSON格式：
{{
  "is_dead": "是/否",
  "scene_description": "行动后的详细场景描述（必须包含：位置、视觉细节、听觉描述、嗅觉描述、触觉描述、氛围营造。根据理智值调整描述风格。如果玩家死亡，描述死亡场景；如果存活，描述新的场景）",
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
  "action_feedback": "行动的反馈描述（如：心跳加速、手心出汗、呼吸急促等生理反应）",
  "new_location": "玩家的新位置（如：一楼大厅、二楼走廊、地下室等）"
}}

请仅返回JSON，不要包含任何其他文字。
        """

        llm_response = await self._call_llm_api(prompt, api_url, api_key, model, temperature)
        if not llm_response:
            await self.send_text("❌ 调用LLM API失败，请稍后再试。")
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
                    await self.send_text("❌ 判定行动结果失败，返回格式不正确。")
                    return False, "JSON解析失败", True
            else:
                await self.send_text("❌ 判定行动结果失败，返回格式不正确。")
                return False, "JSON解析失败", True

        is_dead = result.get("is_dead", "否")
        scene_description = result.get("scene_description", "")
        physical_status = result.get("physical_status", {})
        mental_status = result.get("mental_status", {})
        psychological_pressure = result.get("psychological_pressure", {})
        found_items = result.get("found_items", [])
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
        
        if found_items:
            player_data["inventory"].extend(found_items)
        
        game_state["players"] = players

        if is_dead == "是":
            player_data["is_alive"] = False
            game_state["players"] = players
            self._save_game_state(group_id)
            reply_text = (
                f"💀 **行动结果** - {user_name}\n\n"
                f"📝 **行动**：{action}\n\n"
                f"❌ **你已死亡**！\n\n"
                f"🎬 **场景描述**：\n{scene_description}\n\n"
            )
            if action_feedback:
                reply_text += f"📢 **行动反馈**：{action_feedback}\n\n"
            reply_text += f" 你已无法继续行动，但可以观看其他玩家。"
        else:
            self._save_game_state(group_id)
            reply_text = (
                f"✅ **行动结果** - {user_name}\n\n"
                f"📝 **行动**：{action}\n\n"
                f"🎬 **场景描述**：\n{scene_description}\n\n"
                f"💪 **身体状况**：\n"
                f"体力值：{health}/100\n"
                f"受伤：{injury}\n"
                f"疲劳：{fatigue}\n\n"
                f"🧠 **精神状况**：\n"
                f"理智值：{sanity}/100\n"
                f"状态：{state}\n"
                f"情绪：{emotion}\n\n"
                f"😰 **心理压力**：\n"
                f"恐惧等级：{fear_level}/100\n"
                f"焦虑等级：{anxiety_level}/100\n"
                f"压力等级：{stress_level}/100\n\n"
            )
            if found_items:
                reply_text += f"🎒 **获得物品**：{', '.join(found_items)}\n\n"
            if action_feedback:
                reply_text += f"📢 **行动反馈**：{action_feedback}\n\n"
            reply_text += f"📍 **当前位置**：{new_location}\n\n"
            if random_event:
                reply_text += f"⚡ **环境事件**：{random_event}\n\n"
            reply_text += f"🎉 你存活了下来！继续探索吧。"

        await self.send_text(reply_text)
        
        await self._check_clear_condition(group_id, api_url, api_key, model, temperature)
        
        return True, "已记录行动", True

    async def _end_game(self, group_id: str, api_url: str, api_key: str, model: str, temperature: float) -> Tuple[bool, Optional[str], bool]:
        """结束游戏并判定结局"""
        game_state = game_states.get(group_id, {})

        game_state["game_active"] = False
        self._save_game_state(group_id)
        
        players = game_state.get("players", {})
        
        if not players:
            await self.send_text("❌ 没有玩家参与游戏，无法判定结局。")
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

请仅返回JSON，不要包含任何其他文字。
        """

        llm_response = await self._call_llm_api(prompt, api_url, api_key, model, temperature)
        if not llm_response:
            await self.send_text("❌ 调用LLM API失败，请稍后再试。")
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
                    await self.send_text("❌ 判定结局失败，返回格式不正确。")
                    return False, "JSON解析失败", True
            else:
                await self.send_text("❌ 判定结局失败，返回格式不正确。")
                return False, "JSON解析失败", True

        ending = result.get("ending", "失败")
        truth_revealed = result.get("truth_revealed", "否")
        win_condition_met = result.get("win_condition_met", "否")
        resolve_condition_met = result.get("resolve_condition_met", "否")
        survivors = result.get("survivors", [])

        ending_emoji = {
            "完美": "🏆",
            "成功": "🎉",
            "通关": "✅",
            "失败": "💀"
        }

        reply_text = (
            f"{ending_emoji.get(ending, '❓')} **结局：{ending}**\n\n"
            f"🔍 **推理真相**：{truth_revealed}\n"
            f"🎯 **达成通关**：{win_condition_met}\n"
            f"🔓 **解除怪谈**：{resolve_condition_met}\n"
        )
        
        if survivors:
            reply_text += f"\n👥 **存活玩家**：\n"
            for survivor in survivors:
                reply_text += f"🔸 {survivor}\n"
        
        reply_text += f"\n📜 **隐藏真相**：\n{game_state.get('hidden_truth', '未知')}\n\n"
        reply_text += f"🔚 **游戏结束**。感谢参与！"

        await self.send_text(reply_text)
        
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
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(api_url, headers=headers, json=payload) as response:
                    if response.status == 200:
                        data = await response.json()
                        content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
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
        """删除存档文件"""
        try:
            save_file = os.path.join(DATA_DIR, f"{group_id}.json")
            
            if os.path.exists(save_file):
                os.remove(save_file)
                return True
            
            return False
        except Exception as e:
            print(f"删除存档文件时发生异常: {e}")
            return False

    async def _force_start_new_game(self, group_id: str, api_url: str, api_key: str, model: str, temperature: float, game_mode: str) -> Tuple[bool, Optional[str], bool]:
        """强制开始一个新的规则怪谈游戏（覆盖存档）"""
        prompt = """
你是一个专业的规则怪谈生成器。请生成一个恐怖或诡异的规则怪谈。

要求：
1. 生成一个场景（如：深夜的医院、废弃的学校、神秘的公寓等）
2. 详细描述场景的完整结构，包括：
   - 建筑类型和总体布局
   - 楼层数量（如：地上5层，地下2层）
   - 每层的主要房间和区域
   - 通道、楼梯、电梯等连接方式
   - 特殊区域（如：地下室、天台、禁闭室等）
3. 列出5-8条规则，规则应该看似合理但隐藏着诡异之处
4. 设定通关条件（如：在规定时间内找到出口、收集特定物品、存活到天亮等）
5. 设定解除条件（如：找到规则怪谈的根源并消除它、找到某个特定物品并使用、完成某个仪式等）
6. 规则应该有隐藏的逻辑和真相，需要玩家推理
7. 以JSON格式返回，格式如下：
{
  "scene": "场景名称（如：深夜的废弃医院）",
  "scene_structure": "详细的场景结构描述，包括建筑类型、楼层数、每层的房间布局、通道、特殊区域等",
  "rules": ["规则1", "规则2", ...],
  "win_condition": "通关条件",
  "resolve_condition": "解除条件（解决规则怪谈根源的条件）",
  "hidden_truth": "隐藏的真相（不显示给玩家）",
  "death_triggers": ["会导致死亡的行为1", "会导致死亡的行为2", ...]
}

请仅返回JSON，不要包含任何其他文字。
        """

        await self.send_text("正在生成规则怪谈...")

        llm_response = await self._call_llm_api(prompt, api_url, api_key, model, temperature)
        if not llm_response:
            await self.send_text("❌ 调用LLM API失败，请稍后再试。")
            return False, "LLM API调用失败", True

        try:
            game_data = json.loads(llm_response)
        except json.JSONDecodeError as e:
            print(f"[规则怪谈] JSON解析失败: {e}")
            print(f"[规则怪谈] 尝试提取JSON部分...")
            
            json_match = re.search(r'\{[\s\S]*\}', llm_response)
            if json_match:
                try:
                    game_data = json.loads(json_match.group())
                    print(f"[规则怪谈] 成功提取JSON")
                except json.JSONDecodeError as e2:
                    print(f"[规则怪谈] 提取JSON后仍然解析失败: {e2}")
                    await self.send_text("❌ 生成规则怪谈失败，返回格式不正确。")
                    return False, "JSON解析失败", True
            else:
                await self.send_text("❌ 生成规则怪谈失败，返回格式不正确。")
                return False, "JSON解析失败", True

        max_players = 5 if game_mode == "多人" else 1

        game_states[group_id] = {
            "scene": game_data.get("scene", ""),
            "scene_structure": game_data.get("scene_structure", ""),
            "rules": game_data.get("rules", []),
            "win_condition": game_data.get("win_condition", ""),
            "resolve_condition": game_data.get("resolve_condition", ""),
            "hidden_truth": game_data.get("hidden_truth", ""),
            "death_triggers": game_data.get("death_triggers", []),
            "hints_used": 0,
            "max_hints": 3,
            "game_active": True,
            "max_players": max_players,
            "game_mode": game_mode,
            "players": {}
        }

        self._save_game_state(group_id)

        reply_text = (
            f"🎭 **规则怪谈** ({game_mode}模式)\n\n"
            f"📍 **场景**：{game_data.get('scene', '')}\n\n"
            f"🏗️ **场景结构**：\n{game_data.get('scene_structure', '')}\n\n"
            f"📜 **规则**：\n"
        )

        for i, rule in enumerate(game_data.get("rules", []), 1):
            reply_text += f"{i}. {rule}\n"

        reply_text += f"\n🎯 **通关条件**：{game_data.get('win_condition', '')}\n\n"

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
                reply_text += f"👤 **玩家**：{user_name}\n"
            else:
                reply_text += f"👤 **玩家**：0/1\n"
        else:
            reply_text += f"👥 **玩家**：0/5\n"

        reply_text += f"💡 **提示次数**：0/3\n\n"

        if game_mode == "单人":
            reply_text += f"🔸 使用 `/rg 提示 <规则/线索>` 获取提示\n"
            reply_text += f"🔸 使用 `/rg 推理 <推理内容>` 记录推理\n"
            reply_text += f"🔸 使用 `/rg 行动 <行动描述>` 描述行动\n"
            reply_text += f"🔸 使用 `/rg 状态` 查看游戏状态\n"
            reply_text += f"🔸 使用 `/rg 结束` 结束游戏"
        else:
            reply_text += f"🔸 使用 `/rg 加入` 加入游戏\n"
            reply_text += f"🔸 使用 `/rg 提示 <规则/线索>` 获取提示\n"
            reply_text += f"🔸 使用 `/rg 推理 <推理内容>` 记录推理\n"
            reply_text += f"🔸 使用 `/rg 行动 <行动描述>` 描述行动\n"
            reply_text += f"🔸 使用 `/rg 状态` 查看游戏状态\n"
            reply_text += f"🔸 使用 `/rg 结束` 结束游戏"

        await self.send_text(reply_text)
        return True, "已开始游戏", True

    async def _restore_game(self, group_id: str) -> Tuple[bool, Optional[str], bool]:
        """恢复存档游戏"""
        saved_state = self._load_game_state(group_id)
        if not saved_state:
            await self.send_text("❌ 没有找到存档。请先使用 `/rg 开始` 开始游戏。")
            return False, "无存档", True

        if not saved_state.get("game_active", False):
            await self.send_text("❌ 存档中的游戏已结束，无法恢复。请使用 `/rg 开始` 开始新游戏。")
            return False, "游戏已结束", True

        game_states[group_id] = saved_state

        game_mode = saved_state.get("game_mode", "单人")
        reply_text = (
            f"🎭 **规则怪谈** ({game_mode}模式) - 已恢复存档\n\n"
            f"📍 **场景**：{saved_state.get('scene', '')}\n\n"
            f"📜 **规则**：\n"
        )

        for i, rule in enumerate(saved_state.get("rules", []), 1):
            reply_text += f"{i}. {rule}\n"

        reply_text += f"\n🎯 **通关条件**：{saved_state.get('win_condition', '')}\n\n"

        players = saved_state.get("players", {})
        max_players = saved_state.get("max_players", 5)
        reply_text += f"👥 **玩家**：{len(players)}/{max_players}\n"

        for pid, p_data in players.items():
            status = "存活" if p_data["is_alive"] else "死亡"
            reply_text += f"🔸 {p_data['name']} ({status})\n"

        reply_text += f"\n💡 **提示次数**：{saved_state.get('hints_used', 0)}/{saved_state.get('max_hints', 3)}\n\n"

        if game_mode == "单人":
            reply_text += f"🔸 使用 `/rg 提示 <规则/线索>` 获取提示\n"
            reply_text += f"🔸 使用 `/rg 推理 <推理内容>` 记录推理\n"
            reply_text += f"🔸 使用 `/rg 行动 <行动描述>` 描述行动\n"
            reply_text += f"🔸 使用 `/rg 状态` 查看游戏状态\n"
            reply_text += f"🔸 使用 `/rg 结束` 结束游戏"
        else:
            reply_text += f"🔸 使用 `/rg 加入` 加入游戏\n"
            reply_text += f"🔸 使用 `/rg 提示 <规则/线索>` 获取提示\n"
            reply_text += f"🔸 使用 `/rg 推理 <推理内容>` 记录推理\n"
            reply_text += f"🔸 使用 `/rg 行动 <行动描述>` 描述行动\n"
            reply_text += f"🔸 使用 `/rg 状态` 查看游戏状态\n"
            reply_text += f"🔸 使用 `/rg 结束` 结束游戏"

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

请仅返回JSON，不要包含任何其他文字。
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
                f"🎉 **恭喜！你已达成通关条件！**\n\n"
                f"{result.get('reason', '')}\n\n"
                f"🔸 使用 `/rg 继续` 继续探索完美结局\n"
                f"🔸 使用 `/rg 结束` 结束游戏并查看结局"
            )
            await self.send_text(reply_text)

    async def _continue_to_perfect(self, group_id: str, api_url: str, api_key: str, model: str, temperature: float) -> Tuple[bool, Optional[str], bool]:
        """继续探索完美结局"""
        game_state = game_states.get(group_id, {})
        
        players = game_state.get("players", {})
        
        if not players:
            await self.send_text("❌ 没有玩家参与游戏，无法继续探索。")
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

请仅返回JSON，不要包含任何其他文字。
        """

        llm_response = await self._call_llm_api(prompt, api_url, api_key, model, temperature)
        if not llm_response:
            await self.send_text("❌ 调用LLM API失败，请稍后再试。")
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
                    await self.send_text("❌ 判定完美结局失败，返回格式不正确。")
                    return False, "JSON解析失败", True
            else:
                await self.send_text("❌ 判定完美结局失败，返回格式不正确。")
                return False, "JSON解析失败", True
        
        game_state["game_active"] = False
        self._save_game_state(group_id)
        
        if result.get("perfect") == "是":
            reply_text = (
                f"🏆 **完美结局！** 🏆\n\n"
                f"{result.get('reason', '')}\n\n"
                f"🎊 恭喜你！你已达成完美结局！\n\n"
                f"✅ 推理出规则怪谈的原貌\n"
                f"✅ 达成通关要求\n"
                f"✅ 解除规则怪谈（解决根源）\n\n"
                f"🌟 **隐藏真相**：{game_state.get('hidden_truth', '')}\n\n"
                f"感谢游玩！"
            )
            self._delete_save_file(group_id)
        else:
            reply_text = (
                f"🎮 **继续探索中...**\n\n"
                f"{result.get('reason', '')}\n\n"
                f"💡 完美结局需要同时满足三个条件：\n"
                f"🔸 推理出规则怪谈的原貌\n"
                f"🔸 达成通关要求\n"
                f"🔸 解除规则怪谈（解决根源）\n\n"
                f"当前状态：\n"
                f"{'✅' if result.get('truth_revealed') == '是' else '❌'} 推理出规则怪谈的原貌\n"
                f"{'✅' if result.get('win_condition_met') == '是' else '❌'} 达成通关要求\n"
                f"{'✅' if result.get('resolve_condition_met') == '是' else '❌'} 解除规则怪谈（解决根源）\n\n"
                f"🔸 继续使用 `/rg 推理` 和 `/rg 行动` 探索\n"
                f"🔸 使用 `/rg 继续` 再次检查是否达成完美结局\n"
                f"🔸 使用 `/rg 结束` 结束游戏并查看结局"
            )
            game_state["game_active"] = True
            self._save_game_state(group_id)
        
        await self.send_text(reply_text)
        return True, "已检查完美结局", True
