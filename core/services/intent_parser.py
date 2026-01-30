"""自然语言意图解析

把玩家的自然语言输入解析为结构化的行动（PlayerAction）。

该文件曾被批量替换破坏（docstring/引号断裂、全角标点落入语法层等），此处按原意重写，保持对外接口：
- ActionType
- PlayerAction
- IntentParser.parse()
- IntentParser.is_valid_action()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..llm.client import LLMClient, get_default_max_tokens

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
    """玩家行动结构"""

    action_type: ActionType
    target: str
    description: str
    risk_level: float
    violates_rule: bool
    violated_rule: str | None = None
    consequence: str | None = None
    horror_element: str | None = None
    triggers_event: bool = False
    event_delay: int = 0
    event_description: str | None = None


class IntentParser:
    """使用 LLM 解析玩家意图"""

    def __init__(self, llm_client: LLMClient | None = None):
        self.llm_client: LLMClient = llm_client or LLMClient()

    async def parse(self, user_input: str, context: dict[str, Any]) -> PlayerAction:
        """解析玩家自然语言输入为结构化行动"""

        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(user_input, context)

        try:
            resp = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.7,
                max_tokens=get_default_max_tokens(),
            )
            data = resp.parse_json()
            return self._parse_to_action(data, fallback_description=user_input)
        except Exception as e:
            logger.error(f"解析玩家意图失败: {e}", exc_info=True)
            return PlayerAction(
                action_type=ActionType.OTHER,
                target="",
                description=user_input,
                risk_level=0.0,
                violates_rule=False,
            )

    def _build_system_prompt(self) -> str:
        """构建系统提示词"""

        return (
            "你是规则怪谈游戏的意图解析器。请分析玩家输入，输出结构化 JSON。\n\n"
            "需要判断：\n"
            "1) 行动类型（inspect/move/use_item/interact/communicate/wait/flee/attack/other）\n"
            "2) 行动目标（target）\n"
            "3) 风险等级 risk_level（0.0-1.0）\n"
            "4) 是否违反规则 violates_rule（true/false）以及 violated_rule（可为空）\n"
            "5) 可能后果 consequence（可为空）\n"
            "6) 恐怖元素提示 horror_element（可为空）\n"
            "7) 是否触发延迟事件 triggers_event + event_delay（秒）+ event_description（可为空）\n\n"
            "只返回 JSON（不要 markdown，不要其他文字）：\n"
            "{\n"
            '  "action_type": "other",\n'
            '  "target": "",\n'
            '  "description": "",\n'
            '  "risk_level": 0.0,\n'
            '  "violates_rule": false,\n'
            '  "violated_rule": "",\n'
            '  "consequence": "",\n'
            '  "horror_element": "",\n'
            '  "triggers_event": false,\n'
            '  "event_delay": 0,\n'
            '  "event_description": ""\n'
            "}"
        )

    def _build_user_prompt(self, user_input: str, context: dict[str, Any]) -> str:
        """构建用户提示词"""

        scene_name = context.get("scene_name", "未知场景")
        background = context.get("background", "")
        rules = context.get("rules", [])
        player_status = context.get("player_status", {})
        recent_actions = context.get("recent_actions", [])

        rules_lines: list[str] = []
        for i, rule in enumerate(rules, 1):
            if isinstance(rule, dict):
                text = rule.get("text", rule.get("content", str(rule)))
            else:
                text = str(rule)
            rules_lines.append(f"{i}. {text}")

        recent_lines = [f"- {a}" for a in recent_actions[-5:]]

        return (
            f"场景: {scene_name}\n"
            f"背景: {background}\n\n"
            "规则:\n"
            f"{chr(10).join(rules_lines) if rules_lines else '（无）'}\n\n"
            "玩家状态:\n"
            f"- 理智: {player_status.get('sanity', 100)}/100\n"
            f"- 体力: {player_status.get('health', 100)}/100\n"
            f"- 位置: {player_status.get('location', '未知')}\n\n"
            "最近行动:\n"
            f"{chr(10).join(recent_lines) if recent_lines else '（无）'}\n\n"
            f"玩家输入: {user_input}\n"
        )

    def _parse_to_action(self, data: dict[str, Any], fallback_description: str) -> PlayerAction:
        """将 LLM JSON 转为 PlayerAction，并做兜底"""

        at_raw = str(data.get("action_type", "other") or "other").lower().strip()
        try:
            action_type = ActionType(at_raw)
        except Exception:
            action_type = ActionType.OTHER

        target = str(data.get("target", "") or "").strip()
        description = str(data.get("description", "") or "").strip() or fallback_description

        def _to_bool(v: Any) -> bool:
            if isinstance(v, bool):
                return v
            if isinstance(v, str):
                return v.strip().lower() in {"1", "true", "yes", "y", "是"}
            return bool(v)

        def _to_float(v: Any, default: float = 0.0) -> float:
            try:
                return float(v)
            except Exception:
                return default

        def _to_int(v: Any, default: int = 0) -> int:
            try:
                return int(v)
            except Exception:
                return default

        risk_level = max(0.0, min(1.0, _to_float(data.get("risk_level", 0.0), 0.0)))
        violates_rule = _to_bool(data.get("violates_rule", False))

        violated_rule = str(data.get("violated_rule", "") or "").strip() or None
        consequence = str(data.get("consequence", "") or "").strip() or None
        horror_element = str(data.get("horror_element", "") or "").strip() or None

        triggers_event = _to_bool(data.get("triggers_event", False))
        event_delay = max(0, _to_int(data.get("event_delay", 0), 0))
        event_description = str(data.get("event_description", "") or "").strip() or None

        # 若没有违反规则，清理 violated_rule
        if not violates_rule:
            violated_rule = None

        return PlayerAction(
            action_type=action_type,
            target=target,
            description=description,
            risk_level=risk_level,
            violates_rule=violates_rule,
            violated_rule=violated_rule,
            consequence=consequence,
            horror_element=horror_element,
            triggers_event=triggers_event,
            event_delay=event_delay,
            event_description=event_description,
        )

    async def is_valid_action(self, user_input: str, context: dict[str, Any]) -> bool:
        """判断输入是否像一个“游戏行动”而非闲聊"""

        system_prompt = (
            "你是规则怪谈游戏的意图判断器。请判断玩家输入是否在尝试与游戏互动。\n"
            "只返回 JSON：{\"is_valid_action\": true/false, \"reason\": \"...\"}" 
        )

        prompt = (
            f"场景: {context.get('scene_name', '未知场景')}\n"
            f"玩家输入: {user_input}\n"
            "请判断是否为有效行动。"
        )

        try:
            resp = await self.llm_client.call(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.3,
                max_tokens=get_default_max_tokens(),
            )
            data = resp.parse_json()
            return bool(data.get("is_valid_action", False))
        except Exception as e:
            logger.error(f"判断行动有效性失败: {e}", exc_info=True)
            # 兜底：简单关键词判断
            keywords = [
                "拿", "取", "放", "扔", "用", "打开", "关闭", "检查", "询问",
                "进入", "离开", "触摸", "推", "拉", "按", "转", "看", "听",
                "等待", "躲", "逃", "攻击", "交谈", "观察", "搜索", "移动",
                "前往", "返回", "调查", "寻找", "翻找", "使用", "吃", "喝", "睡",
            ]
            if len(user_input.strip()) < 2:
                return False
            return any(k in user_input for k in keywords)
