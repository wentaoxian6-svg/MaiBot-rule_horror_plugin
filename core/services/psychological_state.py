"""心理状态服务 - 计算恐惧/焦虑/压力/疲劳等心理状态变化与分段叙事。

本模块从 ``action_processor.py`` 抽离，职责单一：
- ``PSYCHOLOGICAL_NARRATIVE`` 心理状态分段叙事常量
- ``PsychologicalStateService.build_psychological_narrative`` 根据 Player 的心理状态阈值生成分段叙事
- ``PsychologicalStateService.calculate_mental_state_change`` 根据行动类型计算恐惧/焦虑/压力变化
- ``PsychologicalStateService.calculate_fatigue_increase`` 根据行动类型计算疲劳增加值

调用方（``ActionProcessor``）通过组合持有 ``PsychologicalStateService`` 实例，
原 ``_build_psychological_narrative`` / ``_calculate_mental_state_change`` /
``_calculate_fatigue_increase`` 方法保留为薄壳委托，避免破坏既有调用点。
"""
from __future__ import annotations

import random

from ...common.constants import (
    AnxietyThresholds,
    FatigueThresholds,
    FearThresholds,
    SanityThresholds,
    StressThresholds,
)
from ..game.models import Player

# 心理状态分段叙事模板（高/中/低三档，低档留空表示无特殊叙事）
# 与 FearThresholds/AnxietyThresholds/StressThresholds/FatigueThresholds 对齐
# sanity 分档与 SanityThresholds 对齐：low(崩坏幻觉) / medium(不安) / high(敏锐感知)
PSYCHOLOGICAL_NARRATIVE: dict[str, dict[str, str]] = {
    "fear": {
        "high": "你的手在抖，心跳快得发疼，每个影子都像在逼近",
        "medium": "你感到一阵阵不安，呼吸变得急促",
        "low": "",
    },
    "anxiety": {
        "high": "焦虑感像石头压在胸口，思绪难以集中",
        "medium": "你感到莫名的烦躁，总觉得自己漏掉了什么",
        "low": "",
    },
    "stress": {
        "high": "压力让你几乎崩溃，每一秒都像在忍受酷刑",
        "medium": "紧张感持续累积，你开始对细节过度敏感",
        "low": "",
    },
    "fatigue": {
        "high": "疲惫席卷全身，眼皮沉重得像灌了铅",
        "medium": "你感到疲倦，反应开始变慢",
        "low": "",
    },
    "sanity": {
        "low": "你看到墙角的阴影在蠕动，耳边有不存在的低语。",
        "medium": "你总觉得有什么在看着你，脊背发凉。",
        "high": "你的感官异常敏锐，能捕捉到微弱的细节。",
    },
}


class PsychologicalStateService:
    """心理状态计算服务。

    封装恐惧/焦虑/压力/疲劳的「分段叙事」与「行动驱动变化」逻辑。
    服务本身无状态，所有方法均可直接接收参数计算；通过组合方式注入到
    ``ActionProcessor`` 中，便于后续单元测试与替换实现。
    """

    def build_psychological_narrative(self, player: Player, sanity: int | None = None) -> str:
        """根据玩家心理状态阈值构建分段叙事片段。

        基于 fear_level/anxiety_level/stress_level/fatigue 的阈值分段，
        返回高/中状态下的叙事描述；低状态（< MEDIUM）不追加叙事。
        若传入 sanity，则按 SanityThresholds 追加理智叙事：
        - sanity <= LOW：幻觉表征叙事（low 档）
        - LOW < sanity < MEDIUM：不安叙事（medium 档）
        - sanity >= HIGH：敏锐感知叙事（high 档）
        - MEDIUM <= sanity < HIGH：无叙事

        分段规则（与 common.constants 中各 Thresholds 对齐）：
        - value >= HIGH：高状态叙事
        - MEDIUM <= value < HIGH：中状态叙事
        - value < MEDIUM：低状态，无叙事（返回空片段）

        Args:
            player: 玩家对象（含 fear/anxiety/stress/fatigue 等心理状态）
            sanity: 玩家理智值；None 时不追加 sanity 叙事（向后兼容）

        Returns:
            拼接后的叙事字符串；若所有状态均处于低档则返回空字符串
        """
        segments: list[str] = []

        # 恐惧叙事
        if player.fear_level >= FearThresholds.HIGH:
            segments.append(PSYCHOLOGICAL_NARRATIVE["fear"]["high"])
        elif player.fear_level >= FearThresholds.MEDIUM:
            segments.append(PSYCHOLOGICAL_NARRATIVE["fear"]["medium"])

        # 焦虑叙事
        if player.anxiety_level >= AnxietyThresholds.HIGH:
            segments.append(PSYCHOLOGICAL_NARRATIVE["anxiety"]["high"])
        elif player.anxiety_level >= AnxietyThresholds.MEDIUM:
            segments.append(PSYCHOLOGICAL_NARRATIVE["anxiety"]["medium"])

        # 压力叙事
        if player.stress_level >= StressThresholds.HIGH:
            segments.append(PSYCHOLOGICAL_NARRATIVE["stress"]["high"])
        elif player.stress_level >= StressThresholds.MEDIUM:
            segments.append(PSYCHOLOGICAL_NARRATIVE["stress"]["medium"])

        # 疲劳叙事
        if player.fatigue >= FatigueThresholds.HIGH:
            segments.append(PSYCHOLOGICAL_NARRATIVE["fatigue"]["high"])
        elif player.fatigue >= FatigueThresholds.MEDIUM:
            segments.append(PSYCHOLOGICAL_NARRATIVE["fatigue"]["medium"])

        # 理智叙事：按 SanityThresholds 分档追加幻觉/不安/敏锐感知描述
        if sanity is not None:
            if sanity <= SanityThresholds.LOW:
                segments.append(PSYCHOLOGICAL_NARRATIVE["sanity"]["low"])
            elif sanity < SanityThresholds.MEDIUM:
                segments.append(PSYCHOLOGICAL_NARRATIVE["sanity"]["medium"])
            elif sanity >= SanityThresholds.HIGH:
                segments.append(PSYCHOLOGICAL_NARRATIVE["sanity"]["high"])

        return " ".join(seg for seg in segments if seg)

    def calculate_fatigue_increase(self, action: str) -> int:
        """根据行动类型计算疲劳增加值

        基础值：每次行动+1
        额外增加：
        - 奔跑/追逐/逃跑：+3
        - 战斗/攻击/搏斗：+4
        - 攀爬/跳跃/游泳：+3
        - 搬运/举重/推拉：+2
        - 搜索/调查/探索：+1
        - 休息/睡觉/静坐：-5（最低到0）
        """
        if not action:
            return 1

        action_lower = action.lower()
        base_increase = 1  # 基础增加
        extra_increase = 0

        # 定义行动类型关键词
        high_intensity = ["奔跑", "追逐", "逃跑", "冲刺", "狂奔", "猛跑"]
        combat = ["战斗", "攻击", "搏斗", "打斗", "打架", "挥拳", "踢", "砍", "刺", "射击"]
        athletic = ["攀爬", "跳跃", "游泳", "翻墙", "爬", "跳", "游"]
        strength = ["搬运", "举重", "推拉", "推", "拉", "抬", "扛", "搬"]
        rest = ["休息", "睡觉", "静坐", "坐", "躺", "睡", "闭目", "养神"]

        # 检查行动类型：多类别可叠加（如"跑着搬东西"同时计算奔跑+搬运）
        # 每个类别至多计入一次，避免同类别关键词重复累加
        if any(keyword in action_lower for keyword in high_intensity):
            extra_increase += 3

        if any(keyword in action_lower for keyword in combat):
            extra_increase += 4

        if any(keyword in action_lower for keyword in athletic):
            extra_increase += 3

        if any(keyword in action_lower for keyword in strength):
            extra_increase += 2

        if any(keyword in action_lower for keyword in rest):
            extra_increase += -5  # 休息减少疲劳

        total = base_increase + extra_increase
        return total  # 允许负值，这样休息可以减少疲劳

    def calculate_mental_state_change(self, action: str, player: Player) -> dict[str, int]:
        """根据行动类型计算心理状态变化

        不同的行动会对恐惧、焦虑、压力产生不同影响：
        - 放松/安全行动：减少各项状态
        - 紧张/危险行动：增加各项状态
        - 探索/发现行动：可能增加焦虑（不确定性）

        Returns:
            字典，包含 fear_change, anxiety_change, stress_change
        """
        action_lower = (action or "").lower()

        # 默认无变化
        changes = {
            "fear_change": 0,
            "anxiety_change": 0,
            "stress_change": 0
        }

        # ===== 放松类行动：减少恐惧/焦虑/压力 =====
        relaxing_actions = ["休息", "睡觉", "静坐", "深呼吸", "冥想", "放松", "养神", "闭目"]
        for keyword in relaxing_actions:
            if keyword in action_lower:
                # 休息时大幅恢复：确定性基础值 4 + 小幅扰动 ±1
                changes["fear_change"] = -(4 + random.randint(-1, 1))
                changes["anxiety_change"] = -(4 + random.randint(-1, 1))
                changes["stress_change"] = -(4 + random.randint(-1, 1))
                return changes

        # 轻度放松行动
        calming_actions = ["散步", "观察", "欣赏", "听", "看风景", "坐", "躺"]
        for keyword in calming_actions:
            if keyword in action_lower:
                # 轻度放松：基础值 2 + 小幅扰动 ±1
                changes["fear_change"] = -(2 + random.randint(-1, 1))
                changes["anxiety_change"] = -(2 + random.randint(-1, 1))
                changes["stress_change"] = -(1 + random.randint(-1, 1))
                return changes

        # ===== 逃跑/躲避类行动：增加恐惧 =====
        escape_actions = ["跑", "逃", "躲", "藏", "避开", "闪躲"]
        for keyword in escape_actions:
            if keyword in action_lower:
                # 逃跑：恐惧基础值 4 + 小幅扰动 ±1
                changes["fear_change"] = 4 + random.randint(-1, 1)
                changes["anxiety_change"] = 2 + random.randint(-1, 1)
                changes["stress_change"] = 3 + random.randint(-1, 1)
                return changes

        # ===== 战斗/对抗类行动：减少恐惧（主动面对），但增加压力 =====
        combat_actions = ["战斗", "攻击", "搏斗", "打斗", "挥拳", "踢", "砍", "刺", "射击", "打"]
        for keyword in combat_actions:
            if keyword in action_lower:
                # 主动对抗：恐惧基础值 2 + 小幅扰动 ±1
                changes["fear_change"] = -(2 + random.randint(-1, 1))  # 主动对抗减少恐惧
                changes["anxiety_change"] = 1 + random.randint(-1, 1)  # 轻微焦虑
                changes["stress_change"] = 4 + random.randint(-1, 1)  # 战斗带来高压力
                return changes

        # ===== 探索/调查类行动：轻微增加焦虑（不确定性） =====
        explore_actions = ["搜索", "调查", "检查", "探索", "查看", "观察", "翻找"]
        for keyword in explore_actions:
            if keyword in action_lower:
                # 探索不确定性焦虑：基础值 1 + 小幅扰动 ±1
                changes["anxiety_change"] = 1 + random.randint(-1, 1)
                return changes

        # ===== 高风险动作：增加恐惧 =====
        risky_actions = ["爬", "跳", "攀爬", "游泳", "翻墙", "钻", "挤"]
        for keyword in risky_actions:
            if keyword in action_lower:
                # 高风险动作：恐惧基础值 2 + 小幅扰动 ±1
                changes["fear_change"] = 2 + random.randint(-1, 1)
                changes["stress_change"] = 1 + random.randint(-1, 1)
                return changes

        # ===== 尖叫/呼救：增加压力 =====
        panic_actions = ["尖叫", "呼救", "喊叫", "大喊"]
        for keyword in panic_actions:
            if keyword in action_lower:
                # 尖叫：恐惧与压力基础值 3 + 小幅扰动 ±1
                changes["fear_change"] = 3 + random.randint(-1, 1)
                changes["stress_change"] = 3 + random.randint(-1, 1)
                return changes

        # ===== 普通行动：轻微自然波动（如果状态不高） =====
        total_stress = player.fear_level + player.anxiety_level + player.stress_level
        if total_stress < 100:  # 只有在相对平静时才有自然波动
            # 基础值 0 + 小幅扰动 ±1，允许平静状态下心理状态轻微上下波动
            changes["fear_change"] = random.randint(-1, 1)
            changes["anxiety_change"] = random.randint(-1, 1)
            changes["stress_change"] = random.randint(-1, 1)

        return changes
