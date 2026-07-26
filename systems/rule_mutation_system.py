"""
规则变异的条件触发系统
改用条件触发制，而非随机概率，增强叙事连贯性
"""

from __future__ import annotations

import logging
from typing import Callable, TypeAlias
from enum import Enum
from dataclasses import dataclass

from ..common.constants import TimeThresholds

logger = logging.getLogger(__name__)

# 类型定义
GameState: TypeAlias = dict[str, object]
MutationDetails: TypeAlias = dict[str, object]
SystemDict: TypeAlias = dict[str, "list | set | dict | int | None"]


class MutationTriggerType(Enum):
    """规则变异触发类型"""
    ENVIRONMENT = "环境条件"
    BEHAVIOR = "行为条件"
    PLOT = "剧情条件"
    TIME = "时间条件"
    ITEM = "物品条件"
    LOCATION = "位置条件"


# 定义检查函数的类型
CheckFunction = Callable[[GameState, str | None, "RuleMutationSystem"], bool]


@dataclass
class MutationCondition:
    """变异条件"""
    condition_type: MutationTriggerType
    description: str
    check_function: CheckFunction
    priority: int = 0


@dataclass
class MutationEvent:
    """变异事件"""
    mutation_id: str
    trigger_type: MutationTriggerType
    trigger_reason: str
    triggered_by: list[str]
    triggered_at: int
    mutation_details: MutationDetails


class RuleMutationSystem:
    """规则变异系统
    
    负责管理规则变异的条件触发，确保：
    - 规则变异基于条件触发，而非随机概率
    - 变异有叙事铺垫，不突兀
    - 支持多种触发条件（环境、行为、剧情、时间、物品、位置）
    - 记录变异历史，便于追踪
    """
    
    def __init__(self):
        self.mutation_conditions: list[MutationCondition] = []
        self.mutation_history: list[MutationEvent] = []
        self.max_mutation_history: int = 100  # 最大变异历史记录数
        self.triggered_conditions: set[str] = set()
        self.violation_counts: dict[str, int] = {}
        self.location_visit_counts: dict[str, int] = {}
        self.item_discovery_counts: dict[str, int] = {}
        self.key_clues_found: set[str] = set()
        self.mutation_cooldown: int = 30
        self.last_mutation_time: int = -999
    
    def add_condition(self, condition: MutationCondition):
        """添加变异条件"""
        self.mutation_conditions.append(condition)
    
    def check_conditions(self, game_state: GameState, player_action: str | None = None,
                         game_time: int = 0) -> list[MutationCondition]:
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
                logger.error(f"[规则变异系统] 检查条件时出错: {e}")
        
        return satisfied_conditions
    
    def trigger_mutation(self, condition: MutationCondition, game_state: GameState,
                        game_time: int, triggered_by: list[str]) -> MutationEvent:
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

        # 限制历史记录数量，避免内存无限增长
        if len(self.mutation_history) > self.max_mutation_history:
            self.mutation_history = self.mutation_history[-self.max_mutation_history:]

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
    
    def get_mutation_history(self) -> list[MutationEvent]:
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
    
    def to_dict(self) -> SystemDict:
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
    def from_dict(cls, data: SystemDict) -> "RuleMutationSystem":
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


def create_default_mutation_conditions() -> list[MutationCondition]:
    """创建默认的变异条件"""
    conditions: list[MutationCondition] = []
    
    def check_behavior_condition(game_state: GameState, player_action: str | None,
                                system: RuleMutationSystem) -> bool:
        """行为条件：连续违反规则
        
        说明：当玩家在短时间内多次违反规则时触发。
        这代表玩家没有理解或故意挑战规则，
        "场景意识"感知到这种挑战，可能通过规则变异来回应。
        """
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
        priority=1
    ))
    
    def check_special_location_condition(game_state: GameState, player_action: str | None,
                                         system: RuleMutationSystem) -> bool:
        """位置条件：多次访问特殊位置
        
        说明：当玩家反复访问特殊/关键位置时触发。
        特殊位置包括：
        - 场景结构中的 special_areas
        - 发现过关键物品的位置
        - NPC提到过的"禁区"
        
        这种重复访问暗示玩家的执念或异常行为，
        可能引起"场景意识"的注意，通过规则变异来回应。
        """
        players = game_state.get("players", {})
        scene_structure = game_state.get("scene_structure", {})
        special_areas = scene_structure.get("special_areas", []) if isinstance(scene_structure, dict) else []
        
        for player_id, player_data in players.items():
            location = player_data.get("location", "")
            if not location:
                continue
            
            # 检查是否是特殊位置
            is_special = False
            
            # 1. 检查是否在 special_areas 中
            if isinstance(special_areas, list):
                for area in special_areas:
                    if isinstance(area, str) and area in location:
                        is_special = True
                        break
            
            # 2. 检查是否在此位置发现过关键物品
            if not is_special:
                key_items_found = game_state.get("key_items_found", {})
                if isinstance(key_items_found, dict):
                    for item_info in key_items_found.values():
                        if isinstance(item_info, dict) and item_info.get("location") == location:
                            is_special = True
                            break
            
            # 3. 检查访问次数
            if is_special:
                visit_count = system.get_location_visit_count(location, player_id)
                if visit_count >= 3:  # 特殊位置只需3次访问即可触发
                    return True
        
        return False
    
    conditions.append(MutationCondition(
        condition_type=MutationTriggerType.LOCATION,
        description="多次访问特殊位置",
        check_function=check_special_location_condition,
        priority=2
    ))

    def check_midnight_time_condition(game_state: GameState, player_action: str | None,
                                       system: RuleMutationSystem) -> bool:
        """时间条件：午夜到来触发规则变异

        说明：当游戏内累计时间首次达到 TimeThresholds.MIDNIGHT（60 分钟）时触发。
        午夜是规则怪谈中“规则开始变得不稳定”的关键节点，
        “场景意识”在此时段让某些规则发生变异，呼应玩家对“夜深了”的紧张感。

        注意：本条件由 check_conditions 的 triggered_conditions 集合保证只触发一次，
        不会在每个 tick 都重复触发；mutation_cooldown 也提供额外保护。
        """
        time_system = game_state.get("time_system", {})
        if not isinstance(time_system, dict):
            return False
        elapsed_minutes = time_system.get("elapsed_minutes", 0)
        # bool 是 int 的子类，需要显式排除，避免 True/False 被当作 1/0
        if isinstance(elapsed_minutes, bool) or not isinstance(elapsed_minutes, (int, float)):
            return False
        return int(elapsed_minutes) >= TimeThresholds.MIDNIGHT

    conditions.append(MutationCondition(
        condition_type=MutationTriggerType.TIME,
        description="午夜到来时，某些规则开始变异",
        check_function=check_midnight_time_condition,
        priority=3
    ))

    return conditions
