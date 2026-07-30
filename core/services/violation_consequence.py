"""违规后果服务 - 处理规则违反的后果与确定性违规匹配。

本模块从 ``action_processor.py`` 抽离，职责单一：
- ``ViolationConsequenceService.handle_violation_consequences`` 统一处理违规后果
- ``ViolationConsequenceService.build_violation_context`` 构建违规上下文
- ``ViolationConsequenceService.handle_general_violation`` 一般违规 → ImmersiveFeedback
- ``ViolationConsequenceService.schedule_delayed_feedback`` 延迟反馈入队（Task 8：不丢失）
- ``ViolationConsequenceService.update_npc_attitudes`` 更新 NPC 态度向量
- ``ViolationConsequenceService.check_hunt_trigger`` / ``trigger_hunt_event`` 追杀事件触发（Task 19）
- ``ViolationConsequenceService.handle_double_edged_violation`` 双刃剑规则处理
- ``ViolationConsequenceService.check_structured_violations`` Task 20 确定性违规匹配
- ``ViolationConsequenceService.is_in_time_window`` / ``check_precondition`` 违规条件辅助判定
- ``ViolationConsequenceService.apply_feedback_state_updates`` 应用沉浸式反馈状态变化

调用方（``ActionProcessor``）通过组合持有 ``ViolationConsequenceService`` 实例，
原 ``_handle_violation_consequences`` 等方法保留为薄壳委托，避免破坏既有调用点。

NPC 查找方法（``_find_runtime_npc`` / ``_get_runtime_npc_memory``）由 ``ActionProcessor``
持有并作为回调注入，因为它们同时被主流程（``_build_hunt_context``）使用，不在本服务内重复实现。
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Any

from ...common import SanityThresholds
from ...common.constants import (
    AnxietyThresholds,
    FearThresholds,
    HealthThresholds,
    StressThresholds,
)
from ...common.models import JsonObject
from ...systems.npc_system import NPCMemory
from ..game.models import GameSession, Player, PlayerStatus, Rule
from ..llm.client import LLMClient, get_default_max_tokens

logger = logging.getLogger(__name__)


class ViolationConsequenceService:
    """违规后果处理服务。

    封装规则违反后的后果处理全流程：确定性违规匹配、沉浸式反馈、NPC 态度更新、
    追杀事件触发、双刃剑规则处理。服务持有 ``llm_client`` 用于 LLM 调用，
    ``message_sender`` / ``session_saver`` 用于消息推送与存档，
    NPC 查找方法以回调形式注入（避免与主流程重复实现）。
    通过组合方式注入到 ``ActionProcessor`` 中。
    """

    def __init__(
        self,
        llm_client: LLMClient,
        message_sender: Callable[[str], Awaitable[bool]] | None,
        session_saver: Callable[[str, GameSession], Awaitable[None]] | None,
        find_runtime_npc: Callable[[GameSession, str], JsonObject | None],
        get_runtime_npc_memory: Callable[[GameSession, str], tuple[JsonObject | None, NPCMemory | None]],
    ) -> None:
        self._llm_client: LLMClient = llm_client
        self._message_sender = message_sender
        self._session_saver = session_saver
        self._find_runtime_npc = find_runtime_npc
        self._get_runtime_npc_memory = get_runtime_npc_memory

    # ------------------------------------------------------------------
    # Task 20：结构化违规条件确定性匹配
    # ------------------------------------------------------------------

    def check_structured_violations(
        self, action: str, player: Player, session: GameSession
    ) -> list[dict[str, Any]]:
        """Task 20：基于规则的结构化条件做确定性违规匹配。

        遍历 ``session.rules``，对每条带有非空 ``conditions`` 的规则，检查
        ``time_window``/``location``/``action_keywords``/``precondition`` 是否全部满足。
        全部满足即判定为违规，返回违规规则列表。

        确定性匹配保证同一行为多次执行判定一致（不会这次罚下次不罚），
        LLM 仅负责叙事化后果，不再判定违规事实。

        Args:
            action: 玩家行动文本
            player: 当前玩家
            session: 游戏会话

        Returns:
            违规规则列表，每项含 ``rule_id``/``surface_text``/``condition_desc``
        """
        violations: list[dict[str, Any]] = []
        action_text = str(action or "").strip()
        if not action_text:
            return violations

        # 当前游戏内时钟：默认 22:00 开局（恐怖游戏惯例），可被 time_manager.start_hour 覆盖
        time_manager = session.time_manager if isinstance(session.time_manager, dict) else {}
        elapsed_minutes = int(time_manager.get("elapsed_minutes", 0) or 0)
        start_hour_raw = time_manager.get("start_hour", 22)
        try:
            start_hour = int(start_hour_raw)
        except (ValueError, TypeError):
            start_hour = 22
        current_hour = (start_hour + elapsed_minutes / 60.0) % 24.0

        for idx, rule_dict in enumerate(session.rules or []):
            if not isinstance(rule_dict, dict):
                continue
            conditions = rule_dict.get("conditions")
            if not isinstance(conditions, dict) or not conditions:
                continue

            # 逐维度检查：任一维度不满足则跳过该规则（该规则不构成违规）
            # 1. time_window：如 "22:00-04:00"（支持跨午夜）
            time_window = conditions.get("time_window")
            if isinstance(time_window, str) and time_window.strip():
                if not self.is_in_time_window(current_hour, time_window):
                    continue

            # 2. location：玩家位置需匹配（子串匹配，兼容"二楼的走廊"含"走廊"）
            loc_cond = conditions.get("location")
            if isinstance(loc_cond, str) and loc_cond.strip():
                if loc_cond.strip() not in str(player.location or "").strip():
                    continue

            # 3. action_keywords：行动需包含至少一个关键词
            kw_cond = conditions.get("action_keywords")
            if isinstance(kw_cond, list) and kw_cond:
                keywords = [str(k).strip() for k in kw_cond if str(k).strip()]
                if not any(k in action_text for k in keywords):
                    continue

            # 4. precondition：前置状态检查（基于玩家背包/状态字段）
            pre_cond = conditions.get("precondition")
            if isinstance(pre_cond, str) and pre_cond.strip():
                if not self.check_precondition(player, pre_cond):
                    continue

            # 所有维度均满足 → 确定性判定为违规
            rule = Rule.from_dict(rule_dict, idx)
            violations.append({
                "rule_id": rule.rule_id,
                "surface_text": rule.surface_text,
                "condition_desc": rule.condition or rule.surface_text,
            })

        return violations

    @staticmethod
    def is_in_time_window(current_hour: float, time_window: str) -> bool:
        """检查当前小时是否落在时间窗内（支持跨午夜，如 22:00-04:00）。

        Args:
            current_hour: 当前游戏内小时（0~24 浮点）
            time_window: 形如 "22:00-04:00" 的时间窗字符串

        Returns:
            是否在时间窗内
        """
        try:
            parts = time_window.split("-")
            if len(parts) != 2:
                return False
            start_str, end_str = parts[0].strip(), parts[1].strip()
            start_h_str, _, start_m_str = start_str.partition(":")
            end_h_str, _, end_m_str = end_str.partition(":")
            start = float(start_h_str) + (float(start_m_str) / 60.0 if start_m_str else 0.0)
            end = float(end_h_str) + (float(end_m_str) / 60.0 if end_m_str else 0.0)
        except (ValueError, AttributeError):
            return False

        if start == end:
            return True
        if start < end:
            # 同日时间窗，如 08:00-18:00
            return start <= current_hour < end
        # 跨午夜时间窗，如 22:00-04:00
        return current_hour >= start or current_hour < end

    @staticmethod
    def check_precondition(player: Player, precondition: str) -> bool:
        """检查玩家是否满足前置状态（基于背包物品/状态字段做关键词匹配）。

        支持的前置状态写法：
        - "持有XX"/"拥有XX"：背包包含含 XX 的物品名
        - "未持有XX"/"没有XX"：背包不包含含 XX 的物品名
        - "穿着XX"/"穿戴XX"：玩家 state 字段包含 XX
        - 其他无法确定性判断的前置状态：不做约束（返回 True），由其他维度决定

        Args:
            player: 当前玩家
            precondition: 前置状态描述

        Returns:
            是否满足前置状态
        """
        pre = str(precondition or "").strip()
        if not pre:
            return True

        inventory_names: list[str] = []
        for item in player.inventory:
            if isinstance(item, dict):
                name = str(item.get("name", "") or "").strip()
                if name:
                    inventory_names.append(name)

        # 持有类：检查背包是否包含某物品
        if pre.startswith("持有") or pre.startswith("拥有"):
            item_keyword = pre[2:].strip()
            if not item_keyword:
                return True
            return any(item_keyword in name for name in inventory_names)

        # 未持有类：检查背包是否不包含某物品
        if pre.startswith("未持有"):
            item_keyword = pre[3:].strip()
            if not item_keyword:
                return True
            return not any(item_keyword in name for name in inventory_names)
        if pre.startswith("没有"):
            item_keyword = pre[2:].strip()
            if not item_keyword:
                return True
            return not any(item_keyword in name for name in inventory_names)

        # 穿着类：检查玩家 state 字段是否包含关键词
        if pre.startswith("穿着") or pre.startswith("穿戴"):
            keyword = pre[2:].strip()
            if not keyword:
                return True
            state = str(getattr(player, "state", "") or "")
            return keyword in state

        # 其他无法确定性判断的前置状态：不做约束，由其他维度决定
        return True

    # ------------------------------------------------------------------
    # 沉浸式反馈状态更新
    # ------------------------------------------------------------------

    def apply_feedback_state_updates(self, player: Player, updates: Mapping[str, Any]) -> None:
        """应用沉浸式反馈带来的额外状态变化。"""
        sanity_delta = updates.get("sanity")
        if isinstance(sanity_delta, int):
            player.sanity = max(SanityThresholds.MIN, min(SanityThresholds.MAX, player.sanity + sanity_delta))

        health_delta = updates.get("health")
        if isinstance(health_delta, int):
            player.health = max(HealthThresholds.MIN, min(HealthThresholds.MAX, player.health + health_delta))

        fear_delta = updates.get("fear_level")
        if isinstance(fear_delta, int):
            player.fear_level = max(FearThresholds.MIN, min(FearThresholds.MAX, player.fear_level + fear_delta))

        anxiety_delta = updates.get("anxiety_level")
        if isinstance(anxiety_delta, int):
            player.anxiety_level = max(AnxietyThresholds.MIN, min(AnxietyThresholds.MAX, player.anxiety_level + anxiety_delta))

        stress_delta = updates.get("stress_level")
        if isinstance(stress_delta, int):
            player.stress_level = max(StressThresholds.MIN, min(StressThresholds.MAX, player.stress_level + stress_delta))

        location = updates.get("location")
        if isinstance(location, str) and location.strip():
            player.location = location.strip()

        if player.health <= 0:
            player.status = PlayerStatus.DEAD

    # ------------------------------------------------------------------
    # 违规后果主流程
    # ------------------------------------------------------------------

    async def handle_violation_consequences(
        self,
        player: Player,
        session: GameSession,
        violated_rule: str,
        action: str,
        group_id: str = "",
    ) -> None:
        """统一处理违规后果

        根据规则类型和剧情上下文，调用不同的处理系统：
        - 区域违规：调用 EnvironmentEvolutionSystem
        - 一般违规：调用 ImmersiveFeedback
        - 同时更新NPC态度
        """
        logger.info(f"处理违规后果: 玩家={player.name}, 规则={violated_rule}")

        try:
            # 1. 收集违规上下文
            violation_context = self.build_violation_context(
                player, session, violated_rule, action
            )

            await self.handle_general_violation(player, session, violation_context, group_id)

            await self.update_npc_attitudes(player, session, violation_context)

            await self.check_hunt_trigger(player, session, violation_context)

            rule_info = violation_context.get("rule_info")
            if isinstance(rule_info, dict) and rule_info.get("rule_type") == "double_edged":
                await self.handle_double_edged_violation(
                    player, session, violation_context["rule_text"]
                )

            logger.info(f"违规后果处理完成: {player.name}")

        except Exception as e:
            logger.error(f"处理违规后果时出错: {e}", exc_info=True)
            # 错误不应影响主流程

    def build_violation_context(
        self,
        player: Player,
        session: GameSession,
        violated_rule: str,
        action: str
    ) -> dict[str, Any]:
        """构建违规上下文"""
        # 获取规则信息
        rule_info = None
        for rule in session.rules:
            if isinstance(rule, dict) and rule.get("text") == violated_rule:
                rule_info = rule
                break

        # 计算近期违规次数
        recent_violations = 0
        for act in player.action_history[-10:]:
            if isinstance(act, dict) and act.get("violated_rule"):
                recent_violations += 1

        # 判断是否为特殊位置
        is_special = False
        scene_structure = session.scene_structure or {}
        special_areas = scene_structure.get("special_areas", []) if isinstance(scene_structure, dict) else []
        if isinstance(special_areas, list):
            for area in special_areas:
                if isinstance(area, str) and area in player.location:
                    is_special = True
                    break

        return {
            "rule_text": violated_rule,
            "rule_info": rule_info,
            "action_description": action,
            "player_health": player.health,
            "player_sanity": player.sanity,
            "player_location": player.location,
            "recent_violations": recent_violations,
            "is_special_location": is_special,
            "scene_name": session.scene_name,
        }

    async def handle_general_violation(
        self,
        player: Player,
        session: GameSession,
        violation_context: dict[str, Any],
        group_id: str = "",
    ) -> None:
        """处理一般违规 - 调用 ImmersiveFeedback"""
        try:
            from ..services.immersive_feedback import ImmersiveFeedback, FeedbackType

            feedback_system = ImmersiveFeedback(self._llm_client)

            # 构建action和game_state
            action = {
                "action_type": "violation",
                "target": player.location,
                "description": violation_context["action_description"],
                "violates_rule": True,
                "violated_rule": violation_context["rule_text"],
                "risk_level": 0.8,
            }

            game_state = {
                "scene_name": session.scene_name,
                "background": session.background,
                "player_status": {
                    "sanity": player.sanity,
                    "health": player.health,
                    "location": player.location,
                }
            }

            # 生成即时反馈
            response = await feedback_system.respond(action, game_state)

            # 如果有延迟反馈，安排延迟发送
            if response.feedback_type == FeedbackType.DELAYED and response.delay_seconds > 0:
                import asyncio
                asyncio.create_task(
                    self.schedule_delayed_feedback(
                        player, session, action, game_state, response.delay_seconds, group_id
                    )
                )

            # 应用状态更新
            if response.should_update_state and response.state_updates:
                self.apply_feedback_state_updates(player, response.state_updates)

            logger.info(f"一般违规反馈生成成功: {player.name}, 类型={response.feedback_type.value}")

        except Exception as e:
            logger.error(f"一般违规处理失败: {e}")

    async def schedule_delayed_feedback(
        self,
        player: Player,
        session: GameSession,
        action: dict[str, Any],
        game_state: dict[str, Any],
        delay_seconds: int,
        group_id: str,
    ) -> None:
        """安排延迟反馈 - 写入 session.pending_feedbacks 队列，由 process_action 在到期时触发。

        延迟为 0 时立即生成并应用反馈（保留原即时触发语义）；
        否则按"当前 elapsed_minutes + 延迟分钟数"计算触发时间点，
        将反馈内容与目标玩家写入队列，等待后续行动检查时追加到结果。
        """
        try:
            from ..services.immersive_feedback import ImmersiveFeedback

            # 构造当前玩家状态快照，用于生成延迟反馈内容
            current_state = {
                "scene_name": session.scene_name,
                "background": session.background,
                "player_status": {
                    "sanity": player.sanity,
                    "health": player.health,
                    "location": player.location,
                }
            }

            feedback_system = ImmersiveFeedback(self._llm_client)
            delayed_response = await feedback_system.generate_delayed_feedback(
                action, current_state
            )

            # 应用即时状态更新（若有）
            if delayed_response.should_update_state and delayed_response.state_updates:
                self.apply_feedback_state_updates(player, delayed_response.state_updates)

            content = delayed_response.content.strip()

            # 立即触发（延迟为 0）：直接发送消息并保存会话，保留原即时语义
            if delay_seconds <= 0:
                if self._message_sender and content:
                    await self._message_sender(f"**异样回响**\n\n{content}")
                if self._session_saver and group_id:
                    await self._session_saver(group_id, session)
                logger.info(f"延迟反馈已生成（立即触发）: {player.name}")
                return

            # 计算触发时间点：当前 elapsed_minutes + 延迟分钟数
            time_manager = session.time_manager if isinstance(session.time_manager, dict) else {}
            current_elapsed = int(time_manager.get("elapsed_minutes", 0) or 0)
            trigger_at_elapsed = current_elapsed + delay_seconds / 60

            # 写入待触发队列，由 process_action 在到期时追加到行动结果
            session.pending_feedbacks.append({
                "trigger_at_elapsed": trigger_at_elapsed,
                "content": content,
                "target_player_id": player.player_id,
            })

            # 保存会话以持久化队列与状态更新
            if self._session_saver and group_id:
                await self._session_saver(group_id, session)

            logger.info(f"延迟反馈已入队: {player.name}, 触发时间={trigger_at_elapsed}分钟")

        except Exception as e:
            logger.error(f"延迟反馈生成失败: {e}")

    async def update_npc_attitudes(
        self,
        player: Player,
        session: GameSession,
        violation_context: dict[str, Any]
    ) -> None:
        """更新NPC态度 - 利用 npc_system.py"""
        rule_info = violation_context.get("rule_info")
        if not isinstance(rule_info, dict):
            return

        related_npc_name = rule_info.get("related_npc")
        opposing_npc_name = rule_info.get("opposing_npc")

        if not related_npc_name:
            return

        try:
            npc_entry, memory = self._get_runtime_npc_memory(session, related_npc_name)
            if npc_entry is None or memory is None:
                return
            memory.update_attitude_vector(
                player.player_id,
                hostility_delta=20,
                trust_delta=-15
            )
            npc_entry["memory"] = memory.to_dict()
            logger.debug(f"NPC {related_npc_name} 对玩家 {player.name} 态度恶化")

            # 更新对抗NPC态度（变好）
            if opposing_npc_name:
                opp_entry, opp_memory = self._get_runtime_npc_memory(session, opposing_npc_name)
                if opp_entry is None or opp_memory is None:
                    return
                opp_memory.update_attitude_vector(
                    player.player_id,
                    affection_delta=10
                )
                opp_entry["memory"] = opp_memory.to_dict()
                logger.debug(f"NPC {opposing_npc_name} 对玩家 {player.name} 态度改善")

        except Exception as e:
            logger.error(f"更新NPC态度失败: {e}")

    async def check_hunt_trigger(
        self,
        player: Player,
        session: GameSession,
        violation_context: dict[str, Any]
    ) -> None:
        """检查是否触发追杀事件（简单事件触发机制）"""
        rule_info = violation_context.get("rule_info")
        if not isinstance(rule_info, dict):
            return

        related_npc = rule_info.get("related_npc")
        if not related_npc or not isinstance(related_npc, str):
            return

        # 检查NPC敌意度
        _npc_entry, memory = self._get_runtime_npc_memory(session, related_npc)
        if memory is None:
            return

        attitude_vector = memory.get_attitude_vector(player.player_id)
        hostility = attitude_vector.get("hostility", 0)

        # 敌意度>70时，概率触发追杀
        if hostility > 70:
            is_special = violation_context.get("is_special_location", False)
            base_chance = 0.3 if is_special else 0.15
            recent_violations = violation_context.get("recent_violations", 0)
            chance = min(0.8, base_chance + (recent_violations * 0.1))

            import random
            if random.random() < chance:
                await self.trigger_hunt_event(player, session, related_npc)

    async def trigger_hunt_event(
        self,
        player: Player,
        session: GameSession,
        npc_name: str
    ) -> None:
        """触发追杀事件 - 通过LLM生成场景，并写入 hunt_state 状态机（Task 19）"""
        # 解析追杀者的真实 npc_id，写入 hunt_state 供 NPC sim 锁定 ATTACK
        pursuer_npc = self._find_runtime_npc(session, npc_name)
        if pursuer_npc is None:
            raise ValueError(f"追杀事件无法解析 NPC: {npc_name}")
        pursuer_npc_id = str(pursuer_npc.get("npc_id", "") or "").strip()
        if not pursuer_npc_id:
            raise ValueError(f"追杀 NPC 缺少 npc_id: {npc_name}")

        hunt_prompt = f"""玩家 {player.name} 在 {player.location} 被 {npc_name} 追杀。

场景：{session.scene_name}
玩家状态：体力{player.health}/100，理智{player.sanity}/100

请生成追杀场景描述，要求：
1. 描述NPC如何出现并追杀玩家
2. 给玩家逃脱或反抗的选择
3. 根据玩家状态调整难度（虚弱玩家更难逃脱）
4. 给出 2-3 条具体的逃脱条件（如"到达X房间""使用Y物品"），玩家达成任一即视为逃脱

返回JSON：
{{
    "scene_description": "追杀场景描述",
    "npc_action": "NPC的追杀行动",
    "player_options": ["选项1", "选项2", "选项3"],
    "escape_difficulty": "逃脱难度描述",
    "escape_conditions": ["逃脱条件1", "逃脱条件2"]
}}"""

        response = await self._llm_client.call(
            prompt=hunt_prompt,
            temperature=0.9,
            max_tokens=get_default_max_tokens(),
        )

        result = response.parse_json()
        logger.info(f"追杀事件已生成: {player.name} 被 {npc_name} 追杀")

        # 写入 hunt_state 状态机（Task 19）：固定 3 回合逃脱窗口，逃脱条件由 LLM 生成
        escape_conditions_raw = result.get("escape_conditions") or []
        escape_conditions: list[str] = []
        if isinstance(escape_conditions_raw, list):
            escape_conditions = [
                str(c).strip() for c in escape_conditions_raw
                if isinstance(c, (str, int, float)) and str(c).strip()
            ]
        session.hunt_state = {
            "active": True,
            "pursuer_npc_id": pursuer_npc_id,
            "remaining_turns": 3,
            "escape_conditions": escape_conditions,
            "triggered_at": datetime.now().isoformat(),
        }

        # 推送追杀场景给玩家
        if self._message_sender is None:
            logger.warning("未配置 message_sender，追杀场景未推送给玩家")
        else:
            scene_description = result.get("scene_description", "")
            npc_action = result.get("npc_action", "")
            player_options = result.get("player_options") or []

            lines = [f"【追杀事件】{npc_name} 正在追杀你！"]
            if scene_description:
                lines.append(f"\n场景：{scene_description}")
            if npc_action:
                lines.append(f"\n追杀行动：{npc_action}")
            if player_options:
                options_text = "\n".join(
                    f"  {i + 1}. {opt}" for i, opt in enumerate(player_options)
                )
                lines.append(f"\n可选行动：\n{options_text}")
            if escape_conditions:
                cond_text = "；".join(escape_conditions)
                lines.append(f"\n逃脱条件：{cond_text}")
                lines.append(f"剩余逃脱回合：3")
            message = "\n".join(lines)

            try:
                await self._message_sender(message)
            except Exception as send_err:
                logger.error(f"推送追杀场景给玩家失败: {send_err}")

    async def handle_double_edged_violation(
        self,
        player: Player,
        session: GameSession,
        violated_rule: str
    ) -> dict[str, Any] | None:
        """处理双刃剑规则违规 - 风险与收益并存

        Returns:
            包含收益信息的字典，如果处理失败返回None
        """
        try:
            de_prompt = f"""玩家触发了双刃剑规则。

规则：{violated_rule}
场景：{session.scene_name}
隐藏真相：{session.hidden_truth}
玩家状态：体力{player.health}，理智{player.sanity}

请生成双刃剑后果，要求：
1. 必须有明确的惩罚（风险）
2. 必须有明确的收益（可能是线索、NPC帮助、关键物品等）
3. 收益必须与剧情真相相关
4. 根据玩家当前状态调整风险-收益平衡

返回JSON：
{{
    "risk_description": "风险/惩罚描述",
    "risk_effects": {{"sanity": -10, "health": -5}},
    "reward_description": "收益描述",
    "reward_type": "线索/NPC帮助/物品/信息",
    "reward_content": "具体收益内容",
    "story_impact": "对剧情的影响"
}}"""

            response = await self._llm_client.call(
                prompt=de_prompt,
                temperature=0.85,
                max_tokens=get_default_max_tokens(),
            )

            result = response.parse_json()

            # 应用惩罚
            risk_effects = result.get("risk_effects", {})
            sanity_delta = risk_effects.get("sanity", 0)
            health_delta = risk_effects.get("health", 0)

            if isinstance(sanity_delta, int):
                player.sanity = max(SanityThresholds.MIN, min(SanityThresholds.MAX, player.sanity + sanity_delta))
            if isinstance(health_delta, int):
                player.health = max(HealthThresholds.MIN, min(HealthThresholds.MAX, player.health + health_delta))

            # 给予收益
            reward_type = result.get("reward_type", "")
            reward_content = result.get("reward_content", "")

            if "线索" in reward_type and reward_content:
                # 添加线索到玩家背包
                player.inventory.append({
                    "type": "clue",
                    "name": "关键线索",
                    "description": reward_content,
                })
            elif "物品" in reward_type and reward_content:
                # 添加物品
                player.inventory.append({
                    "type": "item",
                    "name": reward_content,
                    "description": "双刃剑规则获得的物品",
                })

            logger.info(f"双刃剑规则处理完成: {player.name}, 收益={reward_type}")
            return result

        except Exception as e:
            logger.error(f"处理双刃剑规则失败: {e}")
            return None
