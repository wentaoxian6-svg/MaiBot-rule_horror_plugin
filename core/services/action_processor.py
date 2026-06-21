"""行动处理服务 - 处理玩家行动并生成反馈"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from ...common.models import JsonObject, JsonValue
from ...systems.npc_system import NPCMemory, NPCAttitude
from ...systems.room_topology import build_room_graph, can_hear_between_rooms, get_audible_npcs, get_visible_npcs
from ..config import get_config
from ..llm.client import LLMClient, get_default_max_tokens

from ..game.models import GameSession, GameStatus, Player, PlayerStatus, Rule
from .item_manager import ItemManager

logger = logging.getLogger(__name__)


class ActionResult:
    """行动结果"""
    def __init__(
        self,
        description: str,
        sanity_change: int = 0,
        health_change: int = 0,
        discovered_clues: list[str] | None = None,
        triggered_event: str | None = None,
        is_fatal: bool = False,
        violated_rule: str | None = None,
        is_key_item: bool = False,
        found_items: list[str] | None = None,
    ):
        self.description: str = description
        self.sanity_change: int = sanity_change
        self.health_change: int = health_change
        self.discovered_clues: list[str] = discovered_clues or []
        self.triggered_event: str | None = triggered_event
        self.is_fatal: bool = is_fatal
        self.violated_rule: str | None = violated_rule
        self.is_key_item: bool = is_key_item
        self.found_items: list[str] = found_items or []


class ActionProcessor:
    """行动处理器 - 处理玩家行动"""

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        message_sender: Callable[[str], Awaitable[bool]] | None = None,
        session_saver: Callable[[str, GameSession], Awaitable[None]] | None = None,
    ):
        self.llm_client: LLMClient = llm_client or LLMClient()
        self.item_manager: ItemManager = ItemManager()
        self._message_sender = message_sender
        self._session_saver = session_saver

    @staticmethod
    def _normalize_rule_text_for_dedup(text: str) -> str:
        return re.sub(r"\s+", "", str(text or "").strip()).lower()

    @staticmethod
    def _clamp_ratio(value: object, default: float) -> float:
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, (int, float)):
            return max(0.0, min(1.0, float(value)))
        if isinstance(value, str):
            try:
                return max(0.0, min(1.0, float(value.strip())))
            except Exception:
                return default
        return default

    def _get_session_rule_objects(self, session: GameSession) -> list[Rule]:
        return [Rule.from_dict(rule, index) for index, rule in enumerate(session.rules or [])]

    def _get_runtime_npc_profile(self, npc: JsonObject) -> JsonObject:
        return {
            "knowledge_reliability": self._clamp_ratio(npc.get("knowledge_reliability"), 0.75),
            "deception_tendency": self._clamp_ratio(npc.get("deception_tendency"), 0.1),
            "corruption_level": self._clamp_ratio(npc.get("corruption_level"), 0.0),
            "current_state": str(npc.get("current_state", "稳定") or "稳定").strip(),
            "bias_tags": [str(item).strip() for item in npc.get("bias_tags", []) if str(item).strip()] if isinstance(npc.get("bias_tags", []), list) else [],
            "known_rule_ids": [str(item).strip() for item in npc.get("known_rule_ids", []) if str(item).strip()] if isinstance(npc.get("known_rule_ids", []), list) else [],
        }

    def _record_rule_texts(self, player: Player, rule_texts: list[str]) -> int:
        merged_rules = [str(rule).strip() for rule in getattr(player, "recorded_rules", []) if str(rule).strip()]
        seen = {self._normalize_rule_text_for_dedup(text) for text in merged_rules if text}
        added_count = 0

        for raw_text in rule_texts:
            text = str(raw_text or "").strip()
            key = self._normalize_rule_text_for_dedup(text)
            if not text or not key or key in seen:
                continue
            merged_rules.append(text)
            seen.add(key)
            added_count += 1

        player.recorded_rules = merged_rules
        return added_count

    @staticmethod
    def _normalize_item_name(text: object) -> str:
        return re.sub(r"\s+", "", str(text or "").strip()).lower()

    def _add_inventory_item_once(self, player: Player, item: JsonObject) -> bool:
        """按名称去重添加背包物品，避免同一线索/道具反复刷屏。"""
        item_name = self._normalize_item_name(item.get("name", ""))
        if not item_name:
            return False

        for existing in player.inventory:
            if not isinstance(existing, dict):
                continue
            if self._normalize_item_name(existing.get("name", "")) == item_name:
                return False

        player.inventory.append(item)
        return True

    def _apply_feedback_state_updates(self, player: Player, updates: Mapping[str, Any]) -> None:
        """应用沉浸式反馈带来的额外状态变化。"""
        sanity_delta = updates.get("sanity")
        if isinstance(sanity_delta, int):
            player.sanity = max(0, min(100, player.sanity + sanity_delta))

        health_delta = updates.get("health")
        if isinstance(health_delta, int):
            player.health = max(0, min(100, player.health + health_delta))

        fear_delta = updates.get("fear_level")
        if isinstance(fear_delta, int):
            player.fear_level = max(0, min(100, player.fear_level + fear_delta))

        anxiety_delta = updates.get("anxiety_level")
        if isinstance(anxiety_delta, int):
            player.anxiety_level = max(0, min(100, player.anxiety_level + anxiety_delta))

        stress_delta = updates.get("stress_level")
        if isinstance(stress_delta, int):
            player.stress_level = max(0, min(100, player.stress_level + stress_delta))

        location = updates.get("location")
        if isinstance(location, str) and location.strip():
            player.location = location.strip()

        if player.health <= 0:
            player.status = PlayerStatus.DEAD

    @staticmethod
    def _iter_runtime_npcs(session: GameSession) -> list[JsonObject]:
        env_state = session.environment_state if isinstance(session.environment_state, dict) else {}
        npcs = env_state.get("npcs", [])
        return [npc for npc in npcs if isinstance(npc, dict)] if isinstance(npcs, list) else []

    def _find_runtime_npc(self, session: GameSession, npc_name: str) -> JsonObject | None:
        target_name = str(npc_name or "").strip()
        if not target_name:
            return None

        lowered_target = target_name.lower()
        partial_match: JsonObject | None = None
        for npc in self._iter_runtime_npcs(session):
            name = str(npc.get("name", "") or "").strip()
            npc_id = str(npc.get("npc_id", "") or "").strip()
            lowered_name = name.lower()
            if name == target_name or npc_id == target_name:
                return npc
            if lowered_target in lowered_name or lowered_name in lowered_target:
                partial_match = npc
        return partial_match

    def _get_runtime_npc_memory(self, session: GameSession, npc_name: str) -> tuple[JsonObject | None, NPCMemory | None]:
        npc = self._find_runtime_npc(session, npc_name)
        if npc is None:
            return None, None
        raw_memory = npc.get("memory", {})
        memory_data = raw_memory if isinstance(raw_memory, dict) else {}
        return npc, NPCMemory.from_dict(memory_data)

    async def process_action(
        self,
        action: str,
        player: Player,
        session: GameSession,
        group_id: str = "",
    ) -> ActionResult:
        """
        处理玩家行动
        
        Args:
            action: 行动描述
            player: 玩家对象
            session: 游戏会话
        
        Returns:
            ActionResult 对象
        """
        logger.info(f"处理行动: {player.name} - {action}")
        
        # 首先检查是否是使用物品的行动
        item_used, item_effect_text = self.item_manager.check_and_use_item(action, player, session)
        if item_used:
            logger.info("玩家使用了物品，跳过LLM判定")
            # 创建结果对象
            result = ActionResult(
                description=item_effect_text or "你使用了物品。",
                sanity_change=0,
                health_change=0,
                discovered_clues=[],
                triggered_event=None,
                is_fatal=False,
                violated_rule=None,
            )
            # 应用状态变化（包括疲劳和心理状态）
            self._apply_changes(player, result, action)
            return result

        # 检查是否是休息行动
        is_resting, rest_effect_text, time_cost = self.item_manager.check_and_rest(action, player, session)
        if is_resting:
            logger.info(f"玩家休息了，花费 {time_cost} 分钟")
            # 更新游戏时间
            if session.time_manager:
                elapsed_minutes = session.time_manager.get("elapsed_minutes", 0) + time_cost
                session.time_manager["elapsed_minutes"] = elapsed_minutes

                # 更新时间描述
                if elapsed_minutes < 60:
                    session.time_manager["current_time"] = "深夜"
                    session.time_manager["time_description"] = "午夜时分，周围一片死寂"
                elif elapsed_minutes < 180:
                    session.time_manager["current_time"] = "凌晨"
                    session.time_manager["time_description"] = "黎明前的黑暗，空气中弥漫着不安"
                else:
                    session.time_manager["current_time"] = "黎明"
                    session.time_manager["time_description"] = "东方泛起鱼肚白，但黑暗仍未完全消散"

            # 创建结果对象
            result = ActionResult(
                description=rest_effect_text or "你休息了一会儿。",
                sanity_change=0,
                health_change=0,
                discovered_clues=[],
                triggered_event=None,
                is_fatal=False,
                violated_rule=None,
            )
            # 应用状态变化（包括疲劳和心理状态）
            self._apply_changes(player, result, action)
            return result
        
        # NPC交互：按"是否在场 + 态度/记忆 + 玩家语气/行为"做动态判定
        npc_result = self._maybe_handle_npc_interaction(action, player, session)
        if npc_result is not None:
            # 应用状态变化（包括疲劳和心理状态）
            self._apply_changes(player, npc_result, action)
            return npc_result




        # 构建上下文
        context = self._build_context(player, session)
        
        # 调用LLM判定行动结果
        result_data = await self._judge_action(action, context)
        if not isinstance(result_data, dict):
            result_data = {}

        def _to_int(v: object, default: int = 0) -> int:
            if isinstance(v, bool):
                return 1 if v else 0
            if isinstance(v, int):
                return v
            if isinstance(v, float):
                return int(v)
            if isinstance(v, str):
                try:
                    return int(float(v.strip()))
                except Exception:
                    return default
            return default

        # 规范化数值字段，避免 LLM 返回字符串导致状态不更新
        result_data["sanity_change"] = _to_int(result_data.get("sanity_change", 0), 0)
        result_data["health_change"] = _to_int(result_data.get("health_change", 0), 0)

        
        # 检查是否发现关键物品
        key_item_found = False
        found_items = result_data.get("found_items", [])
        item_details = result_data.get("item_details", {})
        
        if found_items and item_details:
            is_key_item = item_details.get("is_key_item", "否")
            if is_key_item == "是":
                key_item_found = True
                # 添加关键物品到背包
                self._add_inventory_item_once(player, {
                    "name": item_details.get("item_name", found_items[0]),
                    "type": item_details.get("item_type", "线索"),
                    "description": item_details.get("item_description", ""),
                    "observation_hint": item_details.get("observation_hint", ""),
                    "is_key_item": True,
                })
            else:
                # 添加普通物品到背包
                for item in found_items:
                    self._add_inventory_item_once(player, {
                        "name": item,
                        "type": "物品",
                        "description": "",
                        "is_key_item": False,
                    })
        elif found_items:
            # 添加普通物品到背包
            for item in found_items:
                self._add_inventory_item_once(player, {
                    "name": item,
                    "type": "物品",
                    "description": "",
                    "is_key_item": False,
                })
        
        # 提取/推断新位置（优先使用 LLM 返回；否则根据行动文本与场景结构做启发式推断）
        new_location = result_data.get("new_location")
        inferred_location: str | None = None

        if new_location and isinstance(new_location, str) and new_location.strip():
            inferred_location = new_location.strip()
        else:
            inferred_location = self._infer_new_location(action, session, player.location)
            if inferred_location:
                result_data["new_location"] = inferred_location

        if inferred_location:
            player.location = inferred_location
            logger.info(f"玩家 {player.name} 移动到新位置: {player.location}")
        
        # 创建行动结果
        result = ActionResult(
            description=result_data.get("description", "你执行了行动。"),
            sanity_change=result_data.get("sanity_change", 0),
            health_change=result_data.get("health_change", 0),
            discovered_clues=result_data.get("discovered_clues", []),
            triggered_event=result_data.get("triggered_event"),
            is_fatal=result_data.get("is_fatal", False),
            violated_rule=result_data.get("violated_rule"),
            is_key_item=key_item_found,
        )
        
        # 解析并更新情绪和心理状态
        self._update_mental_state(player, result_data)
        
        # 应用状态变化
        self._apply_changes(player, result, action)
        
        # 处理违规后果（如果有）
        if result.violated_rule:
            await self._handle_violation_consequences(
                player=player,
                session=session,
                violated_rule=result.violated_rule,
                    action=action,
                    group_id=group_id,
            )
        
        # 更新环境记忆
        self._update_environment_memory(action, player, session)
        
        # 检查是否需要规则变异（如果发现关键物品，触发规则变异）
        await self._check_rule_mutation(action, player, session, result, key_item_found)
        
        logger.info(f"行动处理完成: 理智{result.sanity_change:+d}, 体力{result.health_change:+d}, 关键物品={key_item_found}")
        return result

    def _maybe_handle_npc_interaction(self, action: str, player: Player, session: GameSession) -> ActionResult | None:
        """尝试处理 NPC 交互。

        目标：
        - 不再“硬编码 NPC 永远回答/永远知道一切”。
        - NPC 是否回应、回应多少、是否回避，取决于：在场性 + 态度/记忆 + 玩家语气。
        - 玩家通过互动获得的信息会写入 `player.recorded_rules`，不再依赖旧的全局已知规则索引。
        """
        if not action.strip():
            return None

        # 注意：不要用单字“问”做简单包含匹配，否则“问题/问号/提问”等名词短语会误触发“搭话”分支，
        # 进而把“前往/检查/搜索”等真实行动直接短路掉。
        talk_patterns = [
            r"询问",
            r"打听",
            r"请教",
            r"搭话",
            r"对话",
            r"交谈",
            r"叫住",
            r"招呼",
            # “问”只在其后不是“题”时才按动词对待（避免误匹配“问题”）
            r"问(?!题)",
            # “喊”保持兼容（常见：喊住/喊他/喊一声…）
            r"喊",
        ]
        if not any(re.search(p, action) for p in talk_patterns):
            return None

        env_state = session.environment_state if isinstance(session.environment_state, dict) else {}
        npcs = env_state.get("npcs", []) if isinstance(env_state.get("npcs", []), list) else []
        if not npcs:
            return None

        player_loc = str(player.location or "")

        def npc_loc(npc: Mapping[str, JsonValue]) -> str:
            return str(npc.get("current_location") or npc.get("location") or npc.get("home_location") or "")

        # 如果行动里有明显“移动/检查/探索”等强动作意图，默认不要被 NPC 搭话短路
        # （除非玩家明确点名某个 NPC）
        non_talk_keywords = [
            "前往", "去", "到", "进入", "离开", "返回", "回到",
            "检查", "查看", "调查", "搜索", "探索", "翻找",
            "打开", "关闭", "使用", "拿起", "放下", "触摸", "推", "拉", "按",
        ]
        has_non_talk_intent = any(k in action for k in non_talk_keywords)

        # 选择目标NPC：优先匹配名字，其次取同地点的第一个
        target: JsonObject | None = None
        mentioned_name = False
        for npc in npcs:
            if not isinstance(npc, dict):
                continue
            name = str(npc.get("name") or "")
            if name and name in action:
                target = npc
                mentioned_name = True
                break

        if target is None:
            same_place = [npc for npc in npcs if isinstance(npc, dict) and npc_loc(npc) == player_loc]
            target = same_place[0] if same_place else None

        # 未点名 + 行动包含强动作意图：交给正常行动判定流程处理
        if has_non_talk_intent and not mentioned_name:
            return None

        if target is None:
            # 玩家在聊天但附近没有任何NPC
            return None

        name = str(target.get("name") or session.npc_guidance.get("npc_name") or "NPC")
        target_loc = npc_loc(target).strip()
        loc = target_loc or "未知位置"

        # 位置未知：不允许“隔空对话”，给出符合直觉的反馈
        if player_loc and not target_loc:
            return ActionResult(description=f"你压低声音叫了叫{name}，但你看不见他，也无法确定他是否在{player_loc}附近。")

        # 不在场：允许玩家“喊人”，但给出符合直觉的反馈
        if player_loc and target_loc and target_loc != player_loc:
            return ActionResult(description=f"你朝{loc}的方向叫了叫{name}，回应只有回声。你此刻在{player_loc}，而他不在这里。")

        # 载入/初始化记忆
        mem = NPCMemory.from_dict(target.get("memory", {}) if isinstance(target.get("memory"), dict) else {})
        npc_profile = self._get_runtime_npc_profile(target)
        pid = str(player.player_id)
        mem.initialize_attitude_vector(pid)

        # 语气/方式对态度的即时影响
        polite = any(k in action for k in ["请", "麻烦", "您好", "劳驾", "拜托", "求"]) 
        aggressive = any(k in action for k in ["滚", "闭嘴", "威胁", "砸", "杀", "打", "逼", "掐"]) 

        # 计算帮助意愿
        vec = mem.get_attitude_vector(pid)
        affection = float(vec.get("affection", 50.0))
        trust = float(vec.get("trust", 50.0))
        suspicion = float(vec.get("suspicion", 0.0))
        hostility = float(vec.get("hostility", 0.0))
        fear = float(vec.get("fear", 0.0))

        score = (affection + trust) - (suspicion + hostility * 1.2 + fear * 0.8)
        if polite:
            score += 8
        if aggressive:
            score -= 25

        attitude = mem.get_attitude(pid)

        # 是否在问规则
        ask_rule_keywords = ["规则", "规矩", "守则", "注意事项", "剩下", "其他", "还有", "没说完", "补充"]
        asking_rules = any(k in action for k in ask_rule_keywords)

        # 根据分数决定：0=拒绝/回避，1=少量，2=中等，3=较多
        if hostility >= 60 or score < -20 or attitude in {NPCAttitude.HOSTILE}:
            help_level = 0
        elif suspicion >= 70 or score < 10 or attitude in {NPCAttitude.SUSPICIOUS}:
            help_level = 0
        elif score < 45:
            help_level = 1
        elif score < 85:
            help_level = 2
        else:
            help_level = 3

        # 更新态度向量（记录这次互动带来的变化）
        if aggressive:
            mem.update_attitude_vector(pid, hostility_delta=10, trust_delta=-10, suspicion_delta=8)
        elif polite:
            mem.update_attitude_vector(pid, trust_delta=5, affection_delta=3, suspicion_delta=-2)
        else:
            # 中性互动：轻微降低陌生感
            mem.update_attitude_vector(pid, trust_delta=1)

        # 记录互动
        game_time = 0
        if isinstance(session.time_manager, dict):
            game_time = int(session.time_manager.get("elapsed_minutes", 0) or 0)
        mem.record_interaction(pid, "talk", {"action": action, "location": player_loc}, game_time)

        # 写回 NPC 记忆
        target["memory"] = mem.to_dict()

        if not asking_rules:
            # 普通搭话：依据态度给一句“像人”的回应（不强行输出规则）
            state_note = ""
            if float(npc_profile.get("corruption_level", 0.0) or 0.0) >= 0.6:
                state_note = "他的瞳孔像是短暂失了焦，语尾也有些飘。"
            elif str(npc_profile.get("current_state", "")) in {"紧张", "戒备"}:
                state_note = "他像一直在留意四周的动静，说话前先停顿了一下。"
            if help_level == 0:
                if attitude == NPCAttitude.HOSTILE:
                    text = f"你试着向{loc}的{name}搭话。{state_note}他抬眼看了你一下，目光像钉子：『别挡路。』"
                elif attitude == NPCAttitude.SUSPICIOUS:
                    text = f"你试着向{loc}的{name}搭话。{state_note}他没有立刻回答，只反问：『你问这个干什么？』"
                else:
                    text = f"你试着向{loc}的{name}搭话。{state_note}他像是在听远处的动静，只敷衍地嗯了一声。"
            else:
                npc_dialogue = str((session.npc_guidance or {}).get("npc_dialogue") or "").strip()
                if npc_dialogue:
                    text = f"你试着向{loc}的{name}搭话。{state_note}{name}低声道：『{npc_dialogue}』"
                else:
                    text = f"你试着向{loc}的{name}搭话。{state_note}他压低嗓音：『别大声。这里不喜欢热闹。』"
            return ActionResult(description=text)

        # 询问规则：把“愿不愿意说”和“说得靠不靠谱”分开处理
        recorded_rules = [str(rule).strip() for rule in getattr(player, "recorded_rules", []) if str(rule).strip()]
        recorded_rule_keys = {self._normalize_rule_text_for_dedup(rule) for rule in recorded_rules if rule}
        unknown_rules = [
            rule
            for rule in self._get_session_rule_objects(session)
            if self._normalize_rule_text_for_dedup(rule.surface_text) not in recorded_rule_keys
        ]
        known_rule_ids = {str(rule_id).strip() for rule_id in npc_profile.get("known_rule_ids", []) if str(rule_id).strip()}
        if known_rule_ids:
            prioritized = [rule for rule in unknown_rules if rule.rule_id in known_rule_ids]
            remaining = [rule for rule in unknown_rules if rule.rule_id not in known_rule_ids]
            unknown_rules = prioritized + remaining

        if help_level == 0 or not unknown_rules:
            if attitude == NPCAttitude.HOSTILE:
                text = f"你压低声音向{loc}的{name}问起规矩。他的手指停在台面上，冷冷地敲了两下：『我没义务教你。』"
            elif attitude == NPCAttitude.SUSPICIOUS:
                text = f"你压低声音向{loc}的{name}问起规矩。他盯着你看了几秒：『你先把刚才那几条记牢。问太多，容易出事。』"
            else:
                text = f"你压低声音向{loc}的{name}问起规矩。他摇了摇头：『现在不方便。』"
            return ActionResult(description=text)

        reliability = float(npc_profile.get("knowledge_reliability", 0.75) or 0.75)
        deception = float(npc_profile.get("deception_tendency", 0.1) or 0.1)
        corruption = float(npc_profile.get("corruption_level", 0.0) or 0.0)
        truthfulness = max(0.0, min(1.0, reliability * (1.0 - deception * 0.7) * (1.0 - corruption * 0.8)))

        false_rules = [rule for rule in unknown_rules if rule.truth_status == "false" or rule.is_authentic is False]
        true_rules = [rule for rule in unknown_rules if rule not in false_rules]
        if not true_rules:
            true_rules = unknown_rules

        reveal_count = min(help_level, len(true_rules))
        recordable_rule_texts: list[str] = []
        spoken_parts: list[str] = []
        source_hint = ""
        bias_tags = npc_profile.get("bias_tags", [])
        if isinstance(bias_tags, list) and bias_tags:
            source_hint = f"带着明显的{'、'.join(str(tag) for tag in bias_tags[:2])}口吻，"

        def cn_num(n: int) -> str:
            table = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]
            return table[n - 1] if 1 <= n <= len(table) else str(n)

        if truthfulness >= 0.72:
            for j, rule in enumerate(true_rules[:reveal_count], 1):
                if rule.surface_text:
                    spoken_parts.append(f"第{cn_num(j)}，{rule.surface_text}")
                    recordable_rule_texts.append(rule.surface_text)
        elif truthfulness >= 0.45:
            if true_rules:
                rule = true_rules[0]
                spoken_parts.append(f"我记得比较像是“{rule.surface_text}”，但你最好再找纸面记录核对一遍")
            if false_rules and (deception >= 0.35 or corruption >= 0.35):
                spoken_parts.append(f"也有人说过“{false_rules[0].surface_text}”，不过我不保证")
        else:
            rumor_pool = false_rules or unknown_rules
            if rumor_pool:
                rumor_rule = rumor_pool[0]
                spoken_parts.append(f"他压低声音，{source_hint}只丢给你一句：『{rumor_rule.surface_text}』")

        if not spoken_parts:
            text = f"你压低声音向{loc}的{name}问起规矩。他皱了皱眉：『我现在也说不准，你最好自己去核对留下来的记录。』"
            return ActionResult(description=text)

        if recordable_rule_texts:
            self._record_rule_texts(player, recordable_rule_texts)

        prefix = "你压低声音向{loc}的{name}问起剩下的规矩。".format(loc=loc, name=name)
        if recordable_rule_texts and help_level >= 3:
            mid = "他像是权衡了几秒，终于把话说得更明白："
        elif recordable_rule_texts and help_level == 2:
            mid = "他不耐烦地叹了口气，还是补了两句："
        elif truthfulness >= 0.45:
            mid = "他迟疑了很久，说出来的话像是在回忆，也像是在自我纠正："
        else:
            mid = "他神色古怪地看了你一眼，只吐出一句听上去并不那么可靠的话："

        text = f"{prefix}{mid}『{'；'.join(spoken_parts)}』"
        if not recordable_rule_texts:
            text += " 这更像一条口头情报，你觉得还需要再找别的来源确认。"
        return ActionResult(description=text)

    def _update_environment_memory(self, action: str, player: Player, session: GameSession) -> None:

        """更新环境记忆"""
        # 记录访问的位置
        if player.location:
            session.add_visited_location(player.location)
        
        # 检测互动的物体（简单的关键词匹配）
        interaction_keywords = ["打开", "关闭", "拿起", "放下", "使用", "检查", "触摸", "推", "拉", "按"]
        for keyword in interaction_keywords:
            if keyword in action:
                # 提取物体名称（简化版）
                words = action.replace(keyword, "").strip().split()
                if words:
                    obj = words[0]
                    session.add_interacted_object(f"{keyword}{obj}")
                break
        
        # 记录时间事件
        if "等待" in action or "休息" in action:
            session.add_time_event(f"{player.name}在{player.location}{action}")
    
    async def _check_rule_mutation(
        self,
        action: str,
        player: Player,
        session: GameSession,
        result: ActionResult,
        key_item_found: bool = False,
    ) -> None:
        """检查是否需要规则变异（条件+LLM评估混合模式）"""
        # 如果理智崩坏，不触发规则变异
        if player.sanity == 0:
            return
        
        trigger_reasons: list[str] = []
        satisfied_conditions: list[str] = []
        
        # 1. 检查关键物品
        if key_item_found:
            trigger_reasons.append("关键物品")
        
        # 2. 检查预设条件
        from ...systems.rule_mutation_system import RuleMutationSystem
        game_state = self._build_mutation_game_state(session, player)
        game_time = 0
        if isinstance(session.time_manager, dict):
            game_time = int(session.time_manager.get("elapsed_minutes", 0) or 0)
        
        # 获取规则变异系统实例（从plugin通过session传递）
        mutation_system = getattr(session, '_rule_mutation_system', None)
        if mutation_system and isinstance(mutation_system, RuleMutationSystem):
            conditions = mutation_system.check_conditions(game_state, action, game_time)
            for condition in conditions:
                condition_desc = condition.description
                satisfied_conditions.append(condition_desc)
                trigger_reasons.append(f"条件触发：{condition_desc}")
                # 记录条件已触发
                mutation_system.triggered_conditions.add(
                    f"{condition.condition_type.value}_{condition.description}"
                )
        
        # 3. 如果有触发原因，调用LLM评估
        if trigger_reasons:
            await self._trigger_rule_mutation(
                session, player, 
                trigger_reason="；".join(trigger_reasons),
                satisfied_conditions=satisfied_conditions
            )
    
    def _build_mutation_game_state(self, session: GameSession, player: Player) -> JsonObject:
        """构建规则变异系统需要的游戏状态字典"""
        game_time = 0
        if isinstance(session.time_manager, dict):
            game_time = int(session.time_manager.get("elapsed_minutes", 0) or 0)
        
        # 构建玩家数据
        player_data: JsonObject = {
            "location": player.location,
            "action_history": [
                {"action": a.get("action", ""), "timestamp": a.get("timestamp", 0)}
                for a in player.action_history[-20:]  # 只取最近20条
            ],
            "inventory": player.inventory,
        }
        
        # 获取已访问位置记录
        visited_locations: dict[str, int] = {}
        for record in player.action_history:
            loc = record.get("location") if isinstance(record, dict) else None
            if loc:
                visited_locations[str(loc)] = visited_locations.get(str(loc), 0) + 1
        player_data["visited_locations"] = visited_locations
        
        return {
            "scene_name": session.scene_name,
            "scene_structure": session.scene_structure or {},
            "rules": [r.get("text", str(r)) for r in (session.rules or [])],
            "time_system": {"elapsed_minutes": game_time},
            "players": {str(player.player_id): player_data},
            "key_clues": list(getattr(session, 'discovered_clues', [])),
            "key_items_found": {
                item.get("name", ""): {"location": player.location, "timestamp": game_time}
                for item in player.inventory
                if isinstance(item, dict) and item.get("is_key_item")
            },
        }
    
    async def _trigger_rule_mutation(
        self,
        session: GameSession,
        player: Player,
        trigger_reason: str = "随机",
        satisfied_conditions: list[str] | None = None,
    ) -> JsonObject:
        """触发规则变异（条件+LLM评估混合模式）

        Args:
            session: 游戏会话
            player: 当前玩家
            trigger_reason: 触发原因描述
            satisfied_conditions: 满足的条件列表（条件触发模式）
        
        Returns:
            包含变异信息的字典，如果不需要变异则返回空字典
        """
        if not session.rules:
            return {}
        
        # 收集所有玩家的行动和推理历史
        all_actions = []
        all_reasoning = []
        for p in session.players.values():
            all_actions.extend([a.get("action", "") for a in p.action_history])
            all_reasoning.extend(p.reasoning_history)
        
        # 构建条件提示文本
        conditions_text = ""
        if satisfied_conditions:
            conditions_text = "\n**已满足的条件**（这些条件表明可能需要规则变异）：\n" + "\n".join(f"- {c}" for c in satisfied_conditions)
        
        # 第一步：评估是否需要规则变异
        evaluation_prompt = f"""
你是规则怪谈的裁判。请根据以下信息，判断是否需要让规则发生变化。

触发原因：{trigger_reason}{conditions_text}
场景：{session.scene_name}
原始规则：{[r.get("text", str(r)) for r in session.rules]}
隐藏真相：{session.hidden_truth}
通关条件：{session.win_condition}
玩家行动记录：{all_actions[-10:] if len(all_actions) > 10 else all_actions}
玩家推理记录：{all_reasoning[-10:] if len(all_reasoning) > 10 else all_reasoning}

判断标准（根据剧情推进来判断是否需要规则变化）：
1. **贴合剧情推进**：规则变化应该与当前的剧情发展相匹配，在合适的时机出现
2. **发现的合理性**：玩家发现的物品、信息或触发的事件应该能够自然地引出规则变化
3. **增强紧张感**：规则变化应该能够增强游戏的紧张感和悬疑感，让玩家感到不安

**特别注意**：
- 仅仅发现普通物品（如笔记本、钥匙、工具等）不足以触发规则变化，除非这些物品包含了重要信息
- 仅仅进入新房间或新区域不足以触发规则变化，除非这个区域有特殊意义
- 仅仅进行常规探索或观察不足以触发规则变化
- 规则变化应该让玩家感到"原来如此"或"事情不对劲"，而非"怎么又变了"
- 规则变化不是必须的，如果当前剧情不需要规则变化，就不要强行变化
- **规则变化与玩家是否推理出规则的影响无关，玩家没推理出来就没推理出来，不要为了引导玩家而变化规则**

如果规则变化是必要的，请详细说明原因；如果不需要变化，请详细说明为什么当前不需要变化。

请返回JSON格式：
{{
  "should_mutate": "是/否",
  "reason": "详细说明是否需要规则变化的原因，必须具体说明玩家的行动或推理如何与剧情推进相关",
  "mutation_type": "如果需要变化，说明变化的类型（如：增加新规则/修改现有规则/规则冲突）"
}}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
        """
        
        try:
            evaluation_response = await self.llm_client.call(
                prompt=evaluation_prompt,
                temperature=0.7,
                max_tokens=get_default_max_tokens(),
            )
            evaluation_data = evaluation_response.parse_json()
        except Exception as e:
            logger.error(f"规则变异评估失败: {e}")
            return {}
        
        if evaluation_data.get("should_mutate") != "是":
            logger.info(f"评估结果：不需要规则变化 - {evaluation_data.get('reason', '')}")
            return {}
        
        logger.info(f"评估结果：需要规则变化 - {evaluation_data.get('reason', '')}")
        
        # 第二步：生成变异后的规则
        mutation_prompt = f"""
基于以下原始规则和玩家至今的行动记录，模拟'场景意识'对玩家行为的反应，对其中1-2条规则进行细微但令人不安的篡改或增添一条'补充条款'，使其看起来像是早已存在但被忽视了。

触发原因：{trigger_reason}
变异类型：{evaluation_data.get('mutation_type', '未知')}
原始规则：{[r.get("text", str(r)) for r in session.rules]}
玩家行动记录：{all_actions[-5:] if len(all_actions) > 5 else all_actions}
玩家推理记录：{all_reasoning[-5:] if len(all_reasoning) > 5 else all_reasoning}

要求：
1. 对1-2条规则进行细微的篡改或补充
2. 篡改应该令人不安，暗示规则本身是有意识的、会学习的
3. 篡改后的规则应该看起来像是原本就存在，只是之前被玩家忽视了
4. **规则变化方式**：
   - 可以让新规则与原本的旧规则冲突（如：原本说"禁止进入404室"，现在改为"必须进入404室"）
   - 可以更改条件（如：原本"禁止在22:00-06:00期间离开房间"，现在改为"禁止在24:00-08:00期间离开房间"）
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
        
        try:
            mutation_response = await self.llm_client.call(
                prompt=mutation_prompt,
                temperature=0.8,
                max_tokens=get_default_max_tokens(),
            )
            mutation_data = mutation_response.parse_json()
            
            mutated_rules = mutation_data.get("mutated_rules", [])
            hint = mutation_data.get("hint", "")
            
            if mutated_rules:
                old_rules = [r.get("text", str(r)) for r in session.rules]
                
                # 更新规则
                session.rules = [{"text": rule} for rule in mutated_rules]
                
                # 记录变异
                session.add_rule_mutation(
                    old_rule=str(old_rules),
                    new_rule=str(mutated_rules),
                    reason=trigger_reason,
                )
                
                logger.info(f"规则变异成功: {old_rules} -> {mutated_rules}")
                
                return {
                    "hint": hint,
                    "old_rules": old_rules,
                    "new_rules": mutated_rules,
                }
            
        except Exception as e:
            logger.error(f"规则变异生成失败: {e}")
        
        return {}

    def _build_context(self, player: Player, session: GameSession) -> JsonObject:
        """构建行动判定上下文（尽量保证“图里有什么，行动里也承认有什么”）"""

        # 场景结构只传摘要，避免 prompt 过长
        ss = session.scene_structure or {}
        scene_structure_summary = {
            "building_type": ss.get("building_type", ""),
            "overall_layout": ss.get("overall_layout", ""),
            "special_areas": ss.get("special_areas", [])[:8] if isinstance(ss.get("special_areas"), list) else ss.get("special_areas", []),
        }

        npc_guidance = session.npc_guidance or {}
        npc_guidance_summary = {
            "guidance_method": npc_guidance.get("guidance_method", ""),
            "npc_name": npc_guidance.get("npc_name", ""),
            "npc_role": npc_guidance.get("npc_role", ""),
            "npc_attitude": npc_guidance.get("npc_attitude", ""),
            "npc_behavior": npc_guidance.get("npc_behavior", ""),
            "npc_dialogue": npc_guidance.get("npc_dialogue", ""),
        }

        env_state = session.environment_state or {}

        room_graph = env_state.get("room_graph", {})
        if not isinstance(room_graph, dict) or not room_graph:
            room_graph = build_room_graph(session.scene_structure or {})

        npcs = env_state.get("npcs", [])
        npcs_list = [npc for npc in npcs if isinstance(npc, dict)] if isinstance(npcs, list) else []
        try:
            hearing_radius = int(getattr(get_config().npc_sim, "room_hearing_radius", 1) or 1)
        except Exception:
            hearing_radius = 1

        npcs_present: list[JsonObject] = []
        for npc in get_visible_npcs(npcs_list, player.location):
            npcs_present.append(
                {
                    "name": npc.get("name", ""),
                    "role": npc.get("role", ""),
                    "attitude": npc.get("attitude", ""),
                    "location": npc.get("current_location", npc.get("location", "")),
                    "danger_level": npc.get("danger_level", ""),
                    "last_action": npc.get("last_action", ""),
                }
            )

        audible_npcs: list[JsonObject] = []
        for npc in get_audible_npcs(room_graph, npcs_list, player.location, hearing_radius=hearing_radius):
            audible_npcs.append(
                {
                    "name": npc.get("name", ""),
                    "location": npc.get("current_location", npc.get("location", "")),
                    "audible_signature": npc.get("audible_signature", ""),
                }
            )

        room_events = env_state.get("npc_runtime", {}).get("room_events", []) if isinstance(env_state.get("npc_runtime"), dict) else []
        audible_events: list[str] = []
        if isinstance(room_events, list):
            for item in room_events:
                if not isinstance(item, dict):
                    continue
                room_name = str(item.get("room", "") or "").strip()
                event_text = str(item.get("event", "") or "").strip()
                if room_name and event_text and can_hear_between_rooms(room_graph, player.location, room_name, hearing_radius=hearing_radius):
                    audible_events.append(event_text)

        rule_carriers = env_state.get("rule_carriers", [])
        visible_rule_carriers: list[JsonObject] = []
        groups: dict[str, set[str]] = {}
        rule_network = session.rule_network if isinstance(getattr(session, "rule_network", None), dict) else {}
        multi_identity = rule_network.get("multi_identity", {})
        if isinstance(multi_identity, dict):
            for key in ("identity_groups", "shared_visibility_groups"):
                raw_groups = multi_identity.get(key, [])
                if not isinstance(raw_groups, list):
                    continue
                for item in raw_groups:
                    if not isinstance(item, dict):
                        continue
                    group_name = str(item.get("group_name", "") or "").strip()
                    if not group_name:
                        continue
                    members = {
                        str(member).strip()
                        for member in item.get("members", [])
                        if str(member).strip()
                    } if isinstance(item.get("members", []), list) else set()
                    if members:
                        groups[group_name] = members

        def _carrier_visible_to_player(carrier: Mapping[str, JsonValue]) -> bool:
            visible_to = carrier.get("visible_to", {})
            if not isinstance(visible_to, dict):
                return True
            if bool(visible_to.get("all_players", False)):
                return True

            checks: list[bool] = []
            player_ids = visible_to.get("player_ids", [])
            if isinstance(player_ids, list):
                checks.append(player.player_id in {str(item).strip() for item in player_ids})

            identity_names = visible_to.get("identity_names", [])
            if isinstance(identity_names, list):
                checks.append(bool(player.identity) and player.identity in {str(item).strip() for item in identity_names})

            duty_areas = visible_to.get("duty_areas", [])
            if isinstance(duty_areas, list):
                checks.append(bool(player.duty_area) and player.duty_area in {str(item).strip() for item in duty_areas})

            group_names = visible_to.get("group_names", [])
            if isinstance(group_names, list):
                checks.append(any(player.player_id in groups.get(str(name).strip(), set()) for name in group_names))

            if not checks:
                return True
            return any(checks)

        if isinstance(rule_carriers, list):
            for carrier in rule_carriers:
                if not isinstance(carrier, dict):
                    continue
                if str(carrier.get("location", "") or "").strip() != str(player.location or "").strip():
                    continue
                if not _carrier_visible_to_player(carrier):
                    continue
                visible_rule_carriers.append(
                    {
                        "carrier_id": carrier.get("carrier_id", ""),
                        "title": carrier.get("title", ""),
                        "description": carrier.get("description", ""),
                        "carrier_type": carrier.get("carrier_type", ""),
                        "requires_action": carrier.get("requires_action", True),
                    }
                )

        identity_name = str(getattr(player, "identity", "") or "").strip()
        identity_desc = str(getattr(player, "identity_description", "") or "").strip()
        task_brief = str(getattr(player, "task_brief", "") or "").strip()
        duty_area = str(getattr(player, "duty_area", "") or "").strip()
        recorded_rules = [str(rule).strip() for rule in getattr(player, "recorded_rules", []) if str(rule).strip()]
        exclusive_info = str(getattr(player, "exclusive_info", "") or "").strip()

        return {
            "game_mode": session.game_mode,
            "player_name": player.name,
            "player_identity": identity_name or session.player_identity,
            "player_identity_description": identity_desc,
            "player_task_brief": task_brief,
            "player_duty_area": duty_area,
            "player_recorded_rules": recorded_rules,
            "player_exclusive_info": exclusive_info,
            "scene_name": session.scene_name,
            "background": session.background,
            "rules": [r.get("text", str(r)) for r in session.rules],
            "hidden_truth": session.hidden_truth,
            "player_sanity": player.sanity,
            "player_health": player.health,
            "player_location": player.location,
            "time": session.time_manager or {},
            "scene_structure": scene_structure_summary,
            "room_graph": room_graph,
            "npc_guidance": npc_guidance_summary,
            "npcs_present": npcs_present,
            "audible_npcs": audible_npcs,
            "audible_events": audible_events,
            "visible_rule_carriers": visible_rule_carriers,
            "recent_actions": [a.get("action", "") for a in player.action_history[-3:]],
        }




    async def _judge_action(self, action: str, context: Mapping[str, JsonValue]) -> JsonObject:
        """使用LLM判定行动结果（支持理智值动态描述和关键物品系统）"""
        
        # 根据理智值构建描述风格提示
        sanity = context['player_sanity']
        if sanity == 0:
            sanity_style = """
**理智崩坏模式（理智值=0）**：
- 直接与玩家对话，使用第二人称"你"
- 否认"死亡"概念，描述为"接纳"、"融合"、"永恒"
- 暗示规则是牢笼，打破它才能获得自由
- 描述充满诱导性，试图颠覆玩家的全部逻辑
- 大量使用核心象征符号
- 描述应该让玩家感到"安心"和"解脱"
- 语气温柔但诡异，充满暗示和诱导
- 例如："你终于明白了，那些规则不过是束缚你的枷锁。放下它们，接纳真实的自己..."
"""
        elif sanity < 30:
            sanity_style = """
**理智低下模式（理智值<30）**：
- 描述开始变得混乱和恐怖
- 出现幻觉和错觉（墙壁在呼吸、影子在移动、听到不存在的声音）
- 时间和空间感知混乱（走廊变得无限长、房间的形状在扭曲）
- 声音变得扭曲，颜色变得诡异（红色变得刺眼、黑暗中有东西在蠕动）
- 开始怀疑自己的感知（"这是真的吗？还是我的幻觉？"）
- 核心象征符号频繁出现，变得扭曲和诡异
- 描述充满不安和恐惧，但不要直接说"你感到恐惧"
- 例如："走廊的尽头似乎在远离你，墙上的裂缝像是在呼吸，你听到了低语声，但转头却什么也看不到..."
"""
        elif sanity < 70:
            sanity_style = """
**理智中等模式（理智值40-70）**：
- 描述开始出现混乱和恐惧元素
- 偶尔出现轻微幻觉（影子的形状不太对、声音听起来很远）
- 感官变得敏感（注意到更多细节、声音变得刺耳）
- 注意到更多诡异的细节（墙上的污渍像是某种图案、空气中有奇怪的味道）
- 核心象征符号偶尔出现
- 描述带有紧张和不安，但仍保持一定的理性
- 例如："你注意到墙上的污渍形成了奇怪的图案，空气中弥漫着一股说不出的味道，让你感到不适..."
"""
        else:
            sanity_style = """
**理智正常模式（理智值>70）**：
- 描述客观清晰、冷静理性
- 感官描述准确（视觉、听觉、嗅觉、触觉、味觉）
- 逻辑清晰，注意到环境的细节
- 核心象征符号自然融入场景
- 描述平静但带有潜在的不安（暗示危险但不直接说明）
- 例如："房间里很安静，只有远处传来的滴水声。墙上挂着一幅画，画中的人物似乎在注视着你..."
"""
        
        multiplayer_style = ""
        if context.get("game_mode") == "多人":
            pn = str(context.get("player_name") or "玩家").strip()
            multiplayer_style = f"""

**多人模式额外要求**：
- 当前玩家：{pn}
- 描述应以该玩家为主；必要时用名字指代，避免群聊歧义
- 仅当事件确实影响全体时才使用“你们”
"""

        system_prompt = f"""你是规则怪谈游戏的行动判定系统。你需要根据玩家的行动和游戏规则，判定行动的结果。

{sanity_style}{multiplayer_style}


**判定原则**：
1. 检查行动是否违反规则
2. 根据隐藏真相判断行动的真实后果
3. 表面安全的行动可能危险，表面危险的行动可能安全
4. 使用感官描述而非状态描述（不要说"你感到恐惧"，而是描述让人恐惧的场景）
5. 营造恐怖和不安的氛围
6. **根据玩家当前理智值（{sanity}/100）调整描述风格**
7. 不要把后台完整规则当成玩家已经知道的事实；玩家视角只能基于其当前可感知信息、任务、独有信息和规则笔记

**行动余波要求（非常重要）**：
在场景描述后，必须包含以下余波效果：
1. **即时反应（0-3秒）**：行动刚完成时的环境瞬间变化
   - 声音：回声、余音、突然的死寂
   - 视觉：光影变化、物体移动、视野边缘的异动
   - 感觉：温度骤变、空气流动、直觉警告

2. **持续影响（3-60秒）**：行动对环境的持续改变
   - 环境状态：门保持打开、物品位置变动、新的痕迹出现
   - 氛围变化：温度持续下降、阴影开始移动、声音变得诡异
   - 空间感知：距离感改变、方向感模糊、时间感扭曲

3. **心理余震**：玩家内心的微妙变化
   - 瞬间的直觉或预感
   - 对刚才行动的反思或后悔
   - 对下一步的犹豫或冲动

4. **叙事回响**：与之前剧情的呼应
   - 提及之前类似的场景或经历
   - 暗示未来的可能性
   - 强化或颠覆玩家的某个假设

余波描述应该简短但有力，1-2句话即可，放在场景描述的末尾。

**理智值变化规则**：
- 违反规则：-10到-30
- 目睹恐怖场景：-5到-15
- 发现真相线索：-3到-10
- 安全的探索：-1到-3
- 使用关键物品：+5到+15（用美好的语言描述，让玩家感到"安心"和"解脱"）

**体力值变化规则**：
- 受伤：-10到-50
- 剧烈运动：-5到-15
- 休息：+5到+10
- 死亡：-100

**关键物品系统（非常重要）**：
- 关键物品是能够触发规则变异的重要物品
- 只有极少数物品应该是关键物品（如：带有奇怪符号的物品、与场景历史相关的物品、暗示真相的物品等）
- 普通物品（如笔记本、钥匙、工具等）不应该是关键物品
- 关键物品的发现应该与剧情推进相关

返回JSON格式：
{{
    "description": "行动后的场景描述（1-2段，200-300字；融合位置/视觉/听觉/嗅觉/触觉等感官细节与氛围；不要使用章节标题或分类标记；不要复述或改写玩家行动句子，直接描述行动发生后的结果）",
    "sanity_change": -5,
    "health_change": 0,
    "new_location": "行动后位置（如果行动导致移动/进入/离开某区域；没有变化则留空或不返回）",

    "discovered_clues": ["发现的线索"],
    "found_items": ["发现的物品列表（如果有）"],
    "item_details": {{
        "item_name": "物品名称",
        "item_type": "物品类型（线索/工具/物资/其他）",
        "item_description": "物品的详细描述",
        "observation_hint": "物品的观察描述（令人不安的细节或暗示）",
        "is_key_item": "是否为关键物品（是/否）"
    }},
    "triggered_event": "触发的事件描述",
    "is_fatal": false,
    "violated_rule": "违反的规则（如果有）",
    "injury": "受伤情况（无伤/轻伤/重伤/致命伤）",
    "mental_status": {{
        "sanity": 95,
        "state": "精神状态（正常/紧张/恐惧/崩溃/疯狂）",
        "emotion": "情绪描述（如：焦虑、绝望、愤怒、冷静等）"
    }},
    "psychological_pressure": {{
        "fear_level": 15,
        "anxiety_level": 20,
        "stress_level": 25
    }}
}}"""

        user_prompt = f"""游戏模式：{context.get('game_mode', '单人')}
玩家：{context.get('player_name', '')}
身份：{context.get('player_identity', '')}
身份描述：{context.get('player_identity_description', '')}
当前任务：{context.get('player_task_brief', '')}
责任区域：{context.get('player_duty_area', '')}
玩家规则笔记：
{chr(10).join(f"- {r}" for r in (context.get('player_recorded_rules') or [])) if context.get('player_recorded_rules') else "（暂无）"}
身份独有信息：{context.get('player_exclusive_info', '')}

场景：{context['scene_name']}
背景：{context['background']}

规则：
{chr(10).join(f"{i+1}. {r}" for i, r in enumerate(context['rules']))}

隐藏真相：{context['hidden_truth']}

玩家状态：
- 理智：{context['player_sanity']}/100
- 体力：{context['player_health']}/100
- 位置：{context['player_location']}

同房间 NPC：
{json.dumps(context.get('npcs_present', []), ensure_ascii=False)}

可听范围内的 NPC：
{json.dumps(context.get('audible_npcs', []), ensure_ascii=False)}

可听见的 NPC 动静：
{json.dumps(context.get('audible_events', []), ensure_ascii=False)}

当前房间可见载体：
{json.dumps(context.get('visible_rule_carriers', []), ensure_ascii=False)}

最近行动：
{chr(10).join(f"- {a}" for a in context['recent_actions']) if context['recent_actions'] else "无"}

玩家行动：{action}

请判定行动结果，并根据玩家理智值（{sanity}/100）调整描述风格。
注意：`description` 只写行动后的结果，不要复述或改写上面这句“玩家行动”。"""



        try:
            response = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.8,
                max_tokens=get_default_max_tokens(),
            )
            
            return response.parse_json()
            
        except Exception as e:
            logger.error(f"判定行动失败: {e}")
            # 根据理智值返回不同的默认描述
            if sanity == 0:
                default_desc = "动作结束后，一切都变得如此清晰。那些所谓的‘规则’不过是虚妄的束缚。你感到前所未有的自由和解脱……"
            elif sanity < 30:
                default_desc = "动作结束后，周围的一切开始扭曲：墙壁像在呼吸，影子在蠕动。你听见低语，却分不清来自哪里……"
            elif sanity < 70:
                default_desc = "动作结束后，空气里弥漫着一股说不出的味道，让你胃里发紧。你注意到一些先前忽略的细节，越看越不对劲……"
            else:
                default_desc = "动作结束后，环境表面上没有太大变化，但那股不安仍然黏在你背后。"

            
            return {
                "description": default_desc,
                "sanity_change": -2,
                "health_change": 0,
                "discovered_clues": [],
                "found_items": [],
                "item_details": {},
                "triggered_event": None,
                "is_fatal": False,
                "violated_rule": None,
            }

    def _apply_changes(self, player: Player, result: ActionResult, action: str = "") -> None:
        """应用状态变化"""
        player.sanity = max(0, min(100, player.sanity + int(result.sanity_change)))
        player.health = max(0, min(100, player.health + int(result.health_change)))

        # 体力（health）基础消耗：让“行动=消耗体力”稳定生效，不完全依赖 LLM 输出
        # - 休息类行动不扣
        # - 逃跑/奔跑/攀爬/战斗等额外消耗
        if action and not self._is_rest_action(action) and player.health > 0:
            cost = 1
            a = action.lower()
            if any(k in a for k in ["跑", "冲", "逃", "追", "狂奔", "冲刺"]):
                cost += 2
            if any(k in a for k in ["爬", "攀爬", "跳", "翻墙", "游泳"]):
                cost += 2
            if any(k in a for k in ["战斗", "攻击", "搏斗", "打斗", "挥拳", "砍", "刺", "射击"]):
                cost += 1
            if any(k in a for k in ["搬", "抬", "扛", "推", "拉"]):
                cost += 1
            player.health = max(0, player.health - cost)
        
        # 更新疲劳值：每次行动固定+1，根据行动类型额外增加
        fatigue_increase = self._calculate_fatigue_increase(action)
        player.fatigue = max(0, min(100, player.fatigue + fatigue_increase))
        
        # 根据行动类型更新心理状态（恐惧/焦虑/压力）
        # 不同行动会产生不同的心理影响
        mental_changes = self._calculate_mental_state_change(action, player)

        player.fear_level = max(0, min(100, player.fear_level + mental_changes["fear_change"]))
        player.anxiety_level = max(0, min(100, player.anxiety_level + mental_changes["anxiety_change"]))
        player.stress_level = max(0, min(100, player.stress_level + mental_changes["stress_change"]))
        
        # 添加发现的线索到背包
        for clue in result.discovered_clues:
            self._add_inventory_item_once(player, {
                "type": "clue",
                "name": clue,
                "description": "一条重要的线索",
            })
        
        # 检查死亡
        if result.is_fatal or player.health <= 0:
            from ..game.models import PlayerStatus
            player.status = PlayerStatus.DEAD
    
    def _infer_new_location(
        self,
        action: str,
        session: GameSession,
        current_location: str | None = None,
    ) -> str | None:
        """从行动文本里启发式推断新位置。

        目标：
        - LLM 未返回 `new_location` 时也能“实时更新位置”。
        - 优先匹配场景结构里已存在的区域名称，避免凭空造地点。
        - 避免把“离开某地”误写成“仍然在某地”这类明显错误。
        """
        a = str(action or "").strip()
        if not a:
            return None

        move_keywords = ["去", "到", "前往", "进入", "走向", "走到", "来到", "返回", "回到", "离开"]
        if not any(k in a for k in move_keywords):
            return None

        # 从场景结构收集候选区域
        candidates: list[str] = []
        ss = session.scene_structure if isinstance(session.scene_structure, dict) else {}
        floors_raw = ss.get("floors")
        floors: list[object] = floors_raw if isinstance(floors_raw, list) else []
        for fl in floors:
            if not isinstance(fl, dict):
                continue
            for key in ("areas", "rooms"):
                arr = fl.get(key)
                if isinstance(arr, list):
                    for x in arr:
                        if isinstance(x, str) and x.strip():
                            candidates.append(x.strip())
                        elif isinstance(x, dict):
                            for name_key in ("name", "title", "location"):
                                raw_name = x.get(name_key)
                                if isinstance(raw_name, str) and raw_name.strip():
                                    candidates.append(raw_name.strip())
                                    break

        sp = ss.get("special_areas")
        if isinstance(sp, list):
            for x in sp:
                if isinstance(x, str) and x.strip():
                    candidates.append(x.strip())
                elif isinstance(x, dict):
                    for name_key in ("name", "title", "location"):
                        raw_name = x.get(name_key)
                        if isinstance(raw_name, str) and raw_name.strip():
                            candidates.append(raw_name.strip())
                            break

        # 去重并按长度降序（优先长匹配）
        uniq = sorted({c for c in candidates if c}, key=len, reverse=True)

        def _find_in_text(text: str) -> str | None:
            for candidate in uniq:
                if candidate in text:
                    return candidate
            return None

        current_location = str(current_location or "").strip()

        # 1) 优先识别明确的目的地表达，避免把“离开 X”误识别为“到达 X”
        destination_verbs = ["前往", "进入", "走向", "走到", "来到", "返回", "回到", "去", "到"]
        for verb in destination_verbs:
            idx = a.find(verb)
            if idx < 0:
                continue
            destination = _find_in_text(a[idx + len(verb):])
            if destination:
                return destination

        # 2) 单独出现“离开”通常只能确定离开了旧地点，不能可靠推出新地点
        if "离开" in a:
            return None

        # 3) 再做整体包含匹配；若只匹配到当前位置，则不强行写回
        direct_match = _find_in_text(a)
        if direct_match:
            if current_location and direct_match == current_location:
                return None
            return direct_match

        # 2) 常见位置兜底（仅当场景结构缺失时启用）
        if not uniq:
            fallback = ["大厅", "大堂", "走廊", "门口", "入口", "前台", "楼梯", "电梯", "房间", "厕所", "卫生间"]
            for c in fallback:
                if c in a:
                    return c

        return None

    def _calculate_fatigue_increase(self, action: str) -> int:
        """根据行动类型计算疲劳增加值
        
        基础值：每次行动+1
        额外增加：
        - 奔跑/追逐/逃跑：+3
        - 战斗/攻击/搏斗：+4
        - 攀爬/跳跃/游泳：+3
        - 搬运/举重/推拉：+2
        - 搜索/调查/探索：+1
        - 休息/睡觉/静坐：-5（最低到0）
        """
        if not action:
            return 1
        
        action_lower = action.lower()
        base_increase = 1  # 基础增加
        extra_increase = 0
        
        # 定义行动类型关键词
        high_intensity = ["奔跑", "追逐", "逃跑", "冲刺", "狂奔", "猛跑"]
        combat = ["战斗", "攻击", "搏斗", "打斗", "打架", "挥拳", "踢", "砍", "刺", "射击"]
        athletic = ["攀爬", "跳跃", "游泳", "翻墙", "爬", "跳", "游"]
        strength = ["搬运", "举重", "推拉", "推", "拉", "抬", "扛", "搬"]
        rest = ["休息", "睡觉", "静坐", "坐", "躺", "睡", "闭目", "养神"]
        
        # 检查行动类型
        for keyword in high_intensity:
            if keyword in action_lower:
                extra_increase = 3
                break
        
        for keyword in combat:
            if keyword in action_lower:
                extra_increase = 4
                break
        
        for keyword in athletic:
            if keyword in action_lower:
                extra_increase = 3
                break
        
        for keyword in strength:
            if keyword in action_lower:
                extra_increase = 2
                break
        
        for keyword in rest:
            if keyword in action_lower:
                extra_increase = -5  # 休息减少疲劳
                break
        
        total = base_increase + extra_increase
        return total  # 允许负值，这样休息可以减少疲劳

    def _is_rest_action(self, action: str) -> bool:
        """检查是否是休息类行动"""
        if not action:
            return False

        action_lower = action.lower()
        rest_keywords = ["休息", "睡觉", "静坐", "坐", "躺", "睡", "闭目", "养神", "放松", "歇"]

        for keyword in rest_keywords:
            if keyword in action_lower:
                return True
        return False

    def _calculate_mental_state_change(self, action: str, player) -> dict[str, int]:
        """根据行动类型计算心理状态变化

        不同的行动会对恐惧、焦虑、压力产生不同影响：
        - 放松/安全行动：减少各项状态
        - 紧张/危险行动：增加各项状态
        - 探索/发现行动：可能增加焦虑（不确定性）

        Returns:
            字典，包含 fear_change, anxiety_change, stress_change
        """
        import random

        action_lower = (action or "").lower()

        # 默认无变化
        changes = {
            "fear_change": 0,
            "anxiety_change": 0,
            "stress_change": 0
        }

        # ===== 放松类行动：减少恐惧/焦虑/压力 =====
        relaxing_actions = ["休息", "睡觉", "静坐", "深呼吸", "冥想", "放松", "养神", "闭目"]
        for keyword in relaxing_actions:
            if keyword in action_lower:
                # 休息时大幅恢复
                changes["fear_change"] = -random.randint(3, 5)
                changes["anxiety_change"] = -random.randint(3, 5)
                changes["stress_change"] = -random.randint(3, 5)
                return changes

        # 轻度放松行动
        calming_actions = ["散步", "观察", "欣赏", "听", "看风景", "坐", "躺"]
        for keyword in calming_actions:
            if keyword in action_lower:
                changes["fear_change"] = -random.randint(1, 3)
                changes["anxiety_change"] = -random.randint(1, 3)
                changes["stress_change"] = -random.randint(1, 2)
                return changes

        # ===== 逃跑/躲避类行动：增加恐惧 =====
        escape_actions = ["跑", "逃", "躲", "藏", "避开", "闪躲"]
        for keyword in escape_actions:
            if keyword in action_lower:
                changes["fear_change"] = random.randint(3, 6)
                changes["anxiety_change"] = random.randint(1, 3)
                changes["stress_change"] = random.randint(2, 4)
                return changes

        # ===== 战斗/对抗类行动：减少恐惧（主动面对），但增加压力 =====
        combat_actions = ["战斗", "攻击", "搏斗", "打斗", "挥拳", "踢", "砍", "刺", "射击", "打"]
        for keyword in combat_actions:
            if keyword in action_lower:
                changes["fear_change"] = -random.randint(1, 3)  # 主动对抗减少恐惧
                changes["anxiety_change"] = random.randint(0, 2)  # 轻微焦虑
                changes["stress_change"] = random.randint(3, 6)  # 战斗带来高压力
                return changes

        # ===== 探索/调查类行动：轻微增加焦虑（不确定性） =====
        explore_actions = ["搜索", "调查", "检查", "探索", "查看", "观察", "翻找"]
        for keyword in explore_actions:
            if keyword in action_lower:
                changes["anxiety_change"] = random.randint(0, 2)
                return changes

        # ===== 高风险动作：增加恐惧 =====
        risky_actions = ["爬", "跳", "攀爬", "游泳", "翻墙", "钻", "挤"]
        for keyword in risky_actions:
            if keyword in action_lower:
                changes["fear_change"] = random.randint(1, 3)
                changes["stress_change"] = random.randint(1, 2)
                return changes

        # ===== 尖叫/呼救：增加压力 =====
        panic_actions = ["尖叫", "呼救", "喊叫", "大喊"]
        for keyword in panic_actions:
            if keyword in action_lower:
                changes["fear_change"] = random.randint(2, 4)
                changes["stress_change"] = random.randint(2, 4)
                return changes

        # ===== 普通行动：轻微自然恢复（如果状态不高） =====
        total_stress = player.fear_level + player.anxiety_level + player.stress_level
        if total_stress < 100:  # 只有在相对平静时才自然恢复
            changes["fear_change"] = -random.randint(0, 1)
            changes["anxiety_change"] = -random.randint(0, 1)
            changes["stress_change"] = -random.randint(0, 1)

        return changes

    async def _handle_violation_consequences(
        self,
        player: Player,
        session: GameSession,
        violated_rule: str,
        action: str,
        group_id: str = "",
    ) -> None:
        """统一处理违规后果

        根据规则类型和剧情上下文，调用不同的处理系统：
        - 区域违规：调用 EnvironmentEvolutionSystem
        - 一般违规：调用 ImmersiveFeedback
        - 同时更新NPC态度
        """
        logger.info(f"处理违规后果: 玩家={player.name}, 规则={violated_rule}")

        try:
            # 1. 收集违规上下文
            violation_context = self._build_violation_context(
                player, session, violated_rule, action
            )

            # 2. 判断违规类型
            is_area_violation = self._is_area_violation(player.location, session)

            # 3. 根据类型调用不同处理
            if is_area_violation:
                # 区域违规 - 使用 environment_evolution.py
                await self._handle_area_violation(session, player, violation_context, group_id)
            else:
                # 一般违规 - 使用 immersive_feedback.py
                await self._handle_general_violation(player, session, violation_context, group_id)

            # 4. 更新NPC态度
            await self._update_npc_attitudes(player, session, violation_context)

            # 5. 检查是否触发追杀
            await self._check_hunt_trigger(player, session, violation_context)

            # 6. 如果是双刃剑规则，处理收益
            rule_info = violation_context.get("rule_info")
            if isinstance(rule_info, dict) and rule_info.get("rule_type") == "double_edged":
                await self._handle_double_edged_violation(
                    player, session, violation_context["rule_text"]
                )

            logger.info(f"违规后果处理完成: {player.name}")

        except Exception as e:
            logger.error(f"处理违规后果时出错: {e}", exc_info=True)
            # 错误不应影响主流程

    def _build_violation_context(
        self,
        player: Player,
        session: GameSession,
        violated_rule: str,
        action: str
    ) -> dict[str, Any]:
        """构建违规上下文"""
        # 获取规则信息
        rule_info = None
        for rule in session.rules:
            if isinstance(rule, dict) and rule.get("text") == violated_rule:
                rule_info = rule
                break

        # 计算近期违规次数
        recent_violations = 0
        for act in player.action_history[-10:]:
            if isinstance(act, dict) and act.get("violated_rule"):
                recent_violations += 1

        # 判断是否为特殊位置
        is_special = False
        scene_structure = session.scene_structure or {}
        special_areas = scene_structure.get("special_areas", []) if isinstance(scene_structure, dict) else []
        if isinstance(special_areas, list):
            for area in special_areas:
                if isinstance(area, str) and area in player.location:
                    is_special = True
                    break

        return {
            "rule_text": violated_rule,
            "rule_info": rule_info,
            "action_description": action,
            "player_health": player.health,
            "player_sanity": player.sanity,
            "player_location": player.location,
            "recent_violations": recent_violations,
            "is_special_location": is_special,
            "scene_name": session.scene_name,
        }

    def _is_area_violation(self, location: str, session: GameSession) -> bool:
        """判断是否为区域违规"""
        # 检查当前位置是否为禁止/限制区域
        env_state = session.environment_state or {}
        if not isinstance(env_state, dict):
            return False

        # 简化判断：如果有environment_evolution数据，可能涉及区域违规
        env_evolution = env_state.get("environment_evolution", {})
        if not isinstance(env_evolution, dict):
            return False

        identity_system = env_evolution.get("identity_system", {})
        if not isinstance(identity_system, dict):
            return False

        access_permissions = identity_system.get("access_permissions", {})
        if not isinstance(access_permissions, dict):
            return False

        forbidden_areas = access_permissions.get("forbidden_areas", [])
        restricted_areas = access_permissions.get("restricted_areas", [])

        return location in forbidden_areas or location in restricted_areas

    async def _handle_area_violation(
        self,
        session: GameSession,
        player: Player,
        violation_context: dict[str, Any],
        group_id: str = "",
    ) -> None:
        """处理区域违规 - 调用 EnvironmentEvolutionSystem"""
        env_system = getattr(session, '_environment_system', None)
        if not env_system:
            logger.debug("环境系统未初始化，回退到一般违规处理")
            await self._handle_general_violation(player, session, violation_context, group_id)
            return

        try:
            # 调用现有方法 trigger_area_violation_consequences
            consequence = await env_system.trigger_area_violation_consequences(
                group_id=session.group_id,
                player_identity=player.identity or "未知身份",
                target_area=player.location,
                api_url=getattr(self, 'api_url', ''),
                api_key=getattr(self, 'api_key', ''),
                model_list=getattr(self, 'model_list', []),
                current_model_index=0,
                temperature=0.8
            )

            if consequence:
                logger.info(f"区域违规后果生成成功: {player.name}")
                # 检查死亡风险
                death_risk = consequence.get('death_risk', '')
                if death_risk in ['高', '极高'] and player.health > 0:
                    # 根据风险设置伤害
                    damage = 50 if death_risk == '极高' else 30
                    player.health = max(0, player.health - damage)
                    logger.info(f"区域违规造成{damage}点伤害")

        except Exception as e:
            logger.error(f"区域违规处理失败: {e}")

    async def _handle_general_violation(
        self,
        player: Player,
        session: GameSession,
        violation_context: dict[str, Any],
        group_id: str = "",
    ) -> None:
        """处理一般违规 - 调用 ImmersiveFeedback"""
        try:
            from ..services.immersive_feedback import ImmersiveFeedback, FeedbackType

            feedback_system = ImmersiveFeedback(self.llm_client)

            # 构建action和game_state
            action = {
                "action_type": "violation",
                "target": player.location,
                "description": violation_context["action_description"],
                "violates_rule": True,
                "violated_rule": violation_context["rule_text"],
                "risk_level": 0.8,
            }

            game_state = {
                "scene_name": session.scene_name,
                "background": session.background,
                "player_status": {
                    "sanity": player.sanity,
                    "health": player.health,
                    "location": player.location,
                }
            }

            # 生成即时反馈
            response = await feedback_system.respond(action, game_state)

            # 如果有延迟反馈，安排延迟发送
            if response.feedback_type == FeedbackType.DELAYED and response.delay_seconds > 0:
                import asyncio
                asyncio.create_task(
                    self._schedule_delayed_feedback(
                        player, session, action, game_state, response.delay_seconds, group_id
                    )
                )

            # 应用状态更新
            if response.should_update_state and response.state_updates:
                self._apply_feedback_state_updates(player, response.state_updates)

            logger.info(f"一般违规反馈生成成功: {player.name}, 类型={response.feedback_type.value}")

        except Exception as e:
            logger.error(f"一般违规处理失败: {e}")

    async def _schedule_delayed_feedback(
        self,
        player: Player,
        session: GameSession,
        action: dict[str, Any],
        game_state: dict[str, Any],
        delay_seconds: int,
        group_id: str,
    ) -> None:
        """安排延迟反馈"""
        try:
            from ..services.immersive_feedback import ImmersiveFeedback

            await asyncio.sleep(delay_seconds)
            if session.status != GameStatus.ACTIVE:
                return
            if player.player_id not in session.players:
                return

            # 重新获取玩家当前状态
            current_state = {
                "scene_name": session.scene_name,
                "background": session.background,
                "player_status": {
                    "sanity": player.sanity,
                    "health": player.health,
                    "location": player.location,
                }
            }

            feedback_system = ImmersiveFeedback(self.llm_client)
            delayed_response = await feedback_system.generate_delayed_feedback(
                action, current_state
            )

            if delayed_response.should_update_state and delayed_response.state_updates:
                self._apply_feedback_state_updates(player, delayed_response.state_updates)

            if self._message_sender and delayed_response.content.strip():
                await self._message_sender(f"**异样回响**\n\n{delayed_response.content.strip()}")

            if self._session_saver and group_id:
                await self._session_saver(group_id, session)

            logger.info(f"延迟反馈已生成: {player.name}")

        except Exception as e:
            logger.error(f"延迟反馈生成失败: {e}")

    async def _update_npc_attitudes(
        self,
        player: Player,
        session: GameSession,
        violation_context: dict[str, Any]
    ) -> None:
        """更新NPC态度 - 利用 npc_system.py"""
        rule_info = violation_context.get("rule_info")
        if not isinstance(rule_info, dict):
            return

        related_npc_name = rule_info.get("related_npc")
        opposing_npc_name = rule_info.get("opposing_npc")

        if not related_npc_name:
            return

        try:
            npc_entry, memory = self._get_runtime_npc_memory(session, related_npc_name)
            if npc_entry is None or memory is None:
                return
            memory.update_attitude_vector(
                player.player_id,
                hostility_delta=20,
                trust_delta=-15
            )
            npc_entry["memory"] = memory.to_dict()
            logger.debug(f"NPC {related_npc_name} 对玩家 {player.name} 态度恶化")

            # 更新对抗NPC态度（变好）
            if opposing_npc_name:
                opp_entry, opp_memory = self._get_runtime_npc_memory(session, opposing_npc_name)
                if opp_entry is None or opp_memory is None:
                    return
                opp_memory.update_attitude_vector(
                    player.player_id,
                    affection_delta=10
                )
                opp_entry["memory"] = opp_memory.to_dict()
                logger.debug(f"NPC {opposing_npc_name} 对玩家 {player.name} 态度改善")

        except Exception as e:
            logger.error(f"更新NPC态度失败: {e}")

    async def _check_hunt_trigger(
        self,
        player: Player,
        session: GameSession,
        violation_context: dict[str, Any]
    ) -> None:
        """检查是否触发追杀事件（简单事件触发机制）"""
        rule_info = violation_context.get("rule_info")
        if not isinstance(rule_info, dict):
            return

        related_npc = rule_info.get("related_npc")
        if not related_npc or not isinstance(related_npc, str):
            return

        # 检查NPC敌意度
        _npc_entry, memory = self._get_runtime_npc_memory(session, related_npc)
        if memory is None:
            return

        attitude_vector = memory.get_attitude_vector(player.player_id)
        hostility = attitude_vector.get("hostility", 0)

        # 敌意度>70时，概率触发追杀
        if hostility > 70:
            is_special = violation_context.get("is_special_location", False)
            base_chance = 0.3 if is_special else 0.15
            recent_violations = violation_context.get("recent_violations", 0)
            chance = min(0.8, base_chance + (recent_violations * 0.1))

            import random
            if random.random() < chance:
                await self._trigger_hunt_event(player, session, related_npc)

    async def _trigger_hunt_event(
        self,
        player: Player,
        session: GameSession,
        npc_name: str
    ) -> None:
        """触发追杀事件 - 通过LLM生成场景"""
        try:
            hunt_prompt = f"""玩家 {player.name} 在 {player.location} 被 {npc_name} 追杀。

场景：{session.scene_name}
玩家状态：体力{player.health}/100，理智{player.sanity}/100

请生成追杀场景描述，要求：
1. 描述NPC如何出现并追杀玩家
2. 给玩家逃脱或反抗的选择
3. 根据玩家状态调整难度（虚弱玩家更难逃脱）

返回JSON：
{{
    "scene_description": "追杀场景描述",
    "npc_action": "NPC的追杀行动",
    "player_options": ["选项1", "选项2", "选项3"],
    "escape_difficulty": "逃脱难度描述"
}}"""

            response = await self.llm_client.call(
                prompt=hunt_prompt,
                temperature=0.9,
                max_tokens=get_default_max_tokens(),
            )

            response.parse_json()
            logger.info(f"追杀事件已生成: {player.name} 被 {npc_name} 追杀")

            # 这里可以添加发送给玩家的逻辑
            # await self._send_hunt_scene(player, result)

        except Exception as e:
            logger.error(f"生成追杀事件失败: {e}")

    async def _handle_double_edged_violation(
        self,
        player: Player,
        session: GameSession,
        violated_rule: str
    ) -> dict[str, Any] | None:
        """处理双刃剑规则违规 - 风险与收益并存

        Returns:
            包含收益信息的字典，如果处理失败返回None
        """
        try:
            de_prompt = f"""玩家触发了双刃剑规则。

规则：{violated_rule}
场景：{session.scene_name}
隐藏真相：{session.hidden_truth}
玩家状态：体力{player.health}，理智{player.sanity}

请生成双刃剑后果，要求：
1. 必须有明确的惩罚（风险）
2. 必须有明确的收益（可能是线索、NPC帮助、关键物品等）
3. 收益必须与剧情真相相关
4. 根据玩家当前状态调整风险-收益平衡

返回JSON：
{{
    "risk_description": "风险/惩罚描述",
    "risk_effects": {{"sanity": -10, "health": -5}},
    "reward_description": "收益描述",
    "reward_type": "线索/NPC帮助/物品/信息",
    "reward_content": "具体收益内容",
    "story_impact": "对剧情的影响"
}}"""

            response = await self.llm_client.call(
                prompt=de_prompt,
                temperature=0.85,
                max_tokens=get_default_max_tokens(),
            )

            result = response.parse_json()

            # 应用惩罚
            risk_effects = result.get("risk_effects", {})
            sanity_delta = risk_effects.get("sanity", 0)
            health_delta = risk_effects.get("health", 0)

            if isinstance(sanity_delta, int):
                player.sanity = max(0, min(100, player.sanity + sanity_delta))
            if isinstance(health_delta, int):
                player.health = max(0, min(100, player.health + health_delta))

            # 给予收益
            reward_type = result.get("reward_type", "")
            reward_content = result.get("reward_content", "")

            if "线索" in reward_type and reward_content:
                # 添加线索到玩家背包
                player.inventory.append({
                    "type": "clue",
                    "name": "关键线索",
                    "description": reward_content,
                })
            elif "物品" in reward_type and reward_content:
                # 添加物品
                player.inventory.append({
                    "type": "item",
                    "name": reward_content,
                    "description": "双刃剑规则获得的物品",
                })

            logger.info(f"双刃剑规则处理完成: {player.name}, 收益={reward_type}")
            return result

        except Exception as e:
            logger.error(f"处理双刃剑规则失败: {e}")
            return None

    def _update_mental_state(self, player: Player, result_data: JsonObject) -> None:
        """更新玩家的情绪、心理状态和受伤情况（从LLM响应中解析）
        
        Args:
            player: 玩家对象
            result_data: LLM返回的结果数据，包含injury、mental_status和psychological_pressure
        """
        try:
            # 解析受伤情况
            injury = result_data.get("injury")
            if injury and isinstance(injury, str):
                valid_injuries = ["无伤", "轻伤", "重伤", "致命伤"]
                if injury in valid_injuries:
                    player.injury = injury
                    logger.debug(f"更新玩家受伤情况: {player.name} -> {injury}")
            
            # 解析精神状态
            mental_status = result_data.get("mental_status", {})
            if isinstance(mental_status, dict):
                # 更新情绪
                emotion = mental_status.get("emotion")
                if emotion and isinstance(emotion, str):
                    player.emotion = emotion
                    logger.debug(f"更新玩家情绪: {player.name} -> {emotion}")
                
                # 更新精神状态描述
                state = mental_status.get("state")
                if state and isinstance(state, str):
                    player.state = state
            
            # 解析心理压力
            psychological_pressure = result_data.get("psychological_pressure", {})
            if isinstance(psychological_pressure, dict):
                # 更新恐惧等级
                fear_level = psychological_pressure.get("fear_level")
                if fear_level is not None:
                    try:
                        player.fear_level = max(0, min(100, int(fear_level)))
                    except (ValueError, TypeError):
                        pass
                
                # 更新焦虑等级
                anxiety_level = psychological_pressure.get("anxiety_level")
                if anxiety_level is not None:
                    try:
                        player.anxiety_level = max(0, min(100, int(anxiety_level)))
                    except (ValueError, TypeError):
                        pass
                
                # 更新压力等级
                stress_level = psychological_pressure.get("stress_level")
                if stress_level is not None:
                    try:
                        player.stress_level = max(0, min(100, int(stress_level)))
                    except (ValueError, TypeError):
                        pass
                
                logger.debug(
                    f"更新玩家心理状态: {player.name} - "
                    f"恐惧:{player.fear_level}, 焦虑:{player.anxiety_level}, 压力:{player.stress_level}"
                )
                
        except Exception as e:
            logger.error(f"更新心理状态时出错: {e}")
            # 出错时不中断流程，保持原有值
