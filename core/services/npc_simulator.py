"""NPC 模拟服务 - 使用独立模型推进 NPC 行动与房间级位置同步。"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from ..config import get_config
from ..game.models import GameSession, Player, PlayerStatus
from ..llm.client import LLMClient, get_default_max_tokens
from ...common.models import GameStateDict, JsonObject
from ...systems.environment_evolution import DoorState
from ...systems.npc_system import BehaviorType, NPC
from ...systems.room_topology import (
    SoundIntensity,
    WallMaterial,
    build_room_graph,
    can_hear_between_rooms,
    get_audible_npcs,
    get_obstacles_for_room,
    get_visible_npcs,
    get_wall_material,
)


logger = logging.getLogger(__name__)


# 声源强度关键词：用于从行动文本推断 SoundIntensity 档位
_LOUD_KEYWORDS = ("喊", "大叫", "呼救", "咆哮", "尖叫", "怒吼", "嘶吼")
_QUIET_KEYWORDS = ("蹑手蹑脚", "悄声", "低语", "轻手轻脚", "屏息")


def _infer_sound_intensity(action_text: str) -> SoundIntensity:
    """从行动文本推断声源强度。

    匹配逻辑：
    - 命中 LOUD 关键词（喊/大叫/呼救/咆哮/尖叫/怒吼/嘶吼）→ LOUD
    - 命中 QUIET 关键词（蹑手蹑脚/悄声/低语/轻手轻脚/屏息）→ QUIET
    - 其余 → NORMAL
    """
    text = action_text or ""
    for kw in _LOUD_KEYWORDS:
        if kw in text:
            return SoundIntensity.LOUD
    for kw in _QUIET_KEYWORDS:
        if kw in text:
            return SoundIntensity.QUIET
    return SoundIntensity.NORMAL


class NPCSimulator:
    """推进 NPC 行动、位置和玩家可感知事件。"""

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self.llm_client = llm_client or LLMClient()

    @staticmethod
    def _get_door_state_between(
        session: GameSession,
        room_a: str,
        room_b: str,
    ) -> DoorState | None:
        """查询两个房间之间的门状态。

        从 ``session.environment_state.doors`` 查询连接 room_a 与 room_b 的门。
        支持列表格式：``[{"rooms": ["A", "B"], "state": "CLOSED"}, ...]``。

        Args:
            session: 游戏会话
            room_a: 房间 A 名称
            room_b: 房间 B 名称

        Returns:
            DoorState 枚举值；若无门或字段缺失则返回 None
        """
        env_state = session.environment_state if isinstance(session.environment_state, dict) else {}
        doors = env_state.get("doors", [])
        if not isinstance(doors, list):
            return None

        ra = str(room_a or "").strip()
        rb = str(room_b or "").strip()
        if not ra or not rb or ra == rb:
            return None

        for door in doors:
            if not isinstance(door, dict):
                continue
            rooms = door.get("rooms", [])
            if not isinstance(rooms, list) or len(rooms) < 2:
                continue
            room_set = {str(r).strip() for r in rooms}
            if ra in room_set and rb in room_set:
                state_str = str(door.get("state", "")).strip()
                try:
                    return DoorState(state_str)
                except ValueError:
                    return None
        return None

    @staticmethod
    def ensure_runtime(session: GameSession) -> JsonObject:
        """确保会话中存在 NPC 运行时结构。"""
        if not isinstance(getattr(session, "environment_state", None), dict):
            session.environment_state = {}

        env_state = session.environment_state
        if not isinstance(env_state.get("room_graph"), dict):
            env_state["room_graph"] = build_room_graph(session.scene_structure or {})
        if not isinstance(env_state.get("rule_carriers"), list):
            env_state["rule_carriers"] = []
        if not isinstance(env_state.get("npcs"), list):
            env_state["npcs"] = []
        if not isinstance(env_state.get("per_player_visibility"), dict):
            env_state["per_player_visibility"] = {}
        if not isinstance(env_state.get("npc_runtime"), dict):
            env_state["npc_runtime"] = {
                "recent_events": [],
                "room_events": [],
                "visible_events": {},
                "audible_events": {},
                "player_perception_hints": {},
                "last_updated_at": None,
            }
        return env_state

    @staticmethod
    def append_recent_event(session: GameSession, event: JsonObject, max_history: int = 20) -> None:
        """写入近期事件。"""
        env_state = NPCSimulator.ensure_runtime(session)
        npc_runtime = env_state["npc_runtime"]
        if not isinstance(npc_runtime, dict):
            return
        events = npc_runtime.get("recent_events", [])
        if not isinstance(events, list):
            events = []
        events.append(event)
        npc_runtime["recent_events"] = events[-max(1, int(max_history)) :]

    @staticmethod
    def _format_intent_constraint(npc_intents: dict[str, BehaviorType]) -> str:
        """格式化意图约束文本，供 prompt 注入。"""
        if not npc_intents:
            return "（无 NPC）"
        lines = [
            f"- {npc_id}: {intent.name}（{intent.value}）"
            for npc_id, intent in npc_intents.items()
        ]
        return "\n".join(lines)

    def _decide_npc_intents(self, session: GameSession) -> dict[str, BehaviorType]:
        """反序列化 NPC，更新需求/作息，用行为树决定意图，并持久化回 env_state。

        流程：
        1. 从 session.environment_state["npcs"] 反序列化为 NPC 实例
        2. 根据游戏时段切换作息（update_activity_by_phase）
        3. 推进需求系统（tick_needs）
        4. 调用 decide_intent 决定意图类别（行为树+态度向量+需求+作息）
        5. 把需求/作息/关系/意图持久化回 env_state["npcs"] 的 dict

        Returns:
            {npc_id: intent} 映射
        """
        env_state = self.ensure_runtime(session)
        npcs_raw = env_state.get("npcs", [])
        if not isinstance(npcs_raw, list):
            return {}

        time_manager = session.time_manager if isinstance(session.time_manager, dict) else {}
        time_phase = str(time_manager.get("time_phase", "opening") or "opening")

        # 构建 game_state 供行为树条件评估（玩家位置用于 INTERACT 判定）
        game_state: GameStateDict = {
            "players": {
                p.player_id: {
                    "name": p.name,
                    "location": p.location,
                    "health": None,
                    "sanity": None,
                    "inventory": None,
                    "action_history": None,
                }
                for p in session.players.values()
            },
            "recent_sounds": [],
            "safe_locations": None,
            "time_system": time_manager,
        }

        npc_by_id = {
            str(n.get("npc_id", "") or ""): n
            for n in npcs_raw
            if isinstance(n, dict)
        }
        npc_intents: dict[str, BehaviorType] = {}

        for npc_raw in npcs_raw:
            if not isinstance(npc_raw, dict):
                continue
            npc_id = str(npc_raw.get("npc_id", "") or "").strip()
            if not npc_id:
                continue
            try:
                npc = NPC.from_dict(npc_raw)
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("NPC 反序列化失败，跳过意图决定: %s (npc_id=%s)", exc, npc_id)
                continue

            # 更新作息和需求
            npc.update_activity_by_phase(time_phase)
            npc.tick_needs()
            # 行为树+态度向量+需求+作息决定意图
            intent = npc.decide_intent(time_phase, game_state)
            npc_intents[npc_id] = intent

            # 持久化运行时字段回 dict（需求/作息/关系/意图）
            npc_dict = npc_by_id.get(npc_id)
            if isinstance(npc_dict, dict):
                npc_dict["hunger"] = npc.hunger
                npc_dict["fatigue"] = npc.fatigue
                npc_dict["curiosity"] = npc.curiosity
                npc_dict["shift"] = npc.shift
                npc_dict["current_activity"] = npc.current_activity
                npc_dict["relationships"] = {
                    k: v.value for k, v in npc.relationships.items()
                }
                npc_dict["current_behavior"] = (
                    npc.current_behavior.value if npc.current_behavior else None
                )

        return npc_intents

    def _build_prompt(
        self,
        session: GameSession,
        acting_player: Player,
        action_text: str,
        action_result: JsonObject,
        npc_intents: dict[str, BehaviorType] | None = None,
    ) -> tuple[str, str]:
        env_state = self.ensure_runtime(session)
        room_graph = env_state.get("room_graph", {})
        rule_carriers = env_state.get("rule_carriers", [])
        recent_events = env_state.get("npc_runtime", {}).get("recent_events", []) if isinstance(env_state.get("npc_runtime"), dict) else []
        multi_identity = session.rule_network.get("multi_identity", {}) if isinstance(getattr(session, "rule_network", None), dict) else {}

        players = [
            {
                "player_id": player.player_id,
                "name": player.name,
                "identity": player.identity,
                "task_brief": player.task_brief,
                "duty_area": player.duty_area,
                "location": player.location,
                "recorded_rules": player.recorded_rules,
            }
            for player in session.players.values()
        ]
        npcs = env_state.get("npcs", [])

        system_prompt = """你是规则怪谈游戏的 NPC 模拟系统。

你的职责：
1. 根据完整规则、隐藏真相、NPC 设定和房间拓扑，推进 NPC 的下一步行动
2. 同步 NPC 的当前位置、目标、动作和可听动静
3. 只输出结构化 JSON，不写解释
4. 不要直接向玩家泄露后台完整规则
5. 位置系统按“房间/区域级”工作，而不是精确坐标
6. 如需更新规则载体状态，只能通过 `carrier_state_updates` 返回结构化补丁
7. NPC 有作息概念：根据当前游戏时段（开场后/数小时后/深夜/午夜/黎明前）和 NPC 的角色设定调整行为
   - 夜班/守夜类 NPC 在“深夜/午夜/黎明前”更警觉、巡逻更频繁、对声音更敏感
   - 白班/常驻类 NPC 在“深夜/午夜”可能困倦、打盹、判断失误，对玩家行为反应迟缓
   - “开场后/数小时后”视为正常时段，NPC 按其既有节奏行动
8. NPC 意图约束：每个 NPC 的意图类别已由行为树+态度向量+需求+作息决定（PATROL/INVESTIGATE/ESCAPE/ATTACK/INTERACT），
   你必须在给定意图类别约束下生成具体行动描述，不得改变意图类别：
   - PATROL（巡逻）：生成巡逻相关行动（移动、巡视、检查、前往下一区域）
   - INVESTIGATE（调查）：生成调查相关行动（查看、搜寻、检视、靠近声源）
   - ESCAPE（逃跑）：生成逃跑相关行动（撤离、躲避、逃往安全处）
   - ATTACK（攻击）：生成攻击相关行动（追击、威胁、攻击）
   - INTERACT（互动）：生成互动相关行动（交谈、观察、互动）

输出格式：
{
  "npc_updates": [
    {
      "npc_id": "guide_0",
      "current_location": "值班室",
      "current_goal": "整理夜班登记簿",
      "last_action": "回到值班室翻找记录",
      "audible_signature": "纸张翻动和金属抽屉碰撞声",
      "movement_note": "他快步穿过走廊，消失在值班室门后"
    }
  ],
  "room_events": [
    {
      "room": "值班室",
      "event": "里面传出翻动纸页与抽屉碰撞的声音"
    }
  ],
  "visible_events": {
    "值班室": ["能在同房间直接看到的事件"]
  },
  "audible_events": {
    "值班室": ["隔壁可听到的事件"]
  },
  "carrier_state_updates": [
    {
      "carrier_id": "carrier_0",
      "description": "载体表面多了一行字",
      "revealed_rules": ["新增或替换后的规则文本"]
    }
  ],
  "player_perception_hints": {
    "player_id": ["该玩家能额外察觉到的提示"]
  }
}
"""

        # 注入当前游戏时段信息，让 NPC 模拟能感知作息并据此调整行为
        time_manager = session.time_manager if isinstance(session.time_manager, dict) else {}
        elapsed_minutes = int(time_manager.get("elapsed_minutes", 0) or 0)
        current_time_phase = str(time_manager.get("current_time", "未知") or "未知")
        time_description = str(time_manager.get("time_description", "") or "")
        time_phase_tag = str(time_manager.get("time_phase", "") or "")

        # 格式化意图约束文本（行为树决定，LLM 必须遵守）
        intent_constraint_text = self._format_intent_constraint(npc_intents or {})

        user_prompt = f"""场景：{session.scene_name}
背景：{session.background}
隐藏真相：{session.hidden_truth}

完整规则：
{json.dumps(session.rules, ensure_ascii=False)}

房间拓扑：
{json.dumps(room_graph, ensure_ascii=False)}

规则载体：
{json.dumps(rule_carriers, ensure_ascii=False)}

多人身份运行时：
{json.dumps(multi_identity, ensure_ascii=False)}

玩家列表：
{json.dumps(players, ensure_ascii=False)}

NPC 当前状态：
{json.dumps(npcs, ensure_ascii=False)}

最近事件：
{json.dumps(recent_events, ensure_ascii=False)}

当前游戏时间：
- 累计分钟：{elapsed_minutes}
- 当前时段：{current_time_phase}
- 时段标签：{time_phase_tag}
- 时段描述：{time_description}

NPC 意图约束（行为树+态度向量+需求+作息决定，必须严格遵守，只能在约束下生成具体行动描述）：
{intent_constraint_text}

本次行动玩家：{acting_player.name}（{acting_player.player_id}）
本次行动位置：{acting_player.location}
本次行动：{action_text}
主流程行动结果：
{json.dumps(action_result, ensure_ascii=False)}

请推进所有 NPC 的下一步房间级行动与可听事件，并根据“当前时段”让 NPC 表现出对应的作息状态。
严格遵循上述“NPC 意图约束”，为每个 NPC 在其意图类别下生成具体行动描述，不得改变意图类别。"""
        return system_prompt, user_prompt

    def _fallback_update(self, session: GameSession) -> JsonObject:
        """模型失败时的最小兜底。"""
        env_state = self.ensure_runtime(session)
        now = datetime.now().isoformat()
        npc_runtime = env_state.get("npc_runtime", {})
        if isinstance(npc_runtime, dict):
            npc_runtime["last_updated_at"] = now
            npc_runtime["room_events"] = []
            npc_runtime["visible_events"] = {}
            npc_runtime["audible_events"] = {}
            npc_runtime["player_perception_hints"] = {}
        return {
            "npc_updates": [],
            "room_events": [],
            "visible_events": {},
            "audible_events": {},
            "carrier_state_updates": [],
            "player_perception_hints": {},
        }

    def _apply_result(self, session: GameSession, result: JsonObject) -> JsonObject:
        """应用 NPC 模拟结果。"""
        env_state = self.ensure_runtime(session)
        npcs_raw = env_state.get("npcs", [])
        npcs = [dict(item) for item in npcs_raw if isinstance(item, dict)] if isinstance(npcs_raw, list) else []
        npc_updates = result.get("npc_updates", [])
        room_events = result.get("room_events", [])
        visible_events = result.get("visible_events", {})
        audible_events = result.get("audible_events", {})
        carrier_state_updates = result.get("carrier_state_updates", [])
        player_perception_hints = result.get("player_perception_hints", {})

        npc_by_id = {str(npc.get("npc_id", "") or ""): npc for npc in npcs}
        timestamp = datetime.now().isoformat()

        if isinstance(npc_updates, list):
            for update in npc_updates:
                if not isinstance(update, dict):
                    continue
                npc_id = str(update.get("npc_id", "") or "").strip()
                if not npc_id or npc_id not in npc_by_id:
                    continue
                npc = npc_by_id[npc_id]
                old_location = str(npc.get("current_location") or npc.get("location") or "").strip()
                new_location = str(update.get("current_location", old_location) or old_location).strip()
                npc["current_location"] = new_location or old_location
                npc["current_goal"] = str(update.get("current_goal", npc.get("current_goal", "")) or "")
                npc["last_action"] = str(update.get("last_action", npc.get("last_action", "")) or "")
                npc["audible_signature"] = str(update.get("audible_signature", npc.get("audible_signature", "")) or "")
                movement_history = npc.get("movement_history", [])
                if not isinstance(movement_history, list):
                    movement_history = []
                movement_history.append(
                    {
                        "timestamp": timestamp,
                        "from": old_location,
                        "to": npc["current_location"],
                        "note": str(update.get("movement_note", "") or ""),
                    }
                )
                npc["movement_history"] = movement_history[-20:]

        env_state["npcs"] = list(npc_by_id.values())

        carriers_raw = env_state.get("rule_carriers", [])
        carriers = [dict(item) for item in carriers_raw if isinstance(item, dict)] if isinstance(carriers_raw, list) else []
        carriers_by_id = {str(carrier.get("carrier_id", "") or ""): carrier for carrier in carriers}
        if isinstance(carrier_state_updates, list):
            for update in carrier_state_updates:
                if not isinstance(update, dict):
                    continue
                carrier_id = str(update.get("carrier_id", "") or "").strip()
                if not carrier_id or carrier_id not in carriers_by_id:
                    continue
                carrier = carriers_by_id[carrier_id]
                if "location" in update:
                    carrier["location"] = str(update.get("location", carrier.get("location", "")) or carrier.get("location", "")).strip()
                if "description" in update:
                    carrier["description"] = str(update.get("description", carrier.get("description", "")) or carrier.get("description", "")).strip()
                if "initially_visible" in update:
                    carrier["initially_visible"] = bool(update.get("initially_visible", carrier.get("initially_visible", False)))
                if "requires_action" in update:
                    carrier["requires_action"] = bool(update.get("requires_action", carrier.get("requires_action", True)))
                if isinstance(update.get("revealed_rules"), list):
                    carrier["revealed_rules"] = [
                        str(item).strip()
                        for item in update.get("revealed_rules", [])
                        if str(item).strip()
                    ]
        env_state["rule_carriers"] = list(carriers_by_id.values())

        npc_runtime = env_state.get("npc_runtime", {})
        if isinstance(npc_runtime, dict):
            npc_runtime["room_events"] = [item for item in room_events if isinstance(item, dict)] if isinstance(room_events, list) else []
            npc_runtime["visible_events"] = visible_events if isinstance(visible_events, dict) else {}
            npc_runtime["audible_events"] = audible_events if isinstance(audible_events, dict) else {}
            npc_runtime["player_perception_hints"] = player_perception_hints if isinstance(player_perception_hints, dict) else {}
            npc_runtime["last_updated_at"] = timestamp

        return {
            "npc_updates": npc_updates if isinstance(npc_updates, list) else [],
            "room_events": room_events if isinstance(room_events, list) else [],
            "visible_events": visible_events if isinstance(visible_events, dict) else {},
            "audible_events": audible_events if isinstance(audible_events, dict) else {},
            "carrier_state_updates": carrier_state_updates if isinstance(carrier_state_updates, list) else [],
            "player_perception_hints": player_perception_hints if isinstance(player_perception_hints, dict) else {},
        }

    def build_perception_for_player(self, session: GameSession, player: Player) -> JsonObject:
        """根据房间级距离生成玩家此刻可感知的 NPC 信息。"""
        env_state = self.ensure_runtime(session)
        room_graph = env_state.get("room_graph", {})
        npcs = env_state.get("npcs", [])
        hearing_radius = int(getattr(get_config().npc_sim, "room_hearing_radius", 1) or 1)

        # 同房间遮挡：根据玩家所在房间的家具类物件过滤可见 NPC
        obstacles = get_obstacles_for_room(env_state, player.location)
        visible_npcs = get_visible_npcs(npcs if isinstance(npcs, list) else [], player.location, obstacles)
        audible_npcs = get_audible_npcs(
            room_graph if isinstance(room_graph, dict) else {},
            npcs if isinstance(npcs, list) else [],
            player.location,
            hearing_radius=hearing_radius,
        )

        room_events = env_state.get("npc_runtime", {}).get("room_events", []) if isinstance(env_state.get("npc_runtime"), dict) else []
        audible_events: list[str] = []
        if isinstance(room_events, list):
            for item in room_events:
                if not isinstance(item, dict):
                    continue
                room_name = str(item.get("room", "") or "").strip()
                event_text = str(item.get("event", "") or "").strip()
                if not room_name or not event_text:
                    continue
                # 补全门状态/声源强度/墙材质，使四步修正生效
                door_state = self._get_door_state_between(session, player.location, room_name)
                sound_intensity = _infer_sound_intensity(event_text)
                wall_material = get_wall_material(
                    room_graph if isinstance(room_graph, dict) else {},
                    player.location,
                    room_name,
                )
                if can_hear_between_rooms(
                    room_graph if isinstance(room_graph, dict) else {},
                    player.location,
                    room_name,
                    hearing_radius=hearing_radius,
                    door_state=door_state,
                    sound_intensity=sound_intensity,
                    wall_material=wall_material,
                ):
                    audible_events.append(event_text)

        npc_runtime = env_state.get("npc_runtime", {}) if isinstance(env_state.get("npc_runtime"), dict) else {}
        visible_events_raw = npc_runtime.get("visible_events", {})
        visible_events: list[str] = []
        if isinstance(visible_events_raw, dict):
            room_events_for_player = visible_events_raw.get(str(player.location or "").strip(), [])
            if isinstance(room_events_for_player, list):
                visible_events.extend(str(item).strip() for item in room_events_for_player if str(item).strip())

        audible_events_raw = npc_runtime.get("audible_events", {})
        if isinstance(audible_events_raw, dict):
            for room_name, events in audible_events_raw.items():
                if not isinstance(events, list):
                    continue
                # 此处在遍历事件前判定可听性，无法绑定单一事件文本，
                # sound_intensity 按 NORMAL 处理；门状态/墙材质仍按房间对补全
                source_room = str(room_name).strip()
                door_state = self._get_door_state_between(session, player.location, source_room)
                wall_material = get_wall_material(
                    room_graph if isinstance(room_graph, dict) else {},
                    player.location,
                    source_room,
                )
                if can_hear_between_rooms(
                    room_graph if isinstance(room_graph, dict) else {},
                    player.location,
                    source_room,
                    hearing_radius=hearing_radius,
                    door_state=door_state,
                    sound_intensity=SoundIntensity.NORMAL,
                    wall_material=wall_material,
                ):
                    audible_events.extend(str(item).strip() for item in events if str(item).strip())

        player_perception_hints: list[str] = []
        hints_raw = npc_runtime.get("player_perception_hints", {})
        if isinstance(hints_raw, dict):
            hints_for_player = hints_raw.get(player.player_id, [])
            if isinstance(hints_for_player, list):
                player_perception_hints = [str(item).strip() for item in hints_for_player if str(item).strip()]

        return {
            "visible_npcs": visible_npcs,
            "audible_npcs": audible_npcs,
            "visible_events": visible_events,
            "audible_events": audible_events,
            "player_perception_hints": player_perception_hints,
        }

    async def simulate_after_action(
        self,
        session: GameSession,
        acting_player: Player,
        action_text: str,
        action_result: JsonObject,
    ) -> JsonObject:
        """在玩家行动后推进 NPC 状态。"""
        self.ensure_runtime(session)
        # 行为树决定意图（更新需求/作息并持久化回 env_state）
        npc_intents = self._decide_npc_intents(session)
        system_prompt, user_prompt = self._build_prompt(
            session, acting_player, action_text, action_result, npc_intents
        )

        try:
            response = await self.llm_client.call_npc_sim(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.7,
                max_tokens=get_default_max_tokens("npc_sim"),
            )
            parsed = response.parse_json()
        except Exception as exc:
            logger.warning("NPC 模拟失败，使用兜底结果: %s", exc)
            parsed = self._fallback_update(session)

        return self._apply_result(session, parsed)

    async def tick(self, session: GameSession) -> list["GameEvent"]:
        """主动推进 NPC 一步，返回要发布给玩家的事件列表。

        在无玩家主动行动时由后台 tick 调用。
        """
        self.ensure_runtime(session)

        # 行为树决定意图（更新需求/作息并持久化回 env_state）
        npc_intents = self._decide_npc_intents(session)
        intent_constraint_text = self._format_intent_constraint(npc_intents)

        system_prompt = """你是规则怪谈游戏的 NPC 模拟系统。
当前没有玩家主动行动。请基于 NPC 的当前目标、位置、情绪，推进他们下一步的行动。
NPC 有作息概念：根据当前游戏时段（开场后/数小时后/深夜/午夜/黎明前）调整 NPC 行为，
夜班/守夜类 NPC 在深夜/午夜更警觉，白班/常驻类 NPC 在深夜/午夜可能困倦、反应迟缓。

重要约束：每个 NPC 的意图类别已由行为树+态度向量+需求+作息决定（PATROL/INVESTIGATE/ESCAPE/ATTACK/INTERACT），
你必须在给定意图类别约束下生成具体行动描述，不得改变意图类别：
- PATROL（巡逻）：生成巡逻相关行动（移动、巡视、检查、前往下一区域）
- INVESTIGATE（调查）：生成调查相关行动（查看、搜寻、检视、靠近声源）
- ESCAPE（逃跑）：生成逃跑相关行动（撤离、躲避、逃往安全处）
- ATTACK（攻击）：生成攻击相关行动（追击、威胁、攻击）
- INTERACT（互动）：生成互动相关行动（交谈、观察、互动）

输出格式同 simulate_after_action。"""

        # 注入当前游戏时段信息，让 tick 推进时 NPC 也能感知作息
        time_manager = session.time_manager if isinstance(session.time_manager, dict) else {}
        elapsed_minutes = int(time_manager.get("elapsed_minutes", 0) or 0)
        current_time_phase = str(time_manager.get("current_time", "未知") or "未知")
        time_description = str(time_manager.get("time_description", "") or "")

        user_prompt = f"""场景：{session.scene_name}
当前玩家位置：{json.dumps({p.player_id: {"name": p.name, "location": p.location} for p in session.players.values()}, ensure_ascii=False)}
NPC 当前状态：{json.dumps(session.environment_state.get('npcs', []) if isinstance(session.environment_state, dict) else [], ensure_ascii=False)}
房间拓扑：{json.dumps(session.environment_state.get('room_graph', {}) if isinstance(session.environment_state, dict) else {}, ensure_ascii=False)}

当前游戏时间：
- 累计分钟：{elapsed_minutes}
- 当前时段：{current_time_phase}
- 时段描述：{time_description}

NPC 意图约束（行为树+态度向量+需求+作息决定，必须严格遵守，只能在约束下生成具体行动描述）：
{intent_constraint_text}
"""

        try:
            response = await self.llm_client.call_npc_sim(
                prompt=user_prompt, system_prompt=system_prompt, temperature=0.6,
                max_tokens=get_default_max_tokens("npc_sim"),
            )
            parsed = response.parse_json()
        except Exception as exc:
            logger.warning("NPC tick 模拟失败: %s", exc)
            return []

        applied = self._apply_result(session, parsed)

        # 把 applied 转换为 GameEvent 列表
        from .event_bus import GameEvent
        events: list[GameEvent] = []
        for update in applied.get("npc_updates", []):
            if not isinstance(update, dict):
                continue
            npc_name = str(update.get("npc_id", "NPC"))
            new_loc = str(update.get("current_location", ""))
            movement_note = str(update.get("movement_note", ""))
            if not movement_note:
                continue
            # 可见集合 = 同房间玩家；可听集合 = 其他活着的玩家
            visible_to = {p.player_id for p in session.players.values()
                          if p.status == PlayerStatus.ALIVE and p.location == new_loc}
            audible_to = {p.player_id for p in session.players.values()
                          if p.status == PlayerStatus.ALIVE and p.player_id not in visible_to}
            events.append(GameEvent(
                event_type="npc_move",
                group_id=session.group_id,
                actor_id=str(update.get("npc_id", "npc")),
                actor_name=npc_name,
                location=new_loc,
                description=movement_note,
                audible_description=f"附近传来{npc_name}的动静",
                visible_to=visible_to,
                audible_to=audible_to,
                importance="low",
            ))
        return events
