"""
规则变异的条件触发系统
改用条件触发制，而非随机概率，增强叙事连贯性
"""

from typing import Dict, List, Optional, Set
from enum import Enum
from dataclasses import dataclass


class MutationTriggerType(Enum):
    """规则变异触发类型"""
    ENVIRONMENT = "环境条件"
    BEHAVIOR = "行为条件"
    PLOT = "剧情条件"
    TIME = "时间条件"
    ITEM = "物品条件"
    LOCATION = "位置条件"


@dataclass
class MutationCondition:
    """变异条件"""
    condition_type: MutationTriggerType
    description: str
    check_function: callable
    priority: int = 0


@dataclass
class MutationEvent:
    """变异事件"""
    mutation_id: str
    trigger_type: MutationTriggerType
    trigger_reason: str
    triggered_by: List[str]
    triggered_at: int
    mutation_details: Dict[str, any]


class RuleMutationSystem:
    """规则变异系统
    
    负责管理规则变异的条件触发，确保：
    - 规则变异基于条件触发，而非随机概率
    - 变异有叙事铺垫，不突兀
    - 支持多种触发条件（环境、行为、剧情、时间、物品、位置）
    - 记录变异历史，便于追踪
    """
    
    def __init__(self):
        self.mutation_conditions: List[MutationCondition] = []
        self.mutation_history: List[MutationEvent] = []
        self.triggered_conditions: Set[str] = set()
        self.violation_counts: Dict[str, int] = {}
        self.location_visit_counts: Dict[str, int] = {}
        self.item_discovery_counts: Dict[str, int] = {}
        self.key_clues_found: Set[str] = set()
        self.mutation_cooldown: int = 30
        self.last_mutation_time: int = -999
    
    def add_condition(self, condition: MutationCondition):
        """添加变异条件"""
        self.mutation_conditions.append(condition)
    
    def check_conditions(self, game_state: Dict, player_action: Optional[str] = None, 
                         game_time: int = 0) -> List[MutationCondition]:
        """检查所有变异条件
        
        Args:
            game_state: 游戏状态
            player_action: 玩家行动（可选）
            game_time: 游戏时间
        
        Returns:
            满足条件的列表
        """
        satisfied_conditions = []
        
        if game_time - self.last_mutation_time < self.mutation_cooldown:
            return satisfied_conditions
        
        for condition in self.mutation_conditions:
            condition_key = f"{condition.condition_type.value}_{condition.description}"
            
            if condition_key in self.triggered_conditions:
                continue
            
            try:
                if condition.check_function(game_state, player_action, self):
                    satisfied_conditions.append(condition)
            except Exception as e:
                print(f"[规则变异系统] 检查条件时出错: {e}")
        
        return satisfied_conditions
    
    def trigger_mutation(self, condition: MutationCondition, game_state: Dict, 
                        game_time: int, triggered_by: List[str]) -> MutationEvent:
        """触发规则变异
        
        Args:
            condition: 触发条件
            game_state: 游戏状态
            game_time: 游戏时间
            triggered_by: 触发者列表
        
        Returns:
            变异事件
        """
        condition_key = f"{condition.condition_type.value}_{condition.description}"
        self.triggered_conditions.add(condition_key)
        self.last_mutation_time = game_time
        
        mutation_event = MutationEvent(
            mutation_id=f"mutation_{len(self.mutation_history)}",
            trigger_type=condition.condition_type,
            trigger_reason=condition.description,
            triggered_by=triggered_by,
            triggered_at=game_time,
            mutation_details={}
        )
        
        self.mutation_history.append(mutation_event)
        
        return mutation_event
    
    def record_violation(self, rule_id: str, player_id: str):
        """记录规则违反"""
        key = f"{rule_id}_{player_id}"
        self.violation_counts[key] = self.violation_counts.get(key, 0) + 1
    
    def get_violation_count(self, rule_id: str, player_id: str) -> int:
        """获取规则违反次数"""
        key = f"{rule_id}_{player_id}"
        return self.violation_counts.get(key, 0)
    
    def record_location_visit(self, location: str, player_id: str):
        """记录位置访问"""
        key = f"{location}_{player_id}"
        self.location_visit_counts[key] = self.location_visit_counts.get(key, 0) + 1
    
    def get_location_visit_count(self, location: str, player_id: str) -> int:
        """获取位置访问次数"""
        key = f"{location}_{player_id}"
        return self.location_visit_counts.get(key, 0)
    
    def record_item_discovery(self, item_id: str, player_id: str):
        """记录物品发现"""
        key = f"{item_id}_{player_id}"
        self.item_discovery_counts[key] = self.item_discovery_counts.get(key, 0) + 1
    
    def record_key_clue(self, clue_id: str):
        """记录关键线索发现"""
        self.key_clues_found.add(clue_id)
    
    def has_found_key_clue(self, clue_id: str) -> bool:
        """检查是否发现关键线索"""
        return clue_id in self.key_clues_found
    
    def get_mutation_history(self) -> List[MutationEvent]:
        """获取变异历史"""
        return self.mutation_history
    
    def reset(self):
        """重置系统"""
        self.triggered_conditions.clear()
        self.violation_counts.clear()
        self.location_visit_counts.clear()
        self.item_discovery_counts.clear()
        self.key_clues_found.clear()
        self.last_mutation_time = -999
    
    def to_dict(self) -> Dict[str, any]:
        """序列化为字典"""
        return {
            "mutation_history": [
                {
                    "mutation_id": event.mutation_id,
                    "trigger_type": event.trigger_type.value,
                    "trigger_reason": event.trigger_reason,
                    "triggered_by": event.triggered_by,
                    "triggered_at": event.triggered_at,
                    "mutation_details": event.mutation_details
                }
                for event in self.mutation_history
            ],
            "triggered_conditions": list(self.triggered_conditions),
            "violation_counts": self.violation_counts,
            "location_visit_counts": self.location_visit_counts,
            "item_discovery_counts": self.item_discovery_counts,
            "key_clues_found": list(self.key_clues_found),
            "last_mutation_time": self.last_mutation_time
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, any]) -> 'RuleMutationSystem':
        """从字典反序列化"""
        system = cls()
        
        system.triggered_conditions = set(data.get("triggered_conditions", []))
        system.violation_counts = data.get("violation_counts", {})
        system.location_visit_counts = data.get("location_visit_counts", {})
        system.item_discovery_counts = data.get("item_discovery_counts", {})
        system.key_clues_found = set(data.get("key_clues_found", []))
        system.last_mutation_time = data.get("last_mutation_time", -999)
        
        mutation_history_data = data.get("mutation_history", [])
        for event_data in mutation_history_data:
            event = MutationEvent(
                mutation_id=event_data["mutation_id"],
                trigger_type=MutationTriggerType(event_data["trigger_type"]),
                trigger_reason=event_data["trigger_reason"],
                triggered_by=event_data["triggered_by"],
                triggered_at=event_data["triggered_at"],
                mutation_details=event_data["mutation_details"]
            )
            system.mutation_history.append(event)
        
        return system


def create_default_mutation_conditions() -> List[MutationCondition]:
    """创建默认的变异条件"""
    conditions = []
    
    def check_environment_condition(game_state: Dict, player_action: Optional[str], 
                                    system: RuleMutationSystem) -> bool:
        """环境条件：特定时间+特定区域"""
        time_system = game_state.get("time_system", {})
        elapsed_minutes = time_system.get("elapsed_minutes", 0)
        
        if elapsed_minutes < 120:
            return False
        
        players = game_state.get("players", {})
        for player_id, player_data in players.items():
            location = player_data.get("location", "")
            if "地下室" in location or "密室" in location:
                return True
        
        return False
    
    conditions.append(MutationCondition(
        condition_type=MutationTriggerType.ENVIRONMENT,
        description="玩家在特定时间进入危险区域",
        check_function=check_environment_condition,
        priority=1
    ))
    
    def check_behavior_condition(game_state: Dict, player_action: Optional[str], 
                                system: RuleMutationSystem) -> bool:
        """行为条件：连续违反规则"""
        players = game_state.get("players", {})
        rules = game_state.get("rules", [])
        
        if not rules:
            return False
        
        for player_id, player_data in players.items():
            action_history = player_data.get("action_history", [])
            recent_actions = action_history[-10:] if len(action_history) > 10 else action_history
            
            violation_count = 0
            for action in recent_actions:
                for rule in rules:
                    if rule in action:
                        violation_count += 1
                        break
            
            if violation_count >= 3:
                return True
        
        return False
    
    conditions.append(MutationCondition(
        condition_type=MutationTriggerType.BEHAVIOR,
        description="玩家连续违反规则",
        check_function=check_behavior_condition,
        priority=2
    ))
    
    def check_plot_condition(game_state: Dict, player_action: Optional[str], 
                            system: RuleMutationSystem) -> bool:
        """剧情条件：收集到关键线索"""
        key_clues = game_state.get("key_clues", [])
        discovered_clues = len([clue for clue in key_clues if system.has_found_key_clue(clue)])
        
        return discovered_clues >= 2
    
    conditions.append(MutationCondition(
        condition_type=MutationTriggerType.PLOT,
        description="收集到足够的关键线索",
        check_function=check_plot_condition,
        priority=3
    ))
    
    def check_time_condition(game_state: Dict, player_action: Optional[str], 
                            system: RuleMutationSystem) -> bool:
        """时间条件：游戏时间超过阈值"""
        time_system = game_state.get("time_system", {})
        elapsed_minutes = time_system.get("elapsed_minutes", 0)
        
        return elapsed_minutes >= 180
    
    conditions.append(MutationCondition(
        condition_type=MutationTriggerType.TIME,
        description="游戏时间超过3小时",
        check_function=check_time_condition,
        priority=4
    ))
    
    def check_item_condition(game_state: Dict, player_action: Optional[str], 
                            system: RuleMutationSystem) -> bool:
        """物品条件：发现关键物品"""
        players = game_state.get("players", {})
        
        for player_id, player_data in players.items():
            inventory = player_data.get("inventory", [])
            for item in inventory:
                if isinstance(item, dict):
                    item_name = item.get("name", "")
                else:
                    item_name = str(item)
                
                if "日记" in item_name or "笔记" in item_name or "档案" in item_name:
                    return True
        
        return False
    
    conditions.append(MutationCondition(
        condition_type=MutationTriggerType.ITEM,
        description="发现关键物品（日记/笔记/档案）",
        check_function=check_item_condition,
        priority=5
    ))
    
    def check_location_condition(game_state: Dict, player_action: Optional[str], 
                                 system: RuleMutationSystem) -> bool:
        """位置条件：多次访问同一位置"""
        players = game_state.get("players", {})
        
        for player_id, player_data in players.items():
            location = player_data.get("location", "")
            visit_count = system.get_location_visit_count(location, player_id)
            
            if visit_count >= 5:
                return True
        
        return False
    
    conditions.append(MutationCondition(
        condition_type=MutationTriggerType.LOCATION,
        description="多次访问同一位置",
        check_function=check_location_condition,
        priority=6
    ))
    
    return conditions
