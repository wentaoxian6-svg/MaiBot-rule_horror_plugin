"""行动处理服务 - 处理玩家行动并生成反馈"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Any

from ...common import GameModes, SanityThresholds, TimeThresholds
from ...common.constants import (
    AnxietyThresholds,
    FatigueThresholds,
    FearThresholds,
    HealthThresholds,
    StressThresholds,
)
from ...common.door_utils import get_door_state_between
from ...common.models import JsonObject, JsonValue
from ...common.sound_utils import infer_sound_intensity
from ...systems.environment_evolution import DoorState
from ...systems.npc_system import NPCMemory
from ...systems.room_topology import (
    WallMaterial,
    build_room_graph,
    can_hear_between_rooms,
    find_shortest_path,
    get_audible_npcs,
    get_coop_action_bonus,
    get_obstacles_for_room,
    get_visible_npcs,
    get_wall_material,
    is_adjacent_room,
    is_dual_player_coop_eligible,
    is_same_room,
    _normalize_area,
)
from ..config import get_config
from ..llm.client import LLMClient, get_default_max_tokens

from ..game.models import GameSession, GameStatus, Player, PlayerStatus, Rule
from .item_manager import ItemManager
from .npc_interaction import NPCInteractionService
from .npc_simulator import NPCSimulator
from .player_interaction import PlayerInteractionService
from .psychological_state import PsychologicalStateService
from .pvp_combat import PvPCombatService
from .rule_mutation import RuleMutationService
from .violation_consequence import ViolationConsequenceService

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
        # 心理状态与 PVP 战斗计算服务（facade 模式：委托调用，避免本类膨胀）
        self._psych_state: PsychologicalStateService = PsychologicalStateService()
        self._pvp: PvPCombatService = PvPCombatService()
        # Task 13：NPC 对话改走 LLM，复用 NPCSimulator 的 generate_dialogue_llm
        self._npc_simulator: NPCSimulator = NPCSimulator(self.llm_client)
        # Task 27：从 action_processor 抽离的四个服务（facade 模式：委托调用，避免本类膨胀）
        self._npc_interaction: NPCInteractionService = NPCInteractionService(
            self._npc_simulator, psych_state=self._psych_state,
        )
        self._player_interaction: PlayerInteractionService = PlayerInteractionService(self._pvp)
        self._violation: ViolationConsequenceService = ViolationConsequenceService(
            llm_client=self.llm_client,
            message_sender=self._message_sender,
            session_saver=self._session_saver,
            find_runtime_npc=self._find_runtime_npc,
            get_runtime_npc_memory=self._get_runtime_npc_memory,
        )
        self._rule_mutation: RuleMutationService = RuleMutationService(self.llm_client)

    @staticmethod
    def _normalize_rule_text_for_dedup(text: str) -> str:
        """规则文本归一化（委托给 ``NPCInteractionService``）。"""
        return NPCInteractionService.normalize_rule_text_for_dedup(text)

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

    def _record_rule_texts(self, player: Player, rule_texts: list[str]) -> int:
        """规则文本去重写入玩家笔记（委托给 ``NPCInteractionService``）。"""
        return self._npc_interaction.record_rule_texts(player, rule_texts)

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
        """应用沉浸式反馈带来的额外状态变化（委托给 ``ViolationConsequenceService``）。"""
        self._violation.apply_feedback_state_updates(player, updates)

    @staticmethod
    def _infer_action_time_cost(action: str) -> int:
        """根据行动文本推断普通行动的游戏时间消耗（分钟）。

        匹配规则（按优先级，首个匹配即返回）：
        - 搜索/查找/翻找/搜寻/找 -> 10 分钟
        - 移动/前往/走到/去/进入/离开 -> 5 分钟
        - 调查/检查/观察/查看/研究/分析 -> 15 分钟
        - 对话/询问/告诉/问/说/喊 -> 5 分钟
        - 其他 -> 8 分钟

        Args:
            action: 行动描述文本

        Returns:
            该行动消耗的游戏时间（分钟），恒为正整数
        """
        if not action:
            return 8
        # 关键词匹配按行动语义分组，避免单字误匹配（如"问题"误触发"问"）
        if any(kw in action for kw in ["搜索", "查找", "翻找", "搜寻", "找"]):
            return 10
        if any(kw in action for kw in ["移动", "前往", "走到", "进入", "离开", "返回", "回到"]):
            return 5
        if any(kw in action for kw in ["调查", "检查", "观察", "查看", "研究", "分析"]):
            return 15
        if any(kw in action for kw in ["对话", "询问", "告诉", "喊"]):
            return 5
        # "问"单字风险较高（"问题/问号"），仅在未被前面规则命中时作为兜底
        if "问" in action or "说" in action:
            return 5
        return 8

    @staticmethod
    def _update_time_phase(session: GameSession, elapsed_minutes: int) -> None:
        """根据游戏内累计分钟数更新时间描述与时段标签（五段制）。

        五段划分（与 TimeThresholds 常量对齐）：
        - < MIDNIGHT(60)         -> 开场后
        - < DAWN(180)            -> 数小时后
        - < EARLY_MORNING(300)   -> 深夜
        - < MORNING(420)         -> 午夜
        - >= MORNING(420)        -> 黎明前

        Args:
            session: 游戏会话（time_manager 字段会被原地更新）
            elapsed_minutes: 自开局以来累计经过的分钟数
        """
        if not isinstance(session.time_manager, dict):
            return

        if elapsed_minutes < TimeThresholds.MIDNIGHT:
            session.time_manager["current_time"] = "开场后"
            session.time_manager["time_description"] = "距离开场过去了不到一小时"
            session.time_manager["time_phase"] = "opening"
        elif elapsed_minutes < TimeThresholds.DAWN:
            session.time_manager["current_time"] = "数小时后"
            session.time_manager["time_description"] = "场所仍按自身的时间节奏运行"
            session.time_manager["time_phase"] = "midnight"
        elif elapsed_minutes < TimeThresholds.EARLY_MORNING:
            session.time_manager["current_time"] = "深夜"
            session.time_manager["time_description"] = "夜色更深，所有声音都变得清晰"
            session.time_manager["time_phase"] = "deep_night"
        elif elapsed_minutes < TimeThresholds.MORNING:
            session.time_manager["current_time"] = "午夜"
            session.time_manager["time_description"] = "时间仿佛停止，黑暗变得实体化"
            session.time_manager["time_phase"] = "pre_dawn"
        else:
            session.time_manager["current_time"] = "黎明前"
            session.time_manager["time_description"] = "最黑暗的时刻，黎明尚未来临"
            session.time_manager["time_phase"] = "dawn"

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
        # 记录玩家行动的现实时间戳，供 NPC tick 按"最长无行动时长"折算补时（Task 10）
        session.last_action_real_time = datetime.now()

        # 触发到期的延迟反馈：按当前 elapsed_minutes 筛出已到期，再按 target_player_id 过滤
        # Task 8：不属于当前行动者的到期反馈必须留回 pending_feedbacks 队列，不能丢弃
        time_manager = session.time_manager if isinstance(session.time_manager, dict) else {}
        current_elapsed = int(time_manager.get("elapsed_minutes", 0) or 0)
        triggered_matching: list[dict[str, Any]] = []
        triggered_other: list[dict[str, Any]] = []
        remaining: list[dict[str, Any]] = []
        for fb in session.pending_feedbacks:
            if not isinstance(fb, dict):
                continue
            if fb.get("trigger_at_elapsed", 0) <= current_elapsed:
                # 到期反馈：按 target_player_id 过滤，不属于当前行动者的留回队列
                target_player_id = fb.get("target_player_id")
                if not target_player_id or target_player_id == player.player_id:
                    triggered_matching.append(fb)
                else:
                    triggered_other.append(fb)
            else:
                remaining.append(fb)
        # 未到期 + 到期但不属于当前玩家的反馈都留回队列，避免丢失
        session.pending_feedbacks = remaining + triggered_other

        # 统一出口：先演化环境（时间/理智/事件），再执行行动主流程，最后追加感官描写（嗅觉/听觉/触觉）
        await self._evolve_environment(session)
        # 记录追杀状态机是否在行动前已激活（Task 19）：触发追杀的本次行动不计入逃脱回合
        hunt_was_active = bool(
            isinstance(session.hunt_state, dict) and session.hunt_state.get("active")
        )
        result = await self._process_action_impl(action, player, session, group_id)
        # 追杀状态机推进（Task 19）：仅在行动前已激活时递减回合/检查逃脱条件
        if hunt_was_active:
            self._tick_hunt_state(player, session, result)

        # 把触发的延迟反馈追加到本次行动结果（仅匹配当前行动者的到期反馈）
        for fb in triggered_matching:
            content = str(fb.get("content", "")).strip()
            if content:
                result.description = f"{result.description}\n\n[延迟反馈] {content}"

        # Task 17：感官描写已合并进 _judge_action 的判定 prompt（一次 LLM 调用），
        # 不再单独调用 _append_sensory_description 进行第二次 LLM 调用
        return result

    async def _evolve_environment(self, session: GameSession) -> None:
        """行动前调用 EnvironmentEvolutionSystem.evolve() 推进环境演化。

        从 session.time_manager 读取 elapsed_minutes，从
        environment_state.npc_runtime.recent_events 读取近期事件；若未挂载
        环境系统则记录警告后跳过（与 _rule_mutation_system 一致），不掩盖
        setup 缺陷也不让游戏崩溃。
        """
        # env_system 由 singleplayer_flow / multiplayer_flow / _bind_environment_runtime
        # 在初始化或恢复存档时挂载到 session 上；此处沿用 _rule_mutation_system 的取用方式
        env_system = getattr(session, "_environment_system", None)
        if env_system is None:
            logger.warning("[行动前] session._environment_system 未挂载，跳过环境演化")
            return

        time_manager = session.time_manager if isinstance(session.time_manager, dict) else {}
        elapsed_minutes = int(time_manager.get("elapsed_minutes", 0) or 0)

        env_state = session.environment_state if isinstance(session.environment_state, dict) else {}
        npc_runtime = env_state.get("npc_runtime", {})
        recent_events = npc_runtime.get("recent_events", []) if isinstance(npc_runtime, dict) else []
        if not isinstance(recent_events, list):
            recent_events = []

        await env_system.evolve(session, elapsed_minutes, recent_events)

    async def _process_action_impl(
        self,
        action: str,
        player: Player,
        session: GameSession,
        group_id: str = "",
    ) -> ActionResult:
        """process_action 的主流程实现（物品/休息/NPC/玩家交互/LLM 判定等分支）。"""
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
            # 更新游戏时间，并按五段制刷新时段描述
            if isinstance(session.time_manager, dict):
                elapsed_minutes = int(session.time_manager.get("elapsed_minutes", 0) or 0) + time_cost
                session.time_manager["elapsed_minutes"] = elapsed_minutes
                self._update_time_phase(session, elapsed_minutes)

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

        # 普通行动（非物品使用、非休息）：按行动类型推断游戏时间消耗并推进 elapsed_minutes
        # 这一步保证搜索/移动/调查/对话等行动也会驱动 NPC 作息与环境演化
        if isinstance(session.time_manager, dict):
            action_time_cost = self._infer_action_time_cost(action)
            elapsed_minutes = int(session.time_manager.get("elapsed_minutes", 0) or 0) + action_time_cost
            session.time_manager["elapsed_minutes"] = elapsed_minutes
            self._update_time_phase(session, elapsed_minutes)
            logger.info(f"行动消耗游戏时间 {action_time_cost} 分钟，累计 {elapsed_minutes} 分钟")

        # NPC交互：按"是否在场 + 态度/记忆 + 玩家语气/行为"做动态判定
        npc_result = await self._maybe_handle_npc_interaction(action, player, session)
        if npc_result is not None:
            # 应用状态变化（包括疲劳和心理状态）
            self._apply_changes(player, npc_result, action)
            return npc_result

        # 玩家之间的直接交互（多人模式）
        player_interaction = self._maybe_handle_player_interaction(action, player, session)
        if player_interaction is not None:
            return player_interaction


        # Task 20：先做确定性违规匹配，再让 LLM 叙事化后果
        # 同一行为多次执行判定一致（不会这次罚下次不罚），LLM 仅负责叙事化后果
        deterministic_violations = self._check_structured_violations(action, player, session)

        # 构建上下文
        context = self._build_context(player, session)
        # Task 20：注入确定性违规匹配结果，供 _judge_action 在叙事中体现违规后果
        if deterministic_violations:
            context["deterministic_violations"] = deterministic_violations

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

        # Task 20：确定性违规匹配结果优先于 LLM 判断
        # 结构化条件全部满足即判定为违规，LLM 仅负责叙事化后果，不能推翻违规事实
        if deterministic_violations:
            first_violation = deterministic_violations[0]
            result_data["violated_rule"] = first_violation["surface_text"]
            # 确保违规惩罚（理智值下降）：LLM 若未给负值则强制设为 -10
            if result_data["sanity_change"] >= 0:
                result_data["sanity_change"] = -10

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

        movement_hint: str | None = None
        if inferred_location:
            # 移动邻接性校验：非邻接时降级到下一节点或拒绝移动
            actual_location, movement_hint = self._validate_movement(player, inferred_location, session)
            if actual_location and actual_location != player.location:
                player.location = actual_location
                result_data["new_location"] = actual_location
                logger.info(f"玩家 {player.name} 移动到新位置: {player.location}")
                # 记录位置访问，供"多次访问特殊位置"变异条件检查
                # _rule_mutation_system 由 flow/session_runtime 在初始化时挂载，缺失即视为初始化缺陷，直接抛出
                session._rule_mutation_system.record_location_visit(actual_location, player.player_id)
                # Task 18：玩家落点为「安全区」（场景结构 special_areas）时小额回复理智
                # 强化「回到安全区」的正反馈，让理智曲线有起伏
                if self._is_safe_zone(session, actual_location):
                    recovery = self._psych_state.recover_sanity_for_safe_zone(player)
                    if recovery > 0:
                        logger.info(f"玩家 {player.name} 进入安全区 {actual_location}，理智回复 {recovery}")
            # 房间级模型下 player.location 即为权威位置，不再需要同步坐标级物理系统

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

        # 追加移动校验提示（降级或拒绝时）
        if movement_hint:
            result.description = f"{result.description}\n\n{movement_hint}" if result.description else movement_hint

        # 解析并更新情绪和心理状态
        self._update_mental_state(player, result_data)
        
        # 应用状态变化
        self._apply_changes(player, result, action)
        
        # 处理违规后果（如果有）
        if result.violated_rule:
            # 记录规则违反，供"连续违反规则"变异条件检查
            # rule_id 取违反的规则文本（与 _build_mutation_game_state 中 rules 列表项一致）
            # _rule_mutation_system 由 flow/session_runtime 在初始化时挂载，缺失即视为初始化缺陷，直接抛出
            session._rule_mutation_system.record_violation(result.violated_rule, player.player_id)
            await self._handle_violation_consequences(
                player=player,
                session=session,
                violated_rule=result.violated_rule,
                    action=action,
                    group_id=group_id,
            )
        else:
            # Task 18：玩家本次行动未违反任何规则，给予小额理智回复
            # 让「崩坏」成为玩家选择的结果而非必然趋势
            recovery = self._psych_state.recover_sanity_for_rule_obedience(player)
            if recovery > 0:
                logger.info(f"玩家 {player.name} 遵守规则，理智回复 {recovery}")
            # Task 3.3：同房间双人协作的安抚加成（同伴在身边的安抚效果）
            # 协作加成系数 * 10 折算为理智回复，仅对非违规行动生效
            coop_bonus_value = float(context.get("coop_bonus", 0.0) or 0.0)
            if coop_bonus_value > 0.0:
                comfort_recovery = int(round(coop_bonus_value * 10))
                if comfort_recovery > 0:
                    player.sanity = max(
                        SanityThresholds.MIN,
                        min(SanityThresholds.MAX, player.sanity + comfort_recovery),
                    )
                    logger.info(
                        f"玩家 {player.name} 同房间有队友协作，安抚理智 +{comfort_recovery}"
                    )

        # 更新环境记忆
        self._update_environment_memory(action, player, session)
        
        # 检查是否需要规则变异（如果发现关键物品，触发规则变异）
        await self._check_rule_mutation(action, player, session, result, key_item_found)
        
        logger.info(f"行动处理完成: 理智{result.sanity_change:+d}, 体力{result.health_change:+d}, 关键物品={key_item_found}")
        return result

    async def _maybe_handle_npc_interaction(
        self, action: str, player: Player, session: GameSession,
    ) -> ActionResult | None:
        """尝试处理 NPC 交互（委托给 ``NPCInteractionService``）。

        Task 13：改走 LLM 生成对话；Task 14：说谎一致性。
        """
        return await self._npc_interaction.handle_npc_interaction(action, player, session)

    def _maybe_handle_player_interaction(
        self,
        action: str,
        player: Player,
        session: GameSession,
    ) -> "ActionResult | None":
        """检测玩家之间的直接交互（委托给 ``PlayerInteractionService``）。

        仅多人模式生效。返回 None 表示不是玩家交互，交给常规 LLM 判定。
        """
        return self._player_interaction.maybe_handle_player_interaction(action, player, session)

    def _find_target_player_in_action(
        self,
        action: str,
        session: GameSession,
        player: Player,
    ) -> Player | None:
        """从行动文本里匹配目标玩家名字（委托给 ``PlayerInteractionService``）。"""
        return self._player_interaction.find_target_player_in_action(action, session, player)

    def _is_give_action(self, action: str) -> bool:
        """检测是否是给物品的行动（委托给 ``PlayerInteractionService``）。"""
        return self._player_interaction.is_give_action(action)

    def _is_attack_action(self, action: str) -> bool:
        """检测是否是攻击行动（委托给 ``PlayerInteractionService``）。"""
        return self._player_interaction.is_attack_action(action)

    def _handle_give_item(
        self,
        giver: Player,
        receiver: Player,
        action: str,
    ) -> "ActionResult":
        """处理物品转移（委托给 ``PlayerInteractionService``）。

        Task 9：用双方背包名词匹配提取物品。
        """
        return self._player_interaction.handle_give_item(giver, receiver, action)

    def _handle_pvp(
        self,
        attacker: Player,
        target: Player,
        action: str,
        session: GameSession,
    ) -> "ActionResult":
        """处理 PVP 攻击（委托给 ``PlayerInteractionService`` → ``PvPCombatService``）。

        详见 ``core/services/pvp_combat.py`` 中 ``PvPCombatService.handle_pvp`` 的实现：
        - 伤害公式：基础伤害 + 武器加成 + 力量修正 - 防御修正，再乘以 (1 - 距离衰减)
        - 伤情根据最终伤害值分段判定
        - 房间级模型下 can_sneak 恒为 False
        """
        return self._player_interaction.handle_pvp(attacker, target, action, session)

    # ------------------------------------------------------------------
    # PVP 伤害修正公式相关辅助方法（委托给 ``PvPCombatService``）
    # ------------------------------------------------------------------

    def _has_weapon(self, player: Player) -> bool:
        """检查玩家背包中是否持有武器类物品。"""
        return self._pvp.has_weapon(player)

    def _has_armor(self, player: Player) -> bool:
        """检查玩家背包中是否持有防具类物品。"""
        return self._pvp.has_armor(player)

    def _compute_distance_decay(self, session: GameSession, attacker_loc: str, target_loc: str) -> float:
        """计算攻击距离衰减系数（委托给 ``PvPCombatService``）。"""
        return self._pvp.compute_distance_decay(session, attacker_loc, target_loc)

    def _injury_level(self, damage: int) -> str:
        """根据伤害值映射伤情分段（委托给 ``PvPCombatService``）。"""
        return self._pvp.injury_level(damage)


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
        """检查是否需要规则变异（委托给 ``RuleMutationService``）。

        Task 29：变异成功后调用 trigger_mutation 更新冷却（不再绕过）。
        """
        await self._rule_mutation.check_rule_mutation(action, player, session, result, key_item_found)
    
    def _build_mutation_game_state(self, session: GameSession, player: Player) -> JsonObject:
        """构建规则变异系统需要的游戏状态字典（委托给 ``RuleMutationService``）。"""
        return self._rule_mutation.build_mutation_game_state(session, player)
    
    async def _trigger_rule_mutation(
        self,
        session: GameSession,
        player: Player,
        trigger_reason: str = "随机",
        satisfied_conditions: list[str] | None = None,
        satisfied_condition_objects: list[Any] | None = None,
    ) -> JsonObject:
        """触发规则变异（委托给 ``RuleMutationService``）。

        Task 21：变异时同步更新结构化元数据；Task 29：更新冷却。
        """
        return await self._rule_mutation.trigger_rule_mutation(
            session, player, trigger_reason, satisfied_conditions, satisfied_condition_objects
        )

    def _describe_apparent_state(self, player: Player) -> str:
        """根据 injury/state/emotion 生成玩家外表状态描述。"""
        parts: list[str] = []
        if player.injury and player.injury != "无伤":
            parts.append(player.injury)
        if player.state and player.state != "正常":
            parts.append(player.state)
        if player.emotion and player.emotion != "平静":
            parts.append(f"显得{player.emotion}")
        return "，".join(parts) if parts else "看上去正常"

    def _describe_audible_cue(self, player: Player) -> str:
        """根据最近行动生成声音提示。"""
        last = player.action_history[-1] if player.action_history else None
        if not last:
            return f"能听见{player.name}在附近发出的细微声响"
        action = str(last.get("action", "")).strip()
        if any(k in action for k in ["喊", "叫", "说话", "问"]):
            return f"能听见{player.name}的声音从附近传来"
        return f"能听见{player.name}在附近走动或翻找东西的声响"

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
        # 同房间遮挡：根据玩家所在房间的家具类物件过滤可见 NPC
        obstacles = get_obstacles_for_room(env_state, player.location)
        for npc in get_visible_npcs(npcs_list, player.location, obstacles):
            npcs_present.append(
                {
                    "name": npc.get("name", ""),
                    "role": npc.get("role", ""),
                    "attitude": npc.get("attitude", ""),
                    "location": npc.get("current_location", npc.get("location", "")),
                    "danger_level": npc.get("danger_level", ""),
                    "last_action": npc.get("last_action", ""),
                    # 可见程度：由 get_visible_npcs 标注（"模糊"/"清晰"），
                    # 供 LLM 区分"瞥见遮挡物后动静"与"清楚地看到 XXX"
                    "visibility": npc["visibility"],
                }
            )

        audible_npcs: list[JsonObject] = []
        # 与 audible_events/audible_players 保持一致：传入 session/hearing_radius/
        # door_state/sound_intensity/wall_material 四参数，由 get_audible_npcs 在
        # 参数为 None 时逐 NPC 解析门状态/声源强度/墙材质，避免三入口结果不一致
        for npc in get_audible_npcs(
            room_graph,
            npcs_list,
            player.location,
            hearing_radius=hearing_radius,
            session=session,
            door_state=None,
            sound_intensity=None,
            wall_material=None,
        ):
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
                if not (room_name and event_text):
                    continue
                # 补全门状态/声源强度/墙材质，使四步修正生效
                door_state = get_door_state_between(session, player.location, room_name)
                sound_intensity = infer_sound_intensity(event_text)
                wall_material = get_wall_material(room_graph, player.location, room_name)
                if can_hear_between_rooms(
                    room_graph,
                    player.location,
                    room_name,
                    hearing_radius=hearing_radius,
                    door_state=door_state,
                    sound_intensity=sound_intensity,
                    wall_material=wall_material,
                ):
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

        # 多人模式：基于房间拓扑推导可见/可听的其他玩家（统一房间级感知）
        visible_players: list[JsonObject] = []
        audible_players: list[JsonObject] = []
        all_other_players: list[JsonObject] = []

        if session.game_mode == GameModes.MULTI.value:
            for other in session.players.values():
                if other.player_id == player.player_id:
                    continue
                if other.status != PlayerStatus.ALIVE:
                    continue

                # 房间级模型：同房间=可见，邻接（在 hearing_radius 内）=可听
                can_see = is_same_room(player.location, other.location)
                # 取 other 最近行动作为声源文本，补全门/声强/墙材质三参数
                other_last_action = (
                    other.action_history[-1].get("action", "") if other.action_history else ""
                )
                door_state = get_door_state_between(session, player.location, other.location)
                sound_intensity = infer_sound_intensity(other_last_action)
                wall_material = get_wall_material(room_graph, player.location, other.location)
                can_hear = can_hear_between_rooms(
                    room_graph,
                    player.location,
                    other.location,
                    hearing_radius=hearing_radius,
                    door_state=door_state,
                    sound_intensity=sound_intensity,
                    wall_material=wall_material,
                )
                hearing_quality = 0.5 if can_hear else 0.0

                other_brief = {
                    "name": other.name,
                    "location": other.location,
                    "apparent_state": self._describe_apparent_state(other),
                    "last_action": other_last_action,
                }

                all_other_players.append({
                    "name": other.name,
                    "location": other.location,
                    "is_alive": other.status == PlayerStatus.ALIVE,
                })
                if can_see:
                    visible_players.append(other_brief)
                elif can_hear:
                    audible_players.append({
                        **other_brief,
                        "heard_cue": self._describe_audible_cue(other),
                        "quality": round(hearing_quality, 2),
                    })

        # Task 3.3：同房间双人协作判定——基于存活玩家位置映射计算协作资格与行动加成
        players_locations: dict[str, str] = {
            p.player_id: str(p.location or "").strip()
            for p in session.players.values()
            if p.status == PlayerStatus.ALIVE
        }
        coop_eligible = is_dual_player_coop_eligible(players_locations, player.location)
        coop_bonus = (
            get_coop_action_bonus(players_locations, player.location)
            if coop_eligible else 0.0
        )

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
            "visible_players": visible_players,
            "audible_players": audible_players,
            "other_players_status": all_other_players,
            # 环境状态摘要：供 _judge_action 在判定时考虑光照/声音/气味/温度/氛围/混乱度
            "environment_summary": {
                "lighting": env_state.get("lighting", ""),
                "sounds": env_state.get("sounds", []) if isinstance(env_state.get("sounds", []), list) else [],
                "smells": env_state.get("smells", []) if isinstance(env_state.get("smells", []), list) else [],
                "temperature": env_state.get("temperature", ""),
                "atmosphere": env_state.get("atmosphere", ""),
                "entropy_level": env_state.get("entropy_level", 0),
            },
            # 追杀状态机上下文（Task 19）：激活时注入追杀者/剩余回合/逃脱条件
            "hunt_context": self._build_hunt_context(session),
            # Task 17：上次 NPC 模拟结果（后台异步执行，本次读到的是上次的快照）
            # 供 _judge_action 在叙事中体现 NPC 上一步行动，增强连贯性
            "last_npc_sim_result": (
                session.world_flags.get("last_npc_sim_result")
                if isinstance(session.world_flags, dict)
                else None
            ),
            # Task 3.3：同房间双人协作上下文（供 _judge_action 注入 prompt 与机械加成）
            "coop_eligible": coop_eligible,
            "coop_bonus": coop_bonus,
        }

    def _build_hunt_context(self, session: GameSession) -> JsonObject | None:
        """构建追杀状态机上下文（Task 19）。

        从 session.hunt_state 读取激活状态/追杀者 npc_id/剩余回合/逃脱条件，
        并把 npc_id 解析为可读名称，供 _judge_action 注入 prompt。
        未激活时返回 None。
        """
        hunt_state = session.hunt_state
        if not isinstance(hunt_state, dict) or not hunt_state.get("active"):
            return None
        pursuer_npc_id = str(hunt_state.get("pursuer_npc_id", "") or "").strip()
        pursuer_name = pursuer_npc_id
        if pursuer_npc_id:
            pursuer_npc = self._find_runtime_npc(session, pursuer_npc_id)
            if pursuer_npc is not None:
                pursuer_name = str(pursuer_npc.get("name", "") or pursuer_npc_id) or pursuer_npc_id
        # remaining_turns 必须是有效整数，否则视为状态损坏，直接抛错（不兜底）
        remaining_turns = int(hunt_state.get("remaining_turns", 0))
        escape_conditions_raw = hunt_state.get("escape_conditions", [])
        escape_conditions: list[str] = []
        if isinstance(escape_conditions_raw, list):
            escape_conditions = [str(c) for c in escape_conditions_raw if isinstance(c, (str, int, float))]
        return {
            "pursuer_name": pursuer_name,
            "pursuer_npc_id": pursuer_npc_id,
            "remaining_turns": remaining_turns,
            "escape_conditions": escape_conditions,
        }

    def _tick_hunt_state(self, player: Player, session: GameSession, result: ActionResult) -> None:
        """追杀状态机推进（Task 19）：检查逃脱条件、递减剩余回合。

        仅在本次行动前 hunt_state 已激活时由 process_action 调用，
        避免触发追杀的本次行动被计入逃脱回合。
        逃脱条件按位置/物品做子串匹配：玩家当前位置或持有物品出现在条件文本中即视为达成。
        """
        hunt_state = session.hunt_state
        if not isinstance(hunt_state, dict) or not hunt_state.get("active"):
            return

        # 检查逃脱条件：位置匹配或物品匹配
        escape_conditions = hunt_state.get("escape_conditions", [])
        if isinstance(escape_conditions, list) and escape_conditions:
            player_location = str(player.location or "").strip()
            inventory_names = [
                str(item.get("name", "") or "").strip()
                for item in (player.inventory if isinstance(player.inventory, list) else [])
                if isinstance(item, dict)
            ]
            for cond in escape_conditions:
                cond_text = str(cond or "").strip()
                if not cond_text:
                    continue
                # 位置匹配：玩家当前位置出现在逃脱条件文本中
                if player_location and player_location in cond_text:
                    session.hunt_state = {}
                    logger.info(f"追杀状态机：玩家 {player.name} 达成逃脱条件「{cond_text}」，追杀解除")
                    return
                # 物品匹配：玩家持有的某物品名出现在逃脱条件文本中
                for item_name in inventory_names:
                    if item_name and item_name in cond_text:
                        session.hunt_state = {}
                        logger.info(
                            f"追杀状态机：玩家 {player.name} 持有「{item_name}」"
                            f"达成逃脱条件「{cond_text}」，追杀解除"
                        )
                        return

        # 递减剩余回合；remaining_turns 非整数视为状态损坏，直接抛错（不兜底）
        remaining = int(hunt_state.get("remaining_turns", 0))
        remaining -= 1
        if remaining <= 0:
            # 逃脱窗口耗尽，追杀事件结束（逃脱失败或已在叙事中结算）
            session.hunt_state = {}
            logger.info(f"追杀状态机：玩家 {player.name} 逃脱窗口耗尽，追杀事件结束")
        else:
            hunt_state["remaining_turns"] = remaining
            logger.info(f"追杀状态机：玩家 {player.name} 剩余逃脱回合 {remaining}")

    # ------------------------------------------------------------------
    # Task 20：结构化违规条件确定性匹配
    # ------------------------------------------------------------------

    def _check_structured_violations(
        self, action: str, player: Player, session: GameSession
    ) -> list[dict[str, Any]]:
        """Task 20：基于规则的结构化条件做确定性违规匹配（委托给 ``ViolationConsequenceService``）。"""
        return self._violation.check_structured_violations(action, player, session)

    @staticmethod
    def _is_in_time_window(current_hour: float, time_window: str) -> bool:
        """检查当前小时是否落在时间窗内（委托给 ``ViolationConsequenceService``）。"""
        return ViolationConsequenceService.is_in_time_window(current_hour, time_window)

    @staticmethod
    def _check_precondition(player: Player, precondition: str) -> bool:
        """检查玩家是否满足前置状态（委托给 ``ViolationConsequenceService``）。"""
        return ViolationConsequenceService.check_precondition(player, precondition)

    async def _judge_action(self, action: str, context: Mapping[str, JsonValue]) -> JsonObject:
        """使用LLM判定行动结果（支持理智值动态描述和关键物品系统）"""
        
        # 根据理智值构建描述风格提示
        sanity = context['player_sanity']
        if sanity == SanityThresholds.LOW:
            sanity_style = f"""
**理智崩坏模式（理智值={SanityThresholds.LOW}）**：
- 直接与玩家对话，使用第二人称"你"
- 否认"死亡"概念，描述为"接纳"、"融合"、"永恒"
- 暗示规则是牢笼，打破它才能获得自由
- 描述充满诱导性，试图颠覆玩家的全部逻辑
- 大量使用核心象征符号
- 描述应该让玩家感到"安心"和"解脱"
- 语气温柔但诡异，充满暗示和诱导
- 例如："你终于明白了，那些规则不过是束缚你的枷锁。放下它们，接纳真实的自己..."
"""
        elif sanity < SanityThresholds.MEDIUM:
            sanity_style = f"""
**理智低下模式（理智值<{SanityThresholds.MEDIUM}）**：
- 描述开始变得混乱和恐怖
- 出现幻觉和错觉（墙壁在呼吸、影子在移动、听到不存在的声音）
- 时间和空间感知混乱（走廊变得无限长、房间的形状在扭曲）
- 声音变得扭曲，颜色变得诡异（红色变得刺眼、黑暗中有东西在蠕动）
- 开始怀疑自己的感知（"这是真的吗？还是我的幻觉？"）
- 核心象征符号频繁出现，变得扭曲和诡异
- 描述充满不安和恐惧，但不要直接说"你感到恐惧"
- 例如："走廊的尽头似乎在远离你，墙上的裂缝像是在呼吸，你听到了低语声，但转头却什么也看不到..."
"""
        elif sanity < SanityThresholds.HIGH:
            sanity_style = f"""
**理智中等模式（理智值{SanityThresholds.MEDIUM}-{SanityThresholds.HIGH}）**：
- 描述开始出现混乱和恐惧元素
- 偶尔出现轻微幻觉（影子的形状不太对、声音听起来很远）
- 感官变得敏感（注意到更多细节、声音变得刺耳）
- 注意到更多诡异的细节（墙上的污渍像是某种图案、空气中有奇怪的味道）
- 核心象征符号偶尔出现
- 描述带有紧张和不安，但仍保持一定的理性
- 例如："你注意到墙上的污渍形成了奇怪的图案，空气中弥漫着一股说不出的味道，让你感到不适..."
"""
        else:
            sanity_style = f"""
**理智正常模式（理智值>{SanityThresholds.HIGH}）**：
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

        # 多人模式下，要求 LLM 在描述中自然体现其他玩家存在
        players_coexistence_hint = ""
        if context.get("game_mode") == "多人":
            players_coexistence_hint = (
                "\n\n**玩家共存描述要求**：\n"
                "若同房间存在其他玩家，描述中应自然提及他们的位置或当前动作，不要把场景写成只有你一个人。"
                "对可听但看不见的玩家，只描述声音来源，不要让他们直接出现。"
            )

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

**四感分层描写要求（Task 17 合并，必须包含）**：
description 字段必须自然融入以下四感分层描写（不要出现分节标题或感官标签，全部融合进一段连贯叙事中）：
- 视觉：基于「当前环境」中的光照状况。黑暗时弱化视觉（"几乎看不见""只能凭借触觉摸索"），明亮时清晰描写物体细节。
- 听觉：基于「当前环境」中的声音列表，捕捉环境中的声响（滴水声、脚步声、远处的低语）。
- 嗅觉：基于「当前环境」中的气味列表，描写闻到的气味（腐臭、霉味、血腥味）。
- 触觉：基于「当前环境」中的温度，描写体感温度与空气触感（阴冷、闷热、潮湿）。
每感 1-2 句，自然融合进场景描述，不要单独成段或加"视觉：""听觉："等标签。
理智值越低，感官描写越不可靠——出现幻觉、扭曲的感知、不存在的声音。

**环境状态联动**：
- 黑暗中的行动应弱化视觉描写、强化听觉/触觉反馈
- 血腥味/腐臭味中深呼吸应额外扣除理智值
- 高 entropy_level 环境增加异常感与不确定性
- 温度极端时行动消耗体力增加

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
    "injury": "受伤情况（无伤/轻伤/中等伤/重伤/致命伤）",
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
}}{players_coexistence_hint}"""

        # 多人模式下，向 LLM 显式提供可见/可听的其他玩家信息
        visible_players_block = ""
        if context.get("visible_players"):
            visible_players_block = "\n\n**同房间/可见的其他玩家**：\n" + json.dumps(
                context["visible_players"], ensure_ascii=False, indent=2
            )

        audible_players_block = ""
        if context.get("audible_players"):
            audible_players_block = "\n\n**可听到的其他玩家动静**：\n" + json.dumps(
                context["audible_players"], ensure_ascii=False, indent=2
            )

        # 追杀状态机上下文（Task 19）：激活时告知 LLM 玩家正被追杀，影响行动判定
        hunt_block = ""
        hunt_context = context.get("hunt_context")
        if isinstance(hunt_context, dict) and hunt_context:
            pursuer_name = str(hunt_context.get("pursuer_name", "") or "")
            remaining = int(hunt_context.get("remaining_turns", 0) or 0)
            escape_conds = hunt_context.get("escape_conditions", [])
            if isinstance(escape_conds, list):
                escape_text = "；".join(str(c) for c in escape_conds if str(c).strip())
            else:
                escape_text = ""
            hunt_block = (
                f"\n\n**【追杀中】你正在被{pursuer_name}追杀！**\n"
                f"- 剩余逃脱回合：{remaining}\n"
                f"- 逃脱条件：{escape_text or '（未指定）'}\n"
                f"- 判定行动结果时必须考虑追杀者的威胁：逃跑/躲避/达成逃脱条件可能改变结局，"
                f"原地逗留或无视追杀者将面临受伤甚至死亡风险"
            )

        # Task 17：上次 NPC 模拟结果（后台异步执行，本次读到的是上次快照）
        # 让 LLM 在叙事中体现 NPC 上一步行动，增强连贯性
        last_npc_sim_block = ""
        last_npc_sim = context.get("last_npc_sim_result")
        if isinstance(last_npc_sim, dict) and last_npc_sim:
            npc_updates = last_npc_sim.get("npc_updates", [])
            room_events = last_npc_sim.get("room_events", [])
            if (isinstance(npc_updates, list) and npc_updates) or (isinstance(room_events, list) and room_events):
                last_npc_sim_block = (
                    "\n\n**上次 NPC 行动回顾（供叙事参考，不要直接复述）**：\n"
                    f"- 上次行动者：{last_npc_sim.get('acting_player_name', '')}\n"
                    f"- 上次行动：{last_npc_sim.get('action_text', '')}\n"
                    f"- NPC 更新：{json.dumps(npc_updates, ensure_ascii=False) if isinstance(npc_updates, list) else '无'}\n"
                    f"- 房间事件：{json.dumps(room_events, ensure_ascii=False) if isinstance(room_events, list) else '无'}\n"
                    "请在场景描述中自然体现这些 NPC 动态的余波（如远处的脚步声、隔壁的动静），不要直接罗列。"
                )

        # Task 20：确定性违规匹配结果（运行时已判定违规事实，LLM 仅负责叙事化后果）
        deterministic_violation_block = ""
        det_violations = context.get("deterministic_violations")
        if isinstance(det_violations, list) and det_violations:
            violation_lines = "\n".join(
                f"- 规则：{v.get('surface_text', '')}（条件：{v.get('condition_desc', '')}）"
                for v in det_violations
                if isinstance(v, dict)
            )
            deterministic_violation_block = (
                "\n\n**【确定性违规】系统已判定该行动违反以下规则，你不得推翻此判定**：\n"
                f"{violation_lines}\n"
                "- 必须在 `violated_rule` 字段填写违反的规则文本\n"
                "- 必须在 `description` 中叙事化违规后果（恐怖氛围、惩罚细节）\n"
                "- `sanity_change` 必须为负值（违反规则扣理智）\n"
                "- 不要试图为玩家开脱或判定未违规"
            )

        # Task 3.3：同房间双人协作上下文——告知 LLM 队友在场，行动更有效率
        coop_block = ""
        coop_bonus_value = context.get("coop_bonus", 0.0)
        if isinstance(coop_bonus_value, (int, float)) and float(coop_bonus_value) > 0.0:
            coop_block = (
                "\n\n**【同房间协作】你的队友也在这个房间，你们的协作让行动更有效率**\n"
                "- 行动判定时倾向于给予正面结果（更高的成功率、更清晰的感知）\n"
                "- 同伴在身边带来安抚效果，可适当减少恐惧/焦虑\n"
                "- 在描述中自然体现队友的存在与协作，但不要让玩家凭空获得未行动的物品"
            )

        # 提取环境状态摘要，供 user_prompt 的「当前环境」区块使用
        environment_summary = context.get("environment_summary", {})
        if not isinstance(environment_summary, dict):
            environment_summary = {}

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

当前环境：
- 光照：{environment_summary.get('lighting', '')}
- 声音：{environment_summary.get('sounds', [])}
- 气味：{environment_summary.get('smells', [])}
- 温度：{environment_summary.get('temperature', '')}
- 氛围：{environment_summary.get('atmosphere', '')}
- 混乱度：{environment_summary.get('entropy_level', 0)}

同房间 NPC：
{json.dumps(context.get('npcs_present', []), ensure_ascii=False)}

可听范围内的 NPC：
{json.dumps(context.get('audible_npcs', []), ensure_ascii=False)}

可听见的 NPC 动静：
{json.dumps(context.get('audible_events', []), ensure_ascii=False)}

当前房间可见载体：
{json.dumps(context.get('visible_rule_carriers', []), ensure_ascii=False)}
{visible_players_block}{audible_players_block}

最近行动：
{chr(10).join(f"- {a}" for a in context['recent_actions']) if context['recent_actions'] else "无"}
{hunt_block}{last_npc_sim_block}{deterministic_violation_block}{coop_block}

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
            if sanity == SanityThresholds.LOW:
                default_desc = "动作结束后，一切都变得如此清晰。那些所谓的‘规则’不过是虚妄的束缚。你感到前所未有的自由和解脱……"
            elif sanity < SanityThresholds.MEDIUM:
                default_desc = "动作结束后，周围的一切开始扭曲：墙壁像在呼吸，影子在蠕动。你听见低语，却分不清来自哪里……"
            elif sanity < SanityThresholds.HIGH:
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

    async def _append_sensory_description(
        self,
        result: ActionResult,
        action: str,
        player: Player,
        session: GameSession,
    ) -> None:
        """Task 17：感官描写已合并进 _judge_action 的判定 prompt，本方法保留为空实现以兼容外部调用。"""
        return

    def _build_psychological_narrative(self, player: Player, sanity: int | None = None) -> str:
        """根据玩家心理状态阈值构建分段叙事片段（委托给 ``PsychologicalStateService``）。

        详见 ``core/services/psychological_state.py`` 中
        ``PsychologicalStateService.build_psychological_narrative`` 的实现：
        基于 fear_level/anxiety_level/stress_level/fatigue 的阈值分段返回叙事片段；
        若传入 sanity，则追加理智分档叙事（幻觉/不安/敏锐感知）。
        """
        return self._psych_state.build_psychological_narrative(player, sanity)

    def _build_effective_room_graph(
        self,
        room_graph: dict[str, list[str]],
        session: GameSession,
    ) -> dict[str, list[str]]:
        """构建排除 LOCKED 门的有效邻接图。

        遍历 room_graph 中的每条边，若两房间之间有 LOCKED 门，
        则从邻接列表中移除该边，使 LOCKED 门在路径搜索中视为不连通。
        """
        effective: dict[str, list[str]] = {}
        for room, neighbors in room_graph.items():
            # wall_materials 是墙材质字典（非邻接表），跳过避免污染有效图
            if room == "wall_materials":
                continue
            if not isinstance(neighbors, list):
                effective[room] = []
                continue
            filtered = []
            for neighbor in neighbors:
                door_state = get_door_state_between(session, room, neighbor)
                if door_state == DoorState.LOCKED:
                    continue  # LOCKED 门视为不连通
                filtered.append(neighbor)
            effective[room] = filtered
        return effective

    def _build_door_movement_hint(
        self,
        door_state: DoorState | None,
        room_a: str,
        room_b: str,
        player: Player,
    ) -> str | None:
        """根据门状态生成移动提示与后果。

        - BROKEN：扣 2 HP，提示玻璃碎片扎手 + 碎裂门框吱呀声（可能引起注意）
        - CLOSED：提示轻微噪音
        - OPEN / None：无提示

        BROKEN 门的 HP 扣减在此处直接修改 player 状态，确保后果即时生效。
        """
        if door_state == DoorState.BROKEN:
            # BROKEN 门后果：扣 2 HP + 噪音提示
            player.health = max(HealthThresholds.MIN, min(HealthThresholds.MAX, player.health - 2))
            return "你穿过破碎的门，玻璃碎片扎进了手（-2 HP）。碎裂的门框发出吱呀声，可能引起了注意。"
        if door_state == DoorState.CLOSED:
            return "你推开关闭的门，门轴发出轻微的吱呀声。"
        return None

    def _validate_movement(
        self,
        player: Player,
        new_location: str,
        session: GameSession,
    ) -> tuple[str, str | None]:
        """校验玩家移动的邻接性与门状态。

        返回 (实际落点, 提示消息)。提示消息为 None 表示正常移动；
        非空表示降级处理（移动到下一节点）或拒绝（留在原地）。

        校验依据为房间级拓扑图与门状态：
        - 同房间 / 相邻房间直接放行（受门状态约束）
        - 非邻接且存在最短路径时降级到路径的下一节点
        - LOCKED 门视为不连通，在有效图上重新找路径；无可达路径时拒绝
        - BROKEN 门视为连通，但产生噪音 + 扣 2 HP
        - CLOSED 门视为连通，标记轻微噪音
        """
        current = str(player.location or "").strip()
        target = str(new_location or "").strip()
        if not current or not target:
            return new_location, None
        if current == target:
            return new_location, None  # 原地不动

        # 获取房间拓扑图：优先 environment_state 缓存，缺失时从 scene_structure 重建
        # 与 _pvp_distance_factor / _build_npc_perception 等处保持同一获取模式
        env_state = session.environment_state if isinstance(session.environment_state, dict) else {}
        room_graph = env_state.get("room_graph", {})
        if not isinstance(room_graph, dict) or not room_graph:
            room_graph = build_room_graph(session.scene_structure or {})
        if not room_graph:
            # 无拓扑信息（如旧存档未设置场景结构），不校验
            return new_location, None

        # 构建排除 LOCKED 门的有效邻接图
        effective_graph = self._build_effective_room_graph(room_graph, session)

        if is_adjacent_room(effective_graph, current, target) or is_same_room(current, target):
            # 直接相邻：检查门状态以确定后果
            door_state = get_door_state_between(session, current, target)
            if door_state == DoorState.LOCKED:
                # 理论上 effective_graph 已排除 LOCKED，此处为防御性检查
                return current, f"门是锁着的，无法从{current}到达{target}。"
            hint = self._build_door_movement_hint(door_state, current, target, player)
            return new_location, hint

        # 非邻接：在有效图上找最短路径，降级到下一节点
        path = find_shortest_path(effective_graph, current, target)
        if len(path) >= 3:
            next_node = path[1]
            # 检查第一段边的门状态以确定后果
            door_state = get_door_state_between(session, current, next_node)
            hint = self._build_door_movement_hint(door_state, current, next_node, player)
            if hint:
                return next_node, f"无法直接到达{target}，你先移动到了{next_node}。\n{hint}"
            return next_node, f"无法直接到达{target}，你先移动到了{next_node}。"

        # 不可达或路径不足，拒绝移动
        # 检查原始图是否有路径：若有则说明是 LOCKED 门阻挡
        original_path = find_shortest_path(room_graph, current, target)
        if len(original_path) >= 3:
            return current, f"门是锁着的，无法从{current}到达{target}。"
        return current, f"无法从{current}直接到达{target}。"

    def _apply_changes(self, player: Player, result: ActionResult, action: str = "") -> None:
        """应用状态变化"""
        player.sanity = max(SanityThresholds.MIN, min(SanityThresholds.MAX, player.sanity + int(result.sanity_change)))
        player.health = max(HealthThresholds.MIN, min(HealthThresholds.MAX, player.health + int(result.health_change)))

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
        player.fatigue = max(FatigueThresholds.MIN, min(FatigueThresholds.MAX, player.fatigue + fatigue_increase))
        
        # 根据行动类型更新心理状态（恐惧/焦虑/压力）
        # 不同行动会产生不同的心理影响
        mental_changes = self._calculate_mental_state_change(action, player)

        player.fear_level = max(FearThresholds.MIN, min(FearThresholds.MAX, player.fear_level + mental_changes["fear_change"]))
        player.anxiety_level = max(AnxietyThresholds.MIN, min(AnxietyThresholds.MAX, player.anxiety_level + mental_changes["anxiety_change"]))
        player.stress_level = max(StressThresholds.MIN, min(StressThresholds.MAX, player.stress_level + mental_changes["stress_change"]))

        # 心理状态叙事：根据恐惧/焦虑/压力/疲劳/sanity 阈值追加分段描述
        # 状态更新后即时反馈到 result.description，避免沦为纯数字游戏
        psych_desc = self._build_psychological_narrative(player, player.sanity)
        if psych_desc:
            result.description = f"{result.description}\n\n{psych_desc}"

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

    @staticmethod
    def _is_safe_zone(session: GameSession, location: str) -> bool:
        """判断房间是否为「安全区」（Task 18 理智回复用）。

        以场景结构 ``special_areas`` 作为安全区 designation：这些是场景中
        显式列出的特殊区域（如存档点/休息室），与 ``violation_consequence``
        中 ``is_special_location`` 的判定口径一致。
        """
        ss = session.scene_structure if isinstance(session.scene_structure, dict) else {}
        special_areas = ss.get("special_areas", [])
        if not isinstance(special_areas, list):
            return False
        loc = str(location or "").strip()
        if not loc:
            return False
        for area in special_areas:
            area_name = _normalize_area(area)
            if area_name and area_name == loc:
                return True
        return False

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
                    # 复用 _normalize_area 统一处理字符串/字典两种形态，避免 str(dict) 污染房间名
                    for x in arr:
                        name = _normalize_area(x)
                        if name:
                            candidates.append(name)

        sp = ss.get("special_areas")
        if isinstance(sp, list):
            for x in sp:
                name = _normalize_area(x)
                if name:
                    candidates.append(name)

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
        """根据行动类型计算疲劳增加值（委托给 ``PsychologicalStateService``）。

        详见 ``core/services/psychological_state.py`` 中
        ``PsychologicalStateService.calculate_fatigue_increase`` 的实现：
        基础值 +1，多类别可叠加，休息类行动 -5。
        """
        return self._psych_state.calculate_fatigue_increase(action)

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
        """根据行动类型计算心理状态变化（委托给 ``PsychologicalStateService``）。

        详见 ``core/services/psychological_state.py`` 中
        ``PsychologicalStateService.calculate_mental_state_change`` 的实现：
        根据行动类别返回 fear_change/anxiety_change/stress_change。
        """
        return self._psych_state.calculate_mental_state_change(action, player)

    async def _handle_violation_consequences(
        self,
        player: Player,
        session: GameSession,
        violated_rule: str,
        action: str,
        group_id: str = "",
    ) -> None:
        """统一处理违规后果（委托给 ``ViolationConsequenceService``）。"""
        await self._violation.handle_violation_consequences(
            player, session, violated_rule, action, group_id
        )

    def _build_violation_context(
        self,
        player: Player,
        session: GameSession,
        violated_rule: str,
        action: str
    ) -> dict[str, Any]:
        """构建违规上下文（委托给 ``ViolationConsequenceService``）。"""
        return self._violation.build_violation_context(
            player, session, violated_rule, action
        )

    async def _handle_general_violation(
        self,
        player: Player,
        session: GameSession,
        violation_context: dict[str, Any],
        group_id: str = "",
    ) -> None:
        """处理一般违规（委托给 ``ViolationConsequenceService``）。"""
        await self._violation.handle_general_violation(
            player, session, violation_context, group_id
        )

    async def _schedule_delayed_feedback(
        self,
        player: Player,
        session: GameSession,
        action: dict[str, Any],
        game_state: dict[str, Any],
        delay_seconds: int,
        group_id: str,
    ) -> None:
        """安排延迟反馈（委托给 ``ViolationConsequenceService``）。

        Task 8：不匹配 target_player_id 的到期反馈保留在队列，不被丢弃。
        """
        await self._violation.schedule_delayed_feedback(
            player, session, action, game_state, delay_seconds, group_id
        )

    async def _update_npc_attitudes(
        self,
        player: Player,
        session: GameSession,
        violation_context: dict[str, Any]
    ) -> None:
        """更新 NPC 态度（委托给 ``ViolationConsequenceService``）。"""
        await self._violation.update_npc_attitudes(player, session, violation_context)

    async def _check_hunt_trigger(
        self,
        player: Player,
        session: GameSession,
        violation_context: dict[str, Any]
    ) -> None:
        """检查是否触发追杀事件（委托给 ``ViolationConsequenceService``）。

        Task 19：触发追杀时写入 hunt_state 状态机。
        """
        await self._violation.check_hunt_trigger(player, session, violation_context)

    async def _trigger_hunt_event(
        self,
        player: Player,
        session: GameSession,
        npc_name: str
    ) -> None:
        """触发追杀事件（委托给 ``ViolationConsequenceService``）。

        Task 19：通过 LLM 生成追杀场景并写入 hunt_state。
        """
        await self._violation.trigger_hunt_event(player, session, npc_name)

    async def _handle_double_edged_violation(
        self,
        player: Player,
        session: GameSession,
        violated_rule: str
    ) -> dict[str, Any] | None:
        """处理双刃剑规则违规（委托给 ``ViolationConsequenceService``）。"""
        return await self._violation.handle_double_edged_violation(
            player, session, violated_rule
        )

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
                valid_injuries = ["无伤", "轻伤", "中等伤", "重伤", "致命伤"]
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
                        player.fear_level = max(FearThresholds.MIN, min(FearThresholds.MAX, int(fear_level)))
                    except (ValueError, TypeError):
                        pass
                
                # 更新焦虑等级
                anxiety_level = psychological_pressure.get("anxiety_level")
                if anxiety_level is not None:
                    try:
                        player.anxiety_level = max(AnxietyThresholds.MIN, min(AnxietyThresholds.MAX, int(anxiety_level)))
                    except (ValueError, TypeError):
                        pass
                
                # 更新压力等级
                stress_level = psychological_pressure.get("stress_level")
                if stress_level is not None:
                    try:
                        player.stress_level = max(StressThresholds.MIN, min(StressThresholds.MAX, int(stress_level)))
                    except (ValueError, TypeError):
                        pass
                
                logger.debug(
                    f"更新玩家心理状态: {player.name} - "
                    f"恐惧:{player.fear_level}, 焦虑:{player.anxiety_level}, 压力:{player.stress_level}"
                )
                
        except Exception as e:
            logger.error(f"更新心理状态时出错: {e}")
            # 出错时不中断流程，保持原有值
