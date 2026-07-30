"""结局判定服务 - 判定游戏结局"""
from __future__ import annotations

import logging

from ...common.models import JsonObject
from ..llm.client import LLMClient, get_default_max_tokens
from ..game.models import Player, GameSession

logger = logging.getLogger(__name__)


def _normalize_match_text(value: object) -> str:
    """归一化文本用于结构化条件匹配：去除全部空白并转小写，便于中文/英文子串包含判定。"""
    return "".join(str(value or "").split()).lower()


class EndingType:
    """结局类型"""
    PERFECT: str = "perfect"      # 完美结局
    SUCCESS: str = "success"      # 成功结局
    CLEARED: str = "cleared"      # 通关结局
    FAILED: str = "failed"        # 失败结局


# 通关级结局集合：受结构化硬门槛约束的结局类型
_PASS_ENDING_TYPES: set[str] = {EndingType.PERFECT, EndingType.SUCCESS, EndingType.CLEARED}


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

    def check_completion_conditions(self, session: GameSession, player: Player) -> bool:
        """确定性校验玩家是否结构化达成通关条件（Task 22 硬门槛）。

        LLM 不再判定"是否通关"，只判定叙事等级；是否"通关"完全由本方法返回值决定。
        任一已声明的结构化条件未满足即返回 False；全部满足才返回 True。
        """
        cc = session.completion_conditions
        if not isinstance(cc, dict) or not cc:
            # 无结构化条件即生成错误，直接抛错，禁止兜底为"已通关"或"未通关"
            raise RuntimeError("通关结构化条件未生成，无法进行结局判定")

        # 1. 关键物品：玩家背包需含全部 required_items
        required_items = cc.get("required_items")
        if isinstance(required_items, list) and required_items:
            inventory_names = [
                str(item.get("name", "") or "").strip()
                for item in (player.inventory or [])
                if isinstance(item, dict)
            ]
            for needed in required_items:
                needed_norm = _normalize_match_text(needed)
                if not needed_norm:
                    continue
                if not any(needed_norm in _normalize_match_text(name) for name in inventory_names):
                    return False

        # 2. 目标位置：玩家当前位置需与 required_location 双向包含匹配
        required_location = cc.get("required_location")
        if isinstance(required_location, str) and required_location.strip():
            loc_norm = _normalize_match_text(player.location)
            target_norm = _normalize_match_text(required_location)
            if not target_norm:
                return False
            if target_norm not in loc_norm and loc_norm not in target_norm:
                return False

        # 3. 目标动作：玩家行动历史需包含 required_action 文本
        required_action = cc.get("required_action")
        if isinstance(required_action, str) and required_action.strip():
            action_norm = _normalize_match_text(required_action)
            if not action_norm:
                return False
            action_texts = [
                str(a.get("action", "") or "").strip()
                for a in (player.action_history or [])
                if isinstance(a, dict)
            ]
            if not any(action_norm in _normalize_match_text(act) for act in action_texts):
                return False

        # 4. NPC 态度/状态：required_npc_state 中每项需匹配运行时 NPC 的 attitude 或 current_state
        required_npc_state = cc.get("required_npc_state")
        if isinstance(required_npc_state, dict) and required_npc_state:
            env_state = session.environment_state if isinstance(session.environment_state, dict) else {}
            npcs = env_state.get("npcs", [])
            if not isinstance(npcs, list):
                npcs = []
            for npc_key, expected_state in required_npc_state.items():
                expected_norm = _normalize_match_text(expected_state)
                key_norm = _normalize_match_text(npc_key)
                if not expected_norm:
                    continue
                npc_match = None
                for npc in npcs:
                    if not isinstance(npc, dict):
                        continue
                    npc_id_norm = _normalize_match_text(npc.get("npc_id"))
                    npc_name_norm = _normalize_match_text(npc.get("name"))
                    if key_norm and (key_norm == npc_id_norm or key_norm == npc_name_norm):
                        npc_match = npc
                        break
                if not npc_match:
                    return False
                attitude_norm = _normalize_match_text(npc_match.get("attitude"))
                current_state_norm = _normalize_match_text(npc_match.get("current_state"))
                if expected_norm not in attitude_norm and expected_norm not in current_state_norm:
                    return False

        return True

    def check_group_completion_conditions(self, session: GameSession) -> bool:
        """多人模式团队结构化通关校验：将全体玩家的物品/位置/行动合并后按同一硬门槛判定。"""
        cc = session.completion_conditions
        if not isinstance(cc, dict) or not cc:
            raise RuntimeError("通关结构化条件未生成，无法进行结局判定")

        players = list(session.players.values())
        if not players:
            return False

        merged_inventory: list[JsonObject] = []
        merged_actions: list[JsonObject] = []
        for p in players:
            merged_inventory.extend(p.inventory or [])
            merged_actions.extend(p.action_history or [])

        # 1. 关键物品：团队背包合并后需含全部 required_items
        required_items = cc.get("required_items")
        if isinstance(required_items, list) and required_items:
            inventory_names = [
                str(item.get("name", "") or "").strip()
                for item in merged_inventory
                if isinstance(item, dict)
            ]
            for needed in required_items:
                needed_norm = _normalize_match_text(needed)
                if not needed_norm:
                    continue
                if not any(needed_norm in _normalize_match_text(name) for name in inventory_names):
                    return False

        # 2. 目标位置：任一玩家抵达 required_location 即视为团队达成该条件
        required_location = cc.get("required_location")
        if isinstance(required_location, str) and required_location.strip():
            target_norm = _normalize_match_text(required_location)
            if not target_norm:
                return False
            if not any(
                (lambda loc_norm: target_norm in loc_norm or loc_norm in target_norm)(_normalize_match_text(p.location))
                for p in players
            ):
                return False

        # 3. 目标动作：团队行动历史合并后需包含 required_action
        required_action = cc.get("required_action")
        if isinstance(required_action, str) and required_action.strip():
            action_norm = _normalize_match_text(required_action)
            if not action_norm:
                return False
            action_texts = [
                str(a.get("action", "") or "").strip()
                for a in merged_actions
                if isinstance(a, dict)
            ]
            if not any(action_norm in _normalize_match_text(act) for act in action_texts):
                return False

        # 4. NPC 态度/状态：与单人数验逻辑一致，读取会话级 environment_state
        required_npc_state = cc.get("required_npc_state")
        if isinstance(required_npc_state, dict) and required_npc_state:
            env_state = session.environment_state if isinstance(session.environment_state, dict) else {}
            npcs = env_state.get("npcs", [])
            if not isinstance(npcs, list):
                npcs = []
            for npc_key, expected_state in required_npc_state.items():
                expected_norm = _normalize_match_text(expected_state)
                key_norm = _normalize_match_text(npc_key)
                if not expected_norm:
                    continue
                npc_match = None
                for npc in npcs:
                    if not isinstance(npc, dict):
                        continue
                    npc_id_norm = _normalize_match_text(npc.get("npc_id"))
                    npc_name_norm = _normalize_match_text(npc.get("name"))
                    if key_norm and (key_norm == npc_id_norm or key_norm == npc_name_norm):
                        npc_match = npc
                        break
                if not npc_match:
                    return False
                attitude_norm = _normalize_match_text(npc_match.get("attitude"))
                current_state_norm = _normalize_match_text(npc_match.get("current_state"))
                if expected_norm not in attitude_norm and expected_norm not in current_state_norm:
                    return False

        return True

    @staticmethod
    def _enforce_hard_gate(ending_data: JsonObject, conditions_met: bool) -> JsonObject:
        """后置硬门槛强制覆盖：LLM 若在未通关时返回通关级结局，强制降级为 failed。"""
        if not conditions_met:
            ending_type = str(ending_data.get("ending_type", "") or "")
            if ending_type in _PASS_ENDING_TYPES:
                logger.warning(
                    f"硬门槛拦截：结构化未通关但 LLM 返回 {ending_type}，强制降级为 failed"
                )
                ending_data["ending_type"] = EndingType.FAILED
                ending_data["reasoning_analysis"] = ""
                ending_data["truth_revealed"] = False
        return ending_data

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

        # 硬门槛：先做结构化通关校验，决定 LLM 是否被允许返回通关级结局
        conditions_met = self.check_completion_conditions(session, player)
        logger.info(f"结构化通关硬门槛: conditions_met={conditions_met}")

        # 构建判定上下文
        context = self._build_context(session, player)
        context["conditions_met"] = conditions_met

        # 调用LLM判定结局
        ending_data = await self._judge_with_llm(context)

        # 后置硬门槛强制覆盖：未结构化通关时，LLM 不得返回通关级结局
        ending_data = self._enforce_hard_gate(ending_data, conditions_met)

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

        # 硬门槛：先做团队结构化通关校验，决定 LLM 是否被允许返回通关级结局
        conditions_met = self.check_group_completion_conditions(session)
        logger.info(f"团队结构化通关硬门槛: conditions_met={conditions_met}")

        context = self._build_group_context(session)
        context["conditions_met"] = conditions_met
        ending_data = await self._judge_group_with_llm(context)

        # 后置硬门槛强制覆盖：未结构化通关时，LLM 不得返回通关级结局
        ending_data = self._enforce_hard_gate(ending_data, conditions_met)

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
        conditions_met = bool(context.get("conditions_met", False))

        system_prompt = """你是规则怪谈游戏的【多人总结局】判定系统。

你需要根据全体玩家的整体表现，给出一个共同结局（不是某个玩家的个人结局）。

重要：团队是否"结构化通关"由系统在判定前已确定性给出（见输入中的 conditions_met），你不负责判定是否通关，只负责在允许的范围内决定叙事等级。

结局类型:
1. perfect(完美): 团队推理出隐藏真相 + 已结构化通关 + 解除怪谈根源
2. success(成功): 团队推理出隐藏真相 + 已结构化通关
3. cleared(通关): 已结构化通关, 但未完全理解真相
4. failed(失败): 未结构化通关，或全员死亡/崩溃

硬门槛约束（必须遵守，违反将被系统强制覆盖）:
- 当 conditions_met 为 false 时：ending_type 只能是 failed，严禁返回 perfect/success/cleared
- 当 conditions_met 为 true 时：ending_type 可以是 perfect/success/cleared/failed，由团队是否推理出真相、是否解除根源、是否有人存活等叙事因素决定

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
- 结构化通关硬门槛(conditions_met):{conditions_met}

玩家列表:
{chr(10).join(players_lines) if players_lines else '无'}

发现的线索:
{chr(10).join(f"- {c}" for c in (context.get('discovered_clues') or [])) if context.get('discovered_clues') else '无'}

关键行动:
{chr(10).join(f"- {a}" for a in (context.get('recent_actions') or [])) if context.get('recent_actions') else '无'}

团队推理:
{chr(10).join(f"- {r}" for r in (context.get('reasoning_history') or [])) if context.get('reasoning_history') else '无'}

请判定【多人总结局】。注意：conditions_met 为 false 时只能返回 failed。"""

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

            # fallback：受硬门槛约束，未结构化通关时只能 failed
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

            if conditions_met and context.get("has_cleared"):
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
        conditions_met = bool(context.get("conditions_met", False))

        system_prompt = """你是规则怪谈游戏的结局判定系统。你需要根据玩家的表现判定结局类型。

重要：玩家是否"结构化通关"由系统在判定前已确定性给出（见输入中的 conditions_met），你不负责判定是否通关，只负责在允许的范围内决定叙事等级。

结局类型:
1. perfect(完美): 推理出隐藏真相 + 已结构化通关 + 解除规则怪谈根源
2. success(成功): 推理出隐藏真相 + 已结构化通关
3. cleared(通关): 已结构化通关, 但未完全理解真相
4. failed(失败): 玩家死亡或未结构化通关

硬门槛约束（必须遵守，违反将被系统强制覆盖）:
- 当 conditions_met 为 false 时：ending_type 只能是 failed，严禁返回 perfect/success/cleared
- 当 conditions_met 为 true 时：ending_type 可以是 perfect/success/cleared/failed，由玩家是否推理出真相、是否解除根源、是否存活等叙事因素决定

判定标准:
- 检查玩家的推理是否接近隐藏真相
- 检查玩家的行动是否解决了根源问题
- 是否通关以 conditions_met 为准，不要自行推断

输出要求（很重要）:
- `description` 只写结局叙事画面（150-250字），不要复盘推理过程，不要解释规则原理，不要评价玩家，字数必须控制在250字以内避免显示问题
- `reasoning_analysis` 只在 perfect/success/cleared 时填写；failed 时必须是空字符串
- failed（玩家死亡或未结构化通关）时：`truth_revealed` 必须为 false
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
- 结构化通关硬门槛(conditions_met):{conditions_met}

玩家推理:
{chr(10).join(f"- {r}" for r in context['reasoning_history']) if context['reasoning_history'] else "无"}

发现的线索:
{chr(10).join(f"- {c}" for c in context['discovered_clues']) if context['discovered_clues'] else "无"}

关键行动:
{chr(10).join(f"- {a}" for a in context['action_history'][-10:]) if context['action_history'] else "无"}

请判定结局。注意：conditions_met 为 false 时只能返回 failed。"""

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
            # 返回默认结局：受硬门槛约束，未结构化通关时只能 failed
            if not context['player_alive']:
                return {
                    "ending_type": EndingType.FAILED,
                    "title": "死亡结局",
                    "description": "你在恐怖中死去,真相永远埋藏在黑暗中.",
                    "reasoning_analysis": "",  # failed 结局必须为空
                    "truth_revealed": False,
                }
            elif conditions_met and context['has_cleared']:
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
    ) -> dict:
        """
        检查通关条件进度

        Args:
            session: 游戏会话
            player: 玩家对象

        Returns:
            {"achieved": bool, "near": bool, "reason": str}
            achieved 表示已达成通关条件；near 表示已备齐条件、只差一次行动完成目标
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

请根据通关条件和玩家的状态,行动,发现的线索,判断玩家的通关进度.

判定标准:
- achieved: 玩家已经实际完成了通关条件描述的达成状态
- near: 玩家已经完成通关所需的全部准备，只差下一次使用 `/rg 行动 <描述>` 直接完成目标。例如已站在出口门前但还没离开，或已找到目标人物且就在身边但还没带出去
- near 不能表示“还需要继续搜索、收集物品、推理、确认路线、处理多个步骤”或“理论上接近”；只要下一次行动不能直接完成目标，就必须为 false
- 不要把玩家尚未明确拥有或抵达的条件当作已经准备完成；拿不准时填 false
- 两者不能同时为 true;拿不准时都填 false,不要凭氛围猜测

返回JSON格式:
{
    "achieved": true/false,
    "near": true/false,
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


        response = await self.llm_client.call(
            prompt=user_prompt,
            system_prompt=system_prompt,
            temperature=0.3,
            max_tokens=get_default_max_tokens(),
        )

        result = response.parse_json()
        achieved = bool(result.get("achieved", False))
        near = bool(result.get("near", False)) and not achieved
        reason = str(result.get("reason", "") or "")

        if achieved:
            logger.info(f"达成通关条件: {reason}")
        elif near:
            logger.info(f"临近通关条件: {reason}")

        return {"achieved": achieved, "near": near, "reason": reason}
