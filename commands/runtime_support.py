from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING
import asyncio
import base64
import logging
import re

from ..common import GameModes, JsonObject, JsonValue, RuleDict
from ..core import GameSession, LLMClient, Player, PlayerStatus
from ..core.services import GameGenerator, NPCSimulator
from ..systems import (
    EnvironmentEvolutionSystem,
    NPC,
    NPCAttitude,
    NPCMemory,
    RuleMutationSystem,
    build_room_graph,
)

if TYPE_CHECKING:
    from ..core.services.event_bus import EventBus

logger = logging.getLogger(__name__)


class RuntimeSupportMixin:
    """运行时辅助与规则载体/NPC 相关工具（facade）。

    本 Mixin 保留为编排门面：工厂方法（``_get_or_create_*``）与跨服务编排
    （``_ensure_story_runtime`` 等）留在原地；规则载体、身份任务卡、图片发送、
    入场描述四类清晰职责分别委托到 ``CarrierService`` / ``IdentityService`` /
    ``ImageDeliveryService`` / ``EntranceService`` 独立服务类。
    """

    # 延迟初始化的服务实例（首次访问时创建，避免在 Mixin 中定义 __init__）
    _event_bus: "EventBus | None" = None
    _carrier_service: "CarrierService | None" = None
    _identity_service: "IdentityService | None" = None
    _image_delivery_service: "ImageDeliveryService | None" = None
    _entrance_service: "EntranceService | None" = None

    # ------------------------------------------------------------------
    # 服务实例延迟创建（宿主类装配后首次访问时构建）
    # ------------------------------------------------------------------

    @property
    def _carrier(self) -> "CarrierService":
        """规则载体服务（延迟初始化）。"""
        if self._carrier_service is None:
            self._carrier_service = CarrierService(self)
        return self._carrier_service

    @property
    def _identity(self) -> "IdentityService":
        """身份任务卡服务（延迟初始化）。"""
        if self._identity_service is None:
            self._identity_service = IdentityService(self)
        return self._identity_service

    @property
    def _image_delivery(self) -> "ImageDeliveryService":
        """图片发送服务（延迟初始化）。"""
        if self._image_delivery_service is None:
            self._image_delivery_service = ImageDeliveryService(self)
        return self._image_delivery_service

    @property
    def _entrance(self) -> "EntranceService":
        """入场描述服务（延迟初始化）。"""
        if self._entrance_service is None:
            self._entrance_service = EntranceService(self)
        return self._entrance_service

    # ------------------------------------------------------------------
    # 通用工具方法（不属于任何单一服务，保留在 Mixin 中）
    # ------------------------------------------------------------------

    @staticmethod
    def _join_cn_items(items: list[str], limit: int = 4) -> str:
        """将若干短语拼接成更自然的中文列举。"""
        cleaned = [str(item).strip() for item in items if str(item).strip()]
        if not cleaned:
            return ""
        visible = cleaned[:limit]
        if len(visible) == 1:
            return visible[0]
        if len(visible) == 2:
            return f"{visible[0]}和{visible[1]}"
        return "、".join(visible[:-1]) + f"和{visible[-1]}"

    def _fatigue_level_from_value(self, fatigue_value: int) -> str:
        """根据疲劳值推导疲劳等级（仅用于玩家展示）。"""
        if fatigue_value >= 80:
            return "极度"
        if fatigue_value >= 60:
            return "严重"
        if fatigue_value >= 40:
            return "中度"
        if fatigue_value >= 20:
            return "轻微"
        return "无"

    def _get_player_fatigue_level(self, player: Player) -> str:
        """统一获取玩家疲劳等级。

        新存档优先使用 `player.fatigue` 疲劳值；旧存档缺失时回退到体力推导，
        避免展示层继续出现"有时是数值、有时是等级"的语义混乱。
        """
        raw_fatigue = getattr(player, "fatigue", None)
        if isinstance(raw_fatigue, (int, float)):
            fatigue_value = max(0, min(100, int(raw_fatigue)))
            return self._fatigue_level_from_value(fatigue_value)
        return self._fatigue_level_from_value(max(0, min(100, 100 - int(player.health))))

    # ------------------------------------------------------------------
    # 工厂方法（延迟初始化，留在 Mixin 中作为编排核心）
    # ------------------------------------------------------------------

    def _get_game_generator(self) -> GameGenerator:
        """获取或创建 GameGenerator（延迟初始化）"""
        if self._game_generator is None:
            self._game_generator = GameGenerator()
        return self._game_generator

    def _get_or_create_environment_system(self, game_states: dict[str, JsonObject]) -> EnvironmentEvolutionSystem:
        """获取或创建环境演化系统（延迟初始化）"""
        if self._environment_system is None:
            self._environment_system = EnvironmentEvolutionSystem(game_states)
        else:
            self._environment_system.game_states.update(game_states)
        return self._environment_system

    def _get_or_create_npc_simulator(self) -> NPCSimulator:
        """获取或创建 NPC 模拟器。"""
        if self._npc_simulator is None:
            self._npc_simulator = NPCSimulator()
        return self._npc_simulator

    def _get_or_create_rule_mutation_system(self) -> RuleMutationSystem:
        """获取或创建规则变异系统（延迟初始化）"""
        if self._rule_mutation_system is None:
            self._rule_mutation_system = RuleMutationSystem()
            # 注册默认变异条件
            from ..systems.rule_mutation_system import create_default_mutation_conditions
            default_conditions = create_default_mutation_conditions()
            for condition in default_conditions:
                self._rule_mutation_system.add_condition(condition)
            logger.info(f"已注册 {len(default_conditions)} 个默认规则变异条件")
        return self._rule_mutation_system

    def _get_or_create_event_bus(self) -> "EventBus":
        """获取或创建事件总线（延迟初始化）。"""
        if self._event_bus is None:
            from ..core.services.event_bus import EventBus
            self._event_bus = EventBus(aggregate_window_seconds=3.0)
            self._register_default_event_handler()
        return self._event_bus

    # ------------------------------------------------------------------
    # Factory Protocol 实现（满足 core.services.factories 中定义的 Protocol）
    #
    # 这些公开方法是对应 ``_get_or_create_*`` 私有方法的公开别名，用于让
    # ``RuntimeSupportMixin`` 的子类（如 ``RuleHorrorCommand``）结构性满足
    # ``RuntimeFactories`` 等 Protocol，从而可以在不修改调用方（如
    # ``flows/multiplayer_flow.py`` 中 ``state.start_npc_tick(self.command)``
    # ）的前提下，由 core 层通过 Protocol 接口获取实例，避免反向依赖。
    # ------------------------------------------------------------------

    def get_or_create_environment_system(self, game_states: dict[str, JsonObject]) -> EnvironmentEvolutionSystem:
        """获取或创建环境演化系统（满足 EnvironmentSystemFactory 协议）。"""
        return self._get_or_create_environment_system(game_states)

    def get_or_create_npc_simulator(self) -> NPCSimulator:
        """获取或创建 NPC 模拟器（满足 NPCSimulatorFactory 协议）。"""
        return self._get_or_create_npc_simulator()

    def get_or_create_rule_mutation_system(self) -> RuleMutationSystem:
        """获取或创建规则变异系统（满足 RuleMutationSystemFactory 协议）。"""
        return self._get_or_create_rule_mutation_system()

    def get_or_create_event_bus(self) -> "EventBus":
        """获取或创建事件总线（满足 EventBusFactory 协议）。"""
        return self._get_or_create_event_bus()

    def _register_default_event_handler(self) -> None:
        """注册默认事件订阅器：把事件转发成群消息。

        注意：P2 阶段 handler 直接调用 self.send_text 发送到当前 stream，
        这意味着事件会被当前群所有成员看到。若事件来自其他 group，此处会误发。
        待 P3 物理系统接线后再细化按 player_id 私聊推送的逻辑。
        """
        from ..core.services.event_bus import GameEvent

        async def handler(event: GameEvent) -> None:
            # 行动者已收到主反馈，不再重复推送
            for pid in event.visible_to:
                if pid == event.actor_id:
                    continue
                await self.send_text(
                    f"（你看到{event.actor_name}：{event.description}）"
                )
            for pid in event.audible_to - event.visible_to:
                if pid == event.actor_id:
                    continue
                await self.send_text(
                    f"（你听到：{event.audible_description}）"
                )

        # 为当前群订阅（self.group_id 由宿主类 RuleHorrorCommand.__init__ 设置）
        if self.group_id:
            self._event_bus.subscribe(self.group_id, handler)

    # ------------------------------------------------------------------
    # NPC 运行时构建（与载体服务协作的编排逻辑，留在 Mixin 中）
    # ------------------------------------------------------------------

    def _infer_default_npc_location(
        self,
        session: GameSession,
        preferred_locations: list[str] | None = None,
    ) -> str:
        """推断 NPC 初始房间。"""
        scene_structure = getattr(session, "scene_structure", {}) or {}
        areas: list[str] = []
        for fl in scene_structure.get("floors", []) or []:
            if isinstance(fl, dict):
                areas.extend([str(x) for x in (fl.get("areas") or fl.get("rooms") or []) if str(x).strip()])
        areas.extend([str(x) for x in (scene_structure.get("special_areas") or []) if str(x).strip()])

        for candidate in preferred_locations or []:
            candidate_text = str(candidate or "").strip()
            if candidate_text:
                return candidate_text

        prefer = ["柜台", "收银", "前台", "服务台", "接待", "值班室", "大厅", "入口", "门口"]
        for keyword in prefer:
            hit = next((area for area in areas if keyword in area), None)
            if hit:
                return hit
        return areas[0] if areas else (session.scene_name or "起始位置")

    def _build_runtime_npcs(
        self,
        session: GameSession,
        game_mode: str | None = None,
        initial_player_id: str | None = None,
    ) -> list[JsonObject]:
        """基于 npc_guidance 归一化运行时 NPC 列表。"""
        npc_guidance = getattr(session, "npc_guidance", {}) or {}
        if not isinstance(npc_guidance, dict) or not npc_guidance:
            return []
        if str(npc_guidance.get("guidance_method", "") or "").strip().lower() == "none":
            return []

        raw_roster = npc_guidance.get("npc_roster", [])
        if not isinstance(raw_roster, list) or not raw_roster:
            raw_roster = [
                {
                    "npc_id": "guide_0",
                    "name": npc_guidance.get("npc_name", "NPC"),
                    "role": npc_guidance.get("npc_role", ""),
                    "attitude": npc_guidance.get("npc_attitude", ""),
                    "behavior_logic_summary": npc_guidance.get("npc_behavior", ""),
                    "current_goal": "观察新来者并维持当前区域秩序",
                    "last_action": "刚结束一轮例行巡视",
                    "audible_signature": "脚步声和布料摩擦声",
                    "danger_level": "低",
                    "can_speak": True,
                }
            ]

        runtime_npcs: list[JsonObject] = []
        for index, raw_npc in enumerate(raw_roster):
            if not isinstance(raw_npc, Mapping):
                continue

            preferred_locations: list[str] = []
            for key in ("current_location", "home_area"):
                location_text = str(raw_npc.get(key, "") or "").strip()
                if location_text:
                    preferred_locations.append(location_text)
            duty_areas_raw = raw_npc.get("duty_areas", [])
            if isinstance(duty_areas_raw, list):
                preferred_locations.extend(str(item).strip() for item in duty_areas_raw if str(item).strip())
            npc_location = self._infer_default_npc_location(session, preferred_locations)

            npc = NPC(
                npc_id=str(raw_npc.get("npc_id", f"guide_{index}") or f"guide_{index}").strip(),
                name=str(raw_npc.get("name", npc_guidance.get("npc_name", "NPC")) or npc_guidance.get("npc_name", "NPC")).strip(),
                role=str(raw_npc.get("role", npc_guidance.get("npc_role", "")) or npc_guidance.get("npc_role", "")).strip(),
                personality="",
                initial_location=npc_location,
            )
            npc.home_area = str(raw_npc.get("home_area", npc_location) or npc_location).strip()
            duty_areas = raw_npc.get("duty_areas", [npc.home_area])
            npc.duty_areas = [str(item).strip() for item in duty_areas if str(item).strip()] if isinstance(duty_areas, list) else [npc.home_area]
            npc.behavior_logic_summary = str(raw_npc.get("behavior_logic_summary", npc_guidance.get("npc_behavior", "")) or npc_guidance.get("npc_behavior", "")).strip()
            npc.current_goal = str(
                raw_npc.get("current_goal", "维持当前区域秩序并观察新来者") or "维持当前区域秩序并观察新来者"
            ).strip()
            npc.last_action = str(raw_npc.get("last_action", "刚结束一轮例行巡视") or "刚结束一轮例行巡视").strip()
            npc.audible_signature = str(raw_npc.get("audible_signature", "脚步声") or "脚步声").strip()
            npc.current_location = str(raw_npc.get("current_location", npc_location) or npc_location).strip()
            npc.danger_level = str(raw_npc.get("danger_level", "低") or "低").strip()
            npc.can_speak = bool(raw_npc.get("can_speak", True))

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

            npc.knowledge_reliability = _clamp_ratio(raw_npc.get("knowledge_reliability", 0.75), 0.75)
            npc.deception_tendency = _clamp_ratio(raw_npc.get("deception_tendency", 0.1), 0.1)
            npc.corruption_level = _clamp_ratio(raw_npc.get("corruption_level", 0.0), 0.0)
            npc.current_state = str(raw_npc.get("current_state", "稳定") or "稳定").strip()
            bias_tags = raw_npc.get("bias_tags", [])
            npc.bias_tags = [str(item).strip() for item in bias_tags if str(item).strip()] if isinstance(bias_tags, list) else []
            known_rule_ids = raw_npc.get("known_rule_ids", [])
            npc.known_rule_ids = [str(item).strip() for item in known_rule_ids if str(item).strip()] if isinstance(known_rule_ids, list) else []

            memory = NPCMemory()
            if game_mode == GameModes.SINGLE.value and initial_player_id:
                memory.initialize_attitude_vector(initial_player_id)
                attitude_text = str(raw_npc.get("attitude", npc_guidance.get("npc_attitude", "")) or npc_guidance.get("npc_attitude", "")).strip()
                if any(keyword in attitude_text for keyword in ["友好", "温和", "热情"]):
                    memory.update_attitude_vector(initial_player_id, affection_delta=10, trust_delta=10)
                    memory.player_attitudes[initial_player_id] = NPCAttitude.FRIENDLY
                elif any(keyword in attitude_text for keyword in ["警告", "严厉", "冷淡", "不耐烦"]):
                    memory.update_attitude_vector(initial_player_id, suspicion_delta=15, trust_delta=-5)
                    memory.player_attitudes[initial_player_id] = NPCAttitude.SUSPICIOUS
                elif any(keyword in attitude_text for keyword in ["敌对", "威胁"]):
                    memory.update_attitude_vector(initial_player_id, hostility_delta=25, trust_delta=-15)
                    memory.player_attitudes[initial_player_id] = NPCAttitude.HOSTILE
                else:
                    memory.player_attitudes[initial_player_id] = NPCAttitude.NEUTRAL
            npc.memory = memory
            runtime_npcs.append(npc.to_dict())

        return runtime_npcs

    def _refresh_npcs_present_summary(self, session: GameSession) -> None:
        """兼容旧逻辑需要的 NPC 摘要字段。"""
        if not isinstance(getattr(session, "environment_state", None), dict):
            return
        env_state = session.environment_state
        npc_guidance = getattr(session, "npc_guidance", {}) or {}
        npcs = env_state.get("npcs", [])
        if not isinstance(npcs, list):
            env_state["npcs_present"] = []
            return

        env_state["npcs_present"] = [
            {
                "name": npc.get("name", "NPC"),
                "role": npc.get("role", ""),
                "attitude": npc.get("attitude", npc_guidance.get("npc_attitude", "")),
                "location": npc.get("current_location", npc.get("location", "")),
            }
            for npc in npcs
            if isinstance(npc, dict)
        ]

    def _ensure_story_runtime(
        self,
        session: GameSession,
        game_mode: str | None = None,
        initial_player_id: str | None = None,
    ) -> JsonObject:
        """确保规则载体、房间图和 NPC 运行时都已初始化。"""
        env_state = self._get_or_create_npc_simulator().ensure_runtime(session)
        room_graph = env_state.get("room_graph", {})
        if not isinstance(room_graph, dict) or not room_graph:
            env_state["room_graph"] = build_room_graph(session.scene_structure or {})
        if not isinstance(env_state.get("rule_carriers"), list) or not env_state.get("rule_carriers"):
            env_state["rule_carriers"] = self._carrier.build_runtime_rule_carriers(session)
        if not isinstance(env_state.get("npcs"), list) or not env_state.get("npcs"):
            env_state["npcs"] = self._build_runtime_npcs(session, game_mode=game_mode, initial_player_id=initial_player_id)
        self._refresh_npcs_present_summary(session)
        return env_state

    # ------------------------------------------------------------------
    # CarrierService 委托（规则载体相关）
    # ------------------------------------------------------------------

    def _normalize_rules_list(self, rules: list[RuleDict | Mapping[str, JsonValue] | str]) -> list[RuleDict]:
        """归一化规则列表，确保每条规则都有 original_index。"""
        return self._carrier.normalize_rules_list(rules)

    def _normalize_rule_text_for_dedup(self, text: str) -> str:
        """归一化规则文本用于去重（移除空白和标点）。"""
        return self._carrier.normalize_rule_text_for_dedup(text)

    def _get_player_recorded_rules(self, player: Player) -> list[str]:
        """获取玩家当前记录的规则文本。"""
        return self._carrier.get_player_recorded_rules(player)

    def _record_rule_texts(self, player: Player, rule_texts: list[str]) -> int:
        """将规则文本去重后写入玩家的规则笔记。"""
        return self._carrier.record_rule_texts(player, rule_texts)

    def _get_player_rules_for_display(self, session: GameSession, player: Player) -> list[str]:
        """获取用于展示和提示的玩家规则笔记。"""
        return self._carrier.get_player_rules_for_display(session, player)

    def _collect_team_rules_for_hint(self, session: GameSession, requester_id: str) -> JsonObject:
        """收集全队规则笔记用于提示模块的进度推断。"""
        return self._carrier.collect_team_rules_for_hint(session, requester_id)

    def _get_scene_rooms(self, session: GameSession) -> list[str]:
        """获取场景中的房间/区域列表。"""
        return self._carrier.get_scene_rooms(session)

    @staticmethod
    def _extract_rule_text(rule: object) -> str:
        """提取规则文本（静态工具方法）。"""
        return CarrierService.extract_rule_text(rule)

    def _get_multi_identity_runtime(self, session: GameSession) -> JsonObject:
        """安全获取多人身份运行时信息。"""
        return self._carrier.get_multi_identity_runtime(session)

    def _get_group_members_by_name(self, session: GameSession) -> dict[str, set[str]]:
        """收集身份组与共享可见组成员。"""
        return self._carrier.get_group_members_by_name(session)

    def _normalize_runtime_carrier(self, carrier: Mapping[str, object], default_location: str) -> JsonObject:
        """归一化运行时规则载体。"""
        return self._carrier.normalize_runtime_carrier(carrier, default_location)

    def _build_default_rule_carriers(self, session: GameSession) -> list[JsonObject]:
        """基于当前规则和多人身份信息构建默认规则载体池。"""
        return self._carrier.build_default_rule_carriers(session)

    def _build_runtime_rule_carriers(self, session: GameSession) -> list[JsonObject]:
        """优先使用模型生成的规则载体，缺失时回退默认兜底。"""
        return self._carrier.build_runtime_rule_carriers(session)

    def _resolve_player_initial_carrier_ids(self, session: GameSession, player: Player) -> list[str]:
        """读取某个玩家在多人模式下的开场可见载体列表。"""
        return self._carrier.resolve_player_initial_carrier_ids(session, player)

    def _carrier_visible_to_player(self, session: GameSession, carrier: Mapping[str, object], player: Player) -> bool:
        """判断载体对玩家是否可见。"""
        return self._carrier.carrier_visible_to_player(session, carrier, player)

    def _mark_carrier_discovered(self, session: GameSession, player: Player, carrier: JsonObject) -> None:
        """标记载体被玩家发现。"""
        self._carrier.mark_carrier_discovered(session, player, carrier)

    def _discover_rule_carriers_for_player(self, session: GameSession, player: Player, action_text: str) -> list[JsonObject]:
        """根据行动和房间位置发现新的规则载体。"""
        return self._carrier.discover_rule_carriers_for_player(session, player, action_text)

    def _format_discovered_carrier_text(self, carriers: list[JsonObject]) -> str:
        """格式化载体发现描述。"""
        return self._carrier.format_discovered_carrier_text(carriers)

    async def _send_initial_rule_exposure(
        self,
        session: GameSession,
        game_mode: str,
        lobby_players: list[tuple[str, str]],
    ) -> None:
        """按玩法处理初始规则载体的记录，并在最后发送纯目标长图。"""
        await self._carrier.send_initial_rule_exposure(session, game_mode, lobby_players)

    # ------------------------------------------------------------------
    # IdentityService 委托（身份任务卡 + 私聊下发）
    # ------------------------------------------------------------------

    def _build_player_private_brief(self, session: GameSession, player: Player) -> str:
        """构造多人模式私聊身份任务卡。"""
        return self._identity.build_player_private_brief(session, player)

    async def _send_private_text(self, target_user_id: str, target_user_name: str, content: str) -> bool:
        """向指定用户发起私聊并发送文本。"""
        return await self._identity.send_private_text(target_user_id, target_user_name, content)

    async def _send_multiplayer_private_infos(self, session: GameSession, lobby_players: list[tuple[str, str]], group_id: str | None = None) -> None:
        """多人模式：把身份任务卡通过私聊发送给每位玩家。"""
        await self._identity.send_multiplayer_private_infos(session, lobby_players, group_id)

    async def _notify_private_delivery_failures(self, players: list[tuple[str, str]], group_id: str) -> None:
        """在群聊提示私聊失败，但不泄露身份正文。"""
        await self._identity.notify_private_delivery_failures(players, group_id)

    # ------------------------------------------------------------------
    # ImageDeliveryService 委托（图片文件发送）
    # ------------------------------------------------------------------

    async def _send_image_path(self, image_path: str) -> None:
        """读取图片文件并发送（base64）。"""
        await self._image_delivery.send_image_path(image_path)

    # ------------------------------------------------------------------
    # EntranceService 委托（入场描述生成）
    # ------------------------------------------------------------------

    def _collect_scene_area_names(self, session: GameSession) -> list[str]:
        """从场景结构里收集可供叙事使用的区域名。"""
        return self._entrance.collect_scene_area_names(session)

    def _build_scene_overview_text(
        self,
        session: GameSession,
        *,
        current_location: str = "",
        plural: bool = False,
    ) -> str:
        """将内部结构化场景转换成玩家可见的整体描述。"""
        return self._entrance.build_scene_overview_text(session, current_location=current_location, plural=plural)

    def _has_opening_guidance(self, session: GameSession) -> bool:
        """判断当前是否有可展示的统一开场正文。"""
        return self._entrance.has_opening_guidance(session)

    async def _generate_entrance_description(self, session: GameSession) -> str:
        """生成统一的开场正文。"""
        return await self._entrance.generate_entrance_description(session)


# ----------------------------------------------------------------------
# 独立服务类（由 RuntimeSupportMixin 延迟装配并委托调用）
# ----------------------------------------------------------------------
# 每个服务类以 ``host`` 引用 Mixin 宿主，通过宿主访问 ctx、send_text、
# send_image、get_image_generator、工厂方法等共享依赖；服务之间不直接
# 互相引用，跨服务协作统一走 ``self._host`` 转发，保持职责单一。
# ----------------------------------------------------------------------


class CarrierService:
    """规则载体服务：构建、发现、记录、可见性判定。

    负责：
    - 规则文本归一化与去重（``normalize_rule_text_for_dedup``/``record_rule_texts``）
    - 规则载体池构建（``build_default_rule_carriers``/``build_runtime_rule_carriers``）
    - 载体可见性判定（``carrier_visible_to_player``）
    - 载体发现与标记（``discover_rule_carriers_for_player``/``mark_carrier_discovered``）
    - 开场规则曝光（``send_initial_rule_exposure``，需配合 IdentityService/ImageDeliveryService）
    """

    def __init__(self, host: "RuntimeSupportMixin") -> None:
        self._host = host

    @staticmethod
    def extract_rule_text(rule: object) -> str:
        """提取规则文本。"""
        if isinstance(rule, dict):
            return str(rule.get("text", rule.get("content", "")) or "").strip()
        return str(rule or "").strip()

    def normalize_rules_list(self, rules: list[RuleDict | Mapping[str, JsonValue] | str]) -> list[RuleDict]:
        """归一化规则列表，确保每条规则都有 original_index。

        Args:
            rules: 原始规则列表

        Returns:
            归一化后的规则列表
        """
        normalized: list[RuleDict] = []
        for i, r in enumerate(rules):
            if isinstance(r, dict):
                text = str(r.get("text", r.get("content", str(r))) or "").strip()

                oi_raw = r.get("original_index", i)
                if isinstance(oi_raw, int):
                    original_index: int | None = oi_raw
                elif oi_raw is None:
                    original_index = None
                elif isinstance(oi_raw, float) and oi_raw.is_integer():
                    original_index = int(oi_raw)
                else:
                    original_index = i

                rule_dict: RuleDict = {
                    "text": text,
                    "original_index": original_index,
                }
                if "source" in r:
                    rule_dict["source"] = str(r["source"])
                normalized.append(rule_dict)
            else:
                normalized.append({
                    "text": str(r or "").strip(),
                    "original_index": i,
                })
        return normalized

    def normalize_rule_text_for_dedup(self, text: str) -> str:
        """归一化规则文本用于去重（移除空白和标点）。

        Args:
            text: 原始文本

        Returns:
            归一化后的文本
        """
        text = re.sub(r"\s+", "", str(text or ""))
        text = re.sub(r"[，,。.!！？?；;:\"'《》【】\[\]（）()\-—…·]", "", text)

        return text

    def get_player_recorded_rules(self, player: Player) -> list[str]:
        """获取玩家当前记录的规则文本。"""
        raw_rules = getattr(player, "recorded_rules", [])
        if not isinstance(raw_rules, list):
            return []
        return [str(rule).strip() for rule in raw_rules if str(rule).strip()]

    def record_rule_texts(self, player: Player, rule_texts: list[str]) -> int:
        """将规则文本去重后写入玩家的规则笔记。"""
        merged_rules = self.get_player_recorded_rules(player)
        seen = {self.normalize_rule_text_for_dedup(text) for text in merged_rules if text}
        added_count = 0

        for raw_text in rule_texts:
            text = str(raw_text or "").strip()
            key = self.normalize_rule_text_for_dedup(text)
            if not text or not key or key in seen:
                continue
            merged_rules.append(text)
            seen.add(key)
            added_count += 1

        player.recorded_rules = merged_rules
        return added_count

    def get_player_rules_for_display(self, session: GameSession, player: Player) -> list[str]:
        """获取用于展示和提示的玩家规则笔记。"""
        _ = session
        return self.get_player_recorded_rules(player)

    def collect_team_rules_for_hint(self, session: GameSession, requester_id: str) -> JsonObject:
        """收集所有 alive 玩家的规则笔记，用于提示模块的进度推断。"""
        requester_rules: list[str] = []
        teammate_rules: list[JsonObject] = []
        alive_count = 0

        # 遍历所有玩家，只收集存活玩家的笔记；调用者本人单独存放
        for player in session.players.values():
            if player.status != PlayerStatus.ALIVE:
                continue
            alive_count += 1
            if player.player_id == requester_id:
                requester_rules = self.get_player_recorded_rules(player)
                continue
            teammate_rules.append({
                "player_name": player.name,
                "rules": self.get_player_recorded_rules(player),
            })

        # 仅当存活玩家数 > 1 时视为多人模式
        return {
            "is_multi_player": alive_count > 1,
            "requester_rules": requester_rules,
            "teammate_rules": teammate_rules,
        }

    def get_scene_rooms(self, session: GameSession) -> list[str]:
        """获取场景中的房间/区域列表。"""
        room_graph = build_room_graph(session.scene_structure or {})
        # wall_materials 是墙材质字典（非房间名），需排除避免污染房间列表
        rooms = [
            str(room).strip()
            for room in room_graph.keys()
            if str(room).strip() and room != "wall_materials"
        ]
        if rooms:
            return rooms
        if session.scene_name:
            return [session.scene_name]
        return ["起始位置"]

    def get_multi_identity_runtime(self, session: GameSession) -> JsonObject:
        """安全获取多人身份运行时信息。"""
        rule_network = session.rule_network if isinstance(getattr(session, "rule_network", None), dict) else {}
        multi_identity = rule_network.get("multi_identity", {})
        return multi_identity if isinstance(multi_identity, dict) else {}

    def get_group_members_by_name(self, session: GameSession) -> dict[str, set[str]]:
        """收集身份组与共享可见组成员。"""
        groups: dict[str, set[str]] = {}
        multi_identity = self.get_multi_identity_runtime(session)
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
        return groups

    def normalize_runtime_carrier(self, carrier: Mapping[str, object], default_location: str) -> JsonObject:
        """归一化运行时规则载体。"""
        revealed_rules_raw = carrier.get("revealed_rules", [])
        revealed_rules = [
            self.extract_rule_text(rule)
            for rule in revealed_rules_raw
            if self.extract_rule_text(rule)
        ] if isinstance(revealed_rules_raw, list) else []
        visible_to = carrier.get("visible_to", {"all_players": True})
        if not isinstance(visible_to, Mapping):
            visible_to = {"all_players": True}
        discovered_by = carrier.get("discovered_by", [])
        return {
            "carrier_id": str(carrier.get("carrier_id", "") or "").strip(),
            "title": str(carrier.get("title", "规则载体") or "规则载体").strip(),
            "location": str(carrier.get("location", default_location) or default_location).strip(),
            "area_scope": str(carrier.get("area_scope", carrier.get("location", default_location)) or default_location).strip(),
            "visible_to": dict(visible_to),
            "revealed_rules": revealed_rules,
            "carrier_type": str(carrier.get("carrier_type", "规则载体") or "规则载体").strip(),
            "description": str(carrier.get("description", "") or "").strip(),
            "is_discovered": bool(carrier.get("is_discovered", False)),
            "discovered_by": [str(item).strip() for item in discovered_by if str(item).strip()] if isinstance(discovered_by, list) else [],
            "initially_visible": bool(carrier.get("initially_visible", False)),
            "requires_action": bool(carrier.get("requires_action", True)),
        }

    def build_default_rule_carriers(self, session: GameSession) -> list[JsonObject]:
        """基于当前规则和多人身份信息构建默认规则载体池。"""
        rooms = self.get_scene_rooms(session)
        carriers: list[JsonObject] = []
        carrier_index = 0

        def add_carrier(
            title: str,
            location: str,
            revealed_rules: list[str],
            visible_to: JsonObject,
            *,
            description: str = "",
            initially_visible: bool = False,
        ) -> None:
            nonlocal carrier_index
            normalized_rules = [text for text in revealed_rules if text]
            if not normalized_rules:
                return
            carriers.append(
                {
                    "carrier_id": f"carrier_{carrier_index}",
                    "title": title,
                    "location": location,
                    "area_scope": location,
                    "visible_to": visible_to,
                    "revealed_rules": normalized_rules,
                    "carrier_type": "规则载体",
                    "description": description or f"你在{location}发现了一份与当前情境相关的规则载体。",
                    "is_discovered": False,
                    "discovered_by": [],
                    "initially_visible": initially_visible,
                }
            )
            carrier_index += 1

        all_rules = [self.extract_rule_text(rule) for rule in session.rules]
        all_rules = [text for text in all_rules if text]

        if session.game_mode == GameModes.MULTI.value:
            multi_identity = self.get_multi_identity_runtime(session)
            assignments = multi_identity.get("assignments", []) if isinstance(multi_identity, dict) else []
            common_rules = multi_identity.get("common_rules", []) if isinstance(multi_identity, dict) else []

            if isinstance(assignments, list):
                for index, assignment in enumerate(assignments):
                    if not isinstance(assignment, dict):
                        continue
                    player_id = str(assignment.get("player_id", "") or "").strip()
                    identity_name = str(assignment.get("identity_name", "身份规则") or "身份规则").strip()
                    duty_area = str(assignment.get("duty_area", "") or "").strip() or rooms[index % len(rooms)]
                    unique_rules = [self.extract_rule_text(rule) for rule in assignment.get("unique_rules", [])] if isinstance(assignment.get("unique_rules", []), list) else []
                    unique_rules = [text for text in unique_rules if text]
                    if unique_rules:
                        add_carrier(
                            title=f"{identity_name}相关记录",
                            location=duty_area,
                            revealed_rules=unique_rules[:2],
                            visible_to={"player_ids": [player_id]},
                            description=f"你在{duty_area}附近注意到一份只与你当前岗位相关的记录。",
                            initially_visible=False,
                        )

            common_rule_texts = [self.extract_rule_text(rule) for rule in common_rules] if isinstance(common_rules, list) else []
            common_rule_texts = [text for text in common_rule_texts if text]
            for index, rule_text in enumerate(common_rule_texts):
                add_carrier(
                    title="公共注意事项",
                    location=rooms[index % len(rooms)],
                    revealed_rules=[rule_text],
                    visible_to={"all_players": True},
                    initially_visible=False,
                )
        else:
            for index in range(0, len(all_rules), 2):
                chunk = all_rules[index:index + 2]
                add_carrier(
                    title=f"{session.scene_name}相关守则",
                    location=rooms[(index // 2) % len(rooms)],
                    revealed_rules=chunk,
                    visible_to={"all_players": True},
                    initially_visible=index == 0,
                )

        return carriers

    def build_runtime_rule_carriers(self, session: GameSession) -> list[JsonObject]:
        """优先使用模型生成的规则载体，缺失时回退默认兜底。"""
        default_location = session.scene_name or "起始位置"
        multi_identity = self.get_multi_identity_runtime(session)
        raw_carriers = multi_identity.get("rule_carriers", [])
        if isinstance(raw_carriers, list) and raw_carriers:
            normalized: list[JsonObject] = []
            for carrier in raw_carriers:
                if not isinstance(carrier, Mapping):
                    continue
                normalized_carrier = self.normalize_runtime_carrier(carrier, default_location)
                if normalized_carrier.get("carrier_id") and normalized_carrier.get("revealed_rules"):
                    normalized.append(normalized_carrier)
            if normalized:
                return normalized
        return self.build_default_rule_carriers(session)

    def resolve_player_initial_carrier_ids(self, session: GameSession, player: Player) -> list[str]:
        """读取某个玩家在多人模式下的开场可见载体列表。"""
        multi_identity = self.get_multi_identity_runtime(session)
        assignments = multi_identity.get("assignments", [])
        if not isinstance(assignments, list):
            return []
        for item in assignments:
            if not isinstance(item, dict):
                continue
            if str(item.get("player_id", "") or "").strip() != player.player_id:
                continue
            initial_ids = item.get("initial_visible_carrier_ids", [])
            if not isinstance(initial_ids, list):
                return []
            return [str(carrier_id).strip() for carrier_id in initial_ids if str(carrier_id).strip()]
        return []

    def carrier_visible_to_player(self, session: GameSession, carrier: Mapping[str, object], player: Player) -> bool:
        """判断载体对玩家是否可见。"""
        visible_to = carrier.get("visible_to", {})
        if not isinstance(visible_to, Mapping):
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
            groups = self.get_group_members_by_name(session)
            checks.append(any(player.player_id in groups.get(str(name).strip(), set()) for name in group_names))

        if not checks:
            return True
        return any(checks)

    def mark_carrier_discovered(self, session: GameSession, player: Player, carrier: JsonObject) -> None:
        """标记载体被玩家发现，并记录到 per_player_visibility。"""
        discovered_by = carrier.get("discovered_by", [])
        if not isinstance(discovered_by, list):
            discovered_by = []
        if player.player_id not in discovered_by:
            discovered_by.append(player.player_id)
        carrier["discovered_by"] = discovered_by
        carrier["is_discovered"] = True

        env_state = self._host._ensure_story_runtime(session)
        per_player_visibility = env_state.get("per_player_visibility", {})
        if not isinstance(per_player_visibility, dict):
            per_player_visibility = {}
            env_state["per_player_visibility"] = per_player_visibility
        player_seen = per_player_visibility.get(player.player_id, [])
        if not isinstance(player_seen, list):
            player_seen = []
        carrier_id = str(carrier.get("carrier_id", "") or "").strip()
        if carrier_id and carrier_id not in player_seen:
            player_seen.append(carrier_id)
        per_player_visibility[player.player_id] = player_seen

    def discover_rule_carriers_for_player(self, session: GameSession, player: Player, action_text: str) -> list[JsonObject]:
        """根据行动和房间位置发现新的规则载体。"""
        env_state = self._host._ensure_story_runtime(session)
        carriers = env_state.get("rule_carriers", [])
        if not isinstance(carriers, list):
            return []

        search_keywords = ["找", "搜", "检查", "观察", "查看", "翻", "调查", "阅读", "翻阅", "检视"]
        passive_keywords = ["靠近", "进入", "前往", "去", "到", "来到", "走进"]
        should_search = any(keyword in action_text for keyword in search_keywords)
        allow_passive_discovery = any(keyword in action_text for keyword in passive_keywords)
        discovered: list[JsonObject] = []

        for carrier in carriers:
            if not isinstance(carrier, dict):
                continue
            if not self.carrier_visible_to_player(session, carrier, player):
                continue
            if str(carrier.get("location", "") or "").strip() != str(player.location or "").strip():
                continue
            discovered_by = carrier.get("discovered_by", [])
            if isinstance(discovered_by, list) and player.player_id in discovered_by:
                continue
            requires_action = bool(carrier.get("requires_action", True))
            if requires_action and not should_search:
                continue
            if not requires_action and not (should_search or allow_passive_discovery):
                continue
            self.mark_carrier_discovered(session, player, carrier)
            revealed_rules = carrier.get("revealed_rules", [])
            texts = [str(item).strip() for item in revealed_rules if str(item).strip()] if isinstance(revealed_rules, list) else []
            self.record_rule_texts(player, texts)
            discovered.append(carrier)
            break

        return discovered

    def format_discovered_carrier_text(self, carriers: list[JsonObject]) -> str:
        """格式化载体发现描述。"""
        sections: list[str] = []
        for carrier in carriers:
            title = str(carrier.get("title", "规则载体") or "规则载体").strip()
            location = str(carrier.get("location", "") or "").strip()
            description = str(carrier.get("description", "") or "").strip()
            rules = carrier.get("revealed_rules", [])
            rule_lines = [f"“{str(item).strip()}”" for item in rules if str(item).strip()] if isinstance(rules, list) else []
            header = f"你在{location}发现了《{title}》。" if location else f"你发现了《{title}》。"
            body_parts: list[str] = []
            if description:
                body_parts.append(description)
            if rule_lines:
                body_parts.append("你从上面记下来的几句要紧内容是：" + "；".join(rule_lines))
            body = "\n".join(body_parts).strip()
            sections.append(f"{header}\n{body}".strip())
        return "\n\n".join(section for section in sections if section.strip())

    async def send_initial_rule_exposure(
        self,
        session: GameSession,
        game_mode: str,
        lobby_players: list[tuple[str, str]],
    ) -> None:
        """按玩法处理初始规则载体的记录，并在最后发送纯目标长图。

        规则载体给出的规则只记录到玩家笔记（通过 `/rg 规则` 查看），
        不再以规则图形式占用开局展示位；开局第三张图只展示单一目标。
        """
        image_generator = self._host.get_image_generator()

        npc_guidance = getattr(session, "npc_guidance", {}) or {}
        guidance_method = ""
        if isinstance(npc_guidance, Mapping):
            guidance_method = str(npc_guidance.get("guidance_method", "") or "").strip().lower()

        if guidance_method == "rule_carrier":
            env_state = self._host._ensure_story_runtime(session)
            carriers = env_state.get("rule_carriers", [])
            if isinstance(carriers, list):
                initial_carriers = [carrier for carrier in carriers if isinstance(carrier, dict) and bool(carrier.get("initially_visible", False))]
                if not initial_carriers:
                    initial_carriers = []

                if game_mode == GameModes.MULTI.value:
                    name_by_id = {str(pid): str(name) for pid, name in lobby_players if str(pid).strip()}
                    for player in session.players.values():
                        preferred_ids = set(self.resolve_player_initial_carrier_ids(session, player))
                        visible = [
                            carrier
                            for carrier in carriers
                            if isinstance(carrier, dict)
                            and carrier.get("carrier_id") in preferred_ids
                            and self.carrier_visible_to_player(session, carrier, player)
                        ]
                        if not visible:
                            visible = [carrier for carrier in initial_carriers if self.carrier_visible_to_player(session, carrier, player)]
                        if not visible:
                            continue
                        for carrier in visible:
                            self.mark_carrier_discovered(session, player, carrier)
                            rules = carrier.get("revealed_rules", [])
                            texts = [str(item).strip() for item in rules if str(item).strip()] if isinstance(rules, list) else []
                            self.record_rule_texts(player, texts)

                        content = self.format_discovered_carrier_text(visible)
                        if content:
                            # 通过 IdentityService 下发私聊
                            await self._host._send_private_text(player.player_id, name_by_id.get(player.player_id, player.name), content)
                            await asyncio.sleep(0.2)
                else:
                    player = next(iter(session.players.values()), None)
                    if player is not None:
                        visible = [carrier for carrier in initial_carriers if self.carrier_visible_to_player(session, carrier, player)]
                        if visible:
                            for carrier in visible:
                                self.mark_carrier_discovered(session, player, carrier)
                                rules = carrier.get("revealed_rules", [])
                                texts = [str(item).strip() for item in rules if str(item).strip()] if isinstance(rules, list) else []
                                self.record_rule_texts(player, texts)

        goal_text = str(session.win_condition or "").strip()
        if goal_text:
            goal_image = await image_generator.generate_goal_image(
                goal_text=goal_text,
                scene_name=session.scene_name,
            )
            session.image_paths.append(goal_image)
            # 通过 ImageDeliveryService 发送图片
            await self._host._send_image_path(goal_image)


class IdentityService:
    """身份任务卡服务：私聊身份卡构建与下发。

    负责：
    - 身份任务卡文本构建（``build_player_private_brief``）
    - 私聊流打开与文本发送（``send_private_text``）
    - 多人模式批量身份卡下发（``send_multiplayer_private_infos``）
    - 私聊失败时的群聊补救提示（``notify_private_delivery_failures``）
    """

    def __init__(self, host: "RuntimeSupportMixin") -> None:
        self._host = host

    def build_player_private_brief(self, session: GameSession, player: Player) -> str:
        """构造多人模式私聊身份任务卡。"""
        lines: list[str] = []
        scene = str(getattr(session, "scene_name", "") or "").strip()
        if scene:
            lines.append(f"场景：{scene}")

        if player.identity:
            lines.append(f"你的身份：{player.identity}")
        if player.identity_description:
            lines.append(f"身份简介：{player.identity_description}")
        if player.task_brief:
            lines.append("")
            lines.append("当前任务：")
            lines.append(str(player.task_brief))
        if player.duty_area:
            lines.append("")
            lines.append(f"责任区域：{player.duty_area}")
        if player.initial_observations:
            lines.append("")
            lines.append("开场观察：")
            for idx, observation in enumerate(player.initial_observations, start=1):
                lines.append(f"{idx}. {observation}")

        if player.exclusive_info:
            lines.append("")
            lines.append("独有信息：")
            lines.append(str(player.exclusive_info))

        lines.append("")
        lines.append("规则不会一次性直接告诉你。")
        lines.append("请结合探索、NPC 行为、规则载体和他人信息，自行推理并使用 `/rg 记录规则 <内容>` 记录。")

        return "\n".join(lines).strip() or "身份信息生成失败。"

    async def send_private_text(self, target_user_id: str, target_user_name: str, content: str) -> bool:
        """向指定用户发起私聊并发送文本。"""
        try:
            uid = str(target_user_id or "").strip()
            if not uid:
                logger.error("私聊发送失败：目标用户 ID 为空")
                return False

            stream_result = await self._host.ctx.chat.open_session(
                platform="qq",
                chat_type="private",
                user_id=uid,
            )
            if not isinstance(stream_result, dict) or not stream_result.get("success"):
                error = stream_result.get("error", "未知错误") if isinstance(stream_result, dict) else "返回格式错误"
                logger.error("无法打开用户 %s 的私聊流：%s", target_user_name or uid, error)
                return False

            private_stream_id = str(stream_result.get("session_id", "") or "").strip()
            if not private_stream_id:
                logger.error("用户 %s 的私聊流缺少 session_id", target_user_name or uid)
                return False

            return await self._host.ctx.send.text(content, private_stream_id)
        except Exception as e:
            logger.error("向用户 %s 发送私聊消息失败：%s", target_user_name or target_user_id, e, exc_info=True)
            return False

    async def send_multiplayer_private_infos(self, session: GameSession, lobby_players: list[tuple[str, str]], group_id: str | None = None) -> None:
        """多人模式：把身份任务卡通过私聊发送给每位玩家。

        Args:
            session: 游戏会话
            lobby_players: 大厅玩家列表
            group_id: 群组ID（私聊失败时用于在群内提示补救方式）
        """
        failed_players: list[tuple[str, str]] = []  # (pid, name)

        try:
            # 使用 lobby_players 的名字更可信（来自当前群聊上下文）
            name_by_id: dict[str, str] = {str(pid): str(name) for pid, name in (lobby_players or []) if str(pid)}

            for pid, p in (session.players or {}).items():
                target_name = name_by_id.get(str(pid), p.name)
                content = self.build_player_private_brief(session, p)
                ok = await self.send_private_text(str(pid), str(target_name or ""), content)
                if not ok:
                    logger.warning(f"向玩家 {pid} 私聊发送身份信息失败，已跳过群聊正文兜底")
                    failed_players.append((str(pid), str(target_name or "")))
                await asyncio.sleep(0.2)

            # 私聊失败时只提示补救方式，不在群聊泄露身份正文
            if failed_players and group_id:
                await self.notify_private_delivery_failures(failed_players, group_id)

        except Exception as e:
            logger.error(f"多人模式私聊下发失败: {e}")

    async def notify_private_delivery_failures(self, players: list[tuple[str, str]], group_id: str) -> None:
        """在群聊提示私聊失败，但不泄露身份正文。

        Args:
            players: (player_id, player_name) 列表
            group_id: 群组ID
        """
        try:
            _ = group_id
            player_names = [name or pid for pid, name in players]
            mentions = "、".join(f"@{name}" for name in player_names)
            message = (
                "以下玩家的身份信息未能通过私聊送达："
                f"{mentions}\n"
                "为避免剧透与泄露，群内不会展示身份正文。"
                "请先添加机器人好友或检查私聊权限，然后重新使用 `/rg 身份` 获取。"
            )
            await self._host.send_text(message)
        except Exception as e:
            logger.error(f"群聊提示私聊失败信息时出错: {e}")


class ImageDeliveryService:
    """图片发送服务：图片文件读取与 base64 发送。

    负责：
    - 读取本地图片文件并通过宿主 ``send_image`` 推送（``send_image_path``）
    """

    def __init__(self, host: "RuntimeSupportMixin") -> None:
        self._host = host

    async def send_image_path(self, image_path: str) -> None:
        """读取图片文件并发送（base64）。"""
        try:
            with open(image_path, "rb") as f:
                image_bytes = f.read()
            image_base64 = base64.b64encode(image_bytes).decode("ascii")
            await self._host.send_image(image_base64)
        except FileNotFoundError:
            logger.error(f"图片文件不存在: {image_path}")
            await self._host.send_text("图片生成失败，请稍后重试。")
        except Exception as e:
            logger.error(f"发送图片失败: {e}")
            await self._host.send_text("发送图片时出错，请稍后重试。")


class EntranceService:
    """入场描述服务：开场正文与场景概览生成。

    负责：
    - 场景区域名收集（``collect_scene_area_names``）
    - 玩家可见的场景整体描述（``build_scene_overview_text``）
    - 开场正文存在性判断（``has_opening_guidance``）
    - 统一开场正文生成（``generate_entrance_description``，调用 LLM）
    """

    def __init__(self, host: "RuntimeSupportMixin") -> None:
        self._host = host

    def collect_scene_area_names(self, session: GameSession) -> list[str]:
        """从场景结构里收集可供叙事使用的区域名。"""
        scene_structure = getattr(session, "scene_structure", {}) or {}
        if not isinstance(scene_structure, Mapping):
            return []

        names: list[str] = []
        floors = scene_structure.get("floors", [])
        if isinstance(floors, list):
            for floor in floors:
                if not isinstance(floor, Mapping):
                    continue
                areas = floor.get("areas") or floor.get("rooms") or []
                if isinstance(areas, list):
                    names.extend(str(area).strip() for area in areas if str(area).strip())

        special_areas = scene_structure.get("special_areas", [])
        if isinstance(special_areas, list):
            names.extend(str(area).strip() for area in special_areas if str(area).strip())

        deduped: list[str] = []
        seen: set[str] = set()
        for name in names:
            key = re.sub(r"\s+", "", name)
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(name)
        return deduped

    def build_scene_overview_text(
        self,
        session: GameSession,
        *,
        current_location: str = "",
        plural: bool = False,
    ) -> str:
        """将内部结构化场景转换成玩家可见的整体描述。"""
        scene_structure = getattr(session, "scene_structure", {}) or {}
        if not isinstance(scene_structure, Mapping):
            scene_structure = {}

        scene_name = str(getattr(session, "scene_name", "") or "这里").strip() or "这里"
        building_type = str(scene_structure.get("building_type", "") or "").strip()
        overall_layout = str(scene_structure.get("overall_layout", "") or "").strip()
        scene_impression = str(scene_structure.get("scene_impression", "") or "").strip()
        if scene_impression:
            description = scene_impression
        else:
            intro_parts = [scene_name]
            if building_type:
                intro_parts.append(f"更像一处{building_type}")
            if overall_layout:
                intro_parts.append(f"整体呈{overall_layout}")
            description = "，".join(intro_parts)

        if current_location:
            viewpoint = "你们" if plural else "你"
            return f"{description}\n\n此刻{viewpoint}位于：{current_location}"
        return description

    def has_opening_guidance(self, session: GameSession) -> bool:
        """判断当前是否有可展示的统一开场正文。"""
        env_state = getattr(session, "environment_state", {}) or {}
        if not isinstance(env_state, Mapping):
            return False
        entrance_description = str(env_state.get("entrance_description", "")).strip()
        return bool(entrance_description)

    async def generate_entrance_description(self, session: GameSession) -> str:
        """生成统一的开场正文。

        Args:
            session: 游戏会话

        Returns:
            开场正文
        """
        llm_client = LLMClient()
        npc_guidance = getattr(session, "npc_guidance", {}) or {}
        guidance_method = ""
        npc_name = ""
        npc_role = ""
        npc_behavior = ""
        conversation_intent = ""
        rule_carrier_title = ""
        rule_carrier_description = ""
        hinted_rule_texts: list[str] = []
        if isinstance(npc_guidance, Mapping):
            guidance_method = str(npc_guidance.get("guidance_method", "") or "").strip().lower()
            npc_name = str(npc_guidance.get("npc_name", "") or "").strip()
            npc_role = str(npc_guidance.get("npc_role", "") or "").strip()
            npc_behavior = str(npc_guidance.get("npc_behavior", "") or "").strip()
            conversation_intent = str(npc_guidance.get("conversation_intent", "") or "").strip()
            rule_carrier_title = str(npc_guidance.get("rule_carrier_title", "") or "").strip()
            rule_carrier_description = str(npc_guidance.get("rule_carrier_description", "") or "").strip()
            hinted_raw = npc_guidance.get("hinted_rule_texts", [])
            if isinstance(hinted_raw, list):
                hinted_rule_texts = [str(item).strip() for item in hinted_raw if str(item).strip()][:2]

        scene_structure = getattr(session, "scene_structure", {}) or {}
        if not isinstance(scene_structure, Mapping):
            scene_structure = {}
        building_type = str(scene_structure.get("building_type", "") or "").strip()
        overall_layout = str(scene_structure.get("overall_layout", "") or "").strip()
        scene_impression = str(scene_structure.get("scene_impression", "") or "").strip()
        floors = scene_structure.get("floors", [])
        floor_summary: list[str] = []
        if isinstance(floors, list):
            for floor in floors[:4]:
                if not isinstance(floor, Mapping):
                    continue
                floor_name = str(floor.get("name", "") or "").strip()
                rooms = floor.get("rooms", [])
                room_names = [str(room).strip() for room in rooms[:6] if str(room).strip()] if isinstance(rooms, list) else []
                if floor_name or room_names:
                    floor_summary.append(f"{floor_name}：{'、'.join(room_names)}".strip("："))

        player = next(iter(session.players.values()), None)
        start_location = str(getattr(player, "location", "") or "").strip() if player else ""
        if not start_location:
            for floor in floors if isinstance(floors, list) else []:
                if isinstance(floor, Mapping):
                    rooms = floor.get("rooms", [])
                    if isinstance(rooms, list) and rooms:
                        start_location = str(rooms[0] or "").strip()
                        if start_location:
                            break
        start_location = start_location or session.scene_name

        plural_hint = ""
        default_entrance = f"你站在{start_location}，周围的布局一时还看不全。眼前没有人主动解释这里的情况。"
        if getattr(session, "game_mode", GameModes.SINGLE.value) == GameModes.MULTI.value:
            plural_hint = "\n8. 必须使用第二人称复数'你们'，禁止出现'你'、'你的'等单数表述\n9. 描述一行人一起进入场景，而不是单独一人\n"
            default_entrance = f"你们站在{start_location}，周围的布局一时还看不全。眼前没有人主动解释这里的情况。"
        if guidance_method == "none":
            if getattr(session, "game_mode", GameModes.SINGLE.value) == GameModes.MULTI.value:
                default_entrance = f"你们停在{start_location}。附近没有人，能看见的出入口和陈设都保持着日常使用过的样子，只是暂时没人回应你们。"
            else:
                default_entrance = f"你停在{start_location}。附近没有人，能看见的出入口和陈设都保持着日常使用过的样子，只是暂时没人回应你。"

        system_prompt = f"""你负责生成规则怪谈开局的第二部分：玩家此刻所在的具体场景，以及现场自然发生的交流。

要求：
1. 只写此刻现场，不复述场所历史、公共背景、玩家身份、到来原因或游戏目标。
2. 第一两句必须明确玩家具体站在哪里、最近的出入口或相邻区域是什么、眼前最显眼的物件或人物在哪里。禁止只写“来到某地”“气氛不安”“四周诡异”这类空泛句子。
3. 空间描述优先使用给定的真实区域与布局，不要虚构结构中不存在的房间。除了位置关系，再挑二到三个能被看到、听到或闻到的具体细节展开（正在运转的设备、贴在墙上的纸、柜台上的物件、光线来源、某个方向传来的动静），让现场有可探索感。
4. 如果有 NPC，由你根据他的身份、动作、交流动机和当下事件即时决定是否开口、说多少、说什么。输入中没有预制台词，也不要套用欢迎、交接、培训、提醒新人、分配任务等固定流程。
5. NPC 可以不理会玩家、说到一半被打断、答非所问、认错人、先处理手头事情、向玩家索取信息，或根本不开口。交流必须服务于他的当下目的，而不是服务于教程。
6. 如果输入提供了“NPC可能顺口带出的说法”，你可以让 NPC 在交流中用自己的口吻自然带出其中一条：可以改写、说一半、夹在抱怨里、当成个人经验讲，但不要逐字宣读，不要说成“规则”“守则”，更不要一次抛出多条。也可以选择完全不提。
7. 除了上述说法，NPC 不知道其他后台规则和隐藏真相，禁止他给出规则清单、编号事项、通关提示或系统说明。
8. 如果现场有纸面载体（守则、告示、便签等），把它作为场景中的实物写出来：它贴在哪、压在哪、被谁递过来、纸面状态如何。只让玩家“看见它的存在”，不要展开正文内容。
9. 如果没有 NPC，就用可见物件、正在运行的设备、远近声源和出入口状态建立现场，不要硬造人物，也不要默认玩家从昏迷中醒来。
10. 只输出一段连贯正文，不加标题、不分点、不总结。长度 180-300 字。{plural_hint}
11. 使用具体名词和动作，减少“似乎、仿佛、让人不安、诡异、阴森、死寂”等抽象气氛词。
12. 不要替玩家决定行动，也不要用结尾句催促玩家去某处。

返回纯文本，不要JSON格式。"""


        user_prompt = f"""场景名称：{session.scene_name}

建筑类型：{building_type}
总体布局：{overall_layout}
可用区域：{'；'.join(floor_summary)}
起始位置：{start_location}
第一空间印象：{scene_impression}
现场类型：{"没有 NPC" if guidance_method == "none" else "现场存在 NPC 或载体"}

NPC/载体状态：
- NPC姓名：{npc_name}
- NPC角色：{npc_role}
- 此刻可见动作：{npc_behavior}
- 交流动机：{conversation_intent}
- NPC可能顺口带出的说法（可改写可不提，最多带一条）：{'；'.join(hinted_rule_texts) if hinted_rule_texts else '无'}
- 载体名称：{rule_carrier_title}
- 载体所在方式：{rule_carrier_description}

直接写出此刻的场景与自然交流。"""

        try:
            response = await llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.8,
            )

            opening = response.clean_content.strip()
            opening = re.sub(r"(?m)^\s*(?:场景|开场|正文)\s*[:：]\s*", "", opening)
            blocked_markers = ("以下规则", "规则如下", "通关条件", "你的目标", "玩家身份", "你必须", "你们必须", "严禁")
            location_is_clear = bool(start_location and start_location in opening)
            content_crossed_boundary = any(marker in opening for marker in blocked_markers)
            if content_crossed_boundary or not location_is_clear or len(opening) < 120:
                correction_prompt = f"""请重写上一版开场。

必须做到：
1. 第一两句明确出现起始位置“{start_location}”，并说明它附近的出入口、相邻区域或显眼物件。
2. 补足二到三个可见、可听或可闻的具体环境细节，让现场有可探索感。
3. 不得复述背景、身份、目标或通关条件；不得输出规则清单或编号事项。
4. NPC 是否说话、说多少由现场和他的交流动机决定，不使用欢迎、交接、培训、提醒新人模板；若有可顺口带出的说法，最多自然带一条。
5. 若现场有纸面载体，只写出它的位置和状态，不展开内容。
6. 只输出 180-300 字的一段现场正文。

上一版：{opening}"""
                corrected = await llm_client.call(
                    prompt=correction_prompt,
                    system_prompt=system_prompt,
                    temperature=0.9,
                )
                corrected_text = corrected.clean_content.strip()
                corrected_text = re.sub(r"(?m)^\s*(?:场景|开场|正文)\s*[:：]\s*", "", corrected_text)
                if corrected_text:
                    opening = corrected_text
            return opening or default_entrance
        except Exception as e:
            logger.error(f"生成入场描述失败: {e}")
            # 返回默认描述
            return default_entrance
