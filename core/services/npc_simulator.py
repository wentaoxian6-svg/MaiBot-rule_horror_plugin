"""NPC 模拟服务 - 使用独立模型推进 NPC 行动与房间级位置同步。"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from ..config import get_config
from ..game.models import GameSession, Player, PlayerStatus
from ..llm.client import LLMClient, get_default_max_tokens
from ...common.door_utils import get_door_state_between
from ...common.models import GameStateDict, JsonObject
from ...common.sound_utils import infer_sound_intensity
from ...systems.npc_system import BehaviorType, NPC
from ...systems.room_topology import (
    SoundIntensity,
    WallMaterial,
    build_room_graph,
    can_hear_between_rooms,
    get_audible_npcs,
    get_obstacles_for_room,
    get_room_depth_factor,
    get_room_distance_decay,
    get_visible_npcs,
    get_wall_material,
    shortest_room_distance,
)


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
        # recent_sounds 按 NPC 位置动态填充，Task 12 修复 _should_investigate 死分支
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

        # 提取真实声源事件（room_events）与房间图，供每个 NPC 按 location 计算 recent_sounds
        npc_runtime = env_state.get("npc_runtime", {})
        room_events_raw = (
            npc_runtime.get("room_events", []) if isinstance(npc_runtime, dict) else []
        )
        room_events: list[JsonObject] = [
            item for item in room_events_raw if isinstance(item, dict)
        ] if isinstance(room_events_raw, list) else []
        room_graph = env_state.get("room_graph", {})
        if not isinstance(room_graph, dict):
            room_graph = {}

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

            # Task 12：按 NPC 当前位置填充 recent_sounds，让 _should_investigate 可触发
            # 距离换算：同房间=0、相邻=10、2 房间外=20……与 _should_investigate 的 <20 阈值对齐
            npc_location = npc.current_location or npc.location or ""
            recent_sounds: list[JsonObject] = []
            for event in room_events:
                event_room = str(event.get("room", "") or "").strip()
                if not event_room:
                    continue
                distance_steps = shortest_room_distance(room_graph, npc_location, event_room)
                if distance_steps is None:
                    # 不可达房间，跳过
                    continue
                # Task 3.3：按房间距离衰减 + 事件房间深度因子折算 NPC 感知质量
                # decay=0（超出有效距离）则 NPC 无法感知该声音；
                # depth_factor 越大（事件房间越偏僻）越难被外部 NPC 感知
                decay = get_room_distance_decay(room_graph, npc_location, event_room)
                if decay <= 0.0:
                    continue
                depth_factor = get_room_depth_factor(room_graph, event_room)
                perception_quality = decay * (1.0 - depth_factor)
                if perception_quality <= 0.0:
                    continue
                # 感知质量越高，等效距离越近（更易触发调查）；越低则等效距离越远
                effective_distance = (float(distance_steps) * 10.0) / perception_quality
                recent_sounds.append({
                    "distance": effective_distance,
                    "location": event_room,
                    "type": str(event.get("event", "") or "").strip() or None,
                    "perception_quality": round(perception_quality, 3),
                })
            game_state["recent_sounds"] = recent_sounds  # type: ignore[typeddict-item]

            # 行为树+态度向量+需求+作息决定意图
            intent = npc.decide_intent(time_phase, game_state)

            # Task 19：追杀状态机激活时，追杀者 NPC 锁定为 ATTACK，覆盖行为树决策
            hunt_state = session.hunt_state
            if (
                isinstance(hunt_state, dict)
                and hunt_state.get("active")
                and str(hunt_state.get("pursuer_npc_id", "") or "").strip() == npc_id
            ):
                intent = BehaviorType.ATTACK
                npc.current_behavior = BehaviorType.ATTACK

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
                # Task 11：持久化 INTERACT 冷却时间戳
                npc_dict["last_interact_time"] = (
                    npc.last_interact_time.isoformat() if npc.last_interact_time else None
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

    @staticmethod
    def _clamp_ratio(value: object, default: float) -> float:
        """将值钳制到 [0, 1] 区间。"""
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, (int, float)):
            return max(0.0, min(1.0, float(value)))
        if isinstance(value, str):
            try:
                return max(0.0, min(1.0, float(value.strip())))
            except (TypeError, ValueError):
                return default
        return default

    async def generate_dialogue_llm(
        self,
        session: GameSession,
        npc: JsonObject,
        player: Player,
        query: str,
    ) -> tuple[str, list[dict[str, str]]]:
        """通过 LLM 生成 NPC 对话（Task 13）。

        注入 NPC 人格/背景、dialogue_history、近期交互、态度向量、deception_tendency/truthfulness、
        真实规则/真相。LLM 报错直接抛 RuntimeError，不兜底。

        Task 14：读取 NPCMemory.rule_versions 保持说谎一致性——同一 NPC 对同一规则
        说过某个版本后，后续被追问时 LLM 必须保持一致，玩家可通过多次试探识别骗子。

        Args:
            session: 游戏会话
            npc: 运行时 NPC 字典（env_state["npcs"] 中的一项）
            player: 玩家对象
            query: 玩家行动文本

        Returns:
            (dialogue_text, rules_mentioned) 元组：
            - dialogue_text: NPC 对话文本（含神态描写）
            - rules_mentioned: [{"rule_id": str, "version": str, "text": str}, ...]
              version 取值："truth"/"rumor"/"lie"/"refused"

        Raises:
            RuntimeError: LLM 调用或解析失败时直接抛出，不兜底
        """
        from ...systems.npc_system import NPCMemory
        from ..game.models import Rule

        # 加载 NPC 记忆
        raw_memory = npc.get("memory", {})
        memory_data = raw_memory if isinstance(raw_memory, dict) else {}
        mem = NPCMemory.from_dict(memory_data)
        pid = str(player.player_id)
        mem.initialize_attitude_vector(pid)

        # NPC 运行时属性
        name = str(npc.get("name", "") or "").strip() or "NPC"
        role = str(npc.get("role", "") or "").strip()
        personality = str(npc.get("personality", "") or "").strip()
        npc_id = str(npc.get("npc_id", "") or "").strip()
        reliability = self._clamp_ratio(npc.get("knowledge_reliability"), 0.75)
        deception = self._clamp_ratio(npc.get("deception_tendency"), 0.1)
        corruption = self._clamp_ratio(npc.get("corruption_level"), 0.0)
        truthfulness = max(0.0, min(1.0, reliability * (1.0 - deception * 0.7) * (1.0 - corruption * 0.8)))
        bias_tags = npc.get("bias_tags", [])
        if isinstance(bias_tags, list):
            bias_tags = [str(t).strip() for t in bias_tags if str(t).strip()]
        else:
            bias_tags = []

        # 态度向量（六维）
        vec = mem.get_attitude_vector(pid)
        attitude = mem.get_attitude(pid)
        attitude_str = attitude.value if hasattr(attitude, "value") else str(attitude)

        # 帮助意愿分级（0=拒绝/回避，1=少量，2=中等，3=较多）
        affection = float(vec.get("affection", 50.0))
        trust = float(vec.get("trust", 50.0))
        suspicion = float(vec.get("suspicion", 0.0))
        hostility = float(vec.get("hostility", 0.0))
        fear = float(vec.get("fear", 0.0))
        polite = any(k in query for k in ["请", "麻烦", "您好", "劳驾", "拜托", "求"])
        aggressive = any(k in query for k in ["滚", "闭嘴", "威胁", "砸", "杀", "打", "逼", "掐"])
        score = (affection + trust) - (suspicion + hostility * 1.2 + fear * 0.8)
        if polite:
            score += 8
        if aggressive:
            score -= 25
        from ...systems.npc_system import NPCAttitude
        if hostility >= 60 or score < -20 or attitude == NPCAttitude.HOSTILE:
            help_level = 0
        elif suspicion >= 70 or score < 10 or attitude == NPCAttitude.SUSPICIOUS:
            help_level = 0
        elif score < 45:
            help_level = 1
        elif score < 85:
            help_level = 2
        else:
            help_level = 3

        # 玩家已记录的规则文本（用于判断哪些规则对玩家是未知的）
        recorded_rules = [str(r).strip() for r in getattr(player, "recorded_rules", []) if str(r).strip()]

        # 规则列表（供 NPC 潜在提及）
        rule_objects = [Rule.from_dict(r, idx) for idx, r in enumerate(session.rules or [])]

        # Task 14：读取该 NPC 之前对各规则说过哪个版本，保持说谎一致性
        rule_versions = mem.rule_versions if isinstance(mem.rule_versions, dict) else {}

        # 近期对话历史（最近 10 轮）
        dialogue_history_raw = npc.get("dialogue_history", [])
        if isinstance(dialogue_history_raw, list):
            recent_dialogues = dialogue_history_raw[-10:]
        else:
            recent_dialogues = []

        # 近期互动记录
        recent_interactions = mem.get_recent_interactions(pid, count=5)

        # 玩家语气
        tone_parts = []
        if polite:
            tone_parts.append("礼貌")
        if aggressive:
            tone_parts.append("粗暴/攻击性")
        if not tone_parts:
            tone_parts.append("中性")
        tone = "、".join(tone_parts)

        # 构建规则列表文本（含 truth_status，供 LLM 判断说真话/谣言/谎言）
        rules_for_prompt = []
        for rule in rule_objects:
            rules_for_prompt.append({
                "rule_id": rule.rule_id,
                "text": rule.surface_text,
                "truth_status": rule.truth_status,
                "is_authentic": rule.is_authentic,
            })

        # 构建之前说法记录文本
        rule_versions_text = (
            "\n".join(f"- {rid}: {ver}" for rid, ver in rule_versions.items())
            if rule_versions else "（暂无）"
        )

        system_prompt = f"""你是规则怪谈游戏中的 NPC「{name}」。你需要根据自己的人格、记忆、对玩家的态度，生成符合角色的对话。

约束：
1. 只输出 JSON，不写解释
2. 对话内容必须符合 NPC 的人格和当前态度
3. 对话要包含神态描写，用【】包裹（如：【{name}皱了皱眉】）
4. 如果玩家在问规则，根据你的诚实度（truthfulness={truthfulness:.2f}）决定说真话/说谣言/说谎：
   - truthfulness >= 0.72：倾向说真话
   - 0.45 <= truthfulness < 0.72：模糊其辞，可能混入谣言
   - truthfulness < 0.45：倾向说谣言或谎言
5. 如果之前对同一规则说过某个版本，必须保持一致——说谎者继续说谎，说真话者继续说真话，不要自相矛盾
6. 帮助意愿分级（help_level={help_level}）：
   - 0：拒绝回答/回避（但仍要有符合角色的神态和语言）
   - 1：只透露少量信息
   - 2：透露中等程度信息
   - 3：较为坦诚地回答
7. 不要直接复述规则原文，用 NPC 自己的口吻转述
8. 对话长度控制在 50-200 字

输出格式：
{{
  "dialogue": "【神态描写】NPC的对话内容",
  "rules_mentioned": [
    {{"rule_id": "rule_0", "version": "truth", "text": "NPC转述的规则文本"}}
  ]
}}

version 取值：
- "truth"：说了真话
- "rumor"：说了谣言/不可靠版本
- "lie"：故意说谎
- "refused"：拒绝提及该规则
"""

        user_prompt = f"""场景：{session.scene_name}
背景：{session.background}
隐藏真相：{session.hidden_truth}

NPC 信息：
- 名字：{name}
- 角色：{role}
- 人格：{personality}
- 知识可靠性：{reliability:.2f}
- 欺骗倾向：{deception:.2f}
- 腐化程度：{corruption:.2f}
- 诚实度：{truthfulness:.2f}
- 偏见标签：{bias_tags}

对玩家「{player.name}」的态度向量：
- 好感度：{affection:.1f}
- 怀疑度：{suspicion:.1f}
- 恐惧度：{fear:.1f}
- 信任度：{trust:.1f}
- 敌意度：{hostility:.1f}
- 依赖度：{float(vec.get('dependence', 0.0)):.1f}
- 态度分类：{attitude_str}
- 帮助意愿分级：{help_level}

玩家语气：{tone}

近期对话历史（最近 {len(recent_dialogues)} 轮）：
{json.dumps(recent_dialogues, ensure_ascii=False, indent=2) if recent_dialogues else "（暂无）"}

近期互动记录：
{json.dumps(recent_interactions, ensure_ascii=False, indent=2) if recent_interactions else "（暂无）"}

该 NPC 之前对各规则的说法（必须保持一致，不要自相矛盾）：
{rule_versions_text}

真实规则（NPC 可能知道，但可根据诚实度决定说真话/谣言/谎言）：
{json.dumps(rules_for_prompt, ensure_ascii=False, indent=2) if rules_for_prompt else "（暂无）"}

玩家已记录的规则笔记：
{json.dumps(recorded_rules, ensure_ascii=False) if recorded_rules else "（暂无）"}

玩家行动：{query}

请生成 NPC「{name}」的对话。如果玩家在问规则，根据诚实度和之前说法决定回复内容，并在 rules_mentioned 中标注每条提及规则的版本。"""

        try:
            response = await self.llm_client.call_npc_sim(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.8,
                max_tokens=get_default_max_tokens("npc_sim"),
            )
            parsed = response.parse_json()
        except Exception as exc:
            # Task 13：不兜底，LLM 报错直接抛 RuntimeError
            raise RuntimeError(
                f"generate_dialogue_llm 调用失败 (npc={name}, npc_id={npc_id}, "
                f"player={player.name}, query={query[:50]}): {exc}"
            ) from exc

        dialogue_text = str(parsed.get("dialogue", "") or "").strip()
        if not dialogue_text:
            raise RuntimeError(
                f"generate_dialogue_llm 返回空对话 (npc={name}, npc_id={npc_id}, "
                f"player={player.name})"
            )

        rules_mentioned_raw = parsed.get("rules_mentioned", [])
        rules_mentioned: list[dict[str, str]] = []
        if isinstance(rules_mentioned_raw, list):
            for item in rules_mentioned_raw:
                if not isinstance(item, dict):
                    continue
                rid = str(item.get("rule_id", "") or "").strip()
                version = str(item.get("version", "") or "").strip()
                text = str(item.get("text", "") or "").strip()
                if rid and version:
                    rules_mentioned.append({"rule_id": rid, "version": version, "text": text})

        # 更新态度向量（根据玩家语气）
        if aggressive:
            mem.update_attitude_vector(pid, hostility_delta=10, trust_delta=-10, suspicion_delta=8)
        elif polite:
            mem.update_attitude_vector(pid, trust_delta=5, affection_delta=3, suspicion_delta=-2)
        else:
            mem.update_attitude_vector(pid, trust_delta=1)

        # 记录互动
        game_time = 0
        if isinstance(session.time_manager, dict):
            game_time = int(session.time_manager.get("elapsed_minutes", 0) or 0)
        mem.record_interaction(pid, "talk", {"action": query, "location": player.location}, game_time)

        # Task 14：记录该 NPC 对各规则说过哪个版本，保持说谎一致性
        for item in rules_mentioned:
            rid = item.get("rule_id", "")
            version = item.get("version", "")
            if rid and version:
                mem.rule_versions[rid] = version

        # 写回 NPC 记忆
        npc["memory"] = mem.to_dict()

        # 记录对话历史到 NPC 实例
        dialogue_history = npc.get("dialogue_history", [])
        if not isinstance(dialogue_history, list):
            dialogue_history = []
        dialogue_history.append({
            "player_id": pid,
            "player_message": query,
            "npc_response": dialogue_text,
            "timestamp": datetime.now().isoformat(),
        })
        # 限制历史记录数量
        max_history = 50
        if len(dialogue_history) > max_history:
            dialogue_history = dialogue_history[-max_history:]
        npc["dialogue_history"] = dialogue_history

        return dialogue_text, rules_mentioned

    def _apply_result_locked(self, session: GameSession, result: JsonObject) -> JsonObject:
        """应用 NPC 模拟结果的内部实现。

        Task 15：拆出此内部方法供已持世界锁的路径（如 state_manager._npc_tick_loop）
        直接调用，避免 asyncio.Lock 不可重入导致的死锁。
        调用方需确保已持世界锁，或 session 为快照副本（非共享状态）。
        """
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

    async def _apply_result(self, session: GameSession, result: JsonObject) -> JsonObject:
        """应用 NPC 模拟结果（Task 15：NPC 写回并发安全）。

        采用"读快照→算差异→持锁合并"模式：
        - tick 路径：调用方（state_manager._npc_tick_loop）已持世界锁，直接调用
          _apply_result_locked 避免 asyncio.Lock 不可重入导致的死锁
        - simulate_after_action 路径：在快照副本上写回，真实 session 写回由
          shared_handlers._apply_world_changes 持世界锁完成，此处无需重复获取
        - 其他直接调用路径：若 session 为真实 session 且世界锁未被持有，则获取世界锁
          短临界区合并，防止并发丢失更新

        判定逻辑：通过 GameStateManager 查找 group_id 对应的 GameState，
        若 state.session is session（真实 session，非快照）且 _world_lock 未被持有，
        则获取世界锁；否则直接调用 _apply_result_locked。
        """
        should_acquire_lock = False
        state = None
        try:
            from ..game.state_manager import GameStateManager
            state_manager = GameStateManager()
            state = state_manager._states.get(session.group_id)
            # 仅当 session 是真实 session（非快照副本）且世界锁未被持有时才获取
            if state is not None and state.session is session and not state._world_lock.locked():
                should_acquire_lock = True
        except Exception:
            # GameStateManager 不可用（如测试环境），不获取锁直接执行
            should_acquire_lock = False

        if should_acquire_lock and state is not None:
            await state._world_lock.acquire()
        try:
            return self._apply_result_locked(session, result)
        finally:
            if should_acquire_lock and state is not None:
                state._world_lock.release()

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
                door_state = get_door_state_between(session, player.location, room_name)
                sound_intensity = infer_sound_intensity(event_text)
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
                door_state = get_door_state_between(session, player.location, source_room)
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

        applied = self._apply_result(session, parsed)

        # Task 17：把本次 NPC 模拟结果存入 session.world_flags，供下次行动判定注入上下文
        # 这样 _judge_action 能感知上次 NPC 行动，叙事更连贯，无需等待本次 NPC sim 完成
        if not isinstance(session.world_flags, dict):
            session.world_flags = {}
        session.world_flags["last_npc_sim_result"] = {
            "timestamp": datetime.now().isoformat(),
            "acting_player_id": acting_player.player_id,
            "acting_player_name": acting_player.name,
            "action_text": action_text,
            "npc_updates": applied.get("npc_updates", []),
            "room_events": applied.get("room_events", []),
        }

        return applied

    def simulate_after_action_background(
        self,
        session: GameSession,
        acting_player: Player,
        action_text: str,
        action_result: JsonObject,
    ) -> asyncio.Task[None]:
        """Task 17：后台推进 NPC 状态（非阻塞）。

        通过 asyncio.create_task 在后台运行 simulate_after_action，
        结果写入 session.world_flags["last_npc_sim_result"]，供下次行动上下文注入。
        调用方无需 await，NPC 模拟延迟不再阻塞行动响应。
        """
        async def _run() -> None:
            try:
                await self.simulate_after_action(session, acting_player, action_text, action_result)
            except Exception as exc:
                logger.warning("后台 NPC 模拟失败: %s", exc)

        return asyncio.create_task(_run())

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
