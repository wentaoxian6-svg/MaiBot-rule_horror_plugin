"""
游戏时间管理系统
统一管理游戏内时间流逝，避免使用真实时间导致的"上线即死亡"问题
"""

from typing import TypeAlias

from ..common.constants import (
    TimePhases,
    TimeThresholds,
    TimeDescriptions,
    FatigueLevel,
    FatigueMultipliers,
    GameModes,
    TimePressureLevels,
)
from ..common.models import TimeInfo

# 类型定义
TimeManagerDict: TypeAlias = dict[str, "int | float | str | None"]
TimeValue: TypeAlias = "int | float | str | None"


class GameTimeManager:
    """游戏时间管理器
    
    负责管理游戏内时间的流逝，确保：
    - 时间只在实际游戏操作时流逝
    - 存档时记录游戏内时间
    - 读档时恢复游戏内时间
    - 支持时间加速/减速机制
    """
    
    def __init__(self):
        self.game_time: int = 0
        self.last_action_time: int | None = None
        self.time_phase: str = TimePhases.MIDNIGHT.value
        self.time_description: str = TimeDescriptions.MIDNIGHT
        self.time_multiplier: float = 1.0
        self.action_count: int = 0
        
    def advance_time(
        self, 
        base_increment: int, 
        fatigue_level: str = FatigueLevel.NONE.value, 
        game_mode: str = GameModes.SINGLE.value
    ) -> TimeInfo:
        """推进游戏时间
        
        Args:
            base_increment: 基础时间增量（分钟）
            fatigue_level: 疲劳程度
            game_mode: 游戏模式
        
        Returns:
            时间信息对象
        """
        fatigue_multiplier = self._get_fatigue_multiplier(fatigue_level)
        mode_multiplier = self._get_mode_multiplier(game_mode)
        
        actual_increment = int(base_increment * fatigue_multiplier * mode_multiplier * self.time_multiplier)
        
        self.game_time += actual_increment
        self.action_count += 1
        self.last_action_time = self.game_time
        
        self._update_time_phase()
        
        return TimeInfo(
            elapsed_minutes=self.game_time,
            time_increment=actual_increment,
            current_time=self.time_phase,
            time_description=self.time_description,
            action_count=self.action_count,
            time_multiplier=self.time_multiplier
        )
    
    def _get_fatigue_multiplier(self, fatigue_level: str) -> float:
        """根据疲劳程度获取时间倍率"""
        multiplier_map = {
            FatigueLevel.NONE.value: FatigueMultipliers.NONE,
            FatigueLevel.SLIGHT.value: FatigueMultipliers.SLIGHT,
            FatigueLevel.MODERATE.value: FatigueMultipliers.MODERATE,
            FatigueLevel.SEVERE.value: FatigueMultipliers.SEVERE,
            FatigueLevel.EXTREME.value: FatigueMultipliers.EXTREME,
        }
        return multiplier_map.get(fatigue_level, FatigueMultipliers.NONE)
    
    def _get_mode_multiplier(self, game_mode: str) -> float:
        """根据游戏模式获取时间倍率"""
        return 0.4 if game_mode == GameModes.MULTI.value else 1.0
    
    def _update_time_phase(self) -> None:
        """更新时间阶段和描述"""
        if self.game_time < TimeThresholds.MIDNIGHT:
            self.time_phase = TimePhases.MIDNIGHT.value
            self.time_description = TimeDescriptions.MIDNIGHT
        elif self.game_time < TimeThresholds.DAWN:
            self.time_phase = TimePhases.DAWN.value
            self.time_description = TimeDescriptions.DAWN
        elif self.game_time < TimeThresholds.EARLY_MORNING:
            self.time_phase = TimePhases.EARLY_MORNING.value
            self.time_description = TimeDescriptions.EARLY_MORNING
        elif self.game_time < TimeThresholds.MORNING:
            self.time_phase = TimePhases.MORNING.value
            self.time_description = TimeDescriptions.MORNING
        else:
            self.time_phase = TimePhases.DAYTIME.value
            self.time_description = TimeDescriptions.DAYTIME
    
    def set_time_multiplier(self, multiplier: float):
        """设置时间倍率（用于特殊事件加速时间）"""
        self.time_multiplier = multiplier
    
    def reset_time_multiplier(self):
        """重置时间倍率"""
        self.time_multiplier = 1.0
    
    def get_time_info(self) -> TimeInfo:
        """获取当前时间信息"""
        return TimeInfo(
            elapsed_minutes=self.game_time,
            time_increment=0,
            current_time=self.time_phase,
            time_description=self.time_description,
            action_count=self.action_count,
            time_multiplier=self.time_multiplier
        )
    
    def to_dict(self) -> TimeManagerDict:
        """序列化为字典（用于存档）"""
        return {
            "game_time": self.game_time,
            "last_action_time": self.last_action_time,
            "time_phase": self.time_phase,
            "time_description": self.time_description,
            "time_multiplier": self.time_multiplier,
            "action_count": self.action_count
        }

    @classmethod
    def from_dict(cls, data: TimeManagerDict) -> 'GameTimeManager':
        """从字典反序列化（用于读档）"""
        manager = cls()
        manager.game_time = data.get("game_time", 0)
        manager.last_action_time = data.get("last_action_time")
        manager.time_phase = data.get("time_phase", TimePhases.MIDNIGHT.value)
        manager.time_description = data.get("time_description", TimeDescriptions.MIDNIGHT)
        manager.time_multiplier = data.get("time_multiplier", 1.0)
        manager.action_count = data.get("action_count", 0)
        return manager
    
    def get_time_pressure(self) -> str:
        """获取时间压力等级描述"""
        if self.game_time < TimeThresholds.MIDNIGHT:
            return TimePressureLevels.LOW.value
        elif self.game_time < TimeThresholds.DAWN:
            return TimePressureLevels.MEDIUM.value
        elif self.game_time < TimeThresholds.EARLY_MORNING:
            return TimePressureLevels.HIGH.value
        else:
            return TimePressureLevels.CRITICAL.value
    
    def is_critical_time(self) -> bool:
        """判断是否处于关键时刻（时间压力极高）"""
        return self.game_time >= TimeThresholds.EARLY_MORNING
    
    def get_remaining_time(self, max_time: int = TimeThresholds.MORNING) -> int:
        """获取剩余时间（分钟）"""
        return max(0, max_time - self.game_time)

    def get(self, key: str, default: TimeValue = None) -> TimeValue:
        """模拟字典的get方法，用于兼容代码

        Args:
            key: 键名
            default: 默认值

        Returns:
            对应的值或默认值
        """
        time_info = self.get_time_info()
        return time_info.to_dict().get(key, default)
