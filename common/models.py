"""
数据模型 - 使用 dataclass 替代字典，提供类型安全
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import TypedDict, TypeAlias

# JSON类型定义 - 使用Any避免递归类型检查的复杂性
# 这些类型用于LLM交互和配置数据，实际内容由运行时保证
from typing import Any

JsonPrimitive: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = Any
JsonObject: TypeAlias = dict[str, JsonValue]


class PlayerStatusDict(TypedDict):
    """玩家状态字典类型"""
    sanity: int
    health: int
    location: str


class RuleDict(TypedDict):
    """规则字典类型 - text是必需的，其他可选"""
    text: str
    original_index: int | None
    source: str
    rule_type: str | None  # "fatal"/"harmful"/"double_edged"/None
    related_npc: str | None  # 相关NPC名称
    opposing_npc: str | None  # 对抗NPC名称（矛盾规则时用）


class StateUpdatesDict(TypedDict):
    """状态更新字典类型（用于 LLM/反馈系统对玩家状态做增量修改）"""
    sanity: int | None
    health: int | None
    location: str | None


class GameContextDict(TypedDict):
    """游戏上下文字典类型"""
    scene_name: str
    background: str
    rules: list[RuleDict]
    player_status: PlayerStatusDict
    recent_actions: list[str]


class InteractionRecordDict(TypedDict):
    """互动记录字典类型"""
    type: str
    details: JsonObject
    time: int
    timestamp: str


class LocationRecordDict(TypedDict):
    """位置记录字典类型"""
    location: str
    time: int
    timestamp: str


class ActionRecordDict(TypedDict):
    """行动记录字典类型"""
    action: str
    time: int
    timestamp: str


class SoundInfoDict(TypedDict):
    """声音信息字典类型"""
    distance: float | None
    location: str | None
    type: str | None


class PlayerDataDict(TypedDict):
    """玩家数据字典类型"""
    name: str | None
    location: str | None
    health: int | None
    sanity: int | None
    inventory: list[str] | None
    action_history: list[str] | None


class GameStateDict(TypedDict):
    """游戏状态字典类型（用于NPC行为树）"""
    players: dict[str, PlayerDataDict] | None
    recent_sounds: list[SoundInfoDict] | None
    safe_locations: list[str] | None
    time_system: JsonObject | None


class BehaviorResultDict(TypedDict):
    """行为结果字典类型"""
    action: str | None
    target: str | None
    result: str | None
    player_id: str | None
    attitude: str | None
    behavior: str | None
    actions: list[Any] | None  # 递归类型用Any避免定义问题
    status: str | None


class AttitudeCheckResultDict(TypedDict):
    """态度检查结果字典类型"""
    has_extreme: bool
    extreme_type: str | None
    dimension: str | None
    value: float | None


class AttitudeContradictionDict(TypedDict):
    """态度矛盾检查结果字典类型"""
    has_contradiction: bool
    contradiction_type: str | None
    description: str | None


class LastSeenInfoDict(TypedDict):
    """最后见到信息字典类型"""
    time: int | None
    location: str | None


@dataclass
class ActionRecord:
    """行动记录"""
    action: str
    timestamp: str
    result: str | None = None

    @classmethod
    def create(cls, action: str, result: str | None = None) -> "ActionRecord":
        """创建行动记录"""
        return cls(
            action=action,
            timestamp=datetime.now().isoformat(),
            result=result
        )


@dataclass
class PhysicalStatus:
    """身体状态"""
    health: int
    injury: str = "无"
    fatigue: str = "无"

    def to_dict(self) -> JsonObject:
        """转换为字典"""
        return {
            "health": self.health,
            "injury": self.injury,
            "fatigue": self.fatigue,
        }


@dataclass
class MentalStatus:
    """精神状态"""
    sanity: int
    state: str = "正常"
    emotion: str = "平静"

    def to_dict(self) -> JsonObject:
        """转换为字典"""
        return {
            "sanity": self.sanity,
            "state": self.state,
            "emotion": self.emotion,
        }


@dataclass
class PsychologicalPressure:
    """心理压力"""
    fear_level: int = 0
    anxiety_level: int = 0
    stress_level: int = 0

    def to_dict(self) -> JsonObject:
        """转换为字典"""
        return {
            "fear_level": self.fear_level,
            "anxiety_level": self.anxiety_level,
            "stress_level": self.stress_level,
        }


@dataclass
class ItemDetails:
    """物品详情"""
    item_name: str
    item_type: str
    item_description: str
    observation_hint: str = ""
    is_key_item: bool = False

    def to_dict(self) -> JsonObject:
        """转换为字典"""
        return {
            "item_name": self.item_name,
            "item_type": self.item_type,
            "item_description": self.item_description,
            "observation_hint": self.observation_hint,
            "is_key_item": "是" if self.is_key_item else "否",
        }


@dataclass
class ActionResult:
    """行动结果"""
    is_dead: bool
    scene_description: str
    physical_status: PhysicalStatus
    mental_status: MentalStatus
    psychological_pressure: PsychologicalPressure
    found_items: list[str] = field(default_factory=list)
    discovered_clues: list[str] = field(default_factory=list)
    item_details: ItemDetails | None = None
    action_feedback: str = ""
    new_location: str | None = None
    violated_rule: str | None = None

    def to_dict(self) -> JsonObject:
        """转换为字典（用于LLM响应）"""
        result: JsonObject = {
            "is_dead": "是" if self.is_dead else "否",
            "scene_description": self.scene_description,
            "physical_status": self.physical_status.to_dict(),
            "mental_status": self.mental_status.to_dict(),
            "psychological_pressure": self.psychological_pressure.to_dict(),
            "found_items": self.found_items,
            "action_feedback": self.action_feedback,
        }

        if self.item_details:
            result["item_details"] = self.item_details.to_dict()

        if self.new_location:
            result["new_location"] = self.new_location

        if self.violated_rule:
            result["violated_rule"] = self.violated_rule

        return result


@dataclass
class TimeInfo:
    """时间信息"""
    elapsed_minutes: int
    time_increment: int
    current_time: str
    time_description: str
    action_count: int
    time_multiplier: float = 1.0

    def to_dict(self) -> JsonObject:
        """转换为字典"""
        return {
            "elapsed_minutes": self.elapsed_minutes,
            "time_increment": self.time_increment,
            "current_time": self.current_time,
            "time_description": self.time_description,
            "action_count": self.action_count,
            "time_multiplier": self.time_multiplier,
        }


@dataclass
class EnvironmentChange:
    """环境变化"""
    change_type: str
    object_id: str
    description: str
    details: JsonObject = field(default_factory=dict)

    def to_dict(self) -> JsonObject:
        """转换为字典"""
        return {
            "type": self.change_type,
            "id": self.object_id,
            "description": self.description,
            **self.details,
        }


@dataclass
class PlayerIdentity:
    """玩家身份（多人模式）"""
    identity_name: str
    identity_description: str
    unique_rules: list[JsonObject] = field(default_factory=list)
    exclusive_info: str = ""

    def to_dict(self) -> JsonObject:
        """转换为字典"""
        return {
            "identity_name": self.identity_name,
            "identity_description": self.identity_description,
            "unique_rules": self.unique_rules,
            "exclusive_info": self.exclusive_info,
        }


@dataclass
class RuleInfo:
    """规则信息"""
    text: str
    original_index: int | None = None
    is_true: bool | None = None
    hidden_meaning: str | None = None
    source: str = "system"
    rule_type: str | None = None  # "fatal"/"harmful"/"double_edged"/None
    related_npc: str | None = None  # 相关NPC名称
    opposing_npc: str | None = None  # 对抗NPC名称（矛盾规则时用）

    def to_dict(self) -> JsonObject:
        """转换为字典"""
        result: JsonObject = {
            "text": self.text,
            "source": self.source,
        }

        if self.original_index is not None:
            result["original_index"] = self.original_index

        if self.is_true is not None:
            result["is_true"] = self.is_true

        if self.hidden_meaning:
            result["hidden_meaning"] = self.hidden_meaning

        if self.rule_type:
            result["rule_type"] = self.rule_type

        if self.related_npc:
            result["related_npc"] = self.related_npc

        if self.opposing_npc:
            result["opposing_npc"] = self.opposing_npc

        return result


@dataclass
class GameContext:
    """游戏上下文"""
    scene_name: str
    background: str
    rules: list[RuleInfo]
    player_status: PlayerStatusDict
    recent_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> JsonObject:
        """转换为字典"""
        return {
            "scene_name": self.scene_name,
            "background": self.background,
            "rules": [r.to_dict() for r in self.rules],
            "player_status": self.player_status,
            "recent_actions": self.recent_actions,
        }
