"""游戏数据模型"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from datetime import datetime
from enum import Enum


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
    location: str = "起始位置"
    inventory: list[dict[str, Any]] = field(default_factory=list)
    reasoning_history: list[str] = field(default_factory=list)
    action_history: list[dict[str, Any]] = field(default_factory=list)
    joined_at: datetime = field(default_factory=datetime.now)
    last_action_at: Optional[datetime] = None
    # 新增字段
    injury: str = "无伤"  # 受伤情况：无伤/轻伤/重伤/致命伤
    state: str = "正常"   # 精神状态：正常/紧张/恐惧/崩溃/疯狂
    emotion: str = "平静"  # 情绪：平静/焦虑/绝望/愤怒等
    # 多人模式身份字段
    identity: Optional[str] = None  # 玩家身份（多人模式）
    identity_description: Optional[str] = None  # 身份描述
    unique_rules: list[dict[str, Any]] = field(default_factory=list)  # 该身份特有的规则
    exclusive_info: Optional[str] = None  # 该身份独有的信息

    def to_dict(self) -> dict[str, Any]:
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
            "location": self.location,
            "inventory": self.inventory,
            "reasoning_history": self.reasoning_history,
            "action_history": self.action_history,
            "joined_at": self.joined_at.isoformat(),
            "last_action_at": self.last_action_at.isoformat() if self.last_action_at else None,
            "injury": self.injury,
            "state": self.state,
            "emotion": self.emotion,
            "identity": self.identity,
            "identity_description": self.identity_description,
            "unique_rules": self.unique_rules,
            "exclusive_info": self.exclusive_info,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Player:
        """从字典创建"""
        player = cls(
            player_id=data["player_id"],
            name=data["name"],
        )
        player.status = PlayerStatus(data.get("status", "alive"))
        player.health = data.get("health", 100)
        player.sanity = data.get("sanity", 100)
        player.fatigue = data.get("fatigue", 0)
        player.stress_level = data.get("stress_level", 0)
        player.anxiety_level = data.get("anxiety_level", 0)
        player.fear_level = data.get("fear_level", 0)
        player.location = data.get("location", "起始位置")
        player.inventory = data.get("inventory", [])
        player.reasoning_history = data.get("reasoning_history", [])
        player.action_history = data.get("action_history", [])
        player.joined_at = datetime.fromisoformat(data["joined_at"]) if "joined_at" in data else datetime.now()
        if data.get("last_action_at"):
            player.last_action_at = datetime.fromisoformat(data["last_action_at"])
        player.injury = data.get("injury", "无伤")
        player.state = data.get("state", "正常")
        player.emotion = data.get("emotion", "平静")
        player.identity = data.get("identity")
        player.identity_description = data.get("identity_description")
        player.unique_rules = data.get("unique_rules", [])
        player.exclusive_info = data.get("exclusive_info")
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
    rules: list[dict[str, Any]] = field(default_factory=list)
    win_condition: str = ""
    clues: list[dict[str, Any]] = field(default_factory=list)
    core_symbols: list[dict[str, Any]] = field(default_factory=list)
    environment_state: dict[str, Any] = field(default_factory=dict)
    time_manager: dict[str, Any] = field(default_factory=dict)
    scene_structure: dict[str, Any] = field(default_factory=dict)
    npc_guidance: dict[str, Any] = field(default_factory=dict)
    hint_count: int = 3
    has_cleared: bool = False
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    ended_at: Optional[datetime] = None
    environment_memory: dict[str, Any] = field(default_factory=lambda: {
        "visited_locations": [],
        "interacted_objects": [],
        "time_based_events": [],
    })
    rule_mutations: list[dict[str, Any]] = field(default_factory=list)
    last_mutation_time: Optional[datetime] = None
    rule_network: dict[str, Any] = field(default_factory=lambda: {
        "rule_connections": [],
        "collaborative_rules": [],
    })

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

    def get_player(self, player_id: str) -> Optional[Player]:
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

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "group_id": self.group_id,
            "scene_name": self.scene_name,
            "background": self.background,
            "player_identity": self.player_identity,
            "hidden_truth": self.hidden_truth,
            "game_mode": self.game_mode,
            "status": self.status.value,
            "players": {pid: p.to_dict() for pid, p in self.players.items()},
            "rules": self.rules,
            "win_condition": self.win_condition,
            "clues": self.clues,
            "core_symbols": self.core_symbols,
            "environment_state": self.environment_state,
            "time_manager": self.time_manager,
            "hint_count": self.hint_count,
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
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GameSession:
        """从字典创建"""
        session = cls(
            group_id=data["group_id"],
            scene_name=data.get("scene_name", ""),
            background=data.get("background", ""),
            player_identity=data.get("player_identity", ""),
            hidden_truth=data.get("hidden_truth", ""),
            game_mode=data.get("game_mode", "单人"),
        )
        session.status = GameStatus(data.get("status", "waiting"))
        session.players = {
            pid: Player.from_dict(p) for pid, p in data.get("players", {}).items()
        }
        session.rules = data.get("rules", [])
        session.win_condition = data.get("win_condition", "")
        session.clues = data.get("clues", [])
        session.core_symbols = data.get("core_symbols", [])
        session.environment_state = data.get("environment_state", {})
        session.time_manager = data.get("time_manager", {})
        session.hint_count = data.get("hint_count", 3)
        session.has_cleared = data.get("has_cleared", False)
        session.created_at = datetime.fromisoformat(data["created_at"]) if "created_at" in data else datetime.now()
        session.updated_at = datetime.fromisoformat(data["updated_at"]) if "updated_at" in data else datetime.now()
        if data.get("ended_at"):
            session.ended_at = datetime.fromisoformat(data["ended_at"])
        
        # 加载新增字段
        session.scene_structure = data.get("scene_structure", {})
        session.npc_guidance = data.get("npc_guidance", {})
        session.environment_memory = data.get("environment_memory", {
            "visited_locations": [],
            "interacted_objects": [],
            "time_based_events": [],
        })
        session.rule_mutations = data.get("rule_mutations", [])
        if data.get("last_mutation_time"):
            session.last_mutation_time = datetime.fromisoformat(data["last_mutation_time"])
        session.rule_network = data.get("rule_network", {
            "rule_connections": [],
            "collaborative_rules": [],
        })
        
        return session
