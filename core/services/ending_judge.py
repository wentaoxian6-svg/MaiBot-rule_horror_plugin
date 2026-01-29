"""结局判定服务 - 判定游戏结局"""
from __future__ import annotations

import logging
from typing import Any, Optional

from ..llm.client import LLMClient
from ..game.models import Player, GameSession

logger = logging.getLogger(__name__)


class EndingType:
    """结局类型"""
    PERFECT = "perfect"      # 完美结局
    SUCCESS = "success"      # 成功结局
    CLEARED = "cleared"      # 通关结局
    FAILED = "failed"        # 失败结局


class EndingResult:
    """结局结果"""
    def __init__(
        self,
        ending_type: str,
        title: str,
        description: str,
        reasoning_analysis: str,
        truth_revealed: bool,
    ):
        self.ending_type = ending_type
        self.title = title
        self.description = description
        self.reasoning_analysis = reasoning_analysis
        self.truth_revealed = truth_revealed


class EndingJudge:
    """结局判定器"""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()

    async def judge_ending(
        self,
        session: GameSession,
        player: Player,
    ) -> EndingResult:
        """
        判定游戏结局
        
        Args:
            session: 游戏会话
            player: 玩家对象
        
        Returns:
            EndingResult 对象
        """
        logger.info(f"判定结局: {player.name}")
        
        # 构建判定上下文
        context = self._build_context(session, player)
        
        # 调用LLM判定结局
        ending_data = await self._judge_with_llm(context)
        
        # 创建结局结果
        result = EndingResult(
            ending_type=ending_data.get("ending_type", EndingType.FAILED),
            title=ending_data.get("title", "未知结局"),
            description=ending_data.get("description", ""),
            reasoning_analysis=ending_data.get("reasoning_analysis", ""),
            truth_revealed=ending_data.get("truth_revealed", False),
        )
        
        logger.info(f"结局判定完成: {result.ending_type} - {result.title}")
        return result

    def _build_context(self, session: GameSession, player: Player) -> dict[str, Any]:
        """构建判定上下文"""
        return {
            "scene_name": session.scene_name,
            "hidden_truth": session.hidden_truth,
            "win_condition": session.win_condition,
            "rules": [r.get("text", str(r)) for r in session.rules],
            "player_alive": player.status.value == "alive",
            "player_sanity": player.sanity,
            "player_health": player.health,
            "reasoning_history": player.reasoning_history,
            "action_history": [a.get("action", "") for a in player.action_history],
            "discovered_clues": [
                item.get("name", "") for item in player.inventory
                if item.get("type") == "clue"
            ],
            "has_cleared": session.has_cleared,
        }

    async def _judge_with_llm(self, context: dict[str, Any]) -> dict[str, Any]:
        """使用LLM判定结局"""
        system_prompt = """你是规则怪谈游戏的结局判定系统。你需要根据玩家的表现判定结局类型。

结局类型：
1. perfect（完美）：推理出隐藏真相 + 达成通关条件 + 解除规则怪谈根源
2. success（成功）：推理出隐藏真相 + 达成通关条件
3. cleared（通关）：达成通关条件，但未完全理解真相
4. failed（失败）：玩家死亡或未达成通关条件

判定标准：
- 检查玩家的推理是否接近隐藏真相
- 检查玩家是否达成通关条件
- 检查玩家的行动是否解决了根源问题

返回JSON格式：
{
    "ending_type": "perfect/success/cleared/failed",
    "title": "结局标题",
    "description": "结局描述（300-500字，详细描述结局场景）",
    "reasoning_analysis": "推理分析（评价玩家的推理过程）",
    "truth_revealed": true/false
}"""

        user_prompt = f"""场景：{context['scene_name']}
隐藏真相：{context['hidden_truth']}
通关条件：{context['win_condition']}

规则：
{chr(10).join(f"{i+1}. {r}" for i, r in enumerate(context['rules']))}

玩家状态：
- 存活：{context['player_alive']}
- 理智：{context['player_sanity']}/100
- 体力：{context['player_health']}/100
- 已通关：{context['has_cleared']}

玩家推理：
{chr(10).join(f"- {r}" for r in context['reasoning_history']) if context['reasoning_history'] else "无"}

发现的线索：
{chr(10).join(f"- {c}" for c in context['discovered_clues']) if context['discovered_clues'] else "无"}

关键行动：
{chr(10).join(f"- {a}" for a in context['action_history'][-10:]) if context['action_history'] else "无"}

请判定结局。"""

        try:
            response = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.7,
                max_tokens=1000,
            )
            
            return response.parse_json()
            
        except Exception as e:
            logger.error(f"判定结局失败: {e}")
            # 返回默认结局
            if not context['player_alive']:
                return {
                    "ending_type": EndingType.FAILED,
                    "title": "死亡结局",
                    "description": "你在恐怖中死去，真相永远埋藏在黑暗中。",
                    "reasoning_analysis": "未能完成推理。",
                    "truth_revealed": False,
                }
            elif context['has_cleared']:
                return {
                    "ending_type": EndingType.CLEARED,
                    "title": "通关结局",
                    "description": "你成功通关了，但似乎还有什么没有发现。",
                    "reasoning_analysis": "完成了基本目标。",
                    "truth_revealed": False,
                }
            else:
                return {
                    "ending_type": EndingType.FAILED,
                    "title": "失败结局",
                    "description": "你未能达成通关条件。",
                    "reasoning_analysis": "未能完成目标。",
                    "truth_revealed": False,
                }

    async def check_win_condition(
        self,
        session: GameSession,
        player: Player,
    ) -> bool:
        """
        检查是否达成通关条件
        
        Args:
            session: 游戏会话
            player: 玩家对象
        
        Returns:
            是否达成通关条件
        """
        context = {
            "win_condition": session.win_condition,
            "player_sanity": player.sanity,
            "player_health": player.health,
            "action_history": [a.get("action", "") for a in player.action_history[-5:]],
            "discovered_clues": [
                item.get("name", "") for item in player.inventory
                if item.get("type") == "clue"
            ],
        }
        
        system_prompt = """你是规则怪谈游戏的通关条件检查系统。

请根据通关条件和玩家的状态、行动、发现的线索，判断玩家是否达成了通关条件。

返回JSON格式：
{
    "achieved": true/false,
    "reason": "判断理由"
}"""

        user_prompt = f"""通关条件：{context['win_condition']}

玩家状态：
- 理智：{context['player_sanity']}/100
- 体力：{context['player_health']}/100

最近行动：
{chr(10).join(f"- {a}" for a in context['action_history']) if context['action_history'] else "无"}

发现的线索：
{chr(10).join(f"- {c}" for c in context['discovered_clues']) if context['discovered_clues'] else "无"}

请判断是否达成通关条件。"""

        try:
            response = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.3,
                max_tokens=300,
            )
            
            result = response.parse_json()
            achieved = result.get("achieved", False)
            
            if achieved:
                logger.info(f"达成通关条件: {result.get('reason', '')}")
            
            return achieved
            
        except Exception as e:
            logger.error(f"检查通关条件失败: {e}")
            return False
