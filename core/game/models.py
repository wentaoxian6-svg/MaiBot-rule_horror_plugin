"""游戏数据模型"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from ...common.models import JsonObject


class GameStatus(Enum):
    """游戏状态枚举"""
    WAITING = "waiting"  # 等待开始
    ACTIVE = "active"    # 进行中
    PAUSED = "paused"    # 暂停
    ENDED = "ended"      # 已结束


class PlayerStatus(Enum):
    """玩家状态枚举"""
    ALIVE = "alive"
    DEAD = "dead"
    SPECTATING = "spectating"


@dataclass
class Rule:
    """规则数据模型。

    兼容旧版仅有 ``text`` / ``hidden_meaning`` / ``is_true`` 的结构，
    同时补充来源、真假、可靠度和变异链等显式信息，便于后续推理。
    """

    rule_id: str
    surface_text: str
    deep_meaning: str = ""
    condition: str = ""
    constraint: str = ""
    consequence: str = ""
    source: str = ""
    source_type: str = "system"
    source_id: str | None = None
    is_authentic: bool | None = None
    reliability: float = 1.0
    evolves_into: str | None = None
    rule_type: str | None = None
    related_npc: str | None = None
    opposing_npc: str | None = None
    truth_status: str = "unknown"
    confidence: float = 1.0

    @staticmethod
    def _as_str(value: object, default: str = "") -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (int, float, bool)):
            return str(value).strip()
        return default

    @staticmethod
    def _as_float(value: object, default: float) -> float:
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except Exception:
                return default
        return default

    @classmethod
    def _normalize_truth_status(cls, value: object, is_authentic: bool | None) -> str:
        raw = cls._as_str(value).lower()
        mapping = {
            "true": "true",
            "authentic": "true",
            "real": "true",
            "false": "false",
            "fake": "false",
            "unknown": "unknown",
            "mutated": "mutated",
            "mixed": "mixed",
        }
        if raw in mapping:
            return mapping[raw]
        if is_authentic is True:
            return "true"
        if is_authentic is False:
            return "false"
        return "unknown"

    def to_dict(self) -> JsonObject:
        """转换为兼容旧逻辑的字典。"""
        truth_status = self._normalize_truth_status(self.truth_status, self.is_authentic)
        is_true = self.is_authentic if isinstance(self.is_authentic, bool) else truth_status == "true"
        return {
            "rule_id": self.rule_id,
            "surface_text": self.surface_text,
            "text": self.surface_text,
            "deep_meaning": self.deep_meaning,
            "hidden_meaning": self.deep_meaning,
            "condition": self.condition,
            "constraint": self.constraint or self.surface_text,
            "consequence": self.consequence,
            "source": self.source,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "is_authentic": self.is_authentic,
            "is_true": is_true,
            "reliability": max(0.0, min(1.0, self.reliability)),
            "evolves_into": self.evolves_into,
            "rule_type": self.rule_type,
            "related_npc": self.related_npc,
            "opposing_npc": self.opposing_npc,
            "truth_status": truth_status,
            "confidence": max(0.0, min(1.0, self.confidence)),
        }

    @classmethod
    def from_dict(cls, data: object, fallback_index: int = 0) -> "Rule":
        """从旧版或新版规则字典创建规则对象。"""
        if isinstance(data, cls):
            return data

        if not isinstance(data, dict):
            text = cls._as_str(data)
            return cls(
                rule_id=f"rule_{fallback_index}",
                surface_text=text,
                constraint=text,
            )

        surface_text = cls._as_str(data.get("surface_text")) or cls._as_str(data.get("text")) or cls._as_str(data.get("content"))
        deep_meaning = cls._as_str(data.get("deep_meaning")) or cls._as_str(data.get("hidden_meaning"))
        condition = cls._as_str(data.get("condition")) or cls._as_str(data.get("trigger_condition"))
        consequence = cls._as_str(data.get("consequence"))
        constraint = cls._as_str(data.get("constraint")) or surface_text
        source = cls._as_str(data.get("source"))
        source_type = cls._as_str(data.get("source_type"), "system") or "system"
        source_id = cls._as_str(data.get("source_id")) or None

        is_authentic_raw = data.get("is_authentic")
        if isinstance(is_authentic_raw, bool):
            is_authentic: bool | None = is_authentic_raw
        else:
            legacy_is_true = data.get("is_true")
            is_authentic = legacy_is_true if isinstance(legacy_is_true, bool) else None

        reliability = cls._as_float(data.get("reliability"), 1.0 if is_authentic is not False else 0.35)
        confidence = cls._as_float(data.get("confidence"), reliability)
        rule_id = cls._as_str(data.get("rule_id")) or f"rule_{fallback_index}"

        return cls(
            rule_id=rule_id,
            surface_text=surface_text,
            deep_meaning=deep_meaning,
            condition=condition,
            constraint=constraint,
            consequence=consequence,
            source=source,
            source_type=source_type,
            source_id=source_id,
            is_authentic=is_authentic,
            reliability=max(0.0, min(1.0, reliability)),
            evolves_into=cls._as_str(data.get("evolves_into")) or None,
            rule_type=cls._as_str(data.get("rule_type")) or None,
            related_npc=cls._as_str(data.get("related_npc")) or None,
            opposing_npc=cls._as_str(data.get("opposing_npc")) or None,
            truth_status=cls._normalize_truth_status(data.get("truth_status"), is_authentic),
            confidence=max(0.0, min(1.0, confidence)),
        )


@dataclass
class Player:
    """玩家数据模型"""
    player_id: str
    name: str
    status: PlayerStatus = PlayerStatus.ALIVE
    health: int = 100
    sanity: int = 100
    fatigue: int = 0
    stress_level: int = 0
    anxiety_level: int = 0
    fear_level: int = 0
    hint_count: int = 3
    location: str = "起始位置"
    inventory: list[JsonObject] = field(default_factory=list)
    reasoning_history: list[str] = field(default_factory=list)
    recorded_rules: list[str] = field(default_factory=list)
    action_history: list[JsonObject] = field(default_factory=list)
    joined_at: datetime = field(default_factory=datetime.now)
    last_action_at: datetime | None = None
    # 新增字段
    injury: str = "无伤"  # 受伤情况：无伤/轻伤/重伤/致命伤
    state: str = "正常"   # 精神状态：正常/紧张/恐惧/崩溃/疯狂
    emotion: str = "平静"  # 情绪：平静/焦虑/绝望/愤怒等
    # 多人模式身份字段
    identity: str | None = None  # 玩家身份（多人模式）
    identity_description: str | None = None  # 身份描述
    task_brief: str | None = None  # 当前任务摘要
    duty_area: str | None = None  # 责任区域
    initial_observations: list[str] = field(default_factory=list)  # 初始观察信息
    unique_rules: list[JsonObject] = field(default_factory=list)  # 该身份特有的规则
    exclusive_info: str | None = None  # 该身份独有的信息

    def to_dict(self) -> JsonObject:
        """转换为字典"""
        return {
            "player_id": self.player_id,
            "name": self.name,
            "status": self.status.value,
            "health": self.health,
            "sanity": self.sanity,
            "fatigue": self.fatigue,
            "stress_level": self.stress_level,
            "anxiety_level": self.anxiety_level,
            "fear_level": self.fear_level,
            "hint_count": self.hint_count,
            "location": self.location,
            "inventory": self.inventory,
            "reasoning_history": self.reasoning_history,
            "recorded_rules": self.recorded_rules,
            "action_history": self.action_history,
            "joined_at": self.joined_at.isoformat(),
            "last_action_at": self.last_action_at.isoformat() if self.last_action_at else None,
            "injury": self.injury,
            "state": self.state,
            "emotion": self.emotion,
            "identity": self.identity,
            "identity_description": self.identity_description,
            "task_brief": self.task_brief,
            "duty_area": self.duty_area,
            "initial_observations": self.initial_observations,
            "unique_rules": self.unique_rules,
            "exclusive_info": self.exclusive_info,
        }

    @classmethod
    def from_dict(cls, data: JsonObject) -> Player:
        """从字典创建"""
        def _to_int(v: object, default: int) -> int:
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

        player = cls(
            player_id=str(data.get("player_id", "") or ""),
            name=str(data.get("name", "") or ""),
        )

        # status
        try:
            player.status = PlayerStatus(str(data.get("status", "alive") or "alive"))
        except Exception:
            player.status = PlayerStatus.ALIVE

        player.health = _to_int(data.get("health", 100), 100)
        player.sanity = _to_int(data.get("sanity", 100), 100)
        player.fatigue = _to_int(data.get("fatigue", 0), 0)
        player.stress_level = _to_int(data.get("stress_level", 0), 0)
        player.anxiety_level = _to_int(data.get("anxiety_level", 0), 0)
        player.fear_level = _to_int(data.get("fear_level", 0), 0)
        player.hint_count = _to_int(data.get("hint_count", 3), 3)

        loc = data.get("location", "起始位置")
        player.location = str(loc) if isinstance(loc, (str, int, float, bool)) else "起始位置"

        inv = data.get("inventory", [])
        player.inventory = [x for x in inv if isinstance(x, dict)] if isinstance(inv, list) else []

        rh = data.get("reasoning_history", [])
        player.reasoning_history = [str(x) for x in rh] if isinstance(rh, list) else []

        rr = data.get("recorded_rules", [])
        player.recorded_rules = [str(x).strip() for x in rr if str(x).strip()] if isinstance(rr, list) else []

        ah = data.get("action_history", [])
        player.action_history = [x for x in ah if isinstance(x, dict)] if isinstance(ah, list) else []

        joined_at = data.get("joined_at")
        if isinstance(joined_at, str) and joined_at:
            try:
                player.joined_at = datetime.fromisoformat(joined_at)
            except Exception:
                player.joined_at = datetime.now()

        last_action_at = data.get("last_action_at")
        if isinstance(last_action_at, str) and last_action_at:
            try:
                player.last_action_at = datetime.fromisoformat(last_action_at)
            except Exception:
                player.last_action_at = None

        injury = data.get("injury", "无伤")
        player.injury = str(injury) if isinstance(injury, (str, int, float, bool)) else "无伤"

        state = data.get("state", "正常")
        player.state = str(state) if isinstance(state, (str, int, float, bool)) else "正常"

        emotion = data.get("emotion", "平静")
        player.emotion = str(emotion) if isinstance(emotion, (str, int, float, bool)) else "平静"

        ident = data.get("identity")
        player.identity = str(ident) if isinstance(ident, str) and ident else None

        ident_desc = data.get("identity_description")
        player.identity_description = str(ident_desc) if isinstance(ident_desc, str) and ident_desc else None

        task_brief = data.get("task_brief")
        player.task_brief = str(task_brief) if isinstance(task_brief, str) and task_brief else None

        duty_area = data.get("duty_area")
        player.duty_area = str(duty_area) if isinstance(duty_area, str) and duty_area else None

        observations = data.get("initial_observations", [])
        player.initial_observations = [str(x).strip() for x in observations if str(x).strip()] if isinstance(observations, list) else []

        ur = data.get("unique_rules", [])
        player.unique_rules = [x for x in ur if isinstance(x, dict)] if isinstance(ur, list) else []

        excl = data.get("exclusive_info")
        player.exclusive_info = str(excl) if isinstance(excl, str) and excl else None

        return player


@dataclass
class GameSession:
    """游戏会话数据模型"""
    group_id: str
    scene_name: str = ""
    background: str = ""
    player_identity: str = ""
    hidden_truth: str = ""
    game_mode: str = "单人"
    status: GameStatus = GameStatus.WAITING
    players: dict[str, Player] = field(default_factory=dict)
    rules: list[JsonObject] = field(default_factory=list)
    win_condition: str = ""
    clues: list[JsonObject] = field(default_factory=list)
    core_symbols: list[JsonObject] = field(default_factory=list)
    environment_state: JsonObject = field(default_factory=dict)
    time_manager: JsonObject = field(default_factory=dict)
    scene_structure: JsonObject = field(default_factory=dict)
    npc_guidance: JsonObject = field(default_factory=dict)
    hint_count: int = 3
    has_cleared: bool = False
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    ended_at: datetime | None = None
    environment_memory: JsonObject = field(default_factory=lambda: {
        "visited_locations": [],
        "interacted_objects": [],
        "time_based_events": [],
    })
    rule_mutations: list[JsonObject] = field(default_factory=list)
    last_mutation_time: datetime | None = None
    rule_network: JsonObject = field(default_factory=lambda: {
        "rule_connections": [],
        "collaborative_rules": [],
    })
    # 全局世界状态：记录公共环境状态（关键道具位置、所有玩家状态等）
    world_flags: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        """统一规则结构，避免旧存档和新生成数据混用时分叉。"""
        self.rules = [Rule.from_dict(rule, index).to_dict() for index, rule in enumerate(self.rules)]

    def get_rule_objects(self) -> list[Rule]:
        """获取规则对象列表。"""
        return [Rule.from_dict(rule, index) for index, rule in enumerate(self.rules)]

    def add_player(self, player: Player) -> bool:
        """添加玩家"""
        if len(self.players) >= 5 and self.game_mode == "多人":
            return False

        # 给一个合理的起始位置（避免行动上下文里永远是“起始位置”，导致叙事割裂）
        if getattr(player, "location", "起始位置") == "起始位置":
            areas: list[str] = []
            for fl in (self.scene_structure.get("floors") or []):
                if isinstance(fl, dict):
                    areas.extend([str(x) for x in (fl.get("areas") or fl.get("rooms") or [])])
            areas.extend([str(x) for x in (self.scene_structure.get("special_areas") or [])])

            prefer = [
                "入口",
                "门口",
                "前台",
                "收银",
                "柜台",
                "大厅",
                "大堂",
                "走廊",
            ]
            start_location = None
            for kw in prefer:
                hit = next((a for a in areas if kw in a), None)
                if hit:
                    start_location = hit
                    break

            player.location = start_location or (areas[0] if areas else (self.scene_name or "起始位置"))

        self.players[player.player_id] = player
        self.updated_at = datetime.now()
        return True

    def remove_player(self, player_id: str) -> bool:
        """移除玩家"""
        if player_id in self.players:
            del self.players[player_id]
            self.updated_at = datetime.now()
            return True
        return False

    def get_player(self, player_id: str) -> Player | None:
        """获取玩家"""
        return self.players.get(player_id)
    
    def add_visited_location(self, location: str) -> None:
        """添加已访问位置"""
        if location not in self.environment_memory["visited_locations"]:
            self.environment_memory["visited_locations"].append(location)
            self.updated_at = datetime.now()
    
    def add_interacted_object(self, obj: str) -> None:
        """添加已互动物体"""
        if obj not in self.environment_memory["interacted_objects"]:
            self.environment_memory["interacted_objects"].append(obj)
            self.updated_at = datetime.now()
    
    def add_time_event(self, event: str) -> None:
        """添加时间事件"""
        self.environment_memory["time_based_events"].append({
            "time": datetime.now().isoformat(),
            "event": event,
        })
        self.updated_at = datetime.now()
    
    def add_rule_mutation(self, old_rule: str, new_rule: str, reason: str) -> None:
        """添加规则变异记录"""
        self.rule_mutations.append({
            "time": datetime.now().isoformat(),
            "old_rule": old_rule,
            "new_rule": new_rule,
            "reason": reason,
        })
        self.last_mutation_time = datetime.now()
        self.updated_at = datetime.now()

    def to_dict(self) -> JsonObject:
        """转换为字典"""
        session_hint_count = self.hint_count
        if self.players:
            session_hint_count = min(player.hint_count for player in self.players.values())
        return {
            "group_id": self.group_id,
            "scene_name": self.scene_name,
            "background": self.background,
            "player_identity": self.player_identity,
            "hidden_truth": self.hidden_truth,
            "game_mode": self.game_mode,
            "status": self.status.value,
            "players": {pid: p.to_dict() for pid, p in self.players.items()},
            "rules": [rule.to_dict() for rule in self.get_rule_objects()],
            "win_condition": self.win_condition,
            "clues": self.clues,
            "core_symbols": self.core_symbols,
            "environment_state": self.environment_state,
            "time_manager": self.time_manager,
            "hint_count": session_hint_count,
            "has_cleared": self.has_cleared,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "scene_structure": self.scene_structure,
            "npc_guidance": self.npc_guidance,
            "environment_memory": self.environment_memory,
            "rule_mutations": self.rule_mutations,
            "last_mutation_time": self.last_mutation_time.isoformat() if self.last_mutation_time else None,
            "rule_network": self.rule_network,
            "world_flags": self.world_flags,
        }

    @classmethod
    def from_dict(cls, data: JsonObject) -> GameSession:
        """从字典创建"""
        def _to_int(v: object, default: int) -> int:
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

        def _to_bool(v: object, default: bool = False) -> bool:
            if isinstance(v, bool):
                return v
            if isinstance(v, (int, float)):
                return bool(v)
            if isinstance(v, str):
                return v.strip().lower() in {"1", "true", "yes", "y", "是"}
            return default

        def _to_dt(v: object) -> datetime | None:
            if isinstance(v, str) and v:
                try:
                    return datetime.fromisoformat(v)
                except Exception:
                    return None
            return None

        session = cls(
            group_id=str(data.get("group_id", "") or ""),
            scene_name=str(data.get("scene_name", "") or ""),
            background=str(data.get("background", "") or ""),
            player_identity=str(data.get("player_identity", "") or ""),
            hidden_truth=str(data.get("hidden_truth", "") or ""),
            game_mode=str(data.get("game_mode", "单人") or "单人"),
        )

        # status
        try:
            session.status = GameStatus(str(data.get("status", "waiting") or "waiting"))
        except Exception:
            session.status = GameStatus.WAITING

        players_raw = data.get("players", {})
        if isinstance(players_raw, dict):
            session.players = {
                str(pid): Player.from_dict(p)
                for pid, p in players_raw.items()
                if isinstance(p, dict)
            }
            for pid, player in session.players.items():
                player_data = players_raw.get(pid, {})
                if isinstance(player_data, dict) and "hint_count" not in player_data:
                    player.hint_count = session.hint_count
        else:
            session.players = {}

        rules_raw = data.get("rules", [])
        session.rules = [Rule.from_dict(x, index).to_dict() for index, x in enumerate(rules_raw)] if isinstance(rules_raw, list) else []

        session.win_condition = str(data.get("win_condition", "") or "")

        clues_raw = data.get("clues", [])
        session.clues = [x for x in clues_raw if isinstance(x, dict)] if isinstance(clues_raw, list) else []

        core_symbols_raw = data.get("core_symbols", [])
        session.core_symbols = [x for x in core_symbols_raw if isinstance(x, dict)] if isinstance(core_symbols_raw, list) else []

        env_state = data.get("environment_state", {})
        session.environment_state = env_state if isinstance(env_state, dict) else {}

        time_mgr = data.get("time_manager", {})
        session.time_manager = time_mgr if isinstance(time_mgr, dict) else {}

        session.hint_count = _to_int(data.get("hint_count", 3), 3)
        session.has_cleared = _to_bool(data.get("has_cleared", False), False)

        session.created_at = _to_dt(data.get("created_at")) or datetime.now()
        session.updated_at = _to_dt(data.get("updated_at")) or datetime.now()
        session.ended_at = _to_dt(data.get("ended_at"))

        # 加载新增字段
        scene_structure = data.get("scene_structure", {})
        session.scene_structure = scene_structure if isinstance(scene_structure, dict) else {}

        npc_guidance = data.get("npc_guidance", {})
        session.npc_guidance = npc_guidance if isinstance(npc_guidance, dict) else {}

        env_mem = data.get("environment_memory", {
            "visited_locations": [],
            "interacted_objects": [],
            "time_based_events": [],
        })
        session.environment_memory = env_mem if isinstance(env_mem, dict) else {
            "visited_locations": [],
            "interacted_objects": [],
            "time_based_events": [],
        }

        rm = data.get("rule_mutations", [])
        session.rule_mutations = [x for x in rm if isinstance(x, dict)] if isinstance(rm, list) else []

        session.last_mutation_time = _to_dt(data.get("last_mutation_time"))

        rn = data.get("rule_network", {
            "rule_connections": [],
            "collaborative_rules": [],
        })
        session.rule_network = rn if isinstance(rn, dict) else {
            "rule_connections": [],
            "collaborative_rules": [],
        }

        # 加载全局世界状态
        wf = data.get("world_flags", {})
        session.world_flags = wf if isinstance(wf, dict) else {}

        # 兼容旧版全局已知规则字段：迁移到玩家个人规则笔记后清理旧键。
        if session.players and isinstance(session.environment_state, dict):
            known_indices = session.environment_state.get("known_rule_indices", [])
            known_extra = session.environment_state.get("known_rule_texts_extra", [])
            legacy_rules: list[str] = []

            if isinstance(known_indices, list):
                for idx in sorted({int(x) for x in known_indices if isinstance(x, int)}):
                    if 0 <= idx < len(session.rules):
                        text = Rule.from_dict(session.rules[idx], idx).surface_text.strip()
                        if text:
                            legacy_rules.append(text)

            if isinstance(known_extra, list):
                for raw_text in known_extra:
                    text = str(raw_text).strip()
                    if text:
                        legacy_rules.append(text)

            if legacy_rules:
                for player in session.players.values():
                    existing_rules = [str(rule).strip() for rule in getattr(player, "recorded_rules", []) if str(rule).strip()]
                    seen = {"".join(text.split()).lower() for text in existing_rules if text}
                    for text in legacy_rules:
                        key = "".join(text.split()).lower()
                        if not key or key in seen:
                            continue
                        existing_rules.append(text)
                        seen.add(key)
                    player.recorded_rules = existing_rules

            session.environment_state.pop("known_rule_indices", None)
            session.environment_state.pop("known_rule_texts_extra", None)

        return session
