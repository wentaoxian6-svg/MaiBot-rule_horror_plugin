"""NPC 模拟服务 - 使用独立模型推进 NPC 行动与房间级位置同步。"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from ..config import get_config
from ..game.models import GameSession, Player
from ..llm.client import LLMClient, get_default_max_tokens
from ...common.models import JsonObject
from ...systems.room_topology import build_room_graph, can_hear_between_rooms, get_audible_npcs, get_visible_npcs


logger = logging.getLogger(__name__)


class NPCSimulator:
    """推进 NPC 行动、位置和玩家可感知事件。"""

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self.llm_client = llm_client or LLMClient()

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

    def _build_prompt(
        self,
        session: GameSession,
        acting_player: Player,
        action_text: str,
        action_result: JsonObject,
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

本次行动玩家：{acting_player.name}（{acting_player.player_id}）
本次行动位置：{acting_player.location}
本次行动：{action_text}
主流程行动结果：
{json.dumps(action_result, ensure_ascii=False)}

请推进所有 NPC 的下一步房间级行动与可听事件。"""
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

        visible_npcs = get_visible_npcs(npcs if isinstance(npcs, list) else [], player.location)
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
                if can_hear_between_rooms(
                    room_graph if isinstance(room_graph, dict) else {},
                    player.location,
                    room_name,
                    hearing_radius=hearing_radius,
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
                if can_hear_between_rooms(
                    room_graph if isinstance(room_graph, dict) else {},
                    player.location,
                    str(room_name).strip(),
                    hearing_radius=hearing_radius,
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
        system_prompt, user_prompt = self._build_prompt(session, acting_player, action_text, action_result)

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
