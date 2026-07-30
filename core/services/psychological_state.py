"""心理状态服务 - 计算恐惧/焦虑/压力/疲劳等心理状态变化与分段叙事。

本模块从 ``action_processor.py`` 抽离，职责单一：
- ``PSYCHOLOGICAL_NARRATIVE_VARIANTS`` 心理状态分段叙事变体常量（每档 5+ 条，Task 16）
- ``PSYCHOLOGICAL_TIER_CROSSING`` 跨越阈值时的强调描述（Task 16）
- ``PsychologicalStateService.build_psychological_narrative`` 根据 Player 的心理状态阈值生成分段叙事
- ``PsychologicalStateService.calculate_mental_state_change`` 根据行动类型计算恐惧/焦虑/压力变化
- ``PsychologicalStateService.calculate_fatigue_increase`` 根据行动类型计算疲劳增加值
- ``PsychologicalStateService.recover_sanity_for_*`` 理智值小额回复路径（Task 18）

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

# 心理状态分段叙事变体模板（高/中/低三档，低档不含变体表示无特殊叙事）
# 与 FearThresholds/AnxietyThresholds/StressThresholds/FatigueThresholds 对齐
# sanity 分档与 SanityThresholds 对齐：low(崩坏幻觉) / medium(不安) / high(敏锐感知) / none(中等区间无叙事)
# Task 16：每档 5+ 条变体，避免重复；同档连续命中时由 build_psychological_narrative 衰减/轮换
PSYCHOLOGICAL_NARRATIVE_VARIANTS: dict[str, dict[str, list[str]]] = {
    "fear": {
        "high": [
            "你的手在抖，心跳快得发疼，每个影子都像在逼近",
            "恐惧攥住喉咙，呼吸急促得几乎换不过气",
            "你浑身发冷，心脏像要跳出胸腔，眼前的一切都在扭曲",
            "腿软得几乎站不住，每一道黑影都像在朝你扑来",
            "你的牙齿在打颤，汗毛倒竖，强烈的恐惧让你几乎无法思考",
            "你感到一种从骨髓里渗出的寒意，连视线都开始模糊",
        ],
        "medium": [
            "你感到一阵阵不安，呼吸变得急促",
            "胸口闷得发慌，总感觉有什么在窥视你",
            "你不停地四处张望，神经绷得很紧",
            "心跳有些加快，一种莫名的不安挥之不去",
            "后背发凉，你下意识屏住了呼吸",
            "你总觉得身后有什么东西，但又不敢回头确认",
        ],
    },
    "anxiety": {
        "high": [
            "焦虑感像石头压在胸口，思绪难以集中",
            "你脑子里乱成一团，每一个念头都在打结",
            "心烦意乱到无法思考，焦虑几乎要把你吞没",
            "你坐立难安，思绪像脱缰的野马般不受控制",
            "胸口闷得发疼，思绪被焦虑撕扯得支离破碎",
            "焦虑如针扎般刺进脑袋，每一个念头都让你头痛欲裂",
        ],
        "medium": [
            "你感到莫名的烦躁，总觉得自己漏掉了什么",
            "心里七上八下的，总觉得哪里不对劲",
            "思绪有些飘忽，总被无名的烦躁打断",
            "你开始反复检查口袋和周围，总觉得忘了什么",
            "心里隐隐不安，总想确认一切都还在原位",
            "你下意识地咬起了指甲，烦躁感挥之不去",
        ],
    },
    "stress": {
        "high": [
            "压力让你几乎崩溃，每一秒都像在忍受酷刑",
            "你感觉快要撑不住了，每一秒都漫长得像一年",
            "巨大的压力让你透不过气，几乎想尖叫出来",
            "全身紧绷到发疼，精神在崩溃的边缘摇摇欲坠",
            "你感到自己被压得喘不过气，每一步都艰难无比",
            "压力像一只无形的手攥住喉咙，让你几近窒息",
        ],
        "medium": [
            "紧张感持续累积，你开始对细节过度敏感",
            "你感到绷着一根弦，稍有动静就会跳起来",
            "身体不自觉地紧绷，对周围的一切都保持警惕",
            "紧张得胃部抽搐，每个细节都被你放大了几倍",
            "你下意识攥紧拳头，整个人处于高度戒备状态",
            "脖子与肩膀僵硬得发酸，紧张感久久不散",
        ],
    },
    "fatigue": {
        "high": [
            "疲惫席卷全身，眼皮沉重得像灌了铅",
            "你已经累到极点，连抬手的力气都快没了",
            "浑身像灌了铅一样沉重，意识也开始模糊",
            "困意一波波袭来，你几乎要站不住",
            "全身酸软无力，眼睛怎么也睁不开",
            "大脑像被灌了浆糊，连简单的思考都变得吃力",
        ],
        "medium": [
            "你感到疲倦，反应开始变慢",
            "困倦感袭来，你开始有些反应迟钝",
            "身体有些发沉，动作明显没有刚才利索",
            "你忍不住打了个哈欠，思绪开始有些迟缓",
            "疲惫让动作变得迟缓，眼前偶尔发花",
            "膝盖有些发软，你感到精力在一点点流失",
        ],
    },
    "sanity": {
        "low": [
            "你看到墙角的阴影在蠕动，耳边有不存在的低语",
            "眼前出现扭曲的幻影，分不清是现实还是幻觉",
            "墙上的纹路像在缓慢移动，你听到自己脑中的低声呢喃",
            "你看到不存在的人影在角落里晃动，耳边传来尖锐的耳鸣",
            "现实在你眼中扭曲变形，幻听与幻视交替出现",
            "你分不清脚下的地面是坚实的还是流动的，耳边不断有呢喃渗入",
        ],
        "medium": [
            "你总觉得有什么在看着你，脊背发凉",
            "影子似乎比刚才更长，你不敢回头看",
            "你感到一股说不出的寒意，仿佛有什么贴在你背后",
            "每走一步都觉得背后有视线跟随，让你头皮发麻",
            "空气中似乎弥漫着一种诡异的凝视感，你不敢抬头",
            "你的呼吸不自觉地放轻，仿佛怕惊动某种看不见的东西",
        ],
        "high": [
            "你的感官异常敏锐，能捕捉到微弱的细节",
            "头脑格外清醒，连远处的细微声响都听得一清二楚",
            "你的感知仿佛被点亮，每个细节都纤毫毕现",
            "你感到前所未有的清醒，思维敏锐得像刀刃",
            "感官被某种力量磨砺，连空气中细微的变化都逃不过你的注意",
            "你异常清明，连风中夹带的微弱气味都能分辨出来",
        ],
    },
}

# 跨越阈值（严重度上升）的强调描述
# Task 16：仅在档位跨越且严重度上升时追加，作为「关键时刻」的强调
PSYCHOLOGICAL_TIER_CROSSING: dict[str, dict[str, str]] = {
    "fear": {
        "high": "【恐惧达临界】心脏猛地一紧，全身血液仿佛凝固——某种不可名状的恐惧彻底攫住了你。",
        "medium": "【恐惧涌上心头】一阵刺骨的寒意从脊椎窜上天灵盖，恐惧感骤然加深。",
    },
    "anxiety": {
        "high": "【焦虑失控】焦虑如潮水般涌来，你的思维几乎被吞噬。",
        "medium": "【焦虑加深】一股无形的烦躁感攫住了你，焦虑明显加重。",
    },
    "stress": {
        "high": "【压力临界】压力达到临界点，你感觉自己快要被压垮了。",
        "medium": "【压力攀升】紧张感陡然上升，全身肌肉都绷紧了。",
    },
    "fatigue": {
        "high": "【疲惫至极】疲惫如潮水般袭来，你几乎要瘫倒在地。",
        "medium": "【疲劳累积】疲倦感陡然加重，身体开始发沉。",
    },
    "sanity": {
        "low": "【理智崩坏】理智的堤坝轰然崩塌，幻觉与现实在你眼前彻底交错。",
        "medium": "【理智下滑】你感到理智正在滑坡，诡异的感觉愈发强烈。",
        # high 是好转方向，不计入「严重度上升」的强调，故不列
    },
}


class PsychologicalStateService:
    """心理状态计算服务。

    封装恐惧/焦虑/压力/疲劳的「分段叙事」与「行动驱动变化」逻辑，
    以及理智值的「小额回复路径」（Task 18）。
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

        Task 16 增强：
        - 每档从 5+ 条变体中随机选取，尽量避开上次选取的变体；
        - 同档连续命中：第 3 次起 30% 概率静默（不输出该状态叙事）；
        - 跨越阈值且严重度上升：追加 ``PSYCHOLOGICAL_TIER_CROSSING`` 强调描述，并重置计数；
        - 通过 ``player.psych_tracking`` 字典记录 last_tier / consecutive_hits / last_narrative。

        Args:
            player: 玩家对象（含 fear/anxiety/stress/fatigue 等心理状态）
            sanity: 玩家理智值；None 时不追加 sanity 叙事（向后兼容）

        Returns:
            拼接后的叙事字符串；若所有状态均处于低档则返回空字符串
        """
        segments: list[str] = []

        # 计算各状态当前档位
        current_tiers: dict[str, str] = {
            "fear": self._tier_of(player.fear_level, FearThresholds.HIGH, FearThresholds.MEDIUM),
            "anxiety": self._tier_of(player.anxiety_level, AnxietyThresholds.HIGH, AnxietyThresholds.MEDIUM),
            "stress": self._tier_of(player.stress_level, StressThresholds.HIGH, StressThresholds.MEDIUM),
            "fatigue": self._tier_of(player.fatigue, FatigueThresholds.HIGH, FatigueThresholds.MEDIUM),
        }
        if sanity is not None:
            current_tiers["sanity"] = self._sanity_tier_of(sanity)

        # 玩家追踪字段：last_tier / consecutive_hits / last_narrative
        # 按 AGENTS.md 不兜底原则：Player 模型必须提供 psych_tracking 字段，否则直接报错
        tracking: dict[str, object] = player.psych_tracking

        for state_name, current_tier in current_tiers.items():
            last_tier_raw = tracking.get(f"{state_name}_last_tier", "none")
            last_narrative_raw = tracking.get(f"{state_name}_last_narrative", "")
            consecutive_raw = tracking.get(f"{state_name}_consecutive_hits", 0)
            last_tier = last_tier_raw if isinstance(last_tier_raw, str) else "none"
            last_narrative = last_narrative_raw if isinstance(last_narrative_raw, str) else ""
            consecutive = consecutive_raw if isinstance(consecutive_raw, int) else 0

            variants_for_state = PSYCHOLOGICAL_NARRATIVE_VARIANTS.get(state_name, {})
            # 低档（none）或该档位无变体：无叙事，重置该状态计数
            if current_tier == "none" or current_tier not in variants_for_state:
                tracking[f"{state_name}_last_tier"] = current_tier
                tracking[f"{state_name}_consecutive_hits"] = 0
                tracking[f"{state_name}_last_narrative"] = ""
                continue

            variants = variants_for_state[current_tier]

            # 跨越阈值：严重度上升时追加强调描述，并重置连续计数
            if current_tier != last_tier:
                if self._severity_of(state_name, current_tier) > self._severity_of(state_name, last_tier):
                    emphasis = PSYCHOLOGICAL_TIER_CROSSING.get(state_name, {}).get(current_tier, "")
                    if emphasis:
                        segments.append(emphasis)
                chosen = self._pick_variant(variants, last_narrative)
                segments.append(chosen)
                tracking[f"{state_name}_last_tier"] = current_tier
                tracking[f"{state_name}_consecutive_hits"] = 1
                tracking[f"{state_name}_last_narrative"] = chosen
                continue

            # 同档连续命中：第 3 次起 30% 概率静默
            new_consecutive = consecutive + 1
            if new_consecutive >= 3 and random.random() < 0.30:
                tracking[f"{state_name}_consecutive_hits"] = new_consecutive
                # 静默时保留 last_narrative，便于下次仍能避开同一变体
                continue

            chosen = self._pick_variant(variants, last_narrative)
            segments.append(chosen)
            tracking[f"{state_name}_last_tier"] = current_tier
            tracking[f"{state_name}_consecutive_hits"] = new_consecutive
            tracking[f"{state_name}_last_narrative"] = chosen

        return " ".join(seg for seg in segments if seg)

    @staticmethod
    def _tier_of(value: int, high: int, medium: int) -> str:
        """通用档位判定：>=high → high；>=medium → medium；其余 → none。"""
        if value >= high:
            return "high"
        if value >= medium:
            return "medium"
        return "none"

    @staticmethod
    def _sanity_tier_of(sanity: int) -> str:
        """理智档位判定：与 SanityThresholds 对齐。"""
        if sanity <= SanityThresholds.LOW:
            return "low"
        if sanity < SanityThresholds.MEDIUM:
            return "medium"
        if sanity >= SanityThresholds.HIGH:
            return "high"
        return "none"  # MEDIUM <= sanity < HIGH：无叙事

    @staticmethod
    def _severity_of(state_name: str, tier: str) -> int:
        """返回档位的严重度（0=无/好，越大越糟）。

        fear/anxiety/stress/fatigue：none=0, medium=1, high=2
        sanity：high(敏锐/好)=0, none(中性)=1, medium(不安)=2, low(崩坏)=3
        """
        if state_name == "sanity":
            return {"high": 0, "none": 1, "medium": 2, "low": 3}.get(tier, 0)
        return {"none": 0, "medium": 1, "high": 2}.get(tier, 0)

    @staticmethod
    def _pick_variant(variants: list[str], last_narrative: str) -> str:
        """从变体列表中随机选取一个，尽量避开上次选取的变体。

        - 列表为空：返回空串
        - 仅 1 条变体：固定返回（无法轮换）
        - 多条变体：剔除上次选取后随机；若剔除后为空（不应发生）则回退首条
        """
        if not variants:
            return ""
        if len(variants) == 1:
            return variants[0]
        candidates = [v for v in variants if v != last_narrative]
        if not candidates:
            return variants[0]
        return random.choice(candidates)

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

    # ===== Task 18：理智值曲线起伏 - 小额回复路径 =====
    # 以下三个方法由 ActionProcessor 在对应场景下调用：
    # - recover_sanity_for_rule_obedience: 行动判定「未违反任何规则」后调用
    # - recover_sanity_for_safe_zone: 玩家落点为「安全房间」（存档点/休息室等）后调用
    # - recover_sanity_for_friendly_interaction: 玩家-NPC 交互且态度友善时调用
    # 数值设计：单次回复 1~4 点，配合违规/危险带来的扣减构成起伏曲线，
    # 让「崩坏」成为玩家选择的结果而非必然趋势。

    def recover_sanity_for_rule_obedience(self, player: Player) -> int:
        """玩家行动未违反规则时的小额理智回复（Task 18）。

        调用时机：``ActionProcessor`` 判定本次行动「无违规」后调用。
        回复量：1~3（带轻微随机），钳制到 ``SanityThresholds.MAX``。

        Args:
            player: 玩家对象

        Returns:
            实际回复量（已钳制到上限，始终 >= 0）
        """
        recovery = random.randint(1, 3)
        before = player.sanity
        player.sanity = max(SanityThresholds.MIN, min(SanityThresholds.MAX, player.sanity + recovery))
        return player.sanity - before

    def recover_sanity_for_safe_zone(self, player: Player) -> int:
        """玩家进入「安全区」时的小额理智回复（Task 18）。

        调用时机：``ActionProcessor`` 判定玩家落点为安全房间（如存档点/休息室）后调用。
        回复量：2~4（比守规则略高，强化「回到安全区」的正反馈）。

        Args:
            player: 玩家对象

        Returns:
            实际回复量（已钳制到上限，始终 >= 0）
        """
        recovery = random.randint(2, 4)
        before = player.sanity
        player.sanity = max(SanityThresholds.MIN, min(SanityThresholds.MAX, player.sanity + recovery))
        return player.sanity - before

    def recover_sanity_for_friendly_interaction(
        self,
        player: Player,
        npc_attitude_vector: dict[str, float],
    ) -> int:
        """与友善 NPC 稳定互动时的小额理智回复（Task 18）。

        调用时机：``ActionProcessor`` 处理玩家-NPC 交互时调用，调用方需先从
        ``NpcMemory.get_attitude_vector(player_id)`` 取出态度向量再传入。
        判定「友善」：hostility < 30 且 trust >= 60（与 npc_system 六维向量对齐）。
        满足条件时回复 1~3；不满足则回复 0，调用方可据此决定是否提示玩家。

        Args:
            player: 玩家对象
            npc_attitude_vector: NPC 对该玩家的态度向量（含 affection/trust/hostility 等）

        Returns:
            实际回复量（0 表示不满足友善条件，未回复）
        """
        if not isinstance(npc_attitude_vector, dict):
            raise TypeError(
                "npc_attitude_vector 必须为字典，请检查 NpcMemory.get_attitude_vector 返回值"
            )

        hostility = float(npc_attitude_vector.get("hostility", 0.0))
        trust = float(npc_attitude_vector.get("trust", 0.0))
        # 友善门槛：敌意低于 30 且信任不低于 60
        if hostility >= 30.0 or trust < 60.0:
            return 0

        recovery = random.randint(1, 3)
        before = player.sanity
        player.sanity = max(SanityThresholds.MIN, min(SanityThresholds.MAX, player.sanity + recovery))
        return player.sanity - before
