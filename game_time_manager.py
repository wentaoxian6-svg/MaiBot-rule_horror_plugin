"""
游戏时间管理系统
统一管理游戏内时间流逝，避免使用真实时间导致的"上线即死亡"问题
"""

from typing import Any


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
        self.time_phase: str = "深夜"
        self.time_description: str = "午夜时分，周围一片死寂"
        self.time_multiplier: float = 1.0
        self.action_count: int = 0
        
    def advance_time(self, base_increment: int, fatigue_level: str = "无", game_mode: str = "单人") -> dict[str, Any]:
        """推进游戏时间
        
        Args:
            base_increment: 基础时间增量（分钟）
            fatigue_level: 疲劳程度（无/轻微/中度/严重/极度）
            game_mode: 游戏模式（单人/多人）
        
        Returns:
            包含时间更新信息的字典
        """
        fatigue_multiplier = self._get_fatigue_multiplier(fatigue_level)
        mode_multiplier = self._get_mode_multiplier(game_mode)
        
        actual_increment = int(base_increment * fatigue_multiplier * mode_multiplier * self.time_multiplier)
        
        self.game_time += actual_increment
        self.action_count += 1
        self.last_action_time = self.game_time
        
        self._update_time_phase()
        
        return {
            "elapsed_minutes": self.game_time,
            "time_increment": actual_increment,
            "current_time": self.time_phase,
            "time_description": self.time_description,
            "action_count": self.action_count
        }
    
    def _get_fatigue_multiplier(self, fatigue_level: str) -> float:
        """根据疲劳程度获取时间倍率"""
        fatigue_multipliers = {
            "无": 1.0,
            "轻微": 1.2,
            "中度": 1.5,
            "严重": 2.0,
            "极度": 3.0
        }
        return fatigue_multipliers.get(fatigue_level, 1.0)
    
    def _get_mode_multiplier(self, game_mode: str) -> float:
        """根据游戏模式获取时间倍率"""
        return 0.4 if game_mode == "多人" else 1.0
    
    def _update_time_phase(self):
        """更新时间阶段和描述"""
        if self.game_time < 60:
            self.time_phase = "深夜"
            self.time_description = "午夜时分，周围一片死寂"
        elif self.game_time < 180:
            self.time_phase = "凌晨"
            self.time_description = "黎明前的黑暗，空气中弥漫着不安"
        elif self.game_time < 300:
            self.time_phase = "黎明"
            self.time_description = "东方泛起鱼肚白，但黑暗仍未完全消散"
        elif self.game_time < 420:
            self.time_phase = "清晨"
            self.time_description = "晨光熹微，雾气缭绕"
        else:
            self.time_phase = "白昼"
            self.time_description = "阳光透过窗户，但依然阴冷"
    
    def set_time_multiplier(self, multiplier: float):
        """设置时间倍率（用于特殊事件加速时间）"""
        self.time_multiplier = multiplier
    
    def reset_time_multiplier(self):
        """重置时间倍率"""
        self.time_multiplier = 1.0
    
    def get_time_info(self) -> dict[str, Any]:
        """获取当前时间信息"""
        return {
            "elapsed_minutes": self.game_time,
            "current_time": self.time_phase,
            "time_description": self.time_description,
            "action_count": self.action_count,
            "time_multiplier": self.time_multiplier
        }
    
    def to_dict(self) -> dict[str, Any]:
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
    def from_dict(cls, data: dict[str, Any]) -> 'GameTimeManager':
        """从字典反序列化（用于读档）"""
        manager = cls()
        manager.game_time = data.get("game_time", 0)
        manager.last_action_time = data.get("last_action_time")
        manager.time_phase = data.get("time_phase", "深夜")
        manager.time_description = data.get("time_description", "午夜时分，周围一片死寂")
        manager.time_multiplier = data.get("time_multiplier", 1.0)
        manager.action_count = data.get("action_count", 0)
        return manager
    
    def get_time_pressure(self) -> str:
        """获取时间压力等级描述"""
        if self.game_time < 60:
            return "低"
        elif self.game_time < 180:
            return "中"
        elif self.game_time < 300:
            return "高"
        else:
            return "极高"
    
    def is_critical_time(self) -> bool:
        """判断是否处于关键时刻（时间压力极高）"""
        return self.game_time >= 300
    
    def get_remaining_time(self, max_time: int = 420) -> int:
        """获取剩余时间（分钟）"""
        return max(0, max_time - self.game_time)

    def get(self, key: str, default: Any = None) -> Any:
        """模拟字典的get方法，用于兼容代码

        Args:
            key: 键名
            default: 默认值

        Returns:
            对应的值或默认值
        """
        time_info = self.get_time_info()
        return time_info.get(key, default)
