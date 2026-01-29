# pyright: reportDeprecated=false
# pyright: reportUnknownVariableType=false
# pyright: reportMissingParameterType=false
# pyright: reportAny=false

"""
NPC行为树和记忆系统
为NPC增加目标导向性和记忆，增强NPC的真实感
"""

from typing import Any
from enum import Enum
from datetime import datetime
import random


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


class BehaviorNode:
    """行为树节点"""

    def __init__(self, behavior_type: BehaviorType, priority: int = 0):
        self.behavior_type: BehaviorType = behavior_type
        self.priority: int = priority
        self.conditions: list[Any] = []
        self.actions: list[Any] = []
        self.children: list[BehaviorNode] = []

    def add_condition(self, condition: Any):
        """添加条件判断"""
        self.conditions.append(condition)

    def add_action(self, action: Any):
        """添加动作"""
        self.actions.append(action)

    def add_child(self, child: "BehaviorNode"):
        """添加子节点"""
        self.children.append(child)

    def evaluate(self, npc: "NPC", game_state: dict[str, Any]) -> bool:
        """评估节点是否可以执行"""
        for condition in self.conditions:
            if not condition(npc, game_state):
                return False
        return True

    def execute(self, npc: "NPC", game_state: dict[str, Any]) -> dict[str, Any]:
        """执行节点动作"""
        result: dict[str, Any] = {
            "behavior": self.behavior_type.value,
            "actions": []
        }

        for action in self.actions:
            action_result = action(npc, game_state)
            result["actions"].append(action_result)

        return result


class NPCMemory:
    """NPC记忆系统

    记录NPC与玩家的互动历史，形成"伪自由意志"
    """

    def __init__(self):
        self.player_interactions: dict[str, list[dict[str, Any]]] = {}
        self.player_locations: dict[str, list[dict[str, Any]]] = {}
        self.player_actions: dict[str, list[dict[str, Any]]] = {}
        self.player_attitudes: dict[str, NPCAttitude] = {}
        self.player_trust_levels: dict[str, float] = {}
        self.player_fear_levels: dict[str, float] = {}
        self.player_suspicion_levels: dict[str, float] = {}
        self.last_seen_time: dict[str, int | None] = {}
        self.last_seen_location: dict[str, str | None] = {}
        self.total_interactions: int = 0
    
    def record_interaction(self, player_id: str, interaction_type: str, details: dict[str, Any], game_time: int):
        """记录与玩家的互动"""
        if player_id not in self.player_interactions:
            self.player_interactions[player_id] = []
        
        interaction = {
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
    
    def update_attitude(self, player_id: str, delta_attitude: float):
        """更新对玩家的态度"""
        if player_id not in self.player_attitudes:
            self.player_attitudes[player_id] = NPCAttitude.NEUTRAL
        
        current_attitude = self.player_attitudes[player_id]
        
        if delta_attitude > 0.3:
            new_attitude = NPCAttitude.FRIENDLY
        elif delta_attitude > 0.1:
            new_attitude = NPCAttitude.NEUTRAL
        elif delta_attitude > -0.1:
            new_attitude = NPCAttitude.SUSPICIOUS
        elif delta_attitude > -0.3:
            new_attitude = NPCAttitude.HOSTILE
        else:
            new_attitude = NPCAttitude.FEARFUL
        
        self.player_attitudes[player_id] = new_attitude
    
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
    
    def get_last_seen_info(self, player_id: str) -> dict[str, Any]:
        """获取最后见到玩家的信息"""
        return {
            "time": self.last_seen_time.get(player_id),
            "location": self.last_seen_location.get(player_id)
        }

    def get_recent_interactions(self, player_id: str, count: int = 5) -> list[dict[str, Any]]:
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

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典"""
        return {
            "player_interactions": self.player_interactions,
            "player_locations": self.player_locations,
            "player_actions": self.player_actions,
            "player_attitudes": {k: v.value for k, v in self.player_attitudes.items()},
            "player_trust_levels": self.player_trust_levels,
            "player_fear_levels": self.player_fear_levels,
            "player_suspicion_levels": self.player_suspicion_levels,
            "last_seen_time": self.last_seen_time,
            "last_seen_location": self.last_seen_location,
            "total_interactions": self.total_interactions
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NPCMemory":
        """从字典反序列化"""
        memory = cls()
        memory.player_interactions = data.get("player_interactions", {})
        memory.player_locations = data.get("player_locations", {})
        memory.player_actions = data.get("player_actions", {})
        
        for player_id, attitude_str in data.get("player_attitudes", {}).items():
            memory.player_attitudes[player_id] = NPCAttitude(attitude_str)
        
        memory.player_trust_levels = data.get("player_trust_levels", {})
        memory.player_fear_levels = data.get("player_fear_levels", {})
        memory.player_suspicion_levels = data.get("player_suspicion_levels", {})
        memory.last_seen_time = data.get("last_seen_time", {})
        memory.last_seen_location = data.get("last_seen_location", {})
        memory.total_interactions = data.get("total_interactions", 0)
        
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
        self.memory: NPCMemory = NPCMemory()
        self.behavior_tree: BehaviorNode | None = None
        self.patrol_route: list[str] = []
        self.patrol_index: int = 0
        self.is_active: bool = True
        self.can_move: bool = True
        self.can_speak: bool = True
        self.danger_level: str = "低"
        self.dialogue_history: list[dict[str, Any]] = []
        self.target_location: str | None = None
        self.current_behavior: BehaviorType | None = None

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
    
    def _should_investigate(self, _npc: "NPC", game_state: dict[str, Any]) -> bool:
        """判断是否应该调查"""
        sounds = game_state.get("recent_sounds", [])
        if not sounds:
            return False

        for sound in sounds:
            if sound.get("distance", 100) < 20:
                return True

        return False

    def _should_escape(self, _npc: "NPC", _game_state: dict[str, Any]) -> bool:
        """判断是否应该逃跑"""
        for _player_id, fear_level in self.memory.player_fear_levels.items():
            if fear_level > 0.7:
                return True
        return False

    def _should_attack(self, _npc: "NPC", _game_state: dict[str, Any]) -> bool:
        """判断是否应该攻击"""
        for _player_id, attitude in self.memory.player_attitudes.items():
            if attitude == NPCAttitude.HOSTILE:
                return True
        return False

    def _should_interact(self, _npc: "NPC", game_state: dict[str, Any]) -> bool:
        """判断是否应该互动"""
        players = game_state.get("players", {})
        for _player_id, player_data in players.items():
            if player_data.get("location") == self.current_location:
                return True
        return False

    def _investigate_sound(self, _npc: "NPC", game_state: dict[str, Any]) -> dict[str, Any]:
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

    def _escape_to_safety(self, _npc: "NPC", game_state: dict[str, Any]) -> dict[str, Any]:
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

    def _attack_player(self, _npc: "NPC", game_state: dict[str, Any]) -> dict[str, Any]:
        """攻击玩家"""
        hostile_players = [
            pid for pid, attitude in self.memory.player_attitudes.items()
            if attitude == NPCAttitude.HOSTILE
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
    
    def _interact_with_player(self, _npc: "NPC", game_state: dict[str, Any]) -> dict[str, Any]:
        """与玩家互动"""
        players = game_state.get("players", {})
        nearby_players = [
            (pid, pdata) for pid, pdata in players.items()
            if pdata.get("location") == self.current_location
        ]

        if nearby_players:
            player_id, player_data = nearby_players[0]
            attitude = self.memory.get_attitude(player_id)

            return {
                "action": "互动",
                "player_id": player_id,
                "attitude": attitude.value,
                "result": f"与玩家{player_data.get('name', '')}互动，态度：{attitude.value}"
            }

        return {"action": "互动", "result": "附近没有玩家"}

    def _patrol(self, _npc: "NPC", _game_state: dict[str, Any]) -> dict[str, Any]:
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

    def update(self, game_state: dict[str, Any], _game_time: int) -> dict[str, Any]:
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
    
    def generate_dialogue(self, player_id: str, context: str) -> str:
        """生成对话内容"""
        attitude = self.memory.get_attitude(player_id)
        trust_level = self.memory.get_trust_level(player_id)
        suspicion_level = self.memory.get_suspicion_level(player_id)
        
        dialogue_prompts = {
            NPCAttitude.FRIENDLY: [
                f"你好，{context}。",
                f"很高兴见到你，{context}。",
                f"有什么我可以帮助你的吗？{context}"
            ],
            NPCAttitude.NEUTRAL: [
                f"嗯，{context}。",
                f"我知道了，{context}。",
                f"好吧，{context}。"
            ],
            NPCAttitude.SUSPICIOUS: [
                f"你确定吗？{context}。",
                f"我有点怀疑，{context}。",
                f"你为什么要问这个？{context}"
            ],
            NPCAttitude.HOSTILE: [
                f"滚开！{context}。",
                f"我不相信你，{context}。",
                f"别靠近我！{context}"
            ],
            NPCAttitude.FEARFUL: [
                f"别...别过来...{context}。",
                f"我...我不知道...{context}。",
                f"求求你...{context}。"
            ]
        }
        
        prompts = dialogue_prompts.get(attitude, dialogue_prompts[NPCAttitude.NEUTRAL])
        return random.choice(prompts)
    
    def to_dict(self) -> dict[str, Any]:
        """序列化为字典"""
        return {
            "npc_id": self.npc_id,
            "name": self.name,
            "role": self.role,
            "personality": self.personality,
            "location": self.location,
            "current_location": self.current_location,
            "memory": self.memory.to_dict(),
            "patrol_route": self.patrol_route,
            "patrol_index": self.patrol_index,
            "is_active": self.is_active,
            "can_move": self.can_move,
            "can_speak": self.can_speak,
            "danger_level": self.danger_level,
            "dialogue_history": self.dialogue_history,
            "target_location": self.target_location,
            "current_behavior": self.current_behavior.value if self.current_behavior else None
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NPC":
        """从字典反序列化"""
        npc = cls(
            data["npc_id"],
            data["name"],
            data["role"],
            data["personality"],
            data["location"]
        )
        
        npc.current_location = data.get("current_location", data["location"])
        npc.memory = NPCMemory.from_dict(data.get("memory", {}))
        npc.patrol_route = data.get("patrol_route", [])
        npc.patrol_index = data.get("patrol_index", 0)
        npc.is_active = data.get("is_active", True)
        npc.can_move = data.get("can_move", True)
        npc.can_speak = data.get("can_speak", True)
        npc.danger_level = data.get("danger_level", "低")
        npc.dialogue_history = data.get("dialogue_history", [])
        npc.target_location = data.get("target_location")
        
        behavior_str = data.get("current_behavior")
        if behavior_str:
            npc.current_behavior = BehaviorType(behavior_str)
        
        return npc
