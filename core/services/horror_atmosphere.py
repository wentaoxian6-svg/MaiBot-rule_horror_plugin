"""恐怖氛围增强系统 - 增强游戏的恐怖氛围"""
from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from ..llm.client import LLMClient, LLMResponse

logger = logging.getLogger(__name__)


class AtmosphereIntensity(Enum):
    """氛围强度枚举"""
    CALM = "calm"
    TENSE = "tense"
    UNSETTLING = "unsettling"
    TERRIFYING = "terrifying"


@dataclass
class AtmosphereEvent:
    """氛围事件数据类"""
    event_type: str
    description: str
    intensity: AtmosphereIntensity
    affects_sanity: bool
    sanity_change: int = 0
    affects_health: bool = False
    health_change: int = 0
    is_subtle: bool = True


class HorrorAtmosphereEnhancer:
    """恐怖氛围增强系统 - 增强游戏的恐怖氛围"""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()
        self._current_intensity = AtmosphereIntensity.CALM
        self._event_history: list[str] = []

    async def generate_atmosphere_event(
        self,
        scene_name: str,
        current_intensity: AtmosphereIntensity,
        player_sanity: int,
        player_health: int,
        location: str,
    ) -> AtmosphereEvent:
        """
        生成氛围事件

        Args:
            scene_name: 场景名称
            current_intensity: 当前氛围强度
            player_sanity: 玩家理智值
            player_health: 玩家体力值
            location: 玩家位置

        Returns:
            AtmosphereEvent 对象
        """
        # 根据玩家状态调整氛围强度
        target_intensity = self._calculate_target_intensity(
            current_intensity, player_sanity, player_health
        )

        # 生成事件
        system_prompt = self._build_system_prompt(target_intensity)
        user_prompt = self._build_event_prompt(
            scene_name, target_intensity, player_sanity, player_health, location
        )

        try:
            response = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.9,
                max_tokens=400,
            )

            result = response.parse_json()
            event = self._parse_atmosphere_event(result)
            self._current_intensity = target_intensity
            self._event_history.append(event.description)

            return event

        except Exception as e:
            logger.error(f"生成氛围事件失败: {e}")
            return self._get_fallback_event(target_intensity)

    def _calculate_target_intensity(
        self,
        current_intensity: AtmosphereIntensity,
        player_sanity: int,
        player_health: int,
    ) -> AtmosphereIntensity:
        """计算目标氛围强度"""
        # 理智越低，氛围越恐怖
        if player_sanity < 30:
            return AtmosphereIntensity.TERRIFYING
        elif player_sanity < 50:
            return AtmosphereIntensity.UNSETTLING
        elif player_sanity < 70:
            return AtmosphereIntensity.TENSE
        else:
            return AtmosphereIntensity.CALM

    def _build_system_prompt(self, intensity: AtmosphereIntensity) -> str:
        """构建系统提示词"""
        intensity_descriptions = {
            AtmosphereIntensity.CALM: "平静，只有微妙的异常暗示",
            AtmosphereIntensity.TENSE: "紧张，有明显的异常现象",
            AtmosphereIntensity.UNSETTLING: "不安，异常现象频繁出现",
            AtmosphereIntensity.TERRIFYING: "恐怖，强烈的恐怖元素和直接威胁",
        }

        description = intensity_descriptions.get(intensity, "未知")

        return f"""你是一个规则怪谈游戏的恐怖氛围生成器。你的任务是生成符合恐怖氛围的事件。

氛围强度：{description}

事件生成要求：
1. 使用感官描述（视觉、听觉、触觉、嗅觉等）
2. 营造恐怖和不安的氛围
3. 根据氛围强度调整事件的恐怖程度
4. 可以影响玩家的理智和体力
5. 保持神秘感和不确定性
6. 使用克系、新怪谈（New Weird）、Liminal Space 风格

请以 JSON 格式返回：
{{
    "event_type": "事件类型（如：环境变化、幻觉、实体出现等）",
    "description": "事件描述（100-150字）",
    "intensity": "{intensity.value}",
    "affects_sanity": true/false,
    "sanity_change": -5,
    "affects_health": true/false,
    "health_change": -2,
    "is_subtle": true/false
}}

注意：
- is_subtle 为 true 时，事件是微妙的，玩家可能不会立即注意到
- is_subtle 为 false 时，事件是明显的，玩家会立即察觉
- 理智和体力的变化应该合理，不要过于剧烈
- 事件应该与场景和氛围相符"""

    def _build_event_prompt(
        self,
        scene_name: str,
        intensity: AtmosphereIntensity,
        player_sanity: int,
        player_health: int,
        location: str,
    ) -> str:
        """构建事件提示词"""
        return f"""当前场景：{scene_name}

氛围强度：{intensity.value}

玩家状态：
- 理智：{player_sanity}/100
- 体力：{player_health}/100
- 位置：{location}

最近的事件：
{chr(10).join(self._event_history[-3:])}

请生成符合当前氛围的事件。"""

    def _parse_atmosphere_event(self, result: dict[str, Any]) -> AtmosphereEvent:
        """将解析结果转换为 AtmosphereEvent 对象"""
        try:
            intensity_str = result.get("intensity", "calm")
            intensity = AtmosphereIntensity(intensity_str.lower())
        except (ValueError, AttributeError):
            intensity = AtmosphereIntensity.CALM

        return AtmosphereEvent(
            event_type=result.get("event_type", "未知事件"),
            description=result.get("description", ""),
            intensity=intensity,
            affects_sanity=bool(result.get("affects_sanity", False)),
            sanity_change=int(result.get("sanity_change", 0)),
            affects_health=bool(result.get("affects_health", False)),
            health_change=int(result.get("health_change", 0)),
            is_subtle=bool(result.get("is_subtle", True)),
        )

    def _get_fallback_event(self, intensity: AtmosphereIntensity) -> AtmosphereEvent:
        """获取备用事件（当 LLM 调用失败时）"""
        fallback_events = {
            AtmosphereIntensity.CALM: AtmosphereEvent(
                event_type="环境变化",
                description="你感觉到空气中有一丝异样的气息。",
                intensity=AtmosphereIntensity.CALM,
                affects_sanity=False,
                is_subtle=True,
            ),
            AtmosphereIntensity.TENSE: AtmosphereEvent(
                event_type="环境变化",
                description="周围的温度似乎下降了几度，你感到一丝寒意。",
                intensity=AtmosphereIntensity.TENSE,
                affects_sanity=True,
                sanity_change=-2,
                is_subtle=True,
            ),
            AtmosphereIntensity.UNSETTLING: AtmosphereEvent(
                event_type="幻觉",
                description="你似乎看到角落里有什么东西在移动，但当你仔细看时，什么也没有。",
                intensity=AtmosphereIntensity.UNSETTLING,
                affects_sanity=True,
                sanity_change=-3,
                is_subtle=True,
            ),
            AtmosphereIntensity.TERRIFYING: AtmosphereEvent(
                event_type="实体出现",
                description="一个扭曲的身影出现在你面前，你感到强烈的恐惧。",
                intensity=AtmosphereIntensity.TERRIFYING,
                affects_sanity=True,
                sanity_change=-5,
                affects_health=True,
                health_change=-2,
                is_subtle=False,
            ),
        }

        return fallback_events.get(intensity, fallback_events[AtmosphereIntensity.CALM])

    async def generate_progressive_distortion(
        self,
        scene_name: str,
        player_sanity: int,
        distortion_level: int = 1,
    ) -> str:
        """
        生成渐进式异化描述

        Args:
            scene_name: 场景名称
            player_sanity: 玩家理智值
            distortion_level: 异化等级（1-3）

        Returns:
            异化描述文本
        """
        system_prompt = """你是一个规则怪谈游戏的渐进式异化生成器。你的任务是生成环境逐渐异化的描述。

异化等级：
- 等级1：微妙的异常，只有细节上的变化
- 等级2：明显的异常，环境开始扭曲
- 等级3：严重的异化，环境已经完全改变

请直接返回描述文本，不要使用 JSON 格式。

注意：
- 使用感官描述
- 营造恐怖和不安的氛围
- 根据异化等级调整描述的恐怖程度
- 使用克系、新怪谈（New Weird）、Liminal Space 风格"""

        level_descriptions = {
            1: "微妙的异常",
            2: "明显的异常",
            3: "严重的异化",
        }

        prompt = f"""当前场景：{scene_name}

玩家理智：{player_sanity}/100

异化等级：{level_descriptions.get(distortion_level, "未知")}（{distortion_level}）

请生成渐进式异化的描述。"""

        try:
            response = await self.llm_client.call(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.9,
                max_tokens=300,
            )

            return response.content.strip()

        except Exception as e:
            logger.error(f"生成渐进式异化失败: {e}")
            return "周围的环境似乎有些不对劲..."

    async def generate_vulnerability_description(
        self,
        player_sanity: int,
        player_health: int,
        location: str,
    ) -> str:
        """
        生成玩家脆弱感描述

        Args:
            player_sanity: 玩家理智值
            player_health: 玩家体力值
            location: 玩家位置

        Returns:
            脆弱感描述文本
        """
        system_prompt = """你是一个规则怪谈游戏的脆弱感生成器。你的任务是生成玩家脆弱感的描述。

脆弱感生成要求：
1. 根据玩家的理智和体力值描述脆弱感
2. 使用感官描述
3. 营造无助和不安的氛围
4. 强调玩家的脆弱性
5. 使用克系、新怪谈（New Weird）、Liminal Space 风格

请直接返回描述文本，不要使用 JSON 格式。

注意：
- 理智越低，描述应该越混乱和不安
- 体力越低，描述应该越虚弱和无力
- 描述应该让玩家感到脆弱和易受攻击"""

        prompt = f"""玩家状态：
- 理智：{player_sanity}/100
- 体力：{player_health}/100
- 位置：{location}

请生成玩家脆弱感的描述。"""

        try:
            response = await self.llm_client.call(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.9,
                max_tokens=300,
            )

            return response.content.strip()

        except Exception as e:
            logger.error(f"生成脆弱感描述失败: {e}")
            return "你感到一阵虚弱，仿佛随时都会倒下。"

    def get_current_intensity(self) -> AtmosphereIntensity:
        """获取当前氛围强度"""
        return self._current_intensity

    def get_event_history(self) -> list[str]:
        """获取事件历史"""
        return self._event_history.copy()

    def clear_event_history(self) -> None:
        """清除事件历史"""
        self._event_history.clear()
