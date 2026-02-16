"""物品管理服务 - 处理物品使用和休息逻辑"""
from __future__ import annotations

import logging
import random
from typing import TypeAlias

from ..game.models import Player, GameSession

logger = logging.getLogger(__name__)

# 类型定义
ItemData: TypeAlias = dict[str, "str | bool | int | None"]


class ItemManager:
    """物品管理器 - 处理物品的使用、休息和效果"""

    # 水类物品关键词
    WATER_KEYWORDS: list[str] = [
        "喝水", "喝", "饮用", "水", "矿泉水", "瓶装水", "水杯", "饮水",
        "喝水水", "喝口水", "喝瓶水", "喝饮料", "喝果汁", "喝牛奶",
        "喝可乐", "喝汽水"
    ]

    # 食物类物品关键词
    FOOD_KEYWORDS: list[str] = [
        "吃", "食用", "食物", "面包", "饼干", "巧克力", "能量棒", "罐头",
        "水果", "苹果", "香蕉", "糖果", "零食", "饭", "吃点东西", "吃点",
        "吃面包", "吃饼干", "吃巧克力", "吃能量棒", "吃罐头", "吃水果",
        "吃苹果", "吃香蕉", "吃糖果", "吃零食", "吃饭", "吃压缩饼干",
        "吃午餐肉", "吃蛋糕", "吃点心", "吃坚果", "吃香肠", "吃火腿"
    ]

    # 休息关键词
    REST_KEYWORDS: list[str] = [
        "休息", "歇息", "休息一下", "歇一下", "休息会儿", "歇会儿",
        "休息片刻", "歇息片刻", "休息一会", "歇息一会", "坐下休息",
        "坐下歇息", "躺下休息", "躺下歇息", "休息恢复", "歇息恢复",
        "休息恢复体力", "歇息恢复体力"
    ]

    # 水类物品名称关键词
    WATER_ITEM_KEYWORDS: list[str] = [
        "水", "矿泉水", "瓶装水", "水杯", "饮料", "果汁", "牛奶", "可乐", "汽水"
    ]

    # 食物类物品名称关键词
    FOOD_ITEM_KEYWORDS: list[str] = [
        "食物", "面包", "饼干", "巧克力", "能量棒", "罐头", "水果", "苹果",
        "香蕉", "糖果", "零食", "饭", "压缩饼干", "午餐肉", "蛋糕", "点心",
        "坚果", "香肠", "火腿"
    ]

    # 疲劳等级（用于 _get_fatigue_level 方法）
    FATIGUE_LEVELS: list[str] = ["无", "轻微", "中度", "严重", "极度"]

    def check_and_use_item(
        self,
        action: str,
        player: Player,
        session: GameSession,  # 保留参数以兼容接口，当前未使用
    ) -> tuple[bool, str | None]:
        """
        检查并使用物品
        
        Args:
            action: 玩家行动描述
            player: 玩家对象
            session: 游戏会话
        
        Returns:
            (是否使用了物品, 效果描述文本)
        """
        if not player.inventory:
            return False, None
        
        action_lower = action.lower()
        
        # 检查是否是使用水的行动
        used_water = any(keyword in action_lower for keyword in self.WATER_KEYWORDS)
        
        # 检查是否是使用食物的行动
        used_food = any(keyword in action_lower for keyword in self.FOOD_KEYWORDS)
        
        if not used_water and not used_food:
            return False, None
        
        # 查找对应的物品
        item_index = -1
        item_name = ""

        if used_water:
            for i, item in enumerate(player.inventory):
                if isinstance(item, dict):
                    item_name = item.get("name", "")
                else:
                    item_name = str(item)

                if any(keyword in item_name.lower() for keyword in self.WATER_ITEM_KEYWORDS):
                    item_index = i
                    break

        elif used_food:
            for i, item in enumerate(player.inventory):
                if isinstance(item, dict):
                    item_name = item.get("name", "")
                else:
                    item_name = str(item)

                if any(keyword in item_name.lower() for keyword in self.FOOD_ITEM_KEYWORDS):
                    item_index = i
                    break
        
        if item_index == -1:
            return False, None
        
        # 应用物品效果
        effect_text = ""
        
        if used_water:
            # 水类物品：降低压力、焦虑和恐惧
            stress_reduction = random.randint(3, 5)
            anxiety_reduction = random.randint(3, 5)
            fear_reduction = random.randint(2, 4)

            current_stress = player.stress_level
            current_anxiety = player.anxiety_level
            current_fear = player.fear_level

            player.stress_level = max(0, current_stress - stress_reduction)
            player.anxiety_level = max(0, current_anxiety - anxiety_reduction)
            player.fear_level = max(0, current_fear - fear_reduction)

            effect_text = (
                f"你喝了{item_name}，感到一阵清凉。"
                f"压力等级降低了{stress_reduction}点，"
                f"焦虑等级降低了{anxiety_reduction}点，"
                f"恐惧等级降低了{fear_reduction}点。"
            )
        
        elif used_food:
            # 食物类物品：恢复体力和少量心理状态
            health_recovery = random.randint(3, 5)
            stress_reduction = random.randint(1, 3)
            anxiety_reduction = random.randint(1, 3)

            current_health = player.health
            new_health = min(100, current_health + health_recovery)

            actual_recovery = new_health - current_health
            player.health = new_health

            # 食物也能带来心理安慰
            player.stress_level = max(0, player.stress_level - stress_reduction)
            player.anxiety_level = max(0, player.anxiety_level - anxiety_reduction)

            effect_text = (
                f"你吃了{item_name}，感到体力恢复了一些。"
                f"体力值回复了{actual_recovery}点，"
                f"压力等级降低了{stress_reduction}点，"
                f"焦虑等级降低了{anxiety_reduction}点。"
            )
        
        # 从背包中移除物品
        player.inventory.pop(item_index)
        
        logger.info(f"玩家 {player.name} 使用了物品: {item_name}")
        
        return True, f"**使用物品**\n\n{effect_text}\n\n{item_name}已从物品栏中移除。"
    
    def get_item_by_name(self, player: Player, item_name: str) -> ItemData | None:
        """根据名称获取物品"""
        for item in player.inventory:
            if isinstance(item, dict):
                if item.get("name", "") == item_name:
                    return item
            elif str(item) == item_name:
                return {"name": item_name}
        return None
    
    def remove_item(self, player: Player, item_name: str) -> bool:
        """从背包中移除物品"""
        for i, item in enumerate(player.inventory):
            if isinstance(item, dict):
                if item.get("name", "") == item_name:
                    player.inventory.pop(i)
                    return True
            elif str(item) == item_name:
                player.inventory.pop(i)
                return True
        return False
    
    def has_item(self, player: Player, item_name: str) -> bool:
        """检查玩家是否拥有某个物品"""
        for item in player.inventory:
            if isinstance(item, dict):
                if item.get("name", "") == item_name:
                    return True
            elif str(item) == item_name:
                return True
        return False
    
    def get_key_items(self, player: Player) -> list[ItemData]:
        """获取玩家的所有关键物品"""
        key_items = []
        for item in player.inventory:
            if isinstance(item, dict) and item.get("is_key_item", False):
                key_items.append(item)
        return key_items
    
    def count_items(self, player: Player) -> int:
        """统计玩家的物品数量"""
        return len(player.inventory)
    
    def check_and_rest(
        self,
        action: str,
        player: Player,
        session: GameSession,  # 保留参数以兼容接口，当前未使用
    ) -> tuple[bool, str | None, int]:
        """
        检查并执行休息（支持自定义休息时间）
        
        Args:
            action: 玩家行动描述
            player: 玩家对象
            session: 游戏会话
        
        Returns:
            (是否休息了, 效果描述文本, 花费的时间（分钟）)
        """
        import re
        
        action_lower = action.lower()
        
        # 检查是否是休息行动
        is_resting = any(keyword in action_lower for keyword in self.REST_KEYWORDS)
        
        if not is_resting:
            return False, None, 0
        
        # 尝试从行动中提取休息时间（支持多种格式）
        # 例如："休息30分钟"、"休息 30 分钟"、"休息30min"、"休息半小时"等
        time_cost = 15  # 默认15分钟
        custom_time = False
        
        # 匹配数字+分钟/min
        time_patterns = [
            r'休息\s*(\d+)\s*分钟',
            r'休息\s*(\d+)\s*min',
            r'歇息\s*(\d+)\s*分钟',
            r'歇息\s*(\d+)\s*min',
            r'休息\s*(\d+)',
            r'歇息\s*(\d+)',
        ]
        
        for pattern in time_patterns:
            match = re.search(pattern, action_lower)
            if match:
                try:
                    time_cost = int(match.group(1))
                    custom_time = True
                    # 限制休息时间在5-120分钟之间
                    if time_cost < 5:
                        time_cost = 5
                    elif time_cost > 120:
                        time_cost = 120
                    break
                except ValueError:
                    pass
        
        # 匹配特殊时间描述
        if not custom_time:
            if '半小时' in action_lower or '半个小时' in action_lower:
                time_cost = 30
                custom_time = True
            elif '一小时' in action_lower or '1小时' in action_lower or '一个小时' in action_lower:
                time_cost = 60
                custom_time = True
            elif '两小时' in action_lower or '2小时' in action_lower or '两个小时' in action_lower:
                time_cost = 120
                custom_time = True
        
        # 获取当前疲劳等级
        current_fatigue = self._get_fatigue_level(player.health)

        # 根据休息时间计算体力恢复量
        # 基础恢复：10-20点（15分钟）
        # 每增加15分钟，额外恢复10-20点
        base_recovery = random.randint(10, 20)
        extra_recovery = (time_cost // 15 - 1) * random.randint(10, 20) if time_cost > 15 else 0
        health_recovery = base_recovery + extra_recovery

        new_health = min(100, player.health + health_recovery)
        actual_health_recovery = new_health - player.health
        player.health = new_health

        # 休息也能恢复心理状态（根据休息时间）
        # 基础恢复：3-5点（15分钟），每增加15分钟额外恢复3-5点
        base_mental_recovery = random.randint(3, 5)
        extra_mental_recovery = (time_cost // 15 - 1) * random.randint(3, 5) if time_cost > 15 else 0
        mental_recovery = base_mental_recovery + extra_mental_recovery

        player.fear_level = max(0, player.fear_level - mental_recovery)
        player.anxiety_level = max(0, player.anxiety_level - mental_recovery)
        player.stress_level = max(0, player.stress_level - mental_recovery)

        # 降低疲劳等级
        new_fatigue = self._get_fatigue_level(player.health)
        fatigue_reduced = (current_fatigue != new_fatigue)

        # 构建效果文本
        if custom_time:
            rest_text = f"你休息了{time_cost}分钟，感到体力恢复了一些。体力值回复了{actual_health_recovery}点，心理状态也平复了一些（恐惧/焦虑/压力各降低{mental_recovery}点）。"
        else:
            rest_text = f"你休息了一会儿，感到体力恢复了一些。体力值回复了{actual_health_recovery}点，心理状态也平复了一些（恐惧/焦虑/压力各降低{mental_recovery}点）。"

        if fatigue_reduced:
            rest_text += f" 疲劳等级从{current_fatigue}降低到了{new_fatigue}。"

        logger.info(f"玩家 {player.name} 休息了 {time_cost} 分钟，恢复体力 {actual_health_recovery} 点，心理状态各降低 {mental_recovery} 点")
        
        return True, f"**休息**\n\n{rest_text}\n\n休息花费了{time_cost}分钟。", time_cost
    
    def _get_fatigue_level(self, health: int) -> str:
        """根据体力值计算疲劳等级"""
        # 使用 FATIGUE_LEVELS 类变量
        if health >= 76:
            return self.FATIGUE_LEVELS[0]  # 无
        elif health >= 51:
            return self.FATIGUE_LEVELS[1]  # 轻微
        elif health >= 26:
            return self.FATIGUE_LEVELS[2]  # 中度
        elif health >= 1:
            return self.FATIGUE_LEVELS[3]  # 严重
        else:
            return self.FATIGUE_LEVELS[4]  # 极度
