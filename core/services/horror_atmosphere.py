"""恐怖氛围增强系统

提供：
- 根据玩家状态生成氛围事件（JSON -> AtmosphereEvent）
- 渐进式异化描写
- 玩家脆弱感描写

该模块曾被批量替换破坏（引号/全角标点落入语法层）。此处按原意重写，保持对外接口稳定。
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from ..llm.client import LLMClient, get_default_max_tokens

logger = logging.getLogger(__name__)

# 类型定义
EventData: TypeAlias = dict[str, "str | bool | int | None"]


class AtmosphereIntensity(Enum):
    """氛围强度枚举"""

    CALM = "calm"
    TENSE = "tense"
    UNSETTLING = "unsettling"
    TERRIFYING = "terrifying"


@dataclass
class AtmosphereEvent:
    """氛围事件"""

    event_type: str
    description: str
    intensity: AtmosphereIntensity
    affects_sanity: bool
    sanity_change: int = 0
    affects_health: bool = False
    health_change: int = 0
    is_subtle: bool = True


class HorrorAtmosphereEnhancer:
    """恐怖氛围增强系统"""

    def __init__(self, llm_client: LLMClient | None = None):
        self.llm_client: LLMClient = llm_client or LLMClient()
        self._current_intensity: AtmosphereIntensity = AtmosphereIntensity.CALM
        self._event_history: list[str] = []

    async def generate_atmosphere_event(
        self,
        scene_name: str,
        current_intensity: AtmosphereIntensity,
        player_sanity: int,
        player_health: int,
        location: str,
        player_fear: int = 0,
        player_anxiety: int = 0,
        player_stress: int = 0,
    ) -> AtmosphereEvent:
        """生成氛围事件"""

        target_intensity = self._calculate_target_intensity(
            current_intensity=current_intensity,
            player_sanity=player_sanity,
            player_health=player_health,
            player_fear=player_fear,
            player_anxiety=player_anxiety,
            player_stress=player_stress,
        )

        system_prompt = self._build_system_prompt(target_intensity)
        # 参数验证
        if not scene_name:
            scene_name = "未知场景"
        if not location:
            location = "未知地点"

        user_prompt = self._build_event_prompt(
            scene_name=scene_name,
            intensity=target_intensity,
            player_sanity=player_sanity,
            player_health=player_health,
            player_fear=player_fear,
            player_anxiety=player_anxiety,
            player_stress=player_stress,
            location=location,
        )

        try:
            response = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.9,
                max_tokens=get_default_max_tokens(),
            )
            data_raw = response.parse_json()
            data: EventData = data_raw if isinstance(data_raw, dict) else {}
            event = self._parse_atmosphere_event(data, target_intensity)

            self._current_intensity = target_intensity
            if event.description:
                self._event_history.append(event.description)
                # 控制历史长度
                if len(self._event_history) > 20:
                    self._event_history = self._event_history[-20:]

            return event
        except Exception as e:
            logger.error(f"生成氛围事件失败: {e}", exc_info=True)
            return self._get_fallback_event(target_intensity)

    def _calculate_target_intensity(
        self,
        current_intensity: AtmosphereIntensity,
        player_sanity: int,
        player_health: int,
        player_fear: int = 0,
        player_anxiety: int = 0,
        player_stress: int = 0,
    ) -> AtmosphereIntensity:
        """根据玩家状态计算目标氛围强度"""

        sanity = max(0, min(100, int(player_sanity)))
        health = max(0, min(100, int(player_health)))
        fear = max(0, min(100, int(player_fear)))
        anxiety = max(0, min(100, int(player_anxiety)))
        stress = max(0, min(100, int(player_stress)))

        # 基于理智/体力/心理状态给出目标强度
        if sanity < 25 or health < 20 or fear > 80 or anxiety > 80 or stress > 80:
            desired = AtmosphereIntensity.TERRIFYING
        elif sanity < 45 or health < 40 or fear > 60 or anxiety > 60 or stress > 60:
            desired = AtmosphereIntensity.UNSETTLING
        elif sanity < 70 or health < 60 or fear > 40 or anxiety > 40 or stress > 40:
            desired = AtmosphereIntensity.TENSE
        else:
            desired = AtmosphereIntensity.CALM

        # 为了避免强度跳变过大：只允许向 desired 方向移动一档
        order = [
            AtmosphereIntensity.CALM,
            AtmosphereIntensity.TENSE,
            AtmosphereIntensity.UNSETTLING,
            AtmosphereIntensity.TERRIFYING,
        ]
        try:
            cur_i = order.index(current_intensity)
        except ValueError:
            cur_i = order.index(self._current_intensity)
        des_i = order.index(desired)

        if des_i > cur_i:
            return order[cur_i + 1]
        if des_i < cur_i:
            return order[cur_i - 1]
        return desired

    def _build_system_prompt(self, intensity: AtmosphereIntensity) -> str:
        """构建系统提示词"""

        intensity_desc = {
            AtmosphereIntensity.CALM: "平静，只有微妙的异常暗示",
            AtmosphereIntensity.TENSE: "紧张，出现明显但仍可解释的异常",
            AtmosphereIntensity.UNSETTLING: "不安，异常频繁且难以自圆其说",
            AtmosphereIntensity.TERRIFYING: "恐怖，出现直接威胁或强烈违和",
        }.get(intensity, "未知")

        return (
            "你是规则怪谈游戏的恐怖氛围生成器。你的任务是根据场景与玩家状态生成一条氛围事件。\n"
            f"当前氛围强度: {intensity.value}（{intensity_desc}）\n\n"
            "要求：\n"
            "1. 使用多感官描写（视觉/听觉/嗅觉/触觉），不要直说‘你很害怕’\n"
            "2. 与场景和地点强相关，避免空泛\n"
            "3. 恐怖应‘暗示’为主，允许少量直接威胁（仅在恐怖强度时）\n"
            "4. 可选地影响理智或体力，幅度要克制\n\n"
            "仅返回 JSON（不要 markdown，不要其他文字）：\n"
            "{\n"
            '  "event_type": "环境变化/幻觉/实体出现/声音/气味/其他",\n'
            '  "description": "事件描述（80-150字）",\n'
            f'  "intensity": "{intensity.value}",\n'
            '  "affects_sanity": true,\n'
            '  "sanity_change": -3,\n'
            '  "affects_health": false,\n'
            '  "health_change": 0,\n'
            '  "is_subtle": true\n'
            "}"
        )

    def _build_event_prompt(
        self,
        scene_name: str,
        intensity: AtmosphereIntensity,
        player_sanity: int,
        player_health: int,
        player_fear: int,
        player_anxiety: int,
        player_stress: int,
        location: str,
    ) -> str:
        """构建事件提示词"""

        recent = self._event_history[-3:]
        recent_text = "\n".join(f"- {x}" for x in recent) if recent else "-（无）"

        return (
            f"场景: {scene_name}\n"
            f"地点: {location}\n"
            f"氛围强度: {intensity.value}\n\n"
            "玩家状态:\n"
            f"- 理智: {player_sanity}/100\n"
            f"- 体力: {player_health}/100\n"
            f"- 恐惧: {player_fear}/100\n"
            f"- 焦虑: {player_anxiety}/100\n"
            f"- 压力: {player_stress}/100\n\n"
            "最近的事件（用于保持连续性，避免重复）：\n"
            f"{recent_text}\n\n"
            "请生成一条新的氛围事件。"
        )

    def _parse_atmosphere_event(
        self, result: EventData, fallback_intensity: AtmosphereIntensity
    ) -> AtmosphereEvent:
        """把 LLM JSON 转成 AtmosphereEvent，并做兜底校验"""

        intensity_raw = str(result.get("intensity", fallback_intensity.value) or "").lower()
        try:
            intensity = AtmosphereIntensity(intensity_raw)
        except Exception:
            intensity = fallback_intensity

        def _to_bool(v: object) -> bool:
            if isinstance(v, bool):
                return v
            if isinstance(v, (int, float)):
                return bool(v)
            if isinstance(v, str):
                return v.strip().lower() in {"1", "true", "yes", "y", "是"}
            return bool(v)

        def _to_int(v: object, default: int = 0) -> int:
            if isinstance(v, bool):
                return 1 if v else 0
            if isinstance(v, int):
                return v
            if isinstance(v, float):
                return int(v)
            if isinstance(v, str):
                try:
                    return int(float(v.strip()))
                except Exception:
                    return default
            return default

        affects_sanity = _to_bool(result.get("affects_sanity", False))
        affects_health = _to_bool(result.get("affects_health", False))

        sanity_change = _to_int(result.get("sanity_change", 0), 0)
        health_change = _to_int(result.get("health_change", 0), 0)

        # 控制变化幅度，避免 LLM 产生离谱数值
        sanity_change = max(-30, min(15, sanity_change))
        health_change = max(-50, min(20, health_change))

        if not affects_sanity:
            sanity_change = 0
        if not affects_health:
            health_change = 0

        return AtmosphereEvent(
            event_type=str(result.get("event_type", "其他") or "其他"),
            description=str(result.get("description", "") or "").strip(),
            intensity=intensity,
            affects_sanity=affects_sanity,
            sanity_change=sanity_change,
            affects_health=affects_health,
            health_change=health_change,
            is_subtle=_to_bool(result.get("is_subtle", True)),
        )

    def _get_fallback_event(self, intensity: AtmosphereIntensity) -> AtmosphereEvent:
        """LLM 失败时的备用事件"""

        fallback_pool: dict[AtmosphereIntensity, list[AtmosphereEvent]] = {
            AtmosphereIntensity.CALM: [
                AtmosphereEvent(
                    event_type="环境变化",
                    description="空气里多了一丝说不清的凉意，像是有人从你身后走过，却没留下脚步声。",
                    intensity=AtmosphereIntensity.CALM,
                    affects_sanity=False,
                    is_subtle=True,
                ),
                AtmosphereEvent(
                    event_type="声音",
                    description="远处传来一声极轻的金属轻碰，随后一切又归于安静，仿佛声音从未出现。",
                    intensity=AtmosphereIntensity.CALM,
                    affects_sanity=False,
                    is_subtle=True,
                ),
            ],
            AtmosphereIntensity.TENSE: [
                AtmosphereEvent(
                    event_type="气味",
                    description="消毒水味忽然变得刺鼻，你的鼻腔像被薄薄的冷雾糊住，呼吸不再顺畅。",
                    intensity=AtmosphereIntensity.TENSE,
                    affects_sanity=True,
                    sanity_change=-2,
                    is_subtle=True,
                ),
                AtmosphereEvent(
                    event_type="环境变化",
                    description="灯光闪了一下，阴影在墙角拉长又缩回去，像有什么东西试图靠近又临时改了主意。",
                    intensity=AtmosphereIntensity.TENSE,
                    affects_sanity=True,
                    sanity_change=-3,
                    is_subtle=True,
                ),
            ],
            AtmosphereIntensity.UNSETTLING: [
                AtmosphereEvent(
                    event_type="幻觉",
                    description="你看到墙面的污渍微微蠕动，像是一张想要开口的嘴。等你眨眼再看，它又变回普通的斑点。",
                    intensity=AtmosphereIntensity.UNSETTLING,
                    affects_sanity=True,
                    sanity_change=-5,
                    is_subtle=False,
                ),
            ],
            AtmosphereIntensity.TERRIFYING: [
                AtmosphereEvent(
                    event_type="实体出现",
                    description="你听到身后有布料拖地的摩擦声，一抹不合常理的影子贴着地面滑过，停在你脚边。",
                    intensity=AtmosphereIntensity.TERRIFYING,
                    affects_sanity=True,
                    sanity_change=-10,
                    affects_health=False,
                    is_subtle=False,
                ),
            ],
        }

        pool = fallback_pool.get(intensity) or fallback_pool[AtmosphereIntensity.CALM]
        return random.choice(pool)

    async def generate_progressive_distortion(
        self,
        scene_name: str,
        player_sanity: int,
        distortion_level: int = 1,
    ) -> str:
        """生成渐进式异化描写"""

        level = max(1, min(3, int(distortion_level)))
        system_prompt = (
            "你是规则怪谈游戏的渐进式异化描写器。\n"
            "请根据异化等级输出一段中文描写（120-200字），只输出纯文本。\n"
            "异化等级说明：\n"
            "- 1：微妙异常（细节不对劲但仍可解释）\n"
            "- 2：明显扭曲（空间/时间/感官开始失真）\n"
            "- 3：彻底异化（现实规则崩塌，出现强烈违和与威胁）\n"
            "写作要求：多感官、克制直白血腥、用细节制造不安。"
        )
        prompt = (
            f"场景: {scene_name}\n"
            f"玩家理智: {player_sanity}/100\n"
            f"异化等级: {level}\n"
            "请输出异化描写。"
        )

        try:
            resp = await self.llm_client.call(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.9,
                max_tokens=get_default_max_tokens(),
            )
            return resp.clean_content
        except Exception as e:
            logger.error(f"生成渐进式异化失败: {e}", exc_info=True)
            if level == 1:
                return "你注意到墙上的纹理似乎比记忆里多了几道细小的裂纹，像有人刻意用指甲轻轻划过。"
            if level == 2:
                return "走廊的尽头像被拉远了一点，你迈出的每一步都像踩在潮湿的棉絮上，声音被吞掉。"
            return "空间像薄纸一样起皱，灯光在你眼前反复折叠，某种‘不属于这里’的轮廓从阴影里缓慢抬头。"

    async def generate_vulnerability_description(
        self,
        player_sanity: int,
        player_health: int,
        location: str,
    ) -> str:
        """生成玩家脆弱感描写"""

        system_prompt = (
            "你是规则怪谈游戏的脆弱感描写器。\n"
            "根据玩家理智/体力生成一段中文描写（80-160字），只输出纯文本。\n"
            "要求：不用直接说‘你害怕’，改用身体与环境细节体现。"
        )
        prompt = (
            f"地点: {location}\n"
            f"玩家理智: {player_sanity}/100\n"
            f"玩家体力: {player_health}/100\n"
            "请输出脆弱感描写。"
        )

        try:
            resp = await self.llm_client.call(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.8,
                max_tokens=get_default_max_tokens(),
            )
            return resp.clean_content
        except Exception as e:
            logger.error(f"生成脆弱感描述失败: {e}", exc_info=True)
            sanity = max(0, min(100, int(player_sanity)))
            health = max(0, min(100, int(player_health)))
            if sanity < 30 or health < 30:
                return "你的呼吸变得又浅又急，掌心冰凉，衣料贴在皮肤上像一层潮湿的膜。你不确定自己还能撑多久。"
            return "你试着让步伐保持平稳，但心跳还是偏快了一些。周围的安静像被刻意放大，逼得你听见自己的吞咽声。"

    def get_current_intensity(self) -> AtmosphereIntensity:
        """获取当前氛围强度"""

        return self._current_intensity

    def get_event_history(self) -> list[str]:
        """获取事件历史"""

        return self._event_history.copy()

    def clear_event_history(self) -> None:
        """清除事件历史"""

        self._event_history.clear()
