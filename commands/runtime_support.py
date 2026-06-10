from __future__ import annotations

import asyncio, base64, logging, re
from collections.abc import Mapping
from ..core import AsyncImageGenerator, GameSession, LLMClient, Player, PlayerStatus
from ..core.services import GameGenerator, NPCSimulator
from ..common import GameModes, JsonObject, JsonValue, RuleDict
from ..systems import ClueDiscoverySystem, EnvironmentEvolutionSystem, GameTimeManager, MultiplayerPhysicsSystem, NPC, NPCAttitude, NPCMemory, RuleMutationSystem, build_room_graph

logger = logging.getLogger(__name__)


class RuntimeSupportMixin:
    """运行时辅助与规则载体/NPC 相关工具。"""

    def _has_opening_guidance(self, session: GameSession) -> bool:
        """判断当前开场是否需要展示 NPC/载体引导段落。"""
        npc_guidance = getattr(session, "npc_guidance", {}) or {}
        if not isinstance(npc_guidance, Mapping) or not npc_guidance:
            return False
        guidance_method = str(npc_guidance.get("guidance_method", "") or "").strip().lower()
        if guidance_method == "none":
            return False
        return any(
            str(npc_guidance.get(key, "") or "").strip()
            for key in ("npc_behavior", "npc_dialogue", "rule_carrier_title", "rule_carrier_description")
        )

    def _get_game_generator(self) -> GameGenerator:
        """获取或创建 GameGenerator（延迟初始化）"""
        if self._game_generator is None:
            self._game_generator = GameGenerator()
        return self._game_generator

    def _get_or_create_environment_system(self, game_states: dict[str, JsonObject]) -> EnvironmentEvolutionSystem:
        """获取或创建环境演化系统（延迟初始化）"""
        if self._environment_system is None:
            self._environment_system = EnvironmentEvolutionSystem(game_states)
        return self._environment_system

    def _get_or_create_npc_simulator(self) -> NPCSimulator:
        """获取或创建 NPC 模拟器。"""
        if self._npc_simulator is None:
            self._npc_simulator = NPCSimulator()
        return self._npc_simulator

    def _get_or_create_game_time_manager(self) -> GameTimeManager:
        """获取或创建游戏时间管理器（延迟初始化）"""
        if self._game_time_manager is None:
            self._game_time_manager = GameTimeManager()
        return self._game_time_manager

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

    def _get_or_create_clue_discovery_system(self) -> ClueDiscoverySystem:
        """获取或创建线索发现系统（延迟初始化）"""
        if self._clue_discovery_system is None:
            self._clue_discovery_system = ClueDiscoverySystem()
        return self._clue_discovery_system

    def _get_or_create_multiplayer_physics_system(self) -> MultiplayerPhysicsSystem:
        """获取或创建多人物理系统（延迟初始化）"""
        if self._multiplayer_physics_system is None:
            self._multiplayer_physics_system = MultiplayerPhysicsSystem()
        return self._multiplayer_physics_system

    async def _send_private_text(self, target_user_id: str, target_user_name: str, content: str) -> bool:
        """向指定用户发起私聊并发送文本。"""
        try:
            uid = str(target_user_id or "").strip()
            if not uid:
                return False

            stream_result = await self.ctx.chat.get_stream_by_user_id(user_id=uid)
            stream_info = stream_result.get("stream") if isinstance(stream_result, dict) else None
            if not isinstance(stream_info, dict):
                logger.warning("未找到用户 %s 的私聊流，无法发送身份信息", target_user_name or uid)
                return False

            private_stream_id = str(stream_info.get("session_id", "") or "").strip()
            if not private_stream_id:
                return False

            return await self.ctx.send.text(content, private_stream_id)
        except Exception as e:
            logger.error(f"发送私聊消息失败: {e}")
            return False

    def _build_player_private_brief(self, session: GameSession, player: Player) -> str:
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

    async def _send_multiplayer_private_infos(self, session: GameSession, lobby_players: list[tuple[str, str]], group_id: str | None = None) -> None:
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
                content = self._build_player_private_brief(session, p)
                ok = await self._send_private_text(str(pid), str(target_name or ""), content)
                if not ok:
                    logger.warning(f"向玩家 {pid} 私聊发送身份信息失败，已跳过群聊正文兜底")
                    failed_players.append((str(pid), str(target_name or "")))
                await asyncio.sleep(0.2)
            
            # 私聊失败时只提示补救方式，不在群聊泄露身份正文
            if failed_players and group_id:
                await self._notify_private_delivery_failures(failed_players, group_id)
                
        except Exception as e:
            logger.error(f"多人模式私聊下发失败: {e}")

    async def _notify_private_delivery_failures(self, players: list[tuple[str, str]], group_id: str) -> None:
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
            await self.send_text(message)
        except Exception as e:
            logger.error(f"群聊提示私聊失败信息时出错: {e}")

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
        避免展示层继续出现“有时是数值、有时是等级”的语义混乱。
        """
        raw_fatigue = getattr(player, "fatigue", None)
        if isinstance(raw_fatigue, (int, float)):
            fatigue_value = max(0, min(100, int(raw_fatigue)))
            return self._fatigue_level_from_value(fatigue_value)
        return self._fatigue_level_from_value(max(0, min(100, 100 - int(player.health))))

    def _normalize_rules_list(self, rules: list[RuleDict | Mapping[str, JsonValue] | str]) -> list[RuleDict]:

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

    def _normalize_rule_text_for_dedup(self, text: str) -> str:
        """归一化规则文本用于去重（移除空白和标点）。
        
        Args:
            text: 原始文本
            
        Returns:
            归一化后的文本
        """
        text = re.sub(r"\s+", "", str(text or ""))
        text = re.sub(r"[，,。.!！？?；;:\"'《》【】\[\]（）()\-—…·]", "", text)

        return text

    def _get_player_recorded_rules(self, player: Player) -> list[str]:
        """获取玩家当前记录的规则文本。"""
        raw_rules = getattr(player, "recorded_rules", [])
        if not isinstance(raw_rules, list):
            return []
        return [str(rule).strip() for rule in raw_rules if str(rule).strip()]

    def _record_rule_texts(self, player: Player, rule_texts: list[str]) -> int:
        """将规则文本去重后写入玩家的规则笔记。"""
        merged_rules = self._get_player_recorded_rules(player)
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

    def _get_player_rules_for_display(self, session: GameSession, player: Player) -> list[str]:
        """获取用于展示和提示的玩家规则笔记。"""
        _ = session
        return self._get_player_recorded_rules(player)

    def _get_scene_rooms(self, session: GameSession) -> list[str]:
        """获取场景中的房间/区域列表。"""
        room_graph = build_room_graph(session.scene_structure or {})
        rooms = [str(room).strip() for room in room_graph.keys() if str(room).strip()]
        if rooms:
            return rooms
        if session.scene_name:
            return [session.scene_name]
        return ["起始位置"]

    @staticmethod

    def _extract_rule_text(rule: object) -> str:
        if isinstance(rule, dict):
            return str(rule.get("text", rule.get("content", "")) or "").strip()
        return str(rule or "").strip()

    def _get_multi_identity_runtime(self, session: GameSession) -> JsonObject:
        """安全获取多人身份运行时信息。"""
        rule_network = session.rule_network if isinstance(getattr(session, "rule_network", None), dict) else {}
        multi_identity = rule_network.get("multi_identity", {})
        return multi_identity if isinstance(multi_identity, dict) else {}

    def _get_group_members_by_name(self, session: GameSession) -> dict[str, set[str]]:
        """收集身份组与共享可见组成员。"""
        groups: dict[str, set[str]] = {}
        multi_identity = self._get_multi_identity_runtime(session)
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

    def _normalize_runtime_carrier(self, carrier: Mapping[str, object], default_location: str) -> JsonObject:
        """归一化运行时规则载体。"""
        revealed_rules_raw = carrier.get("revealed_rules", [])
        revealed_rules = [
            self._extract_rule_text(rule)
            for rule in revealed_rules_raw
            if self._extract_rule_text(rule)
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

    def _build_default_rule_carriers(self, session: GameSession) -> list[JsonObject]:
        """基于当前规则和多人身份信息构建默认规则载体池。"""
        rooms = self._get_scene_rooms(session)
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

        all_rules = [self._extract_rule_text(rule) for rule in session.rules]
        all_rules = [text for text in all_rules if text]

        if session.game_mode == GameModes.MULTI.value:
            multi_identity = self._get_multi_identity_runtime(session)
            assignments = multi_identity.get("assignments", []) if isinstance(multi_identity, dict) else []
            common_rules = multi_identity.get("common_rules", []) if isinstance(multi_identity, dict) else []

            if isinstance(assignments, list):
                for index, assignment in enumerate(assignments):
                    if not isinstance(assignment, dict):
                        continue
                    player_id = str(assignment.get("player_id", "") or "").strip()
                    identity_name = str(assignment.get("identity_name", "身份规则") or "身份规则").strip()
                    duty_area = str(assignment.get("duty_area", "") or "").strip() or rooms[index % len(rooms)]
                    unique_rules = [self._extract_rule_text(rule) for rule in assignment.get("unique_rules", [])] if isinstance(assignment.get("unique_rules", []), list) else []
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

            common_rule_texts = [self._extract_rule_text(rule) for rule in common_rules] if isinstance(common_rules, list) else []
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

    def _build_runtime_rule_carriers(self, session: GameSession) -> list[JsonObject]:
        """优先使用模型生成的规则载体，缺失时回退默认兜底。"""
        default_location = session.scene_name or "起始位置"
        multi_identity = self._get_multi_identity_runtime(session)
        raw_carriers = multi_identity.get("rule_carriers", [])
        if isinstance(raw_carriers, list) and raw_carriers:
            normalized: list[JsonObject] = []
            for carrier in raw_carriers:
                if not isinstance(carrier, Mapping):
                    continue
                normalized_carrier = self._normalize_runtime_carrier(carrier, default_location)
                if normalized_carrier.get("carrier_id") and normalized_carrier.get("revealed_rules"):
                    normalized.append(normalized_carrier)
            if normalized:
                return normalized
        return self._build_default_rule_carriers(session)

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

    def _resolve_player_initial_carrier_ids(self, session: GameSession, player: Player) -> list[str]:
        """读取某个玩家在多人模式下的开场可见载体列表。"""
        multi_identity = self._get_multi_identity_runtime(session)
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
            env_state["rule_carriers"] = self._build_runtime_rule_carriers(session)
        if not isinstance(env_state.get("npcs"), list) or not env_state.get("npcs"):
            env_state["npcs"] = self._build_runtime_npcs(session, game_mode=game_mode, initial_player_id=initial_player_id)
        self._refresh_npcs_present_summary(session)
        return env_state

    def _carrier_visible_to_player(self, session: GameSession, carrier: Mapping[str, object], player: Player) -> bool:
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
            groups = self._get_group_members_by_name(session)
            checks.append(any(player.player_id in groups.get(str(name).strip(), set()) for name in group_names))

        if not checks:
            return True
        return any(checks)

    def _mark_carrier_discovered(self, session: GameSession, player: Player, carrier: JsonObject) -> None:
        discovered_by = carrier.get("discovered_by", [])
        if not isinstance(discovered_by, list):
            discovered_by = []
        if player.player_id not in discovered_by:
            discovered_by.append(player.player_id)
        carrier["discovered_by"] = discovered_by
        carrier["is_discovered"] = True

        env_state = self._ensure_story_runtime(session)
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

    def _discover_rule_carriers_for_player(self, session: GameSession, player: Player, action_text: str) -> list[JsonObject]:
        """根据行动和房间位置发现新的规则载体。"""
        env_state = self._ensure_story_runtime(session)
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
            if not self._carrier_visible_to_player(session, carrier, player):
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
            self._mark_carrier_discovered(session, player, carrier)
            revealed_rules = carrier.get("revealed_rules", [])
            texts = [str(item).strip() for item in revealed_rules if str(item).strip()] if isinstance(revealed_rules, list) else []
            self._record_rule_texts(player, texts)
            discovered.append(carrier)
            break

        return discovered

    def _format_discovered_carrier_text(self, carriers: list[JsonObject]) -> str:
        """格式化载体发现描述。"""
        sections: list[str] = []
        for carrier in carriers:
            title = str(carrier.get("title", "规则载体") or "规则载体").strip()
            location = str(carrier.get("location", "") or "").strip()
            description = str(carrier.get("description", "") or "").strip()
            rules = carrier.get("revealed_rules", [])
            rule_lines = [f"- {str(item).strip()}" for item in rules if str(item).strip()] if isinstance(rules, list) else []
            header = f"你在{location}发现了《{title}》。" if location else f"你发现了《{title}》。"
            body = "\n".join([description] + rule_lines).strip()
            sections.append(f"{header}\n{body}".strip())
        return "\n\n".join(section for section in sections if section.strip())

    async def _send_initial_rule_exposure(
        self,
        session: GameSession,
        game_mode: str,
        lobby_players: list[tuple[str, str]],
    ) -> None:
        """按玩法发送初始规则载体，并在最后发送目标图。"""
        image_generator = self.get_image_generator()

        npc_guidance = getattr(session, "npc_guidance", {}) or {}
        guidance_method = ""
        if isinstance(npc_guidance, Mapping):
            guidance_method = str(npc_guidance.get("guidance_method", "") or "").strip().lower()

        if guidance_method == "rule_carrier":
            env_state = self._ensure_story_runtime(session)
            carriers = env_state.get("rule_carriers", [])
            if isinstance(carriers, list):
                initial_carriers = [carrier for carrier in carriers if isinstance(carrier, dict) and bool(carrier.get("initially_visible", False))]
                if not initial_carriers:
                    initial_carriers = []

                if game_mode == GameModes.MULTI.value:
                    name_by_id = {str(pid): str(name) for pid, name in lobby_players if str(pid).strip()}
                    for player in session.players.values():
                        preferred_ids = set(self._resolve_player_initial_carrier_ids(session, player))
                        visible = [
                            carrier
                            for carrier in carriers
                            if isinstance(carrier, dict)
                            and carrier.get("carrier_id") in preferred_ids
                            and self._carrier_visible_to_player(session, carrier, player)
                        ]
                        if not visible:
                            visible = [carrier for carrier in initial_carriers if self._carrier_visible_to_player(session, carrier, player)]
                        if not visible:
                            continue
                        for carrier in visible:
                            self._mark_carrier_discovered(session, player, carrier)
                            rules = carrier.get("revealed_rules", [])
                            texts = [str(item).strip() for item in rules if str(item).strip()] if isinstance(rules, list) else []
                            self._record_rule_texts(player, texts)

                        content = self._format_discovered_carrier_text(visible)
                        if content:
                            await self._send_private_text(player.player_id, name_by_id.get(player.player_id, player.name), content)
                            await asyncio.sleep(0.2)
                else:
                    player = next(iter(session.players.values()), None)
                    if player is not None:
                        visible = [carrier for carrier in initial_carriers if self._carrier_visible_to_player(session, carrier, player)]
                        if visible:
                            for carrier in visible:
                                self._mark_carrier_discovered(session, player, carrier)
                                rules = carrier.get("revealed_rules", [])
                                texts = [str(item).strip() for item in rules if str(item).strip()] if isinstance(rules, list) else []
                                self._record_rule_texts(player, texts)

                            first_carrier = visible[0]
                            rules_image = await image_generator.generate_rules_image(
                                rules_title=str(first_carrier.get("title", f"{session.scene_name} - 规则") or f"{session.scene_name} - 规则"),
                                rules=self._normalize_rules_list(first_carrier.get("revealed_rules", [])) if isinstance(first_carrier.get("revealed_rules", []), list) else [],
                                win_condition="",
                                game_mode=game_mode,
                            )
                            await self._send_image_path(rules_image)

        goal_image = await image_generator.generate_rules_image(
            rules_title="目标",
            rules=[],
            win_condition=session.win_condition,
            game_mode=game_mode,
        )
        await self._send_image_path(goal_image)

    async def _send_image_path(self, image_path: str) -> None:
        """读取图片文件并发送（base64）。"""
        try:
            with open(image_path, "rb") as f:
                image_bytes = f.read()
            image_base64 = base64.b64encode(image_bytes).decode("ascii")
            await self.send_image(image_base64)
        except FileNotFoundError:
            logger.error(f"图片文件不存在: {image_path}")
            await self.send_text("图片生成失败，请稍后重试。")
        except Exception as e:
            logger.error(f"发送图片失败: {e}")
            await self.send_text("发送图片时出错，请稍后重试。")

    # ============== 命令处理器==============

    async def _generate_entrance_description(
        self,
        session: GameSession
    ) -> str:
        """生成入场描述
        
        Args:
            session: 游戏会话
        
        Returns:
            入场描述文本
        """
        llm_client = LLMClient()
        guidance_method = ""
        npc_guidance = getattr(session, "npc_guidance", {}) or {}
        if isinstance(npc_guidance, Mapping):
            guidance_method = str(npc_guidance.get("guidance_method", "") or "").strip().lower()

        plural_hint = ""
        default_entrance = f"你来到了{session.scene_name}。这里的气氛让你感到不安。"
        if getattr(session, "game_mode", GameModes.SINGLE.value) == GameModes.MULTI.value:
            plural_hint = "\n8. 必须使用第二人称复数'你们'，禁止出现'你'、'你的'等单数表述\n9. 描述一行人一起进入场景，而不是单独一人\n"
            default_entrance = f"你们来到了{session.scene_name}。这里的气氛让你们感到不安。"
        if guidance_method == "none":
            if getattr(session, "game_mode", GameModes.SINGLE.value) == GameModes.MULTI.value:
                default_entrance = f"你们在{session.scene_name}里先后恢复意识，四周一时看不见任何人。空气里弥漫着异样的安静。"
            else:
                default_entrance = f"你在{session.scene_name}里醒来，四周空无一人。短暂的恍惚过去后，你才意识到这里安静得有些不正常。"

        system_prompt = f"""你是规则怪谈游戏的入场描述生成器。你需要生成玩家进入场景时的描述。

入场描述要求：
1. 描述玩家如何到达这个场景
2. 描述玩家进入场景时的第一印象
3. 描述玩家进入时的感受
4. 描述环境的初始状态
5. 使用感官细节（视觉、听觉、嗅觉、触觉）
6. 营造紧张和不安的氛围
7. 长度：150-200字{plural_hint}
8. 如果当前开场没有 NPC 出场，应把重点放在“玩家在空无一人的地方醒来或恢复意识后，独自观察环境并意识到异常”上
9. 不要写成系统提示、玩法说明或任务清单

返回纯文本，不要JSON格式。"""


        user_prompt = f"""场景名称：{session.scene_name}

背景：{session.background}

玩家身份：{session.player_identity}

开场类型：{"无NPC开场，玩家独自或一行人醒来后自行探索" if guidance_method == "none" else "存在NPC或载体参与的开场"}

请生成入场描述。"""

        try:
            response = await llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.8,
            )

            return response.clean_content
        except Exception as e:
            logger.error(f"生成入场描述失败: {e}")
            # 返回默认描述
            return default_entrance

