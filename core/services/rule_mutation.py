"""规则变异服务 - 检查与触发规则变异（条件 + LLM 评估混合模式）。

本模块从 ``action_processor.py`` 抽离，职责单一：
- ``RuleMutationService.check_rule_mutation`` 检查是否需要规则变异（关键物品/预设条件）
- ``RuleMutationService.build_mutation_game_state`` 构建变异系统需要的游戏状态字典
- ``RuleMutationService.trigger_rule_mutation`` 触发规则变异（LLM 评估 + 元数据同步）

调用方（``ActionProcessor``）通过组合持有 ``RuleMutationService`` 实例，
原 ``_check_rule_mutation`` / ``_build_mutation_game_state`` / ``_trigger_rule_mutation``
方法保留为薄壳委托，避免破坏既有调用点。

Task 21：变异时同步更新 related_npc/hidden_meaning/condition/conditions 结构化元数据。
Task 29：变异成功后调用 mutation_system.trigger_mutation 更新冷却（不再绕过）。
"""
from __future__ import annotations

import copy
import difflib
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ...common import SanityThresholds
from ...common.models import JsonObject
from ..game.models import GameSession, Player, Rule
from ..llm.client import LLMClient, get_default_max_tokens

if TYPE_CHECKING:
    from .action_processor import ActionResult

logger = logging.getLogger(__name__)


class RuleMutationService:
    """规则变异计算服务。

    封装规则变异的「条件检查 → LLM 评估 → 规则替换 → 元数据同步 → 冷却更新」全流程。
    服务持有 ``llm_client`` 用于 LLM 调用，其余状态由方法参数传入。
    通过组合方式注入到 ``ActionProcessor`` 中，便于后续单元测试与替换实现。
    """

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm_client: LLMClient = llm_client

    async def check_rule_mutation(
        self,
        action: str,
        player: Player,
        session: GameSession,
        result: ActionResult,
        key_item_found: bool = False,
    ) -> None:
        """检查是否需要规则变异（条件+LLM评估混合模式）"""
        # 如果理智崩坏，不触发规则变异
        if player.sanity == SanityThresholds.LOW:
            return

        trigger_reasons: list[str] = []
        satisfied_conditions: list[str] = []

        # 1. 检查关键物品
        if key_item_found:
            trigger_reasons.append("关键物品")

        # 2. 检查预设条件
        from ...systems.rule_mutation_system import RuleMutationSystem
        game_state = self.build_mutation_game_state(session, player)
        game_time = 0
        if isinstance(session.time_manager, dict):
            game_time = int(session.time_manager.get("elapsed_minutes", 0) or 0)

        # 获取规则变异系统实例（从plugin通过session传递）
        mutation_system = getattr(session, '_rule_mutation_system', None)
        satisfied_condition_objects: list[Any] = []
        if mutation_system and isinstance(mutation_system, RuleMutationSystem):
            conditions = mutation_system.check_conditions(game_state, action, game_time)
            for condition in conditions:
                condition_desc = condition.description
                satisfied_conditions.append(condition_desc)
                trigger_reasons.append(f"条件触发：{condition_desc}")
                # Task 29：收集满足的 condition 对象，交给 trigger_rule_mutation 在变异成功后调用 trigger_mutation
                # 此处不再手动 triggered_conditions.add(...)，否则会绕过 trigger_mutation 导致 last_mutation_time 不更新
                satisfied_condition_objects.append(condition)

        # 3. 如果有触发原因，调用LLM评估
        if trigger_reasons:
            await self.trigger_rule_mutation(
                session, player,
                trigger_reason="；".join(trigger_reasons),
                satisfied_conditions=satisfied_conditions,
                satisfied_condition_objects=satisfied_condition_objects,
            )

    def build_mutation_game_state(self, session: GameSession, player: Player) -> JsonObject:
        """构建规则变异系统需要的游戏状态字典"""
        game_time = 0
        if isinstance(session.time_manager, dict):
            game_time = int(session.time_manager.get("elapsed_minutes", 0) or 0)

        # 构建玩家数据
        player_data: JsonObject = {
            "location": player.location,
            "action_history": [
                {"action": a.get("action", ""), "timestamp": a.get("timestamp", 0)}
                for a in player.action_history[-20:]  # 只取最近20条
            ],
            "inventory": player.inventory,
        }

        # 获取已访问位置记录
        visited_locations: dict[str, int] = {}
        for record in player.action_history:
            loc = record.get("location") if isinstance(record, dict) else None
            if loc:
                visited_locations[str(loc)] = visited_locations.get(str(loc), 0) + 1
        player_data["visited_locations"] = visited_locations

        return {
            "scene_name": session.scene_name,
            "scene_structure": session.scene_structure or {},
            "rules": [r.get("text", str(r)) for r in (session.rules or [])],
            "time_system": {"elapsed_minutes": game_time},
            "players": {str(player.player_id): player_data},
            "key_clues": list(getattr(session, 'discovered_clues', [])),
            "key_items_found": {
                item.get("name", ""): {"location": player.location, "timestamp": game_time}
                for item in player.inventory
                if isinstance(item, dict) and item.get("is_key_item")
            },
        }

    async def trigger_rule_mutation(
        self,
        session: GameSession,
        player: Player,
        trigger_reason: str = "随机",
        satisfied_conditions: list[str] | None = None,
        satisfied_condition_objects: list[Any] | None = None,
    ) -> JsonObject:
        """触发规则变异（条件+LLM评估混合模式）

        Args:
            session: 游戏会话
            player: 当前玩家
            trigger_reason: 触发原因描述
            satisfied_conditions: 满足的条件描述列表（条件触发模式）
            satisfied_condition_objects: 满足的 ``MutationCondition`` 对象列表，
                变异成功后用于调用 ``mutation_system.trigger_mutation`` 更新冷却（Task 29）

        Returns:
            包含变异信息的字典，如果不需要变异则返回空字典
        """
        if not session.rules:
            return {}

        # 收集所有玩家的行动和推理历史
        all_actions = []
        all_reasoning = []
        for p in session.players.values():
            all_actions.extend([a.get("action", "") for a in p.action_history])
            all_reasoning.extend(p.reasoning_history)

        # 构建条件提示文本
        conditions_text = ""
        if satisfied_conditions:
            conditions_text = "\n**已满足的条件**（这些条件表明可能需要规则变异）：\n" + "\n".join(f"- {c}" for c in satisfied_conditions)

        # 第一步：评估是否需要规则变异
        evaluation_prompt = f"""
你是规则怪谈的裁判。请根据以下信息，判断是否需要让规则发生变化。

触发原因：{trigger_reason}{conditions_text}
场景：{session.scene_name}
原始规则：{[r.get("text", str(r)) for r in session.rules]}
隐藏真相：{session.hidden_truth}
通关条件：{session.win_condition}
玩家行动记录：{all_actions[-10:] if len(all_actions) > 10 else all_actions}
玩家推理记录：{all_reasoning[-10:] if len(all_reasoning) > 10 else all_reasoning}

判断标准（根据剧情推进来判断是否需要规则变化）：
1. **贴合剧情推进**：规则变化应该与当前的剧情发展相匹配，在合适的时机出现
2. **发现的合理性**：玩家发现的物品、信息或触发的事件应该能够自然地引出规则变化
3. **增强紧张感**：规则变化应该能够增强游戏的紧张感和悬疑感，让玩家感到不安

**特别注意**：
- 仅仅发现普通物品（如笔记本、钥匙、工具等）不足以触发规则变化，除非这些物品包含了重要信息
- 仅仅进入新房间或新区域不足以触发规则变化，除非这个区域有特殊意义
- 仅仅进行常规探索或观察不足以触发规则变化
- 规则变化应该让玩家感到"原来如此"或"事情不对劲"，而非"怎么又变了"
- 规则变化不是必须的，如果当前剧情不需要规则变化，就不要强行变化
- **规则变化与玩家是否推理出规则的影响无关，玩家没推理出来就没推理出来，不要为了引导玩家而变化规则**

如果规则变化是必要的，请详细说明原因；如果不需要变化，请详细说明为什么当前不需要变化。

请返回JSON格式：
{{
  "should_mutate": "是/否",
  "reason": "详细说明是否需要规则变化的原因，必须具体说明玩家的行动或推理如何与剧情推进相关",
  "mutation_type": "如果需要变化，说明变化的类型（如：增加新规则/修改现有规则/规则冲突）"
}}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
        """

        try:
            evaluation_response = await self._llm_client.call(
                prompt=evaluation_prompt,
                temperature=0.7,
                max_tokens=get_default_max_tokens(),
            )
            evaluation_data = evaluation_response.parse_json()
        except Exception as e:
            logger.error(f"规则变异评估失败: {e}")
            return {}

        if evaluation_data.get("should_mutate") != "是":
            logger.info(f"评估结果：不需要规则变化 - {evaluation_data.get('reason', '')}")
            return {}

        logger.info(f"评估结果：需要规则变化 - {evaluation_data.get('reason', '')}")

        # 第二步：生成变异后的规则
        # Task 21：要求 LLM 一并更新结构化元数据（related_npc/hidden_meaning/condition/conditions），
        # 避免变异后新规则文本与旧元数据不一致导致后果处理按旧元数据执行
        mutation_prompt = f"""
基于以下原始规则和玩家至今的行动记录，模拟'场景意识'对玩家行为的反应，对其中1-2条规则进行细微但令人不安的篡改或增添一条'补充条款'，使其看起来像是早已存在但被忽视了。

触发原因：{trigger_reason}
变异类型：{evaluation_data.get('mutation_type', '未知')}
原始规则：{[r.get("text", str(r)) for r in session.rules]}
玩家行动记录：{all_actions[-5:] if len(all_actions) > 5 else all_actions}
玩家推理记录：{all_reasoning[-5:] if len(all_reasoning) > 5 else all_reasoning}

要求：
1. 对1-2条规则进行细微的篡改或补充
2. 篡改应该令人不安，暗示规则本身是有意识的、会学习的
3. 篡改后的规则应该看起来像是原本就存在，只是之前被玩家忽视了
4. **规则变化方式**：
   - 可以让新规则与原本的旧规则冲突（如：原本说"禁止进入404室"，现在改为"必须进入404室"）
   - 可以更改条件（如：原本"禁止在22:00-06:00期间离开房间"，现在改为"禁止在24:00-08:00期间离开房间"）
   - 可以增加新的限制或放宽限制
   - 要贴合剧情推进，让玩家感到规则在根据他们的行为调整
5. **新规则必须简洁、直接，每条规则严格控制在30-50字之间**
6. **只说明禁止、允许或要求做的行为，不解释原因**
7. **使用标准格式：禁止XX / 当XX时，必须XX / 只有XX时才能XX / 必须XX / 严禁XX**
8. **严禁在规则中包含"如果"、"鉴于"、"因为"、"所以"等解释性词语**
9. **严禁在规则中包含多个句子或分号，每条规则只能是一个简单句**
10. **严禁在规则中添加背景故事或额外说明**
11. **Task 21 结构化元数据同步（重要）**：每条变异后的规则必须一并输出与新文本一致的结构化元数据，
    不能沿用旧规则的元数据。若某字段对新规则不适用，填 null 或空数组：
    - `related_npc`：新规则关联的 NPC 名称（无关联填 null）
    - `hidden_meaning`：新规则的隐藏含义（无隐藏含义填 null）
    - `condition`：新规则的触发条件描述（无触发条件填 null）
    - `conditions`：新规则的结构化违规条件对象，供运行时确定性匹配
      - `time_window`：违规时间窗如 "22:00-04:00"（无时间约束填 null）
      - `location`：违规位置如 "走廊"（无位置约束填 null）
      - `action_keywords`：触发违规的动作关键词数组（无动作约束填 []）
      - `precondition`：违规前置状态如 "持有手电筒"（无前置状态填 null）
12. 返回格式：
{{
  "mutated_rules": [
    {{
      "text": "新规则文本",
      "related_npc": "关联NPC名称或null",
      "hidden_meaning": "新规则的隐藏含义或null",
      "condition": "新规则的触发条件描述或null",
      "conditions": {{
        "time_window": "22:00-04:00或null",
        "location": "走廊或null",
        "action_keywords": ["跑", "大声"],
        "precondition": "持有手电筒或null"
      }}
    }}
  ],
  "hint": "一句暗示规则已变的低语（如：墙上的文字似乎更潦草了）"
}}

请仅返回JSON，不要包含任何其他文字。**重要：不要使用任何emoji表情符号。**
        """

        try:
            mutation_response = await self._llm_client.call(
                prompt=mutation_prompt,
                temperature=0.8,
                max_tokens=get_default_max_tokens(),
            )
            mutation_data = mutation_response.parse_json()

            mutated_rules = mutation_data.get("mutated_rules", [])
            hint = mutation_data.get("hint", "")

            if mutated_rules:
                # 旧规则深拷贝写入规则历史，保留完整结构化字段（rule_type/related_npc/hidden_meaning/version 等）
                old_rules_dicts = [Rule.from_dict(rule, idx).to_dict() for idx, rule in enumerate(session.rules)]
                session.rule_history.append({
                    "time": datetime.now().isoformat(),
                    "reason": trigger_reason,
                    "rules": copy.deepcopy(old_rules_dicts),
                })

                old_rules = [r.get("text", str(r)) for r in session.rules]

                # Task 21：变异时同步更新 related_npc/hidden_meaning/condition/conditions 等结构化元数据
                # LLM 返回对象格式（含元数据）；若返回字符串格式则按"清空走通用路径"处理
                def _normalize_meta(value: Any) -> str | None:
                    """将 LLM 返回的元数据值规范化：null/空字符串 → None。"""
                    if value is None:
                        return None
                    s = str(value).strip()
                    if not s or s.lower() == "null":
                        return None
                    return s

                new_rules_list: list[JsonObject] = []
                used_old_indices: set[int] = set()

                for mutated_item in mutated_rules:
                    # Task 21：支持对象格式（含结构化元数据）和字符串格式（仅文本，元数据清空）
                    if isinstance(mutated_item, dict):
                        mutated_text = str(mutated_item.get("text", "") or "").strip()
                        new_related_npc = _normalize_meta(mutated_item.get("related_npc"))
                        new_hidden_meaning = _normalize_meta(mutated_item.get("hidden_meaning"))
                        new_condition = _normalize_meta(mutated_item.get("condition"))
                        new_conditions_raw = mutated_item.get("conditions")
                        new_conditions = new_conditions_raw if isinstance(new_conditions_raw, dict) else {}
                    else:
                        mutated_text = str(mutated_item or "").strip()
                        new_related_npc = None
                        new_hidden_meaning = None
                        new_condition = None
                        new_conditions = {}

                    if not mutated_text:
                        continue

                    # 在未匹配的旧规则中寻找最相似的一条
                    best_idx = -1
                    best_ratio = 0.0
                    for idx, old_rule_dict in enumerate(old_rules_dicts):
                        if idx in used_old_indices:
                            continue
                        old_text = str(old_rule_dict.get("text", ""))
                        ratio = difflib.SequenceMatcher(None, old_text, mutated_text).ratio()
                        if ratio > best_ratio:
                            best_ratio = ratio
                            best_idx = idx

                    # 相似度 >= 0.3 视为对旧规则的变异；否则视为新增"补充条款"规则
                    if best_idx >= 0 and best_ratio >= 0.3:
                        used_old_indices.add(best_idx)
                        old_rule = old_rules_dicts[best_idx]
                        # 浅拷贝即可：规则字典内均为基本类型值
                        new_rule = dict(old_rule)
                        # 更新文本相关字段
                        new_rule["text"] = mutated_text
                        new_rule["surface_text"] = mutated_text
                        new_rule["constraint"] = mutated_text
                        # Task 21：同步更新结构化元数据，避免新规则文本配旧元数据导致后果处理按旧元数据执行
                        new_rule["related_npc"] = new_related_npc
                        new_rule["deep_meaning"] = new_hidden_meaning or ""
                        new_rule["hidden_meaning"] = new_hidden_meaning or ""
                        new_rule["condition"] = new_condition or ""
                        new_rule["conditions"] = new_conditions
                        # version 递增：旧规则无 version（0）则新版本从 1 开始
                        old_version = old_rule.get("version", 0)
                        if isinstance(old_version, (int, float)) and not isinstance(old_version, bool):
                            new_rule["version"] = int(old_version) + 1
                        else:
                            new_rule["version"] = 1
                        new_rules_list.append(new_rule)
                    else:
                        # 新增"补充条款"规则：结构化字段从 LLM 元数据初始化，version 从 1 开始
                        new_rules_list.append({
                            "rule_id": f"rule_mutated_{len(new_rules_list)}",
                            "surface_text": mutated_text,
                            "text": mutated_text,
                            "constraint": mutated_text,
                            "source": "mutation",
                            "source_type": "mutation",
                            "truth_status": "mutated",
                            "version": 1,
                            "related_npc": new_related_npc,
                            "deep_meaning": new_hidden_meaning or "",
                            "hidden_meaning": new_hidden_meaning or "",
                            "condition": new_condition or "",
                            "conditions": new_conditions,
                        })

                # 统一通过 Rule 归一化，确保结构化字段完整
                session.rules = [Rule.from_dict(rule, idx).to_dict() for idx, rule in enumerate(new_rules_list)]

                # 记录变异
                session.add_rule_mutation(
                    old_rule=str(old_rules),
                    new_rule=str(mutated_rules),
                    reason=trigger_reason,
                )

                # Task 29：变异成功后调用 mutation_system.trigger_mutation 更新冷却
                # 此前 _check_rule_mutation 手动 triggered_conditions.add(...) 绕过了 trigger_mutation，
                # 导致 last_mutation_time 不更新、mutation_cooldown 失效
                mutation_system = getattr(session, '_rule_mutation_system', None)
                if mutation_system is not None and satisfied_condition_objects:
                    game_time = 0
                    if isinstance(session.time_manager, dict):
                        game_time = int(session.time_manager.get("elapsed_minutes", 0) or 0)
                    for condition in satisfied_condition_objects:
                        mutation_system.trigger_mutation(
                            condition=condition,
                            game_state=self.build_mutation_game_state(session, player),
                            game_time=game_time,
                            triggered_by=[player.player_id],
                        )

                logger.info(f"规则变异成功: {old_rules} -> {mutated_rules}")

                return {
                    "hint": hint,
                    "old_rules": old_rules,
                    "new_rules": mutated_rules,
                }

        except Exception as e:
            logger.error(f"规则变异生成失败: {e}")

        return {}
