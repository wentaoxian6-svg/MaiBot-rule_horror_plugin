"""自然语言意图解析器 - 解析玩家的自然语言输入为游戏行动"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from ..llm.client import LLMClient, LLMResponse

logger = logging.getLogger(__name__)


class ActionType(Enum):
    """行动类型枚举"""
    INSPECT = "inspect"
    MOVE = "move"
    USE_ITEM = "use_item"
    INTERACT = "interact"
    COMMUNICATE = "communicate"
    WAIT = "wait"
    FLEE = "flee"
    ATTACK = "attack"
    OTHER = "other"


@dataclass
class PlayerAction:
    """玩家行动数据类"""
    action_type: ActionType
    target: str
    description: str
    risk_level: float
    violates_rule: bool
    violated_rule: Optional[str] = None
    consequence: Optional[str] = None
    horror_element: Optional[str] = None
    triggers_event: bool = False
    event_delay: int = 0
    event_description: Optional[str] = None


class IntentParser:
    """自然语言意图解析器 - 使用 LLM 解析玩家意图"""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()

    async def parse(
        self,
        user_input: str,
        context: dict[str, Any],
    ) -> PlayerAction:
        """
        解析玩家的自然语言输入

        Args:
            user_input: 玩家输入的文本
            context: 游戏上下文，包含场景信息、玩家状态等

        Returns:
            PlayerAction 对象
        """
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(user_input, context)

        try:
            response = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.7,
                max_tokens=500,
            )

            result = response.parse_json()
            return self._parse_to_action(result)

        except Exception as e:
            logger.error(f"解析玩家意图失败: {e}")
            return PlayerAction(
                action_type=ActionType.OTHER,
                target="",
                description=user_input,
                risk_level=0.0,
                violates_rule=False,
            )

    def _build_system_prompt(self) -> str:
        """构建系统提示词"""
        return """你是一个规则怪谈游戏的意图解析器。你的任务是分析玩家的自然语言输入，判断他们想做什么。

请根据以下规则进行分析：
1. 判断玩家的行动类型（检查、移动、使用物品、互动、交流、等待、逃跑、攻击、其他）
2. 识别行动的目标对象
3. 评估行动的风险等级（0.0-1.0）
4. 判断是否违反了任何规则
5. 预测可能的后果
6. 识别恐怖元素
7. 判断是否触发延迟事件

请以 JSON 格式返回结果，格式如下：
{
    "action_type": "行动类型",
    "target": "目标对象",
    "description": "行动描述",
    "risk_level": 0.0-1.0,
    "violates_rule": false,
    "violated_rule": "违反的规则（如果有）",
    "consequence": "可能的后果",
    "horror_element": "恐怖元素描述",
    "triggers_event": false,
    "event_delay": 0,
    "event_description": "延迟事件描述"
}

注意：
- 风险等级越高，行动越危险
- 如果违反规则，violates_rule 为 true
- 恐怖元素应该营造紧张和不安的氛围
- 延迟事件用于在一段时间后触发异常"""

    def _build_user_prompt(self, user_input: str, context: dict[str, Any]) -> str:
        """构建用户提示词"""
        scene_name = context.get("scene_name", "未知场景")
        background = context.get("background", "")
        rules = context.get("rules", [])
        player_status = context.get("player_status", {})
        recent_actions = context.get("recent_actions", [])

        prompt = f"""当前场景：{scene_name}

场景背景：
{background}

当前规则：
"""
        for i, rule in enumerate(rules, 1):
            rule_text = rule.get("text", rule.get("content", str(rule)))
            prompt += f"{i}. {rule_text}\n"

        prompt += f"""
玩家状态：
- 理智：{player_status.get('sanity', 100)}/100
- 体力：{player_status.get('health', 100)}/100
- 位置：{player_status.get('location', '未知')}

最近行动：
"""
        for action in recent_actions[-5:]:
            prompt += f"- {action}\n"

        prompt += f"""
玩家输入：{user_input}

请分析玩家的意图并返回 JSON 格式的结果。"""

        return prompt

    def _parse_to_action(self, result: dict[str, Any]) -> PlayerAction:
        """将解析结果转换为 PlayerAction 对象"""
        try:
            action_type_str = result.get("action_type", "other")
            action_type = ActionType(action_type_str.lower())
        except (ValueError, AttributeError):
            action_type = ActionType.OTHER

        return PlayerAction(
            action_type=action_type,
            target=result.get("target", ""),
            description=result.get("description", ""),
            risk_level=float(result.get("risk_level", 0.0)),
            violates_rule=bool(result.get("violates_rule", False)),
            violated_rule=result.get("violated_rule"),
            consequence=result.get("consequence"),
            horror_element=result.get("horror_element"),
            triggers_event=bool(result.get("triggers_event", False)),
            event_delay=int(result.get("event_delay", 0)),
            event_description=result.get("event_description"),
        )

    async def is_valid_action(self, user_input: str, context: dict[str, Any]) -> bool:
        """
        判断输入是否是有效的游戏行动

        Args:
            user_input: 玩家输入
            context: 游戏上下文

        Returns:
            是否是有效的游戏行动
        """
        system_prompt = """你是一个规则怪谈游戏的意图判断器。你的任务是判断玩家的输入是否是在尝试与游戏场景互动。

请判断：
1. 玩家是否在尝试与场景互动？
2. 输入是否与游戏相关？

请以 JSON 格式返回：
{
    "is_valid_action": true/false,
    "reason": "判断理由"
}

注意：
- 如果玩家只是在闲聊、询问帮助、或与游戏无关的内容，返回 false
- 如果玩家在描述行动、询问场景、检查物品等，返回 true"""

        prompt = f"""当前场景：{context.get('scene_name', '未知场景')}

玩家输入：{user_input}

请判断这是否是有效的游戏行动。"""

        try:
            response = await self.llm_client.call(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.3,
                max_tokens=200,
            )

            result = response.parse_json()
            return bool(result.get("is_valid_action", False))

        except Exception as e:
            logger.error(f"判断行动有效性失败: {e}")
            return False
