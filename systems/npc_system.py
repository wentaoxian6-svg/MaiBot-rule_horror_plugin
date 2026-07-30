"""
NPC行为树和记忆系统
为NPC增加目标导向性和记忆，增强NPC的真实感
"""

from __future__ import annotations

from typing import Callable
from enum import Enum
from datetime import datetime
import random

from ..common.models import (
    GameStateDict,
    BehaviorResultDict,
    AttitudeCheckResultDict,
    AttitudeContradictionDict,
    LastSeenInfoDict,
    InteractionRecordDict,
    LocationRecordDict,
    ActionRecordDict,
    JsonObject,
)


class NPCAttitude(Enum):
    """NPC对玩家的态度"""
    FRIENDLY = "友好"
    NEUTRAL = "中立"
    SUSPICIOUS = "怀疑"
    HOSTILE = "敌对"
    FEARFUL = "恐惧"


class BehaviorType(Enum):
    """行为类型"""
    PATROL = "巡逻"
    INVESTIGATE = "调查"
    INTERACT = "互动"
    ESCAPE = "逃跑"
    ATTACK = "攻击"
    IDLE = "待机"


ConditionFunction = Callable[["NPC", GameStateDict], bool]
ActionFunction = Callable[["NPC", GameStateDict], BehaviorResultDict]


class BehaviorNode:
    """行为树节点"""

    def __init__(self, behavior_type: BehaviorType, priority: int = 0) -> None:
        self.behavior_type: BehaviorType = behavior_type
        self.priority: int = priority
        self.conditions: list[ConditionFunction] = []
        self.actions: list[ActionFunction] = []
        self.children: list[BehaviorNode] = []

    def add_condition(self, condition: ConditionFunction) -> None:
        """添加条件判断"""
        self.conditions.append(condition)

    def add_action(self, action: ActionFunction) -> None:
        """添加动作"""
        self.actions.append(action)

    def add_child(self, child: BehaviorNode) -> None:
        """添加子节点"""
        self.children.append(child)

    def evaluate(self, npc: NPC, game_state: GameStateDict) -> bool:
        """评估节点是否可以执行"""
        for condition in self.conditions:
            if not condition(npc, game_state):
                return False
        return True

    def execute(self, npc: NPC, game_state: GameStateDict) -> BehaviorResultDict:
        """执行节点动作"""
        result: BehaviorResultDict = {
            "action": None,
            "target": None,
            "result": None,
            "player_id": None,
            "attitude": None,
            "behavior": self.behavior_type.value,
            "actions": [],
            "status": None
        }

        for action in self.actions:
            action_result = action(npc, game_state)
            if result["actions"] is not None:
                result["actions"].append(action_result)

        return result


class NPCMemory:
    """NPC记忆系统

    记录NPC与玩家的互动历史，形成"伪自由意志"
    支持6维态度向量系统
    """

    def __init__(self):
        self.player_interactions: dict[str, list[InteractionRecordDict]] = {}
        self.player_locations: dict[str, list[LocationRecordDict]] = {}
        self.player_actions: dict[str, list[ActionRecordDict]] = {}
        self.player_attitudes: dict[str, NPCAttitude] = {}
        
        # 6维态度向量系统
        self.player_affection: dict[str, float] = {}  # 好感度 (0-100)
        self.player_suspicion: dict[str, float] = {}  # 怀疑度 (0-100)
        self.player_fear: dict[str, float] = {}  # 恐惧度 (0-100)
        self.player_trust: dict[str, float] = {}  # 信任度 (0-100)
        self.player_hostility: dict[str, float] = {}  # 敌意度 (0-100)
        self.player_dependence: dict[str, float] = {}  # 依赖度 (0-100)
        
        # 旧版兼容（保留）
        self.player_trust_levels: dict[str, float] = {}
        self.player_fear_levels: dict[str, float] = {}
        self.player_suspicion_levels: dict[str, float] = {}
        
        self.last_seen_time: dict[str, int | None] = {}
        self.last_seen_location: dict[str, str | None] = {}
        self.total_interactions: int = 0

        # Task 14：NPC 说谎一致性——记录该 NPC 对各规则说过哪个版本
        # 键：rule_id，值：版本类型（"truth"/"rumor"/"lie"/"refused"）
        # LLM 生成对白前读取此记录，说谎者保持一致，玩家可通过多次试探识别骗子
        self.rule_versions: dict[str, str] = {}
    
    def initialize_attitude_vector(self, player_id: str):
        """初始化玩家的态度向量（初始值：好感度50、信任度50、其他0）"""
        if player_id not in self.player_affection:
            self.player_affection[player_id] = 50.0
            self.player_suspicion[player_id] = 0.0
            self.player_fear[player_id] = 0.0
            self.player_trust[player_id] = 50.0
            self.player_hostility[player_id] = 0.0
            self.player_dependence[player_id] = 0.0
    
    def update_attitude_vector(self, player_id: str, 
                              affection_delta: float = 0.0,
                              suspicion_delta: float = 0.0,
                              fear_delta: float = 0.0,
                              trust_delta: float = 0.0,
                              hostility_delta: float = 0.0,
                              dependence_delta: float = 0.0):
        """更新玩家的态度向量
        
        Args:
            player_id: 玩家ID
            affection_delta: 好感度变化
            suspicion_delta: 怀疑度变化
            fear_delta: 恐惧度变化
            trust_delta: 信任度变化
            hostility_delta: 敌意度变化
            dependence_delta: 依赖度变化
        """
        self.initialize_attitude_vector(player_id)
        
        self.player_affection[player_id] = max(0.0, min(100.0, 
            self.player_affection[player_id] + affection_delta))
        self.player_suspicion[player_id] = max(0.0, min(100.0, 
            self.player_suspicion[player_id] + suspicion_delta))
        self.player_fear[player_id] = max(0.0, min(100.0, 
            self.player_fear[player_id] + fear_delta))
        self.player_trust[player_id] = max(0.0, min(100.0, 
            self.player_trust[player_id] + trust_delta))
        self.player_hostility[player_id] = max(0.0, min(100.0, 
            self.player_hostility[player_id] + hostility_delta))
        self.player_dependence[player_id] = max(0.0, min(100.0, 
            self.player_dependence[player_id] + dependence_delta))
    
    def get_attitude_vector(self, player_id: str) -> dict[str, float]:
        """获取玩家的态度向量"""
        self.initialize_attitude_vector(player_id)
        return {
            "affection": self.player_affection[player_id],
            "suspicion": self.player_suspicion[player_id],
            "fear": self.player_fear[player_id],
            "trust": self.player_trust[player_id],
            "hostility": self.player_hostility[player_id],
            "dependence": self.player_dependence[player_id]
        }
    
    def check_extreme_attitude(self, player_id: str) -> AttitudeCheckResultDict:
        """检查是否有极端态度（用于触发特殊事件）
        
        Returns:
            {"has_extreme": bool, "extreme_type": str, "value": float}
        """
        self.initialize_attitude_vector(player_id)
        
        vector = self.get_attitude_vector(player_id)
        
        for dimension, value in vector.items():
            if value >= 100.0:
                return {
                    "has_extreme": True,
                    "extreme_type": f"{dimension}_max",
                    "dimension": dimension,
                    "value": value
                }
            elif value <= 0.0 and dimension in ["affection", "trust"]:
                return {
                    "has_extreme": True,
                    "extreme_type": f"{dimension}_min",
                    "dimension": dimension,
                    "value": value
                }
        
        return {
            "has_extreme": False,
            "extreme_type": None,
            "dimension": None,
            "value": None
        }

    def check_attitude_contradiction(self, player_id: str) -> AttitudeContradictionDict:
        """检查态度矛盾（如：高好感度+高怀疑度）
        
        Returns:
            {"has_contradiction": bool, "contradiction_type": str}
        """
        self.initialize_attitude_vector(player_id)
        
        vector = self.get_attitude_vector(player_id)
        
        if vector["affection"] > 70 and vector["suspicion"] > 60:
            return {
                "has_contradiction": True,
                "contradiction_type": "affection_suspicion",
                "description": "喜欢但怀疑"
            }
        
        if vector["trust"] > 70 and vector["hostility"] > 60:
            return {
                "has_contradiction": True,
                "contradiction_type": "trust_hostility",
                "description": "信任但敌对"
            }
        
        if vector["fear"] > 70 and vector["dependence"] > 60:
            return {
                "has_contradiction": True,
                "contradiction_type": "fear_dependence",
                "description": "害怕但依赖"
            }
        
        return {
            "has_contradiction": False,
            "contradiction_type": None,
            "description": None
        }

    def record_interaction(self, player_id: str, interaction_type: str, details: JsonObject, game_time: int):
        """记录与玩家的互动"""
        if player_id not in self.player_interactions:
            self.player_interactions[player_id] = []
        
        interaction: InteractionRecordDict = {
            "type": interaction_type,
            "details": details,
            "time": game_time,
            "timestamp": datetime.now().isoformat()
        }
        
        self.player_interactions[player_id].append(interaction)
        self.total_interactions += 1
    
    def record_player_location(self, player_id: str, location: str, game_time: int):
        """记录玩家位置"""
        if player_id not in self.player_locations:
            self.player_locations[player_id] = []
        
        self.player_locations[player_id].append({
            "location": location,
            "time": game_time,
            "timestamp": datetime.now().isoformat()
        })
        
        self.last_seen_time[player_id] = game_time
        self.last_seen_location[player_id] = location
    
    def record_player_action(self, player_id: str, action: str, game_time: int):
        """记录玩家行动"""
        if player_id not in self.player_actions:
            self.player_actions[player_id] = []
        
        self.player_actions[player_id].append({
            "action": action,
            "time": game_time,
            "timestamp": datetime.now().isoformat()
        })
    
    def update_trust_level(self, player_id: str, delta_trust: float):
        """更新对玩家的信任度"""
        if player_id not in self.player_trust_levels:
            self.player_trust_levels[player_id] = 0.5
        
        self.player_trust_levels[player_id] = max(0.0, min(1.0, 
            self.player_trust_levels[player_id] + delta_trust))
    
    def update_fear_level(self, player_id: str, delta_fear: float):
        """更新对玩家的恐惧度"""
        if player_id not in self.player_fear_levels:
            self.player_fear_levels[player_id] = 0.0
        
        self.player_fear_levels[player_id] = max(0.0, min(1.0, 
            self.player_fear_levels[player_id] + delta_fear))
    
    def update_suspicion_level(self, player_id: str, delta_suspicion: float):
        """更新对玩家的怀疑度"""
        if player_id not in self.player_suspicion_levels:
            self.player_suspicion_levels[player_id] = 0.0
        
        self.player_suspicion_levels[player_id] = max(0.0, min(1.0, 
            self.player_suspicion_levels[player_id] + delta_suspicion))
    
    def get_attitude(self, player_id: str) -> NPCAttitude:
        """获取对玩家的态度"""
        return self.player_attitudes.get(player_id, NPCAttitude.NEUTRAL)
    
    def get_trust_level(self, player_id: str) -> float:
        """获取对玩家的信任度"""
        return self.player_trust_levels.get(player_id, 0.5)
    
    def get_fear_level(self, player_id: str) -> float:
        """获取对玩家的恐惧度"""
        return self.player_fear_levels.get(player_id, 0.0)
    
    def get_suspicion_level(self, player_id: str) -> float:
        """获取对玩家的怀疑度"""
        return self.player_suspicion_levels.get(player_id, 0.0)
    
    def get_last_seen_info(self, player_id: str) -> LastSeenInfoDict:
        """获取最后见到玩家的信息"""
        return {
            "time": self.last_seen_time.get(player_id),
            "location": self.last_seen_location.get(player_id)
        }

    def get_recent_interactions(self, player_id: str, count: int = 5) -> list[InteractionRecordDict]:
        """获取最近的互动记录"""
        interactions = self.player_interactions.get(player_id, [])
        return interactions[-count:] if interactions else []

    def has_recent_interaction(self, player_id: str, interaction_type: str,
                               time_window: int = 30, game_time: int = 0) -> bool:
        """检查是否有最近的特定类型互动"""
        interactions = self.player_interactions.get(player_id, [])
        for interaction in reversed(interactions):
            if interaction["type"] == interaction_type:
                if game_time - interaction["time"] <= time_window:
                    return True
        return False

    def to_dict(self) -> JsonObject:
        """序列化为字典"""
        return {
            "player_interactions": self.player_interactions,
            "player_locations": self.player_locations,
            "player_actions": self.player_actions,
            "player_attitudes": {k: v.value for k, v in self.player_attitudes.items()},
            "player_affection": self.player_affection,
            "player_suspicion": self.player_suspicion,
            "player_fear": self.player_fear,
            "player_trust": self.player_trust,
            "player_hostility": self.player_hostility,
            "player_dependence": self.player_dependence,
            "player_trust_levels": self.player_trust_levels,
            "player_fear_levels": self.player_fear_levels,
            "player_suspicion_levels": self.player_suspicion_levels,
            "last_seen_time": self.last_seen_time,
            "last_seen_location": self.last_seen_location,
            "total_interactions": self.total_interactions,
            "rule_versions": self.rule_versions
        }

    @classmethod
    def from_dict(cls, data: JsonObject) -> "NPCMemory":
        """从字典反序列化"""
        memory = cls()
        memory.player_interactions = data.get("player_interactions", {})
        memory.player_locations = data.get("player_locations", {})
        memory.player_actions = data.get("player_actions", {})
        
        for player_id, attitude_str in data.get("player_attitudes", {}).items():
            memory.player_attitudes[player_id] = NPCAttitude(attitude_str)
        
        memory.player_affection = data.get("player_affection", {})
        memory.player_suspicion = data.get("player_suspicion", {})
        memory.player_fear = data.get("player_fear", {})
        memory.player_trust = data.get("player_trust", {})
        memory.player_hostility = data.get("player_hostility", {})
        memory.player_dependence = data.get("player_dependence", {})
        
        memory.player_trust_levels = data.get("player_trust_levels", {})
        memory.player_fear_levels = data.get("player_fear_levels", {})
        memory.player_suspicion_levels = data.get("player_suspicion_levels", {})
        memory.last_seen_time = data.get("last_seen_time", {})
        memory.last_seen_location = data.get("last_seen_location", {})
        memory.total_interactions = data.get("total_interactions", 0)

        # Task 14：反序列化说谎一致性记录，兼容旧存档（无该字段时为空 dict）
        rule_versions_raw = data.get("rule_versions", {})
        memory.rule_versions = rule_versions_raw if isinstance(rule_versions_raw, dict) else {}

        return memory


class NPC:
    """NPC类

    具有行为树和记忆系统的NPC
    """

    def __init__(self, npc_id: str, name: str, role: str,
                 personality: str, initial_location: str):
        self.npc_id: str = npc_id
        self.name: str = name
        self.role: str = role
        self.personality: str = personality
        self.location: str = initial_location
        self.current_location: str = initial_location
        self.home_area: str = initial_location
        self.duty_areas: list[str] = [initial_location]
        self.behavior_logic_summary: str = ""
        self.current_goal: str = ""
        self.last_action: str = ""
        self.last_observed_players: list[str] = []
        self.audible_signature: str = ""
        self.movement_history: list[dict[str, object]] = []
        self.memory: NPCMemory = NPCMemory()
        self.behavior_tree: BehaviorNode | None = None
        self.patrol_route: list[str] = []
        self.patrol_index: int = 0
        self.is_active: bool = True
        self.can_move: bool = True
        self.can_speak: bool = True
        self.danger_level: str = "低"
        self.knowledge_reliability: float = 0.75
        self.deception_tendency: float = 0.1
        self.corruption_level: float = 0.0
        self.current_state: str = "稳定"
        self.bias_tags: list[str] = []
        self.known_rule_ids: list[str] = []
        self.dialogue_history: list[dict[str, object]] = []
        self.max_dialogue_history: int = 50  # 最大对话历史记录数
        self.target_location: str | None = None
        self.current_behavior: BehaviorType | None = None
        # INTERACT 冷却（Task 11）：记录上次互动的现实时间，冷却期内不再强制互动
        self.last_interact_time: datetime | None = None
        # 需求系统 (0-100)：驱动 NPC 主动行为，每 tick 衰减/增长
        self.hunger: float = 30.0       # 饥饿度：递增，>80 触发找食物
        self.fatigue: float = 20.0      # 疲劳度：工作时递增、休息时递减，>90 触发找休息处
        self.curiosity: float = 50.0    # 好奇心：递减，>70 触发调查
        # 作息字段：根据游戏时段切换，影响行为优先级
        self.shift: str = "flexible"           # 班次：day/night/flexible
        self.current_activity: str = "working"  # 当前活动：working/resting
        # NPC-NPC 关系矩阵：记录对其他 NPC 的态度
        self.relationships: dict[str, NPCAttitude] = {}

    def record_dialogue(self, player_id: str, player_message: str, npc_response: str) -> None:
        """记录对话历史

        Args:
            player_id: 玩家ID
            player_message: 玩家消息
            npc_response: NPC回复
        """
        dialogue_record: dict[str, object] = {
            "player_id": player_id,
            "player_message": player_message,
            "npc_response": npc_response,
            "timestamp": datetime.now().isoformat()
        }

        self.dialogue_history.append(dialogue_record)

        # 限制历史记录数量，避免内存无限增长
        if len(self.dialogue_history) > self.max_dialogue_history:
            self.dialogue_history = self.dialogue_history[-self.max_dialogue_history:]

    def get_recent_dialogue(self, player_id: str | None = None, count: int = 5) -> list[dict[str, object]]:
        """获取最近的对话记录

        Args:
            player_id: 玩家ID（可选，不提供则返回所有玩家的记录）
            count: 返回记录数量

        Returns:
            对话记录列表
        """
        if player_id is None:
            return self.dialogue_history[-count:] if self.dialogue_history else []

        # 筛选特定玩家的记录
        player_dialogues = [d for d in self.dialogue_history if d.get("player_id") == player_id]
        return player_dialogues[-count:] if player_dialogues else []

    def set_patrol_route(self, route: list[str]):
        """设置巡逻路线"""
        self.patrol_route = route
        self.patrol_index = 0
    
    def build_behavior_tree(self):
        """构建行为树"""
        root = BehaviorNode(BehaviorType.PATROL, priority=0)
        
        investigate_node = BehaviorNode(BehaviorType.INVESTIGATE, priority=1)
        investigate_node.add_condition(self._should_investigate)
        investigate_node.add_action(self._investigate_sound)
        root.add_child(investigate_node)
        
        escape_node = BehaviorNode(BehaviorType.ESCAPE, priority=2)
        escape_node.add_condition(self._should_escape)
        escape_node.add_action(self._escape_to_safety)
        root.add_child(escape_node)
        
        attack_node = BehaviorNode(BehaviorType.ATTACK, priority=3)
        attack_node.add_condition(self._should_attack)
        attack_node.add_action(self._attack_player)
        root.add_child(attack_node)
        
        interact_node = BehaviorNode(BehaviorType.INTERACT, priority=4)
        interact_node.add_condition(self._should_interact)
        interact_node.add_action(self._interact_with_player)
        root.add_child(interact_node)
        
        patrol_node = BehaviorNode(BehaviorType.PATROL, priority=5)
        patrol_node.add_action(self._patrol)
        root.add_child(patrol_node)
        
        self.behavior_tree = root
    
    def _should_investigate(self, _npc: "NPC", game_state: GameStateDict) -> bool:
        """判断是否应该调查"""
        sounds = game_state.get("recent_sounds", [])
        if not sounds:
            return False

        for sound in sounds:
            if sound.get("distance", 100) < 20:
                return True

        return False

    def _should_escape(self, _npc: "NPC", _game_state: GameStateDict) -> bool:
        """判断是否应该逃跑

        Task 12 修复：改读新六维态度向量的 player_fear（0-100），
        不再读旧版 player_fear_levels（0-1）。
        """
        for _player_id, fear_level in self.memory.player_fear.items():
            if fear_level > 70:
                return True
        return False

    def _should_attack(self, _npc: "NPC", _game_state: GameStateDict) -> bool:
        """判断是否应该攻击

        Task 12 修复：改读新六维态度向量的 player_hostility（0-100），
        不再读旧版 player_attitudes（NPCAttitude 枚举，update_attitude 已删除）。
        阈值与 decide_intent step 2 的 hostility>80 对齐。
        """
        for _player_id, hostility_level in self.memory.player_hostility.items():
            if hostility_level > 80:
                return True
        return False

    def _should_interact(self, _npc: "NPC", game_state: GameStateDict) -> bool:
        """判断是否应该互动

        Task 11 修复：加入冷却机制，近 N 秒已互动过则跳过，
        避免 NPC 被同房间玩家粘住后每 tick 强制互动。
        """
        # 冷却判定：上次互动时间在冷却期内则不再触发
        if self.last_interact_time is not None:
            from ..core.config import get_config
            cooldown_seconds = get_config().npc_sim.npc_interact_cooldown_seconds
            elapsed = (datetime.now() - self.last_interact_time).total_seconds()
            if elapsed < cooldown_seconds:
                return False

        players = game_state.get("players", {})
        for _player_id, player_data in players.items():
            if player_data.get("location") == self.current_location:
                return True
        return False

    def _investigate_sound(self, _npc: "NPC", game_state: GameStateDict) -> BehaviorResultDict:
        """调查声音"""
        sounds = game_state.get("recent_sounds", [])
        if not sounds:
            return {"action": "等待声音", "result": "无声音可调查"}

        nearest_sound = min(sounds, key=lambda s: s.get("distance", 100))
        self.target_location = nearest_sound.get("location", self.current_location)

        return {
            "action": "调查声音",
            "target": self.target_location,
            "result": f"前往{self.target_location}调查声音"
        }

    def _escape_to_safety(self, _npc: "NPC", game_state: GameStateDict) -> BehaviorResultDict:
        """逃往安全地点"""
        safe_locations = game_state.get("safe_locations", [self.current_location])
        if safe_locations:
            self.target_location = random.choice(safe_locations)
        else:
            self.target_location = self.current_location

        return {
            "action": "逃跑",
            "target": self.target_location,
            "result": f"逃往{self.target_location}"
        }

    def _attack_player(self, _npc: "NPC", game_state: GameStateDict) -> BehaviorResultDict:
        """攻击玩家

        Task 12 修复：目标选择改用 player_hostility（0-100），
        不再读旧版 player_attitudes 枚举。
        """
        hostile_players = [
            pid for pid, hostility in self.memory.player_hostility.items()
            if hostility > 80
        ]

        if hostile_players:
            target_player = hostile_players[0]
            players = game_state.get("players", {})
            if target_player in players:
                self.target_location = players[target_player].get("location", self.current_location)

        return {
            "action": "攻击",
            "target": self.target_location,
            "result": f"前往{self.target_location}攻击玩家"
        }
    
    def _interact_with_player(self, _npc: "NPC", game_state: GameStateDict) -> BehaviorResultDict:
        """与玩家互动"""
        players = game_state.get("players", {})
        nearby_players = [
            (pid, pdata) for pid, pdata in players.items()
            if pdata.get("location") == self.current_location
        ]

        if nearby_players:
            player_id, player_data = nearby_players[0]
            attitude = self.memory.get_attitude(player_id)
            # Task 11：记录互动现实时间，用于冷却判定
            self.last_interact_time = datetime.now()

            return {
                "action": "互动",
                "player_id": player_id,
                "attitude": attitude.value,
                "result": f"与玩家{player_data.get('name', '')}互动，态度：{attitude.value}"
            }

        return {"action": "互动", "result": "附近没有玩家"}

    def _patrol(self, _npc: "NPC", _game_state: GameStateDict) -> BehaviorResultDict:
        """巡逻"""
        if not self.patrol_route:
            return {"action": "巡逻", "result": "无巡逻路线，原地待命"}

        next_location = self.patrol_route[self.patrol_index]
        self.target_location = next_location
        self.patrol_index = (self.patrol_index + 1) % len(self.patrol_route)

        return {
            "action": "巡逻",
            "target": next_location,
            "result": f"前往{next_location}巡逻"
        }

    def update(self, game_state: GameStateDict, _game_time: int) -> BehaviorResultDict:
        """更新NPC状态"""
        if not self.is_active:
            return {"status": "inactive"}

        if not self.behavior_tree:
            self.build_behavior_tree()

        if self.behavior_tree:
            for node in sorted(self.behavior_tree.children, key=lambda n: n.priority):
                if node.evaluate(self, game_state):
                    result = node.execute(self, game_state)
                    self.current_behavior = node.behavior_type

                    if self.target_location and self.can_move:
                        self.current_location = self.target_location

                    return result

        return {"status": "idle"}

    def tick_needs(self) -> None:
        """每次 tick 更新需求值。

        饥饿度持续递增；疲劳度根据作息递增或递减；好奇心缓慢递减。
        """
        # 饥饿度每 tick 递增
        self.hunger = min(100.0, self.hunger + 1.5)
        # 疲劳度：工作时递增，休息时递减
        if self.current_activity == "resting":
            self.fatigue = max(0.0, self.fatigue - 5.0)
        else:
            self.fatigue = min(100.0, self.fatigue + 2.0)
        # 好奇心每 tick 缓慢递减（调查后可回升）
        self.curiosity = max(0.0, self.curiosity - 0.5)

    def update_activity_by_phase(self, time_phase: str) -> None:
        """根据游戏时段切换作息。

        Args:
            time_phase: 时段标签（opening/midnight/deep_night/pre_dawn/dawn）
        """
        night_phases = ("midnight", "deep_night", "pre_dawn", "dawn")
        is_night = time_phase in night_phases

        if self.shift == "night":
            # 夜班：夜间工作，白天休息
            self.current_activity = "working" if is_night else "resting"
        elif self.shift == "day":
            # 白班：白天工作，夜间休息
            self.current_activity = "resting" if is_night else "working"
        else:
            # flexible：疲劳度高时自动休息
            self.current_activity = "resting" if self.fatigue > 80 else "working"

    def decide_intent(self, time_phase: str, game_state: GameStateDict | None = None) -> BehaviorType:
        """根据行为树+态度向量+需求+作息决定意图类别。

        优先级（从高到低）：
        1. 行为树评估（环境/玩家交互条件，激活 build_behavior_tree）
        2. 态度向量极端值（恐惧/敌意/怀疑，6维向量）
        3. 需求驱动（饥饿/疲劳）
        4. 作息驱动（班次+时段）
        5. 好奇心驱动
        6. 默认巡逻

        Args:
            time_phase: 当前游戏时段标签（opening/midnight/deep_night/pre_dawn/dawn）
            game_state: 可选的游戏状态，用于行为树条件评估

        Returns:
            意图类别（BehaviorType 枚举）
        """
        # 确保行为树已构建（激活行为树）
        if not self.behavior_tree:
            self.build_behavior_tree()

        # 1. 行为树优先级评估（ESCAPE > ATTACK > INVESTIGATE > INTERACT）
        if game_state and self.behavior_tree:
            for node in sorted(self.behavior_tree.children, key=lambda n: n.priority):
                if node.evaluate(self, game_state):
                    self.current_behavior = node.behavior_type
                    # Task 11：INTERACT 意图触发时记录现实时间，用于冷却判定
                    if node.behavior_type == BehaviorType.INTERACT:
                        self.last_interact_time = datetime.now()
                    return node.behavior_type

        # 2. 态度向量极端值（6维向量，补充行为树未覆盖的情况）
        for hostility_level in self.memory.player_hostility.values():
            if hostility_level > 80:
                self.current_behavior = BehaviorType.ATTACK
                return BehaviorType.ATTACK
        for fear_level in self.memory.player_fear.values():
            if fear_level > 70:
                self.current_behavior = BehaviorType.ESCAPE
                return BehaviorType.ESCAPE
        for suspicion_level in self.memory.player_suspicion.values():
            if suspicion_level > 60:
                self.current_behavior = BehaviorType.INVESTIGATE
                return BehaviorType.INVESTIGATE

        # 3. 需求驱动
        if self.fatigue > 90:
            # 疲劳极高，找休息处（巡逻回家区域）
            self.current_behavior = BehaviorType.PATROL
            return BehaviorType.PATROL
        if self.hunger > 80:
            # 饥饿极高，找食物
            self.current_behavior = BehaviorType.PATROL
            return BehaviorType.PATROL

        # 4. 作息驱动
        night_phases = ("midnight", "deep_night", "pre_dawn", "dawn")
        is_night = time_phase in night_phases
        if self.shift == "night" and is_night and self.current_activity == "working":
            # 夜班 NPC 在夜间巡逻
            self.current_behavior = BehaviorType.PATROL
            return BehaviorType.PATROL
        if self.shift == "day" and is_night and self.current_activity == "resting":
            # 白班 NPC 夜间被惊醒，调查
            self.current_behavior = BehaviorType.INVESTIGATE
            return BehaviorType.INVESTIGATE

        # 5. 好奇心驱动
        if self.curiosity > 70:
            self.current_behavior = BehaviorType.INVESTIGATE
            return BehaviorType.INVESTIGATE

        # 6. 默认巡逻
        self.current_behavior = BehaviorType.PATROL
        return BehaviorType.PATROL

    def update_relationship(self, other_npc_id: str, attitude: NPCAttitude) -> None:
        """更新对其他 NPC 的态度。

        Args:
            other_npc_id: 其他 NPC 的 ID
            attitude: 态度（NPCAttitude 枚举）
        """
        self.relationships[other_npc_id] = attitude

    def to_dict(self) -> JsonObject:
        """序列化为字典"""
        return {
            "npc_id": self.npc_id,
            "name": self.name,
            "role": self.role,
            "personality": self.personality,
            "location": self.location,
            "current_location": self.current_location,
            "home_area": self.home_area,
            "duty_areas": self.duty_areas,
            "behavior_logic_summary": self.behavior_logic_summary,
            "current_goal": self.current_goal,
            "last_action": self.last_action,
            "last_observed_players": self.last_observed_players,
            "audible_signature": self.audible_signature,
            "movement_history": self.movement_history,
            "memory": self.memory.to_dict(),
            "patrol_route": self.patrol_route,
            "patrol_index": self.patrol_index,
            "is_active": self.is_active,
            "can_move": self.can_move,
            "can_speak": self.can_speak,
            "danger_level": self.danger_level,
            "knowledge_reliability": self.knowledge_reliability,
            "deception_tendency": self.deception_tendency,
            "corruption_level": self.corruption_level,
            "current_state": self.current_state,
            "bias_tags": self.bias_tags,
            "known_rule_ids": self.known_rule_ids,
            "dialogue_history": self.dialogue_history,
            "target_location": self.target_location,
            "current_behavior": self.current_behavior.value if self.current_behavior else None,
            "last_interact_time": self.last_interact_time.isoformat() if self.last_interact_time else None,
            "hunger": self.hunger,
            "fatigue": self.fatigue,
            "curiosity": self.curiosity,
            "shift": self.shift,
            "current_activity": self.current_activity,
            "relationships": {k: v.value for k, v in self.relationships.items()}
        }

    @classmethod
    def from_dict(cls, data: JsonObject) -> "NPC":
        """从字典反序列化"""
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

        def _clamp_0_100(value: object, default: float) -> float:
            """将值钳制到 0-100 区间（用于需求系统字段）。"""
            if isinstance(value, bool):
                return 100.0 if value else 0.0
            if isinstance(value, (int, float)):
                return max(0.0, min(100.0, float(value)))
            if isinstance(value, str):
                try:
                    return max(0.0, min(100.0, float(value.strip())))
                except (TypeError, ValueError):
                    return default
            return default

        npc = cls(
            data["npc_id"],
            data["name"],
            data["role"],
            data["personality"],
            data["location"]
        )
        
        npc.current_location = data.get("current_location", data["location"])
        npc.home_area = str(data.get("home_area", npc.current_location) or npc.current_location)
        duty_areas = data.get("duty_areas", [npc.current_location])
        npc.duty_areas = [str(item) for item in duty_areas if str(item).strip()] if isinstance(duty_areas, list) else [npc.current_location]
        npc.behavior_logic_summary = str(data.get("behavior_logic_summary", "") or "")
        npc.current_goal = str(data.get("current_goal", "") or "")
        npc.last_action = str(data.get("last_action", "") or "")
        last_observed_players = data.get("last_observed_players", [])
        npc.last_observed_players = [str(item) for item in last_observed_players if str(item).strip()] if isinstance(last_observed_players, list) else []
        npc.audible_signature = str(data.get("audible_signature", "") or "")
        movement_history = data.get("movement_history", [])
        npc.movement_history = [item for item in movement_history if isinstance(item, dict)] if isinstance(movement_history, list) else []
        npc.memory = NPCMemory.from_dict(data.get("memory", {}))
        npc.patrol_route = data.get("patrol_route", [])
        npc.patrol_index = data.get("patrol_index", 0)
        npc.is_active = data.get("is_active", True)
        npc.can_move = data.get("can_move", True)
        npc.can_speak = data.get("can_speak", True)
        npc.danger_level = data.get("danger_level", "低")
        npc.knowledge_reliability = _clamp_ratio(data.get("knowledge_reliability"), 0.75)
        npc.deception_tendency = _clamp_ratio(data.get("deception_tendency"), 0.1)
        npc.corruption_level = _clamp_ratio(data.get("corruption_level"), 0.0)
        npc.current_state = str(data.get("current_state", "稳定") or "稳定")
        bias_tags = data.get("bias_tags", [])
        npc.bias_tags = [str(item).strip() for item in bias_tags if str(item).strip()] if isinstance(bias_tags, list) else []
        known_rule_ids = data.get("known_rule_ids", [])
        npc.known_rule_ids = [str(item).strip() for item in known_rule_ids if str(item).strip()] if isinstance(known_rule_ids, list) else []
        npc.dialogue_history = data.get("dialogue_history", [])
        npc.target_location = data.get("target_location")
        
        behavior_str = data.get("current_behavior")
        if behavior_str:
            npc.current_behavior = BehaviorType(behavior_str)

        # Task 11：反序列化 INTERACT 冷却时间戳
        last_interact_raw = data.get("last_interact_time")
        if isinstance(last_interact_raw, str) and last_interact_raw:
            try:
                npc.last_interact_time = datetime.fromisoformat(last_interact_raw)
            except (TypeError, ValueError):
                npc.last_interact_time = None

        # 需求系统 (0-100)
        npc.hunger = _clamp_0_100(data.get("hunger"), 30.0)
        npc.fatigue = _clamp_0_100(data.get("fatigue"), 20.0)
        npc.curiosity = _clamp_0_100(data.get("curiosity"), 50.0)
        # 作息字段
        shift_raw = str(data.get("shift", "flexible") or "flexible").strip().lower()
        npc.shift = shift_raw if shift_raw in ("day", "night", "flexible") else "flexible"
        activity_raw = str(data.get("current_activity", "working") or "working").strip().lower()
        npc.current_activity = activity_raw if activity_raw in ("working", "resting") else "working"
        # NPC-NPC 关系矩阵
        relationships_raw = data.get("relationships", {})
        if isinstance(relationships_raw, dict):
            for other_id, attitude_str in relationships_raw.items():
                try:
                    npc.relationships[str(other_id)] = NPCAttitude(attitude_str)
                except (TypeError, ValueError):
                    continue

        return npc
