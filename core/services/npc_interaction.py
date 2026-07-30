"""NPC 交互服务 - 处理玩家与 NPC 的对话交互。

本模块从 ``action_processor.py`` 抽离，职责单一：
- ``NPCInteractionService.handle_npc_interaction`` 检测并处理 NPC 交互（Task 13：改走 LLM 生成对话）
- ``NPCInteractionService.record_rule_texts`` 把 NPC 提及的规则版本去重写入玩家笔记
- ``NPCInteractionService.normalize_rule_text_for_dedup`` 规则文本归一化（去重用）

调用方（``ActionProcessor``）通过组合持有 ``NPCInteractionService`` 实例，
原 ``_maybe_handle_npc_interaction`` / ``_record_rule_texts`` /
``_normalize_rule_text_for_dedup`` 方法保留为薄壳委托，避免破坏既有调用点。

注：``ActionResult`` 定义在 ``action_processor.py``，为避免循环导入，
本模块在 ``handle_npc_interaction`` 内部按需 lazy import。
"""
from __future__ import annotations

import logging
import re
from collections.abc import Mapping

from ...common.models import JsonObject, JsonValue
from ..game.models import GameSession, Player
from .npc_simulator import NPCSimulator
from .psychological_state import PsychologicalStateService

logger = logging.getLogger(__name__)


class NPCInteractionService:
    """NPC 交互服务。

    封装玩家与 NPC 的对话交互逻辑：检测对话意图、选择目标 NPC、
    调用 ``NPCSimulator.generate_dialogue_llm`` 生成对白、把提及规则写入玩家笔记。
    服务持有 ``npc_simulator`` 实例，通过组合方式注入到 ``ActionProcessor`` 中。
    """

    def __init__(
        self,
        npc_simulator: NPCSimulator,
        psych_state: PsychologicalStateService | None = None,
    ) -> None:
        self._npc_simulator: NPCSimulator = npc_simulator
        # Task 18：持有心理状态服务，用于 NPC 友善互动后的小额理智回复
        self._psych_state: PsychologicalStateService | None = psych_state

    @staticmethod
    def normalize_rule_text_for_dedup(text: str) -> str:
        return re.sub(r"\s+", "", str(text or "").strip()).lower()

    def record_rule_texts(self, player: Player, rule_texts: list[str]) -> int:
        merged_rules = [str(rule).strip() for rule in getattr(player, "recorded_rules", []) if str(rule).strip()]
        seen = {self.normalize_rule_text_for_dedup(text) for text in merged_rules if text}
        added_count = 0

        for raw_text in rule_texts:
            text = str(raw_text or "").strip()
            key = self.normalize_rule_text_for_dedup(text)
            if not text or not key or key in seen:
                continue
            merged_rules.append(text)
            seen.add(key)
            added_count += 1

        player.recorded_rules = merged_rules
        return added_count

    async def handle_npc_interaction(
        self, action: str, player: Player, session: GameSession,
    ):
        """尝试处理 NPC 交互（Task 13：改走 LLM 生成对话）。

        目标：
        - 问规则/自由交谈均走 npc_sim LLM 生成对白，注入 dialogue_history/近期交互/
          态度向量/deception_tendency。
        - 模板代码已删除，无兜底，LLM 报错直接抛 RuntimeError（由 generate_dialogue_llm 抛出）。
        - 玩家通过互动获得的信息会写入 `player.recorded_rules`。
        - Task 14：NPC 说谎一致性由 generate_dialogue_llm 内部读取/写回 memory.rule_versions 保证。
        """
        # lazy import 避免与 action_processor.py 形成模块级循环导入
        from .action_processor import ActionResult

        if not action.strip():
            return None

        # 注意：不要用单字"问"做简单包含匹配，否则"问题/问号/提问"等名词短语会误触发"搭话"分支，
        # 进而把"前往/检查/搜索"等真实行动直接短路掉。
        talk_patterns = [
            r"询问",
            r"打听",
            r"请教",
            r"搭话",
            r"对话",
            r"交谈",
            r"叫住",
            r"招呼",
            # "问"只在其后不是"题"时才按动词对待（避免误匹配"问题"）
            r"问(?!题)",
            # "喊"保持兼容（常见：喊住/喊他/喊一声…）
            r"喊",
        ]
        if not any(re.search(p, action) for p in talk_patterns):
            return None

        env_state = session.environment_state if isinstance(session.environment_state, dict) else {}
        npcs = env_state.get("npcs", []) if isinstance(env_state.get("npcs", []), list) else []
        if not npcs:
            return None

        player_loc = str(player.location or "")

        def npc_loc(npc: Mapping[str, JsonValue]) -> str:
            return str(npc.get("current_location") or npc.get("location") or npc.get("home_location") or "")

        # 如果行动里有明显"移动/检查/探索"等强动作意图，默认不要被 NPC 搭话短路
        # （除非玩家明确点名某个 NPC）
        non_talk_keywords = [
            "前往", "去", "到", "进入", "离开", "返回", "回到",
            "检查", "查看", "调查", "搜索", "探索", "翻找",
            "打开", "关闭", "使用", "拿起", "放下", "触摸", "推", "拉", "按",
        ]
        has_non_talk_intent = any(k in action for k in non_talk_keywords)

        # 选择目标NPC：优先匹配名字，其次取同地点的第一个
        target: JsonObject | None = None
        mentioned_name = False
        for npc in npcs:
            if not isinstance(npc, dict):
                continue
            name = str(npc.get("name") or "")
            if name and name in action:
                target = npc
                mentioned_name = True
                break

        if target is None:
            same_place = [npc for npc in npcs if isinstance(npc, dict) and npc_loc(npc) == player_loc]
            target = same_place[0] if same_place else None

        # 未点名 + 行动包含强动作意图：交给正常行动判定流程处理
        if has_non_talk_intent and not mentioned_name:
            return None

        if target is None:
            # 玩家在聊天但附近没有任何NPC
            return None

        name = str(target.get("name") or session.npc_guidance.get("npc_name") or "NPC")
        target_loc = npc_loc(target).strip()
        loc = target_loc or "未知位置"

        # 位置未知：不允许"隔空对话"，给出符合直觉的反馈
        if player_loc and not target_loc:
            return ActionResult(description=f"你压低声音叫了叫{name}，但你看不见他，也无法确定他是否在{player_loc}附近。")

        # 不在场：允许玩家"喊人"，但给出符合直觉的反馈
        if player_loc and target_loc and target_loc != player_loc:
            return ActionResult(description=f"你朝{loc}的方向叫了叫{name}，回应只有回声。你此刻在{player_loc}，而他不在这里。")

        # Task 13：调用 LLM 生成 NPC 对话（无兜底，报错直接抛 RuntimeError）
        # generate_dialogue_llm 内部完成：加载记忆、计算帮助意愿、更新态度向量、
        # 记录互动、写回 memory.rule_versions（Task 14）、追加 dialogue_history
        dialogue_text, rules_mentioned = await self._npc_simulator.generate_dialogue_llm(
            session, target, player, action,
        )

        # 把 NPC 提及的规则版本写入玩家笔记
        # truth/rumor/lie 版本均含具体文本，玩家会记下听到的内容（含谎言，玩家此时不知是谎言）
        # refused 版本不写入（NPC 拒绝提及该规则）
        recordable_texts: list[str] = []
        for item in rules_mentioned:
            version = str(item.get("version", "") or "").strip()
            text = str(item.get("text", "") or "").strip()
            if version in ("truth", "rumor", "lie") and text:
                recordable_texts.append(text)
        if recordable_texts:
            self.record_rule_texts(player, recordable_texts)

        # Task 18：与友善 NPC 稳定互动后小额回复理智
        # generate_dialogue_llm 已把更新后的态度向量写回 target["memory"]，
        # 此处重新加载取最新态度向量，传入 psychological_state 判定是否满足友善门槛
        if self._psych_state is not None:
            from ...systems.npc_system import NPCMemory

            raw_memory = target.get("memory", {})
            memory_data = raw_memory if isinstance(raw_memory, dict) else {}
            mem = NPCMemory.from_dict(memory_data)
            attitude_vector = mem.get_attitude_vector(player.player_id)
            recovery = self._psych_state.recover_sanity_for_friendly_interaction(player, attitude_vector)
            if recovery > 0:
                logger.info(f"玩家 {player.name} 与友善 NPC {name} 互动，理智回复 {recovery}")

        return ActionResult(description=dialogue_text)
