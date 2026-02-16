"""沉浸式反馈系统

根据玩家行动与当前游戏状态，生成具有恐怖氛围的沉浸式文本反馈。

该文件曾被批量替换破坏（引号缺失、全角标点落入语法层等），此处按原意重写并保持对外接口：
- FeedbackType
- FeedbackResponse
- ImmersiveFeedback（respond / generate_delayed_feedback / generate_sensory_description）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from collections.abc import Mapping

from ...common.models import JsonValue, StateUpdatesDict
from ..llm.client import LLMClient, get_default_max_tokens

logger = logging.getLogger(__name__)


class FeedbackType(Enum):
    """反馈类型枚举"""

    IMMEDIATE = "immediate"
    DELAYED = "delayed"
    SUBTLE = "subtle"
    OVERT = "overt"


@dataclass
class FeedbackResponse:
    """反馈响应"""

    content: str
    feedback_type: FeedbackType
    delay_seconds: int = 0
    should_update_state: bool = False
    state_updates: StateUpdatesDict = field(default_factory=dict)


class ImmersiveFeedback:
    """沉浸式反馈系统"""

    def __init__(self, llm_client: LLMClient | None = None):
        self.llm_client: LLMClient = llm_client or LLMClient()

    async def respond(self, action: Mapping[str, JsonValue], game_state: Mapping[str, JsonValue]) -> FeedbackResponse:
        """根据行动生成即时反馈（若违反规则则偏向延迟反馈结构）"""

        if bool(action.get("violates_rule", False)):
            return await self._generate_violation_feedback(action, game_state)
        return await self._generate_normal_feedback(action, game_state)

    async def _generate_violation_feedback(
        self, action: Mapping[str, JsonValue], game_state: Mapping[str, JsonValue]
    ) -> FeedbackResponse:
        """生成违反规则的反馈（通常为“先正常后异常”的延迟结构）"""

        system_prompt = (
            "你是规则怪谈游戏的反馈生成器。玩家可能违反了规则，但你不要立刻明说。\n"
            "生成一条看似正常、但带有不安细节的反馈；必要时安排延迟揭示。\n\n"
            "返回 JSON（不要 markdown，不要其他文字）：\n"
            "{\n"
            '  "content": "反馈内容（80-160字）",\n'
            '  "feedback_type": "immediate/delayed/subtle/overt",\n'
            '  "delay_seconds": 0,\n'
            '  "should_update_state": true,\n'
            '  "state_updates": {"sanity": -5, "health": 0}\n'
            "}\n\n"
            "规则：\n"
            "- 若 feedback_type = delayed，则 delay_seconds > 0\n"
            "- state_updates 可为空；如有变化，幅度要克制（理智 -1~-15，体力 -1~-20）\n"
            "- 不要出现 emoji"
        )

        user_prompt = self._build_feedback_prompt(action, game_state)

        try:
            resp = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.8,
                max_tokens=get_default_max_tokens(),
            )
            return self._parse_feedback_response(resp.parse_json())
        except Exception as e:
            logger.error(f"生成违反规则反馈失败: {e}", exc_info=True)
            # fallback：给一个“表面正常”的即时反馈，并安排稍后揭示
            return FeedbackResponse(
                content="你完成了动作。周围似乎没有立刻发生变化，但某种细小的错位感在你脑后停留不去。",
                feedback_type=FeedbackType.DELAYED,
                delay_seconds=30,
                should_update_state=False,
                state_updates={},
            )

    async def _generate_normal_feedback(
        self, action: Mapping[str, JsonValue], game_state: Mapping[str, JsonValue]
    ) -> FeedbackResponse:
        """生成正常行动反馈"""

        system_prompt = (
            "你是规则怪谈游戏的沉浸式反馈生成器。\n"
            "对玩家的正常行动给出结果描写，允许加入微妙的不安暗示或线索。\n\n"
            "返回 JSON（不要 markdown，不要其他文字）：\n"
            "{\n"
            '  "content": "反馈内容（80-180字）",\n'
            '  "feedback_type": "immediate/subtle/overt",\n'
            '  "delay_seconds": 0,\n'
            '  "should_update_state": false,\n'
            '  "state_updates": {}\n'
            "}\n\n"
            "规则：\n"
            "- feedback_type = subtle 时，多写暗示；overt 时结果更直接\n"
            "- 不要出现 emoji"
        )

        user_prompt = self._build_feedback_prompt(action, game_state)

        try:
            resp = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.8,
                max_tokens=get_default_max_tokens(),
            )
            return self._parse_feedback_response(resp.parse_json())
        except Exception as e:
            logger.error(f"生成正常反馈失败: {e}", exc_info=True)
            return FeedbackResponse(
                content="你执行了行动。空气里没有立刻给出答案，但你能感觉到某些细节被你触碰到了。",
                feedback_type=FeedbackType.IMMEDIATE,
                delay_seconds=0,
                should_update_state=False,
                state_updates={},
            )

    def _build_feedback_prompt(self, action: Mapping[str, JsonValue], game_state: Mapping[str, JsonValue]) -> str:
        """构建反馈提示词"""

        action_type = action.get("action_type", "unknown")
        target = action.get("target", "")
        description = action.get("description", "")
        risk_level = action.get("risk_level", 0.0)
        violates_rule = action.get("violates_rule", False)
        violated_rule = action.get("violated_rule", "")

        scene_name = game_state.get("scene_name", "未知场景")
        background = game_state.get("background", "")
        player_status = game_state.get("player_status", {})

        return (
            f"场景: {scene_name}\n"
            f"背景: {background}\n\n"
            "玩家行动:\n"
            f"- 类型: {action_type}\n"
            f"- 目标: {target}\n"
            f"- 描述: {description}\n"
            f"- 风险等级: {risk_level}\n"
            f"- 违反规则: {violates_rule}\n"
            f"- 违反的规则: {violated_rule}\n\n"
            "玩家状态:\n"
            f"- 理智: {player_status.get('sanity', 100)}/100\n"
            f"- 体力: {player_status.get('health', 100)}/100\n"
            f"- 位置: {player_status.get('location', '未知')}\n\n"
            "请生成沉浸式反馈。"
        )

    def _parse_feedback_response(self, data: Mapping[str, JsonValue]) -> FeedbackResponse:
        """把 LLM JSON 转成 FeedbackResponse，并做兜底"""

        ft_raw = str(data.get("feedback_type", "immediate") or "immediate").lower().strip()
        try:
            feedback_type = FeedbackType(ft_raw)
        except Exception:
            feedback_type = FeedbackType.IMMEDIATE

        def _to_bool(v: object) -> bool:
            if isinstance(v, bool):
                return v
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

        delay_seconds = max(0, _to_int(data.get("delay_seconds", 0), 0))
        should_update_state = _to_bool(data.get("should_update_state", False))

        updates: StateUpdatesDict = {}
        raw_updates = data.get("state_updates")
        if isinstance(raw_updates, dict):
            # 理智值更新：限制范围 -30 ~ +15
            if "sanity" in raw_updates:
                delta = _to_int(raw_updates.get("sanity"), 0)
                updates["sanity"] = max(-30, min(15, delta))
            # 体力值更新：限制范围 -50 ~ +20
            if "health" in raw_updates:
                delta = _to_int(raw_updates.get("health"), 0)
                updates["health"] = max(-50, min(20, delta))
            # 恐惧值更新：限制范围 -15 ~ +30（允许自然恢复）
            if "fear_level" in raw_updates:
                delta = _to_int(raw_updates.get("fear_level"), 0)
                updates["fear_level"] = max(-15, min(30, delta))
            # 焦虑值更新：限制范围 -12 ~ +25（允许自然恢复）
            if "anxiety_level" in raw_updates:
                delta = _to_int(raw_updates.get("anxiety_level"), 0)
                updates["anxiety_level"] = max(-12, min(25, delta))
            # 压力值更新：限制范围 -12 ~ +25（允许自然恢复）
            if "stress_level" in raw_updates:
                delta = _to_int(raw_updates.get("stress_level"), 0)
                updates["stress_level"] = max(-12, min(25, delta))
            # 位置更新：直接设置新位置
            if "location" in raw_updates:
                loc = raw_updates.get("location")
                if isinstance(loc, str):
                    updates["location"] = loc

        # 注意：delayed 类型的反馈在生成时 delay_seconds 应该为 0
        # 因为它是在延迟时间到达后才调用的，不需要再设置延迟

        content = str(data.get("content", "") or "").strip()
        if not content:
            content = "你完成了行动，但某些细节让你难以忽视。"

        return FeedbackResponse(
            content=content,
            feedback_type=feedback_type,
            delay_seconds=delay_seconds,
            should_update_state=should_update_state,
            state_updates=updates,
        )

    async def generate_delayed_feedback(
        self, original_action: Mapping[str, JsonValue], game_state: Mapping[str, JsonValue]
    ) -> FeedbackResponse:
        """生成延迟反馈（用于玩家违反规则后的一段时间揭示异常）"""

        system_prompt = (
            "你是规则怪谈游戏的延迟反馈生成器。玩家之前的行动可能触发了规则的惩罚或异常，现在需要逐渐揭示。\n"
            "请输出一条更明显、更不安的反馈，但仍保持克制与暗示。\n\n"
            "返回 JSON（不要 markdown，不要其他文字）：\n"
            "{\n"
            '  "content": "延迟反馈内容（100-220字）",\n'
            '  "feedback_type": "delayed",\n'
            '  "delay_seconds": 0,\n'
            '  "should_update_state": true,\n'
            '  "state_updates": {"sanity": -5}\n'
            "}\n\n"
            "规则：\n"
            "- feedback_type 固定为 delayed\n"
            "- delay_seconds 保持 0（因为这是现在要发送的文本）\n"
            "- 不要出现 emoji"
        )

        user_prompt = self._build_delayed_feedback_prompt(original_action, game_state)

        try:
            resp = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.9,
                max_tokens=get_default_max_tokens(),
            )
            return self._parse_feedback_response(resp.parse_json())
        except Exception as e:
            logger.error(f"生成延迟反馈失败: {e}", exc_info=True)
            return FeedbackResponse(
                content="你忽然意识到之前的动作留下了某种‘回声’。它沿着墙角爬行，在你看不见的地方，把事情改写了一点点。",
                feedback_type=FeedbackType.DELAYED,
                delay_seconds=0,
                should_update_state=True,
                state_updates={"sanity": -5},
            )

    def _build_delayed_feedback_prompt(
        self, original_action: Mapping[str, JsonValue], game_state: Mapping[str, JsonValue]
    ) -> str:
        """构建延迟反馈提示词"""

        action_type = original_action.get("action_type", "unknown")
        target = original_action.get("target", "")
        description = original_action.get("description", "")
        violated_rule = original_action.get("violated_rule", "")

        scene_name = game_state.get("scene_name", "未知场景")
        player_status = game_state.get("player_status", {})

        return (
            f"场景: {scene_name}\n\n"
            "玩家之前的行动:\n"
            f"- 类型: {action_type}\n"
            f"- 目标: {target}\n"
            f"- 描述: {description}\n"
            f"- 违反的规则: {violated_rule}\n\n"
            "玩家当前状态:\n"
            f"- 理智: {player_status.get('sanity', 100)}/100\n"
            f"- 体力: {player_status.get('health', 100)}/100\n"
            f"- 位置: {player_status.get('location', '未知')}\n\n"
            "请生成延迟反馈，揭示之前行动导致的异常。"
        )

    async def generate_sensory_description(self, target: str, game_state: Mapping[str, JsonValue]) -> str:
        """生成某个目标的感官描写（纯文本）"""

        system_prompt = (
            "你是规则怪谈游戏的感官描写生成器。\n"
            "请为给定目标生成一段多感官描写（80-160字），只输出纯文本。\n"
            "要求：克制、细节、暗示，不要出现 emoji。"
        )
        scene_name = game_state.get("scene_name", "未知场景")
        background = game_state.get("background", "")

        prompt = (
            f"场景: {scene_name}\n"
            f"背景: {background}\n"
            f"目标: {target}\n\n"
            "请输出感官描写。"
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
            logger.error(f"生成感官描述失败: {e}", exc_info=True)
            return f"你看着{target}，细节像被刻意擦掉了一块，只剩下某种不合逻辑的空白。"
