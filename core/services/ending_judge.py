"""结局判定服务 - 判定游戏结局"""
from __future__ import annotations

import logging

from ...common.models import JsonObject
from ..llm.client import LLMClient, get_default_max_tokens
from ..game.models import Player, GameSession

logger = logging.getLogger(__name__)


class EndingType:
    """结局类型"""
    PERFECT: str = "perfect"      # 完美结局
    SUCCESS: str = "success"      # 成功结局
    CLEARED: str = "cleared"      # 通关结局
    FAILED: str = "failed"        # 失败结局


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
        self.ending_type: str = ending_type
        self.title: str = title
        self.description: str = description
        self.reasoning_analysis: str = reasoning_analysis
        self.truth_revealed: bool = truth_revealed


class EndingJudge:
    """结局判定器"""

    def __init__(self, llm_client: LLMClient | None = None):
        self.llm_client: LLMClient = llm_client or LLMClient()

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

    async def judge_group_ending(self, session: GameSession) -> EndingResult:
        """判定多人模式的总结局（全体玩家共同结局）。"""
        logger.info(f"判定总结局: {session.group_id}")

        context = self._build_group_context(session)
        ending_data = await self._judge_group_with_llm(context)

        result = EndingResult(
            ending_type=ending_data.get("ending_type", EndingType.FAILED),
            title=ending_data.get("title", "未知总结局"),
            description=ending_data.get("description", ""),
            reasoning_analysis=ending_data.get("reasoning_analysis", ""),
            truth_revealed=ending_data.get("truth_revealed", False),
        )

        logger.info(f"总结局判定完成: {result.ending_type} - {result.title}")
        return result

    def _build_group_context(self, session: GameSession) -> JsonObject:
        """构建多人总结局判定上下文。"""
        players = list(session.players.values())

        # 汇总线索（兼容不同 type 写法）
        discovered_clues: list[str] = []
        for p in players:
            for item in p.inventory or []:
                if not isinstance(item, dict):
                    continue
                itype = str(item.get("type", "") or "")
                if itype in {"clue", "线索", "Clue"}:
                    name = str(item.get("name", "") or "").strip()
                    if name and name not in discovered_clues:
                        discovered_clues.append(name)

        # 会话级线索（如果有）
        session_clues = getattr(session, "discovered_clues", None)
        if isinstance(session_clues, list) and session_clues:
            for clue in session_clues:
                if isinstance(clue, dict):
                    clue_name = str(clue.get("name", clue.get("description", "")) or "").strip()
                else:
                    clue_name = str(clue or "").strip()
                if clue_name and clue_name not in discovered_clues:
                    discovered_clues.append(clue_name)


        # 汇总行动/推理（保留最近）
        merged_actions: list[str] = []
        for p in players:
            for a in (p.action_history or [])[-8:]:
                if isinstance(a, dict):
                    act = str(a.get("action", "") or "").strip()
                    if act:
                        merged_actions.append(f"{p.name}: {act}")
        merged_actions = merged_actions[-25:]

        merged_reasoning: list[str] = []
        for p in players:
            for r in (p.reasoning_history or [])[-6:]:
                rr = str(r or "").strip()
                if rr:
                    merged_reasoning.append(f"{p.name}: {rr}")
        merged_reasoning = merged_reasoning[-25:]

        players_state: list[JsonObject] = []
        for p in players:
            players_state.append({
                "name": p.name,
                "alive": (p.status.value == "alive"),
                "sanity": p.sanity,
                "health": p.health,
                "location": getattr(p, "location", "") or "",
            })

        return {
            "scene_name": session.scene_name,
            "hidden_truth": session.hidden_truth,
            "win_condition": session.win_condition,
            "rules": [r.get("text", str(r)) for r in session.rules],
            "has_cleared": session.has_cleared,
            "players": players_state,
            "discovered_clues": discovered_clues,
            "recent_actions": merged_actions,
            "reasoning_history": merged_reasoning,
        }

    async def _judge_group_with_llm(self, context: JsonObject) -> JsonObject:
        """使用 LLM 判定多人总结局。"""
        system_prompt = """你是规则怪谈游戏的【多人总结局】判定系统。

你需要根据全体玩家的整体表现，给出一个共同结局（不是某个玩家的个人结局）。

结局类型:
1. perfect(完美): 团队推理出隐藏真相 + 达成通关条件 + 解除怪谈根源
2. success(成功): 团队推理出隐藏真相 + 达成通关条件
3. cleared(通关): 达成通关条件, 但未完全理解真相
4. failed(失败): 未达成通关条件，或全员死亡/崩溃

输出要求（很重要）:
- `description` 只写结局叙事画面（150-250字），必须以“你们/众人/全体”视角叙述，可点名 1-2 个玩家名字，但不要写成个人独角戏
- 不要复盘推理过程，不要解释规则原理，不要评价玩家
- `reasoning_analysis` 只在 perfect/success/cleared 时填写；failed 时必须是空字符串
- failed 时：`truth_revealed` 必须为 false
- 严禁使用任何 emoji

返回JSON格式:
{
    "ending_type": "perfect/success/cleared/failed",
    "title": "总结局标题",
    "description": "结局描述（纯叙事）",
    "reasoning_analysis": "推理分析（可为空）",
    "truth_revealed": true/false
}"""

        players_lines = []
        for p in (context.get("players") or []):
            if isinstance(p, dict):
                players_lines.append(
                    f"- {p.get('name','玩家')}: 存活={p.get('alive')} 理智={p.get('sanity')} 体力={p.get('health')} 位置={p.get('location')}"
                )

        user_prompt = f"""场景:{context.get('scene_name','')}
隐藏真相:{context.get('hidden_truth','')}
通关条件:{context.get('win_condition','')}

规则:
{chr(10).join(f"{i+1}. {r}" for i, r in enumerate(context.get('rules', []) or []))}

团队状态:
- 已通关:{context.get('has_cleared', False)}

玩家列表:
{chr(10).join(players_lines) if players_lines else '无'}

发现的线索:
{chr(10).join(f"- {c}" for c in (context.get('discovered_clues') or [])) if context.get('discovered_clues') else '无'}

关键行动:
{chr(10).join(f"- {a}" for a in (context.get('recent_actions') or [])) if context.get('recent_actions') else '无'}

团队推理:
{chr(10).join(f"- {r}" for r in (context.get('reasoning_history') or [])) if context.get('reasoning_history') else '无'}

请判定【多人总结局】。"""

        try:
            response = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.7,
                max_tokens=1500,
            )
            data_raw = response.parse_json()
            return data_raw if isinstance(data_raw, dict) else {}
        except Exception as e:
            logger.error(f"判定总结局失败: {e}")

            # fallback
            all_dead = True
            for p in (context.get("players") or []):
                if isinstance(p, dict) and p.get("alive"):
                    all_dead = False
                    break

            if all_dead:
                return {
                    "ending_type": EndingType.FAILED,
                    "title": "团灭结局",
                    "description": "你们在相互呼喊与回声里走散。灯光逐一熄灭，脚步声被更深的寂静吞没；当最后一个人停下呼吸时，整座场景像合上眼睛一样归于黑暗。",
                    "reasoning_analysis": "",
                    "truth_revealed": False,
                }

            if context.get("has_cleared"):
                return {
                    "ending_type": EndingType.CLEARED,
                    "title": "通关结局",
                    "description": "你们终于推开那道门，空气里残留的焦灼与潮湿同时退去。出口近在咫尺，却仍有某个细节像刺一样扎在记忆里——你们知道还有一层答案，被留在了黑暗中。",
                    "reasoning_analysis": "完成了基本目标。",
                    "truth_revealed": False,
                }

            return {
                "ending_type": EndingType.FAILED,
                "title": "失败结局",
                "description": "你们试图在规则的缝隙里寻找生路，但每一次选择都让场景更紧地收拢。门无声地合上，时间像被按下暂停——而你们仍被留在原地，等待下一次循环。",
                "reasoning_analysis": "",
                "truth_revealed": False,
            }


    def _build_context(self, session: GameSession, player: Player) -> JsonObject:
        """构建判定上下文"""
        # 从多个来源收集线索
        discovered_clues = []

        # 从玩家背包中获取线索
        for item in player.inventory:
            if item.get("type") == "clue":
                discovered_clues.append(item.get("name", ""))

        # 从会话中获取已发现线索（如果有）
        session_clues = getattr(session, "discovered_clues", None)
        if isinstance(session_clues, list) and session_clues:
            for clue in session_clues:
                if isinstance(clue, dict):
                    clue_name = str(clue.get("name", clue.get("description", "")) or "")
                else:
                    clue_name = str(clue or "")
                clue_name = clue_name.strip()
                if clue_name and clue_name not in discovered_clues:
                    discovered_clues.append(clue_name)


        return {
            "scene_name": session.scene_name,
            "hidden_truth": session.hidden_truth,
            "win_condition": session.win_condition,
            "rules": [r.get("text", str(r)) for r in session.rules],
            "player_alive": player.status.value == "alive",
            "player_sanity": player.sanity,
            "player_health": player.health,
            "player_fear": getattr(player, 'fear_level', 0),
            "player_anxiety": getattr(player, 'anxiety_level', 0),
            "player_stress": getattr(player, 'stress_level', 0),
            "player_fatigue": getattr(player, 'fatigue', 0),
            "reasoning_history": player.reasoning_history,
            "action_history": [a.get("action", "") for a in player.action_history],
            "discovered_clues": discovered_clues,
            "has_cleared": session.has_cleared,
        }

    async def _judge_with_llm(self, context: JsonObject) -> JsonObject:
        """使用LLM判定结局"""
        system_prompt = """你是规则怪谈游戏的结局判定系统。你需要根据玩家的表现判定结局类型。

结局类型:
1. perfect(完美): 推理出隐藏真相 + 达成通关条件 + 解除规则怪谈根源
2. success(成功): 推理出隐藏真相 + 达成通关条件
3. cleared(通关): 达成通关条件, 但未完全理解真相
4. failed(失败): 玩家死亡或未达成通关条件

判定标准:
- 检查玩家的推理是否接近隐藏真相
- 检查玩家是否达成通关条件
- 检查玩家的行动是否解决了根源问题

输出要求（很重要）:
- `description` 只写结局叙事画面（150-250字），不要复盘推理过程，不要解释规则原理，不要评价玩家，字数必须控制在250字以内避免显示问题
- `reasoning_analysis` 只在 perfect/success/cleared 时填写；failed 时必须是空字符串
- failed（玩家死亡或未达成通关条件）时：`truth_revealed` 必须为 false
- 严禁使用任何 emoji

返回JSON格式:
{
    "ending_type": "perfect/success/cleared/failed",
    "title": "结局标题",
    "description": "结局描述（纯叙事）",
    "reasoning_analysis": "推理分析（可为空）",
    "truth_revealed": true/false
}"""


        user_prompt = f"""场景:{context['scene_name']}
隐藏真相:{context['hidden_truth']}
通关条件:{context['win_condition']}

规则:
{chr(10).join(f"{i+1}. {r}" for i, r in enumerate(context['rules']))}

玩家状态:
- 存活:{context['player_alive']}
- 理智:{context['player_sanity']}/100
- 体力:{context['player_health']}/100
- 恐惧:{context['player_fear']}/100
- 焦虑:{context['player_anxiety']}/100
- 压力:{context['player_stress']}/100
- 疲劳:{context['player_fatigue']}/100
- 已通关:{context['has_cleared']}

玩家推理:
{chr(10).join(f"- {r}" for r in context['reasoning_history']) if context['reasoning_history'] else "无"}

发现的线索:
{chr(10).join(f"- {c}" for c in context['discovered_clues']) if context['discovered_clues'] else "无"}

关键行动:
{chr(10).join(f"- {a}" for a in context['action_history'][-10:]) if context['action_history'] else "无"}

请判定结局."""

        try:
            response = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.7,
                max_tokens=1500,  # 限制token数，约1000-1200个汉字
            )

            data_raw = response.parse_json()
            return data_raw if isinstance(data_raw, dict) else {}
            
        except Exception as e:
            logger.error(f"判定结局失败: {e}")
            # 返回默认结局
            if not context['player_alive']:
                return {
                    "ending_type": EndingType.FAILED,
                    "title": "死亡结局",
                    "description": "你在恐怖中死去,真相永远埋藏在黑暗中.",
                    "reasoning_analysis": "",  # failed 结局必须为空
                    "truth_revealed": False,
                }
            elif context['has_cleared']:
                return {
                    "ending_type": EndingType.CLEARED,
                    "title": "通关结局",
                    "description": "你成功通关了,但似乎还有什么没有发现.",
                    "reasoning_analysis": "完成了基本目标.",
                    "truth_revealed": False,
                }
            else:
                return {
                    "ending_type": EndingType.FAILED,
                    "title": "失败结局",
                    "description": "你未能达成通关条件.",
                    "reasoning_analysis": "",  # failed 结局必须为空
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
        
        system_prompt = """你是规则怪谈游戏的通关条件检查系统.

请根据通关条件和玩家的状态,行动,发现的线索,判断玩家是否达成了通关条件.

返回JSON格式:
{
    "achieved": true/false,
    "reason": "判断理由"
}"""

        action_history_raw = context.get("action_history", [])
        if not isinstance(action_history_raw, list):
            action_history_raw = []
        action_lines = [str(a).strip() for a in action_history_raw if str(a).strip()]

        clues_raw = context.get("discovered_clues", [])
        if not isinstance(clues_raw, list):
            clues_raw = []
        clue_lines = [str(c).strip() for c in clues_raw if str(c).strip()]

        user_prompt = f"""通关条件:{context['win_condition']}

玩家状态:
- 理智:{context['player_sanity']}/100
- 体力:{context['player_health']}/100

最近行动:
{chr(10).join(f"- {a}" for a in action_lines) if action_lines else "无"}

发现的线索:
{chr(10).join(f"- {c}" for c in clue_lines) if clue_lines else "无"}

请判断是否达成通关条件."""


        try:
            response = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.3,
                max_tokens=get_default_max_tokens(),
            )
            
            result = response.parse_json()
            achieved = result.get("achieved", False)
            
            if achieved:
                logger.info(f"达成通关条件: {result.get('reason', '')}")
            
            return achieved
            
        except Exception as e:
            logger.error(f"检查通关条件失败: {e}")
            return False
