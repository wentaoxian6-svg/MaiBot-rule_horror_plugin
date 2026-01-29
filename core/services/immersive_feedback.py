"""沉浸式反馈系统 - 生成符合恐怖氛围的反馈"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from ..llm.client import LLMClient, LLMResponse

logger = logging.getLogger(__name__)


class FeedbackType(Enum):
    """反馈类型枚举"""
    IMMEDIATE = "immediate"
    DELAYED = "delayed"
    SUBTLE = "subtle"
    OVERT = "overt"


@dataclass
class FeedbackResponse:
    """反馈响应数据类"""
    content: str
    feedback_type: FeedbackType
    delay_seconds: int = 0
    should_update_state: bool = False
    state_updates: dict[str, Any] = None

    def __post_init__(self):
        if self.state_updates is None:
            self.state_updates = {}


class ImmersiveFeedback:
    """沉浸式反馈系统 - 生成符合恐怖氛围的反馈"""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()

    async def respond(
        self,
        action: dict[str, Any],
        game_state: dict[str, Any],
    ) -> FeedbackResponse:
        """
        根据行动生成沉浸式反馈

        Args:
            action: 玩家行动信息
            game_state: 游戏状态

        Returns:
            FeedbackResponse 对象
        """
        violates_rule = action.get("violates_rule", False)

        if violates_rule:
            return await self._generate_violation_feedback(action, game_state)
        else:
            return await self._generate_normal_feedback(action, game_state)

    async def _generate_violation_feedback(
        self,
        action: dict[str, Any],
        game_state: dict[str, Any],
    ) -> FeedbackResponse:
        """生成违反规则的反馈（延迟反馈）"""
        system_prompt = """你是一个规则怪谈游戏的反馈生成器。当玩家违反规则时，你需要生成延迟反馈。

规则：
1. 不要立即告诉玩家他们违反了规则
2. 先给出看似正常的反馈
3. 在后续的反馈中逐渐揭示异常
4. 使用感官描述而非状态描述
5. 营造不安和恐怖的氛围

请以 JSON 格式返回：
{
    "content": "反馈内容",
    "feedback_type": "immediate/delayed/subtle/overt",
    "delay_seconds": 0,
    "should_update_state": true/false,
    "state_updates": {}
}

注意：
- feedback_type 为 "delayed" 时，delay_seconds 应该大于 0
- state_updates 可以包含玩家状态的变化（理智、体力等）
- 使用克系、新怪谈（New Weird）、Liminal Space 风格"""

        user_prompt = self._build_feedback_prompt(action, game_state)

        try:
            response = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.8,
                max_tokens=400,
            )

            result = response.parse_json()
            return self._parse_feedback_response(result)

        except Exception as e:
            logger.error(f"生成违反规则反馈失败: {e}")
            return FeedbackResponse(
                content="你执行了行动。一切看起来都很正常。",
                feedback_type=FeedbackType.IMMEDIATE,
                delay_seconds=30,
                should_update_state=False,
            )

    async def _generate_normal_feedback(
        self,
        action: dict[str, Any],
        game_state: dict[str, Any],
    ) -> FeedbackResponse:
        """生成正常行动的反馈"""
        system_prompt = """你是一个规则怪谈游戏的反馈生成器。你需要为玩家的正常行动生成沉浸式反馈。

规则：
1. 使用感官描述而非状态描述
2. 营造紧张和不安的氛围
3. 给出微妙的暗示和线索
4. 根据行动的风险等级调整反馈的紧张程度
5. 保持神秘感和不确定性

请以 JSON 格式返回：
{
    "content": "反馈内容",
    "feedback_type": "immediate/subtle/overt",
    "delay_seconds": 0,
    "should_update_state": true/false,
    "state_updates": {}
}

注意：
- feedback_type 为 "subtle" 时，给出微妙的暗示
- feedback_type 为 "overt" 时，直接描述结果
- state_updates 可以包含玩家状态的变化（理智、体力等）
- 使用克系、新怪谈（New Weird）、Liminal Space 风格"""

        user_prompt = self._build_feedback_prompt(action, game_state)

        try:
            response = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.8,
                max_tokens=400,
            )

            result = response.parse_json()
            return self._parse_feedback_response(result)

        except Exception as e:
            logger.error(f"生成正常反馈失败: {e}")
            return FeedbackResponse(
                content="你执行了行动。",
                feedback_type=FeedbackType.IMMEDIATE,
                should_update_state=False,
            )

    def _build_feedback_prompt(
        self,
        action: dict[str, Any],
        game_state: dict[str, Any],
    ) -> str:
        """构建反馈提示词"""
        action_type = action.get("action_type", "unknown")
        target = action.get("target", "")
        description = action.get("description", "")
        risk_level = action.get("risk_level", 0.0)
        violates_rule = action.get("violates_rule", False)

        scene_name = game_state.get("scene_name", "未知场景")
        background = game_state.get("background", "")
        player_status = game_state.get("player_status", {})

        prompt = f"""当前场景：{scene_name}

场景背景：
{background}

玩家行动：
- 类型：{action_type}
- 目标：{target}
- 描述：{description}
- 风险等级：{risk_level}
- 违反规则：{violates_rule}

玩家状态：
- 理智：{player_status.get('sanity', 100)}/100
- 体力：{player_status.get('health', 100)}/100
- 位置：{player_status.get('location', '未知')}

请生成沉浸式反馈。"""

        return prompt

    def _parse_feedback_response(self, result: dict[str, Any]) -> FeedbackResponse:
        """将解析结果转换为 FeedbackResponse 对象"""
        try:
            feedback_type_str = result.get("feedback_type", "immediate")
            feedback_type = FeedbackType(feedback_type_str.lower())
        except (ValueError, AttributeError):
            feedback_type = FeedbackType.IMMEDIATE

        return FeedbackResponse(
            content=result.get("content", ""),
            feedback_type=feedback_type,
            delay_seconds=int(result.get("delay_seconds", 0)),
            should_update_state=bool(result.get("should_update_state", False)),
            state_updates=result.get("state_updates", {}),
        )

    async def generate_delayed_feedback(
        self,
        original_action: dict[str, Any],
        game_state: dict[str, Any],
    ) -> FeedbackResponse:
        """
        生成延迟反馈（在玩家违反规则后的一段时间）

        Args:
            original_action: 原始行动
            game_state: 游戏状态

        Returns:
            FeedbackResponse 对象
        """
        system_prompt = """你是一个规则怪谈游戏的延迟反馈生成器。玩家之前违反了规则，现在需要揭示异常。

规则：
1. 逐渐揭示异常
2. 使用感官描述
3. 营造恐怖和不安的氛围
4. 给出玩家违反规则的暗示
5. 保持神秘感

请以 JSON 格式返回：
{
    "content": "反馈内容",
    "feedback_type": "delayed",
    "delay_seconds": 0,
    "should_update_state": true/false,
    "state_updates": {}
}

注意：
- 这是延迟反馈，所以应该揭示之前的行动导致的异常
- state_updates 可以包含玩家状态的变化（理智、体力等）
- 使用克系、新怪谈（New Weird）、Liminal Space 风格"""

        user_prompt = self._build_delayed_feedback_prompt(original_action, game_state)

        try:
            response = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.9,
                max_tokens=400,
            )

            result = response.parse_json()
            return self._parse_feedback_response(result)

        except Exception as e:
            logger.error(f"生成延迟反馈失败: {e}")
            return FeedbackResponse(
                content="等等...有些不对劲。",
                feedback_type=FeedbackType.DELAYED,
                should_update_state=True,
                state_updates={"sanity": -5},
            )

    def _build_delayed_feedback_prompt(
        self,
        original_action: dict[str, Any],
        game_state: dict[str, Any],
    ) -> str:
        """构建延迟反馈提示词"""
        action_type = original_action.get("action_type", "unknown")
        target = original_action.get("target", "")
        description = original_action.get("description", "")
        violated_rule = original_action.get("violated_rule", "")

        scene_name = game_state.get("scene_name", "未知场景")
        player_status = game_state.get("player_status", {})

        prompt = f"""当前场景：{scene_name}

玩家之前的行动：
- 类型：{action_type}
- 目标：{target}
- 描述：{description}
- 违反的规则：{violated_rule}

玩家当前状态：
- 理智：{player_status.get('sanity', 100)}/100
- 体力：{player_status.get('health', 100)}/100

现在需要揭示之前的行动导致的异常。请生成延迟反馈。"""

        return prompt

    async def generate_sensory_description(
        self,
        target: str,
        game_state: dict[str, Any],
    ) -> str:
        """
        生成目标的感官描述

        Args:
            target: 目标对象
            game_state: 游戏状态

        Returns:
            感官描述文本
        """
        system_prompt = """你是一个规则怪谈游戏的感官描述生成器。你需要为目标生成沉浸式的感官描述。

规则：
1. 使用多感官描述（视觉、听觉、触觉、嗅觉等）
2. 营造不安和恐怖的氛围
3. 给出微妙的暗示和线索
4. 保持神秘感和不确定性
5. 根据场景的恐怖程度调整描述的紧张程度

请直接返回描述文本，不要使用 JSON 格式。

注意：
- 使用克系、新怪谈（New Weird）、Liminal Space 风格
- 描述应该让玩家感到不安和好奇
- 避免直接揭示真相，保持神秘感"""

        scene_name = game_state.get("scene_name", "未知场景")
        background = game_state.get("background", "")

        prompt = f"""当前场景：{scene_name}

场景背景：
{background}

目标：{target}

请生成沉浸式的感官描述。"""

        try:
            response = await self.llm_client.call(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.8,
                max_tokens=300,
            )

            return response.content.strip()

        except Exception as e:
            logger.error(f"生成感官描述失败: {e}")
            return f"你看着{target}，感觉有些异样。"
