from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from ..common import GameModes
from ..core import (
    GameSession,
    GameStateManager,
    GameStatus,
    LLMClient,
    Player,
    PlayerStatus,
    SaveManager,
    get_default_max_tokens,
)

logger = logging.getLogger(__name__)


def _detect_spoiler(
    hint_text: str,
    next_action: str,
    hidden_truth: str,
    guidance_target: str,
) -> tuple[bool, list[str]]:
    """检测 LLM 输出是否泄露隐藏真相。返回 (是否泄露, 命中关键词列表)。"""
    if not hidden_truth:
        return False, []

    combined = f"{hint_text} {next_action}"
    leaked: list[str] = []

    # 1. hidden_truth 前 20 字片段检测
    probe = hidden_truth[:20].strip()
    if probe and probe in combined:
        leaked.append(probe)

    # 2. truth 方向时加强真相关键词检测
    if guidance_target == "truth":
        # 按标点切分 hidden_truth，保留长度 >= 3 的片段
        keywords = [w for w in re.split(r"[，。；,.;\s]+", hidden_truth) if len(w) >= 3]
        for kw in keywords:
            if kw in combined:
                leaked.append(kw)

    return bool(leaked), leaked


class SharedCommandHandlersMixin:
    """共享命令处理逻辑。"""

    @staticmethod
    def _get_multiplayer_host_id(session: Any) -> str:
        """获取多人大厅/对局的房主 ID。"""
        env_state = session.environment_state if isinstance(getattr(session, "environment_state", None), dict) else {}
        lobby = env_state.get("lobby", {})
        if not isinstance(lobby, dict):
            return ""
        return str(lobby.get("host_id", "") or "").strip()

    async def _ensure_active_game_session(self, session: Any) -> bool:
        """确保当前会话已经正式开局。"""
        if getattr(session, "status", None) == GameStatus.ACTIVE:
            return True

        if getattr(session, "game_mode", None) == GameModes.MULTI.value and getattr(session, "status", None) == GameStatus.WAITING:
            await self.send_text("当前仍在多人大厅阶段，请等待房主发送 `/rg 开始 多人 开始` 正式开局。")
            return False

        await self.send_text("游戏尚未开始。请先使用 `/rg 开始` 或 `/rg 恢复`。")
        return False

    async def _handle_离开(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        _ = rest_input
        state_manager = GameStateManager()
        state = await state_manager.get_world(group_id)

        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            session = state.session
            if user_id not in session.players:
                await self.send_text("你不在当前游戏中。")
                return False, "不在游戏中", 2

            session.remove_player(user_id)
            message_lines = [f"{user_name} 离开了游戏。"]

            if session.players:
                env_state = session.environment_state if isinstance(getattr(session, "environment_state", None), dict) else {}
                lobby_meta = env_state.get("lobby", {}) if isinstance(env_state.get("lobby"), dict) else {}
                host_id = str(lobby_meta.get("host_id", "") or "").strip()
                if session.status == GameStatus.WAITING and host_id == user_id:
                    new_host_id = next(iter(session.players.keys()))
                    new_host = session.players[new_host_id]
                    lobby_meta["host_id"] = new_host_id
                    lobby_meta["host_name"] = new_host.name
                    env_state["lobby"] = lobby_meta
                    message_lines.append(f"新的房主是：{new_host.name}")

                await SaveManager().save_immediately(group_id, session)
            else:
                session.status = GameStatus.ENDED
                await state_manager.remove(group_id)
                await SaveManager().delete(group_id)

            await self.send_text("\n".join(message_lines))
            return True, "离开成功", 2
        finally:
            state.release_world()

    async def _handle_状态(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        _ = rest_input
        _ = user_name

        state_manager = GameStateManager()
        state = await state_manager.get_world(group_id)
        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            session = state.session
            title = session.scene_name or ("多人大厅" if session.game_mode == GameModes.MULTI.value and session.status == GameStatus.WAITING else "未生成")
            env_state = session.environment_state if isinstance(getattr(session, "environment_state", None), dict) else {}
            if session.status == GameStatus.WAITING and isinstance(env_state.get("lobby"), dict):
                lobby = env_state["lobby"]
                host_name = str(lobby.get("host_name", "房主") or "房主")
                target_players = lobby.get("target_players")
                target_text = f"{target_players}人" if isinstance(target_players, int) and target_players > 0 else "未指定"
                player_count = len(session.players)
                await self.send_text(
                    f"这里还是多人大厅，场景还没真正展开。\n\n"
                    f"目前由 {host_name} 在组织这局，已经到场 {player_count} 人，目标人数是 {target_text}。"
                )
                return True, "状态已显示", 2

            player = session.players.get(user_id)
            if not player:
                await self.send_text(f"《{title}》仍在继续，但你当前不在这局游戏里。")
                return False, "不在游戏中", 2

            location = str(getattr(player, "location", "") or "未知位置").strip()
            fatigue = self._get_player_fatigue_level(player)
            rule_count = len(self._get_player_recorded_rules(player))
            self_status = "还活着" if player.status == PlayerStatus.ALIVE else "已经死亡"
            parts = [
                f"你现在仍在《{title}》里，位置大概在{location}。",
                f"你的状态还算明确：体力 {player.health}/100，理智 {player.sanity}/100，疲劳感是“{fatigue}”，目前{self_status}。",
                f"手里已经记下了 {rule_count} 条规则笔记。"
            ]

            other_lines: list[str] = []
            for pid, other in session.players.items():
                if pid == user_id:
                    continue
                other_status = "还活着" if other.status == PlayerStatus.ALIVE else "已经死亡"
                other_loc = str(getattr(other, "location", "") or "未知位置").strip()
                other_lines.append(
                    f"{other.name}在{other_loc}附近，体力 {other.health}/100，理智 {other.sanity}/100，{other_status}。"
                )

            if other_lines:
                parts.append("你还能确认到其他人的情况：\n" + "\n".join(other_lines))

            await self.send_text("\n\n".join(parts))
            return True, "状态已显示", 2
        finally:
            state.release_world()

    async def _handle_规则(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        _ = rest_input
        _ = user_name

        state_manager = GameStateManager()
        state = await state_manager.get_world(group_id)
        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            session = state.session
            player = session.players.get(user_id)
            if not player:
                await self.send_text("你不在游戏中。")
                return False, "不在游戏中", 2
            if not await self._ensure_active_game_session(session):
                return False, "游戏未开始", 2

            before_rules = list(self._get_player_recorded_rules(player))
            display_rules = self._get_player_rules_for_display(session, player)
            if len(display_rules) > len(before_rules):
                await SaveManager().schedule_save(group_id, session)

            if not display_rules:
                await self.send_text(
                    "你翻了翻手头能留下的东西，暂时还没有整理出真正能确认的规则。\n\n"
                    "先继续探索、观察 NPC、检查纸面载体，或者用 `/rg 推理` 和 `/rg 记录规则` 慢慢把线索收拢起来。"
                )
                return True, "规则已显示", 2

            lines = ["你把自己一路记下的内容重新理了理，目前能确认的几条是：", ""]
            for index, text in enumerate(display_rules, start=1):
                lines.append(f"{index}. {text}")
            if session.win_condition:
                lines.extend(["", f"至于最后该怎么脱身，你现在更接近的答案是：{session.win_condition}"])

            await self.send_text("\n".join(lines))
            return True, "规则已显示", 2
        finally:
            state.release_world()

    async def _handle_线索(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        state_manager = GameStateManager()
        state = await state_manager.get_world(group_id)
        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            if not await self._ensure_active_game_session(state.session):
                return False, "游戏未开始", 2

            player = state.session.players.get(user_id)
            if not player:
                await self.send_text("你不在游戏中。")
                return False, "不在游戏中", 2

            inventory = getattr(player, "inventory", []) or []
            clue_items: list[dict[str, Any]] = []
            for item in inventory:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type", "") or "").strip().lower()
                name = str(item.get("name", "") or "").strip()
                if "线索" in item_type or item_type == "clue" or "线索" in name:
                    clue_items.append(item)

            query = (rest_input or "").strip()
            if query:
                matches = [item for item in clue_items if query in str(item.get("name", "") or "")]
                if not matches:
                    await self.send_text(f"未找到线索：{query}")
                    return False, "未找到线索", 2

                lines = ["你把这条线索单独拎出来又看了一遍：", ""]
                for item in matches:
                    lines.append(f"{item.get('name', '未知线索')}")
                    description = str(item.get("description", "") or "").strip()
                    if description:
                        lines.append(description)
                    observation_hint = str(item.get("observation_hint", "") or "").strip()
                    if observation_hint:
                        lines.append(f"你越看越觉得：{observation_hint}")
                    lines.append("")
                await self.send_text("\n".join(lines).strip())
                return True, "线索已显示", 2

            if not clue_items:
                await self.send_text(
                    "你手头暂时还没有能单独拎出来的线索。\n\n"
                    "继续用 `/rg 行动` 去探索，或者翻看道具、观察 NPC，也许会有东西慢慢浮出来。"
                )
                return True, "线索已显示", 2

            lines = ["你把目前捞到的线索排了一遍，最值得记住的有：", ""]
            for index, item in enumerate(clue_items, start=1):
                name = str(item.get("name", "未知线索") or "未知线索").strip()
                description = str(item.get("description", "") or "").strip()
                lines.append(f"{index}. {name}")
                if description:
                    lines.append(f"   {description}")
            await self.send_text("\n".join(lines))
            return True, "线索已显示", 2
        finally:
            state.release_world()

    async def _handle_提示(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        hint_type = (rest_input or "").strip()
        if "线索" in hint_type:
            player_preference = "clue"
        elif "规则" in hint_type:
            player_preference = "rule"
        else:
            player_preference = "auto"

        state_manager = GameStateManager()
        state = await state_manager.get_world(group_id)
        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            session = state.session
            player = session.players.get(user_id)
            if not player:
                await self.send_text("你不在游戏中。")
                return False, "不在游戏中", 2
            if not await self._ensure_active_game_session(session):
                return False, "游戏未开始", 2

            if player.hint_count <= 0:
                await self.send_text("你的提示次数已用完！")
                return False, "无提示次数", 2

            player.hint_count -= 1

            # 完整规则表（真实基准）
            all_rules = [self._extract_rule_text(rule) for rule in (session.rules or [])]
            all_rules = [text for text in all_rules if text]

            # 调用者与全队规则笔记（用于进度推断）
            team_rules_data = self._collect_team_rules_for_hint(session, user_id)
            requester_rules = team_rules_data["requester_rules"]
            is_multi_player = team_rules_data["is_multi_player"]
            teammate_rules = team_rules_data["teammate_rules"]

            # 背包物品列表（参考材料）
            inventory = getattr(player, "inventory", []) or []

            def format_item(item: dict[str, Any]) -> str:
                name = str(item.get("name", "") or "").strip()
                description = str(item.get("description", "") or "").strip()
                observation_hint = str(item.get("observation_hint", "") or "").strip()
                is_key = bool(item.get("is_key_item", False))
                parts = [name]
                if is_key:
                    parts.append("关键")
                if description:
                    parts.append(f"描述:{description}")
                if observation_hint:
                    parts.append(f"观察提示:{observation_hint}")
                return " | ".join(parts)

            # 调用者笔记带编号（便于 LLM 自主引用）
            requester_rules_block = (
                "\n".join(f"{i + 1}. {text}" for i, text in enumerate(requester_rules))
                if requester_rules
                else "（暂无）"
            )

            # 多人模式：队友笔记
            teammate_block = ""
            if is_multi_player:
                teammate_lines = []
                for mate in teammate_rules:
                    mate_name = mate["player_name"]
                    mate_rules = mate["rules"]
                    if mate_rules:
                        mate_text = "; ".join(mate_rules)
                    else:
                        mate_text = "（暂无）"
                    teammate_lines.append(f"- {mate_name}: {mate_text}")
                teammate_block = "\n\n【队友规则笔记】（用于推断全队进度）：\n" + "\n".join(teammate_lines)

            rules_block = "\n".join(f"{i + 1}. {text}" for i, text in enumerate(all_rules)) if all_rules else "（无）"

            # 背包物品列表（参考材料）
            inventory_lines: list[str] = []
            for item in inventory:
                if isinstance(item, dict) and str(item.get("name", "") or "").strip():
                    inventory_lines.append(format_item(item))
                elif item:
                    inventory_lines.append(str(item))
            inventory_block = "\n".join(f"- {text}" for text in inventory_lines) if inventory_lines else "（空）"

            system_prompt = (
                "你是规则怪谈游戏的提示生成器。你知道后台完整规则与隐藏真相，但必须严格控制剧透。\n\n"
                "推理流程（内部完成，不要在输出中展示）：\n"
                "1) 对比【玩家规则笔记】与【完整规则表】，识别：\n"
                "   - 误解：玩家笔记中与真实规则矛盾或反向的条目\n"
                "   - 遗漏：玩家尚未记录但对通关关键的真实规则\n"
                "   - 误信：玩家笔记中把假规则当真，或把真规则当假\n"
                "2) 对比【玩家规则笔记】与【隐藏真相】，判断玩家是否触及真相：\n"
                "   - 未触及：笔记只停留在表层规则\n"
                "   - 接近：笔记中出现与真相相关的线索碎片但未串联\n"
                "   - 偏离：玩家推理方向与真相背道而驰\n"
                "3) 基于进度选择本次引导目标 guidance_target：\n"
                "   - rule：玩家存在误解/遗漏/误信，需要纠正或补全规则认知\n"
                "   - truth：玩家规则基本正确，但未触及或已偏离真相，需要暗示规则之间的反常或关联\n"
                "   若玩家偏好与进度判断冲突，以进度判断为主，偏好仅作次要参考\n\n"
                "硬性要求：\n"
                "1) 只输出 JSON，不要 markdown，不要多余文字，不要解释推理过程\n"
                "2) 禁止直接复述或泄露隐藏真相；禁止给出完整答案\n"
                "3) 提示必须间接、隐喻、可执行——给出一个低风险观察/验证方向，而非结论\n"
                "4) 不得直白说'你错了''真相是'，要让玩家自己产生怀疑\n\n"
                "输出 JSON：\n"
                '{"guidance_target":"rule|truth","progress_assessment":"10字内进度标签","hint":"...","next_action":"..."}'
            )

            user_prompt = (
                f"场景：{session.scene_name}\n"
                f"背景：{session.background}\n"
                f"通关条件：{session.win_condition}\n"
                f"隐藏真相（仅供内部推理，禁止输出）：{session.hidden_truth}\n\n"
                f"【后台完整规则表】（真实基准）：\n{rules_block}\n\n"
                f"【调用者规则笔记】（待评估）：\n{requester_rules_block}\n"
                f"{teammate_block}\n\n"
                f"【玩家状态】理智{player.sanity}/100 体力{player.health}/100 位置:{player.location}\n"
                f"【背包物品】（参考材料，可选择性引用）：\n{inventory_block}\n\n"
                f"【玩家本次偏好】：{player_preference}\n"
                "请按 system 流程推断进度后选择 guidance_target，给出非直白引导。"
            )

            hidden_truth = session.hidden_truth

            async def _call_llm(temp: float, extra_note: str = "") -> dict[str, str]:
                prompt = user_prompt
                if extra_note:
                    prompt = f"{user_prompt}\n\n{extra_note}"
                response = await LLMClient().call(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    temperature=temp,
                    max_tokens=min(600, get_default_max_tokens()),
                )
                data = response.parse_json()
                if not isinstance(data, dict):
                    raise RuntimeError(f"LLM 返回非 JSON 对象: {response!r}")
                return {
                    "guidance_target": str(data.get("guidance_target", "") or "").strip(),
                    "progress_assessment": str(data.get("progress_assessment", "") or "").strip(),
                    "hint": str(data.get("hint", "") or "").strip(),
                    "next_action": str(data.get("next_action", "") or "").strip(),
                }

            # 首次生成
            result = await _call_llm(0.4)
            guidance_target = result["guidance_target"]
            progress_assessment = result["progress_assessment"]
            hint_text = result["hint"]
            next_action = result["next_action"]

            leaked, leaked_words = _detect_spoiler(hint_text, next_action, hidden_truth, guidance_target)
            if leaked:
                logger.warning("首次提示生成命中剧透检测，重试一次。泄露片段: %s", leaked_words)
                # 重试一次
                result = await _call_llm(0.7, "上一次生成疑似泄露真相，请严格避免")
                guidance_target = result["guidance_target"]
                progress_assessment = result["progress_assessment"]
                hint_text = result["hint"]
                next_action = result["next_action"]
                leaked, leaked_words = _detect_spoiler(hint_text, next_action, hidden_truth, guidance_target)
                if leaked:
                    logger.error("二次提示生成仍命中剧透检测，泄露片段: %s", leaked_words)
                    raise RuntimeError("提示生成失败：检测到剧透风险")

            # 进度标签与引导方向仅写日志
            logger.info("提示生成: guidance_target=%s progress_assessment=%s", guidance_target, progress_assessment)

            hint_message = f"{hint_text}\n\n下一步建议：{next_action}" if next_action else hint_text
            await SaveManager().schedule_save(group_id, session)
            await self.send_text(f"**提示（你还剩{player.hint_count}次）**\n\n{hint_message}")
            return True, "提示已发送", 2
        except Exception as exc:
            logger.error("生成提示失败: %s", exc, exc_info=True)
            await self.send_text(f"生成提示时出错：{exc}")
            return False, "生成失败", 2
        finally:
            state.release_world()

    async def _handle_推理(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        if not rest_input:
            await self.send_text("请提供推理内容。用法：`/rg 推理 <推理内容>`")
            return False, "缺少推理内容", 2

        state_manager = GameStateManager()
        state = await state_manager.get_world(group_id)
        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            if not await self._ensure_active_game_session(state.session):
                return False, "游戏未开始", 2

            player = state.session.players.get(user_id)
            if not player:
                await self.send_text("你不在游戏中。")
                return False, "不在游戏中", 2
            if player.status != PlayerStatus.ALIVE:
                await self.send_text("你已经死亡，无法进行推理。")
                return False, "已死亡", 2

            player.reasoning_history.append(rest_input.strip())
            await SaveManager().schedule_save(group_id, state.session)
            await self.send_text(f"**{user_name} 的推理**\n\n{rest_input.strip()}\n\n推理已记录。")
            return True, "推理已记录", 2
        finally:
            state.release_world()

    async def _handle_记录规则(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        note_text = str(rest_input or "").strip()
        if not note_text:
            await self.send_text("请提供规则内容。用法：`/rg 记录规则 <规则内容>`")
            return False, "缺少规则内容", 2

        state_manager = GameStateManager()
        state = await state_manager.get_world(group_id)
        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            if not await self._ensure_active_game_session(state.session):
                return False, "游戏未开始", 2

            player = state.session.players.get(user_id)
            if not player:
                await self.send_text("你不在游戏中。")
                return False, "不在游戏中", 2
            if player.status != PlayerStatus.ALIVE:
                await self.send_text("你已经死亡，无法记录规则。")
                return False, "已死亡", 2

            added_count = self._record_rule_texts(player, [note_text])
            if added_count <= 0:
                await self.send_text("这条规则已经在你的笔记里了。")
                return False, "规则重复", 2

            await SaveManager().schedule_save(group_id, state.session)
            rule_count = len(self._get_player_recorded_rules(player))
            await self.send_text(
                f"{user_name} 又记下了一条新的规则。\n\n"
                f"现在你的规则笔记里一共有 {rule_count} 条内容了。"
            )
            return True, "规则已记录", 2
        finally:
            state.release_world()

    async def _handle_行动(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        action_text = str(rest_input or "").strip()
        if not action_text:
            await self.send_text("请提供行动描述。用法：`/rg 行动 <行动描述>`")
            return False, "缺少行动描述", 2

        state_manager = GameStateManager()

        # 阶段1：短临界区校验。持玩家锁校验玩家在游戏里、游戏激活、玩家活着，立即释放锁。
        state = await state_manager.get_for_player(group_id, user_id)
        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2
        try:
            session = state.session
            player = session.players.get(user_id)
            if not player:
                await self.send_text("你不在游戏中。")
                return False, "不在游戏中", 2
            if not await self._ensure_active_game_session(session):
                return False, "游戏未开始", 2
            if player.status != PlayerStatus.ALIVE:
                await self.send_text("你已经死亡，无法行动。")
                return False, "已死亡", 2
        finally:
            state.release_player(user_id)

        # 阶段2：取只读快照。重建 Player 和 GameSession 副本用于 LLM 判定，避免持锁。
        snapshot = await state_manager.get_snapshot(group_id)
        if not snapshot:
            return False, "快照不可用", 2

        # 在快照基础上重建副本
        session_snapshot = GameSession.from_dict(snapshot["session"])
        player_snapshot = Player.from_dict(snapshot["players_snapshot"][user_id])

        # 阶段3：无锁 LLM 判定。在快照副本上跑 process_action 和 npc_simulator，完全无锁，可并行。
        self._ensure_story_runtime(
            session_snapshot,
            game_mode=getattr(session_snapshot, "game_mode", None),
            initial_player_id=user_id,
        )
        result = await self._action_processor.process_action(
            action=action_text,
            player=player_snapshot,
            session=session_snapshot,
            group_id=group_id,
        )

        now = datetime.now()
        player_snapshot.action_history.append({"action": action_text, "timestamp": now.isoformat()})
        player_snapshot.last_action_at = now

        discovered_carriers = self._discover_rule_carriers_for_player(session_snapshot, player_snapshot, action_text)

        npc_perception: dict[str, Any] = {}
        if bool(self.get_config("npc_sim.enabled", True)) and bool(self.get_config("npc_sim.trigger_on_every_action", True)):
            action_payload = {
                "description": result.description,
                "sanity_change": result.sanity_change,
                "health_change": result.health_change,
                "discovered_clues": list(result.discovered_clues),
                "found_items": list(result.found_items),
                "triggered_event": result.triggered_event,
                "is_fatal": result.is_fatal,
                "violated_rule": result.violated_rule,
            }
            try:
                npc_result = await self._get_or_create_npc_simulator().simulate_after_action(
                    session_snapshot,
                    player_snapshot,
                    action_text,
                    action_payload,
                )
                npc_perception = self._get_or_create_npc_simulator().build_perception_for_player(session_snapshot, player_snapshot)
            except Exception as exc:
                logger.warning("NPC 模拟执行失败: %s", exc)
                npc_result = None
        else:
            npc_result = None

        near_win = False
        if not session_snapshot.has_cleared:
            win_progress = await self._ending_judge.check_win_condition(session=session_snapshot, player=player_snapshot)
            if isinstance(win_progress, dict):
                if win_progress.get("achieved"):
                    session_snapshot.has_cleared = True
                elif win_progress.get("near"):
                    near_win = True
            elif win_progress:
                # 兼容旧的布尔返回
                session_snapshot.has_cleared = True

        # 阶段4：短临界区提交变更。重新持玩家锁，把 LLM 结果中的玩家私有变更应用回真 player；
        # 同时持世界锁，把世界变更应用回真 session。
        state = await state_manager.get_for_player(group_id, user_id)
        if not state or not state.session:
            await self.send_text("提交时发现游戏已结束。")
            return False, "游戏结束", 2
        try:
            session = state.session
            player = session.players.get(user_id)
            if not player:
                await self.send_text("提交时发现玩家已不在游戏中。")
                return False, "玩家消失", 2

            # 应用玩家私有变更
            self._apply_player_changes(player, player_snapshot, result)

            # 应用世界变更（短世界锁）。注意：get_world 已持世界锁，需用 release_world 释放，
            # 不能用 async with（GameState 已不再实现 __aenter__/__aexit__ 上下文管理器协议）。
            world_state = await state_manager.get_world(group_id)
            try:
                if world_state and world_state.session:
                    self._apply_world_changes(world_state.session, session_snapshot, result, npc_result)
            finally:
                if world_state:
                    world_state.release_world()
        finally:
            state.release_player(user_id)

        # 阶段5：无锁广播 + 后续处理（图片、文本、存档）。
        await self._broadcast_action_events(group_id, user_id, result, npc_result, snapshot)

        # 用真 session 的最新状态生成展示
        state_for_display = await state_manager.get_snapshot(group_id)
        if state_for_display:
            session = GameSession.from_dict(state_for_display["session"])
            player = session.players.get(user_id, player_snapshot)
        else:
            session = session_snapshot
            player = player_snapshot

        injury = str(getattr(player, "injury", "无伤") or "无伤")
        fatigue = self._get_player_fatigue_level(player)
        state_desc = str(getattr(player, "state", "正常") or "正常")
        emotion = str(getattr(player, "emotion", "平静") or "平静")
        fear_level = int(getattr(player, "fear_level", 0) or 0)
        anxiety_level = int(getattr(player, "anxiety_level", 0) or 0)
        stress_level = int(getattr(player, "stress_level", 0) or 0)
        image_generator = self.get_image_generator()
        action_image = await image_generator.generate_action_result_image(
            user_name=user_name,
            action=action_text,
            is_dead=(player.status != PlayerStatus.ALIVE),
            scene_description=result.description,
            action_feedback=result.triggered_event or "",
            health=player.health,
            injury=injury,
            fatigue=fatigue,
            sanity=player.sanity,
            state=state_desc,
            emotion=emotion,
            fear_level=fear_level,
            anxiety_level=anxiety_level,
            stress_level=stress_level,
            found_items=list(result.found_items),
            found_clues=list(result.discovered_clues),
            new_location=player.location,
            random_event=None,
        )
        session.image_paths.append(action_image)
        await self._send_image_path(action_image)

        follow_up_sections: list[str] = []
        if discovered_carriers:
            follow_up_sections.append(self._format_discovered_carrier_text(discovered_carriers))

        if npc_perception:
            perception_lines: list[str] = []
            visible_npcs = npc_perception.get("visible_npcs", [])
            if isinstance(visible_npcs, list) and visible_npcs:
                names = []
                for npc in visible_npcs:
                    if not isinstance(npc, dict):
                        continue
                    npc_name = str(npc.get("name", "NPC") or "NPC").strip()
                    npc_action = str(npc.get("last_action", "") or "").strip()
                    names.append(f"{npc_name}（{npc_action or '就在附近'}）")
                if names:
                    perception_lines.append("你能直接看到：" + "、".join(names))

            visible_events = npc_perception.get("visible_events", [])
            if isinstance(visible_events, list):
                for event_text in visible_events:
                    text = str(event_text or "").strip()
                    if text:
                        perception_lines.append(text)

            audible_events = npc_perception.get("audible_events", [])
            if isinstance(audible_events, list):
                for event_text in audible_events:
                    text = str(event_text or "").strip()
                    if text:
                        perception_lines.append(f"你听见：{text}")

            hints = npc_perception.get("player_perception_hints", [])
            if isinstance(hints, list):
                for hint in hints:
                    text = str(hint or "").strip()
                    if text:
                        perception_lines.append(text)

            if perception_lines:
                follow_up_sections.append("\n".join(perception_lines))

        if follow_up_sections:
            await self.send_text("\n\n".join(section for section in follow_up_sections if section.strip()))

        if session.has_cleared:
            await self.send_text(
                "目标已经达成，这一局随时可以收束。\n\n"
                "- `/rg 结束`：直接以当前状态通关结算\n"
                "- `/rg 继续`：留下来继续探索，弄清怪谈的根源，冲击完美结局"
            )
        elif near_win:
            # 持世界锁写入真实 session 的 near_win_notified 标记，
            # 避免在展示用 session 副本上写丢失导致提示反复发送。
            world_state = await state_manager.get_world(group_id)
            if world_state and world_state.session:
                real_env = world_state.session.environment_state
                if not isinstance(real_env, dict):
                    real_env = {}
                    world_state.session.environment_state = real_env
                if not real_env.get("near_win_notified"):
                    real_env["near_win_notified"] = True
                    world_state.release_world()
                    await self.send_text(
                        "你距离目标只差最后一步了。\n\n"
                        "现在你可以选择：\n"
                        "- 直接完成目标，尽快脱身（完成后用 `/rg 结束` 结算）\n"
                        "- 先不急着离开，继续探索这里的真相，设法从根源上解决怪谈，达成完美结局"
                    )
                else:
                    world_state.release_world()
            else:
                # session 不存在，仍然发送提示
                await self.send_text(
                    "你距离目标只差最后一步了。\n\n"
                    "现在你可以选择：\n"
                    "- 直接完成目标，尽快脱身（完成后用 `/rg 结束` 结算）\n"
                    "- 先不急着离开，继续探索这里的真相，设法从根源上解决怪谈，达成完美结局"
                )

        if result.is_fatal or player.status != PlayerStatus.ALIVE:
            if player.sanity == 0:
                await self.send_text("……\n\n你感到某种‘秩序’正在接纳你。")
            else:
                violated = result.violated_rule or "未知"
                await self.send_text(
                    f"你已经死了。\n\n"
                    f"真正把你推到这一步的，是那条被你碰开的规则：{violated}\n\n"
                    "现在可以用 `/rg 结束` 看这一局最终落到了什么结局。"
                )

        await SaveManager().schedule_save(group_id, session)
        return True, "行动已执行", 2

    def _apply_player_changes(
        self,
        player: Player,
        snapshot_player: Player,
        result: "ActionResult",
    ) -> None:
        """把 LLM 判定结果中玩家私有状态变更应用回真 player。

        Args:
            player: 真实 player（持玩家锁）
            snapshot_player: 快照副本（LLM 在其上跑判定）
            result: ActionResult
        """
        # 基本状态
        player.sanity = snapshot_player.sanity
        player.health = snapshot_player.health
        player.fatigue = snapshot_player.fatigue
        player.stress_level = snapshot_player.stress_level
        player.anxiety_level = snapshot_player.anxiety_level
        player.fear_level = snapshot_player.fear_level
        player.injury = snapshot_player.injury
        player.state = snapshot_player.state
        player.emotion = snapshot_player.emotion

        # 位置
        player.location = snapshot_player.location

        # 背包：直接替换为快照版本（因为 LLM 可能添加/删除物品）
        player.inventory = list(snapshot_player.inventory)

        # 规则笔记
        player.recorded_rules = list(snapshot_player.recorded_rules)

        # 行动历史
        player.action_history = list(snapshot_player.action_history)
        player.last_action_at = snapshot_player.last_action_at

        # 死亡判定
        if result.is_fatal or player.sanity == 0 or player.health == 0:
            player.status = PlayerStatus.DEAD

    def _apply_world_changes(
        self,
        session: GameSession,
        snapshot_session: GameSession,
        result: "ActionResult",
        npc_result: dict | None,
    ) -> None:
        """把 LLM 判定结果中的世界变更应用回真 session（持世界锁）。

        Args:
            session: 真实 session（持世界锁）
            snapshot_session: 快照副本
            result: ActionResult
            npc_result: NPC 模拟结果（可为 None）
        """
        # 版本检查：若世界在 LLM 判定期间被其他玩家修改过，记录 warning。
        # 采用最后写赢策略（last-write-wins），不抛异常以免阻塞玩家行动。
        snapshot_version = snapshot_session.world_version
        current_version = session.world_version
        if snapshot_version != current_version:
            logger.warning(
                "世界版本不匹配（snapshot=%d, current=%d），采用最后写赢策略，可能丢失部分更新",
                snapshot_version, current_version,
            )

        _ = npc_result  # NPC 推进结果已通过 snapshot_session.environment_state 体现

        # 应用 environment_state（包含 NPC 推进、规则载体变更、room_events 等）
        if isinstance(snapshot_session.environment_state, dict):
            # 直接替换 environment_state，因为 LLM/NPC 模拟直接修改了它
            session.environment_state = dict(snapshot_session.environment_state)

        # 应用规则变异记录
        if snapshot_session.rule_mutations:
            # 追加新增的变异记录（避免重复）
            existing_count = len(session.rule_mutations)
            new_mutations = snapshot_session.rule_mutations[existing_count:]
            if new_mutations:
                session.rule_mutations.extend(new_mutations)
                session.last_mutation_time = snapshot_session.last_mutation_time

        # 应用通关状态
        if snapshot_session.has_cleared:
            session.has_cleared = True

        # 应用 environment_memory
        if isinstance(snapshot_session.environment_memory, dict):
            # 合并 visited_locations / interacted_objects / time_based_events
            for key in ("visited_locations", "interacted_objects"):
                existing = set(session.environment_memory.get(key, []))
                new_items = snapshot_session.environment_memory.get(key, [])
                for item in new_items:
                    if item not in existing:
                        session.environment_memory.setdefault(key, []).append(item)
                        existing.add(item)
            # time_based_events 直接追加新的
            existing_time_events = len(session.environment_memory.get("time_based_events", []))
            new_time_events = snapshot_session.environment_memory.get("time_based_events", [])[existing_time_events:]
            if new_time_events:
                session.environment_memory.setdefault("time_based_events", []).extend(new_time_events)

        # 同步世界版本号：快照版本是 LLM 判定时的版本，应用变更后真实版本应为"快照版本+1"
        session.world_version = snapshot_session.world_version + 1

        session.updated_at = datetime.now()

    async def _broadcast_action_events(
        self,
        group_id: str,
        user_id: str,
        result: "ActionResult",
        npc_result: dict | None,
        snapshot: dict | None,
    ) -> None:
        """把本次行动发布给其他可感知的玩家。

        基于 snapshot 计算可见/可听集合，构造 GameEvent 并发布到 EventBus。
        单人模式不产生任何事件推送。
        """
        _ = npc_result
        if not snapshot:
            return
        session_data = snapshot.get("session", {})
        # 判断多人模式（GameModes.MULTI.value 是 "多人"）
        game_mode = session_data.get("game_mode")
        if game_mode != GameModes.MULTI.value:
            return

        players_snapshot = snapshot.get("players_snapshot", {})
        player_snapshot_data = players_snapshot.get(user_id, {})
        player_location = player_snapshot_data.get("location", "")
        player_name = player_snapshot_data.get("name", "玩家")

        # 推算可见/可听集合
        visible_to: set[str] = set()
        audible_to: set[str] = set()
        for pid, pdata in players_snapshot.items():
            if pid == user_id:
                continue
            if pdata.get("status") != "alive":
                # 兼容枚举值
                if str(pdata.get("status", "")).lower() != "alive":
                    continue
            other_location = pdata.get("location", "")
            if other_location == player_location and other_location:
                visible_to.add(pid)
            # 邻接房间：可听（P3 阶段会改用物理系统；这里先用房间拓扑兜底）
            # 简化：所有其他位置都算可听（实际应该用 room_graph 判断邻接）
            # 这里先用简单的"非同房间但都存在"作为可听条件，避免引入复杂的 room_graph 查找
            elif other_location and other_location != player_location:
                audible_to.add(pid)

        # 判断是否高重要性
        is_high_importance = bool(getattr(result, "is_fatal", False)) or bool(getattr(result, "violated_rule", None))

        description = str(getattr(result, "description", ""))[:200]
        audible_description = self._summarize_audible(description)

        from ..core.services.event_bus import GameEvent
        event = GameEvent(
            event_type="player_action",
            group_id=group_id,
            actor_id=user_id,
            actor_name=player_name,
            location=player_location,
            description=description,
            audible_description=audible_description,
            visible_to=visible_to,
            audible_to=audible_to,
            importance="high" if is_high_importance else "normal",
        )

        event_bus = self._get_or_create_event_bus()
        await event_bus.publish(event)

    def _summarize_audible(self, description: str) -> str:
        """把行动描述压成可听范围内的声音提示。

        Args:
            description: 完整的行动描述

        Returns:
            50 字内的声音提示
        """
        snippet = description[:50].rstrip()
        return f"从附近传来声响，似乎是有人在{snippet}……"

    async def _handle_结束(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        _ = rest_input
        _ = user_name

        state_manager = GameStateManager()
        state = await state_manager.get_world(group_id)
        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            session = state.session
            # 停止 NPC tick（多人模式可能已启动）
            await state.stop_npc_tick()
            player = session.players.get(user_id)
            if not player:
                await self.send_text("你不在游戏中。")
                return False, "不在游戏中", 2
            if not await self._ensure_active_game_session(session):
                return False, "游戏未开始", 2
            if session.game_mode == GameModes.MULTI.value:
                host_id = self._get_multiplayer_host_id(session)
                if host_id and user_id != host_id:
                    await self.send_text(
                        "多人模式仅房主可使用 `/rg 结束` 结算整局。\n"
                        "如果你只是想退出当前游戏，请使用 `/rg 离开`。"
                    )
                    return False, "仅房主可结束", 2

            await self.send_text("正在判定结局...")
            ending = await self._ending_judge.judge_ending(session=session, player=player)

            session.status = GameStatus.ENDED
            session.ended_at = datetime.now()

            forced_end = player.status == PlayerStatus.ALIVE and not session.has_cleared
            ending_type = str(getattr(ending, "ending_type", "") or "")
            hide_explain = forced_end or ending_type == "failed" or player.status != PlayerStatus.ALIVE

            reasoning_analysis = "" if hide_explain else str(getattr(ending, "reasoning_analysis", "") or "")
            truth_revealed = False if hide_explain else bool(getattr(ending, "truth_revealed", False))
            hidden_truth = session.hidden_truth if truth_revealed else None

            image_generator = self.get_image_generator()
            ending_image = await image_generator.generate_ending_image(
                ending_title=ending.title,
                ending_description=ending.description,
                reasoning_analysis=reasoning_analysis,
                truth_revealed=truth_revealed,
                hidden_truth=hidden_truth,
                ending_type=ending.ending_type,
            )
            session.image_paths.append(ending_image)
            await self._send_image_path(ending_image)

            # 先清理本局生成的图片，再删除存档
            await SaveManager().mark_ended_and_cleanup(group_id)
            await state_manager.remove(group_id)
            await SaveManager().delete(group_id)

            logger.info("游戏结束: %s, 结局: %s", group_id, ending.ending_type)
            return True, "游戏已结束", 2
        except Exception as exc:
            logger.error("判定结局失败: %s", exc, exc_info=True)
            await self.send_text(f"判定结局时出错：{exc}")
            return False, "判定失败", 2
        finally:
            state.release_world()

    async def _handle_帮助(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        _ = group_id
        _ = user_id
        _ = user_name
        _ = rest_input

        help_text = (
            "**规则怪谈游戏帮助**\n\n"
            "**命令列表**\n"
            "- `/rg 开始 单人` - 生成并开始单人游戏\n"
            "- `/rg 开始 多人` - 创建多人大厅\n"
            "- `/rg 开始 多人 开始` - 房主在人数到齐后正式开局\n"
            "- `/rg 强制开始 单人/多人` - 覆盖当前进度并重新开始\n"
            "- `/rg 恢复` - 恢复默认存档\n"
            "- `/rg 保存 <名称>` - 手动保存\n"
            "- `/rg 读取 <名称>` - 读取指定存档\n"
            "- `/rg 存档列表` - 查看存档\n"
            "- `/rg 清理存档` - 清理已结束存档和过期图片缓存\n"
            "- `/rg 加入` - 加入多人大厅\n"
            "- `/rg 身份` - 重新获取你的身份任务卡\n"
            "- `/rg 离开` - 离开当前游戏\n"
            "- `/rg 状态` - 查看当前状态\n"
            "- `/rg 剧情` - 重发剧情导入\n"
            "- `/rg 规则` - 查看你的规则笔记\n"
            "- `/rg 记录规则 <内容>` - 手动记录规则笔记\n"
            "- `/rg 场景` - 查看场景整体印象和当前位置\n"
            "- `/rg 区域` - 查看场景中的全部区域\n"
            "- `/rg 道具 [名称]` - 查看道具列表或详情\n"
            "- `/rg 线索 [名称]` - 查看已整理出的线索\n"
            "- `/rg 提示 <规则/线索>` - 获取非剧透提示\n"
            "- `/rg 推理 <内容>` - 记录推理\n"
            "- `/rg 行动 <描述>` - 推进行动\n"
            "- `/rg 继续` - 达成目标后继续探索，冲击完美结局\n"
            "- `/rg 结束` - 结束游戏并判定结局\n\n"
            "**提示**\n"
            "- 规则不会一次性全部告诉你，探索、观察和推理都很重要。"
        )
        await self.send_text(help_text)
        return True, "帮助已发送", 2

    async def _handle_剧情(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        _ = user_id
        _ = user_name
        _ = rest_input

        state_manager = GameStateManager()
        state = await state_manager.get_world(group_id)
        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            session = state.session
            if not await self._ensure_active_game_session(session):
                return False, "游戏未开始", 2

            image_generator = self.get_image_generator()

            scene_image = await image_generator.generate_scene_image(
                scene_name=session.scene_name,
                background=session.background,
                player_identity=session.player_identity,
                core_symbols=getattr(session, "core_symbols", None),
                use_cache=True,
            )
            session.image_paths.append(scene_image)
            await self._send_image_path(scene_image)

            entrance_description = None
            if isinstance(getattr(session, "environment_state", None), dict):
                entrance_description = session.environment_state.get("entrance_description")

            npc_guidance = getattr(session, "npc_guidance", {}) or {}
            if entrance_description and self._has_opening_guidance(session):
                entrance_long_image = await image_generator.generate_entrance_long_image(
                    scene_name=session.scene_name,
                    entrance_description=str(entrance_description),
                    npc_guidance=npc_guidance,
                    use_cache=True,
                )
                session.image_paths.append(entrance_long_image)
                await self._send_image_path(entrance_long_image)

            return True, "剧情已显示", 2
        except Exception as exc:
            logger.error("显示剧情失败: %s", exc, exc_info=True)
            await self.send_text(f"显示剧情时出错：{exc}")
            return False, "显示失败", 2
        finally:
            state.release_world()

    async def _handle_道具(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        return await self._handle_物品栏(group_id, user_id, user_name, rest_input)

    async def _handle_清理存档(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        _ = user_id
        _ = user_name
        _ = rest_input

        save_manager = SaveManager()
        try:
            cleaned_saves = await save_manager.cleanup_ended_saves(group_id)
        except Exception as exc:
            logger.error("清理已结束存档失败: %s", exc, exc_info=True)
            cleaned_saves = 0

        cleaned_images = 0
        try:
            import time
            from pathlib import Path

            cutoff = time.time() - 30 * 86400
            temp_dir = Path(self._temp_images_dir)
            if temp_dir.exists():
                for path in temp_dir.rglob("*"):
                    if not path.is_file() or path.name == "cache_index.json":
                        continue
                    try:
                        if path.stat().st_mtime < cutoff:
                            path.unlink()
                            cleaned_images += 1
                    except Exception:
                        continue
        except Exception as exc:
            logger.error("清理图片缓存失败: %s", exc, exc_info=True)

        await self.send_text(
            "**清理完成**\n\n"
            f"- 已清理已结束存档：{cleaned_saves} 个\n"
            f"- 已清理过期图片缓存：{cleaned_images} 个（>30天）"
        )
        return True, "清理完成", 2

    async def _handle_场景(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        _ = user_id
        _ = user_name
        _ = rest_input

        state_manager = GameStateManager()
        state = await state_manager.get_world(group_id)
        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            if not await self._ensure_active_game_session(state.session):
                return False, "游戏未开始", 2

            session = state.session
            scene_structure = getattr(session, "scene_structure", {}) or {}
            if not scene_structure:
                await self.send_text("这里的整体样子暂时还理不清，只能继续边走边看。")
                return False, "无场景结构", 2

            player = session.players.get(user_id)
            current_location = str(getattr(player, "location", "") or "").strip() if player else ""
            await self.send_text(self._build_scene_overview_text(session, current_location=current_location, plural=False))
            return True, "场景已显示", 2
        finally:
            state.release_world()

    async def _handle_区域(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        _ = user_id
        _ = user_name
        _ = rest_input

        state_manager = GameStateManager()
        state = await state_manager.get_world(group_id)
        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            if not await self._ensure_active_game_session(state.session):
                return False, "游戏未开始", 2

            areas = self._collect_scene_area_names(state.session)
            if not areas:
                await self.send_text("这里的区域暂时还无法确认。")
                return False, "无区域信息", 2

            lines = ["**场景区域**", ""]
            lines.extend(f"{index}. {area}" for index, area in enumerate(areas, start=1))
            await self.send_text("\n".join(lines))
            return True, "区域已显示", 2
        finally:
            state.release_world()

    async def _handle_物品栏(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        state_manager = GameStateManager()
        state = await state_manager.get_world(group_id)
        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            if not await self._ensure_active_game_session(state.session):
                return False, "游戏未开始", 2

            player = state.session.players.get(user_id)
            if not player:
                await self.send_text("你不在游戏中。")
                return False, "不在游戏中", 2

            inventory = getattr(player, "inventory", []) or []
            if not inventory:
                await self.send_text("你摸了摸身上，暂时没有什么能拿得出手的东西。")
                return True, "物品栏已显示", 2

            query = (rest_input or "").strip()
            if query:
                matches: list[dict[str, Any]] = []
                for item in inventory:
                    if isinstance(item, dict) and query in str(item.get("name", "") or ""):
                        matches.append(item)

                if not matches:
                    await self.send_text(f"未找到道具：{query}\n\n你可以使用 `/rg 道具` 查看道具列表。")
                    return False, "未找到道具", 2
                if len(matches) > 3:
                    await self.send_text(f"匹配到多个道具（{len(matches)}个），请提供更精确的名称。")
                    return False, "匹配过多", 2

                lines = ["你把东西拿到眼前仔细看了看：", ""]
                for item in matches:
                    name = item.get("name", "未知")
                    item_type = item.get("type", "物品")
                    description = item.get("description", "")
                    observation_hint = item.get("observation_hint", "")
                    is_key = bool(item.get("is_key_item", False))
                    lines.append(f"{name}{'（关键物品）' if is_key else ''}")
                    lines.append(f"大致算是：{item_type}")
                    if description:
                        lines.append(description)
                    if observation_hint:
                        lines.append(f"细看之下，你会注意到：{observation_hint}")
                    lines.append("")

                await self.send_text("\n".join(lines).strip())
                return True, "道具详情已显示", 2

            try:
                inventory_image = await self.get_image_generator().generate_inventory_image(
                    inventory_data=inventory,
                    player_name=user_name,
                    use_cache=True,
                )
                session.image_paths.append(inventory_image)
                await self._send_image_path(inventory_image)
            except Exception as exc:
                logger.debug("生成或发送道具图片失败，回退为文本: %s", exc)

            lines = [f"你现在随身带着这些东西：", ""]
            for index, item in enumerate(inventory, start=1):
                if isinstance(item, dict):
                    name = item.get("name", "未知物品")
                    description = item.get("description", "")
                    key_marker = "（关键）" if bool(item.get("is_key_item", False)) else ""
                    lines.append(f"{index}. {name}{key_marker}")
                    if description:
                        lines.append(f"   {description}")
                else:
                    lines.append(f"{index}. {item}")
            lines.append("")
            lines.append("如果想把某件东西单独拿出来看清楚，可以用 `/rg 道具 <名称>`。")
            await self.send_text("\n".join(lines))
            return True, "物品栏已显示", 2
        finally:
            state.release_world()

    async def _handle_背包(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        return await self._handle_物品栏(group_id, user_id, user_name, rest_input)

    async def _handle_继续(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        _ = user_id
        _ = user_name
        _ = rest_input

        state_manager = GameStateManager()
        state = await state_manager.get_world(group_id)
        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            if not await self._ensure_active_game_session(state.session):
                return False, "游戏未开始", 2

            if not state.session.has_cleared:
                await self.send_text("你还没有达成目标，先继续探索吧。")
                return False, "未通关", 2

            await self.send_text(
                "你已经摸到了离开的路，但故事还没有被你看完。\n\n"
                "如果你还想继续追下去，就继续用 `/rg 行动 <行动描述>` 往前走。"
            )
            return True, "继续探索", 2
        finally:
            state.release_world()

    async def _handle_恢复(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        _ = user_id
        _ = user_name
        _ = rest_input

        save_manager = SaveManager()
        try:
            session = await save_manager.load(group_id)
            if not session:
                await self.send_text("未找到存档。请使用 `/rg 开始` 开始新游戏。")
                return False, "无存档", 2
            if session.status == GameStatus.ENDED:
                await self.send_text("该存档已结束。请使用 `/rg 开始` 开始新游戏。")
                return False, "存档已结束", 2

            state_manager = GameStateManager()
            state = await state_manager.get_world_or_create(group_id)
            try:
                self.rehydrate_session_runtime(session, group_id)
                state.session = session
            finally:
                state.release_world()

            await self.send_text(
                f"你重新接上了《{session.scene_name}》这局游戏里的进度。\n\n"
                f"当前是{session.game_mode}模式，一共有 {len(session.players)} 名玩家还在局内。"
            )
            return True, "存档已恢复", 2
        except Exception as exc:
            logger.error("恢复存档失败: %s", exc, exc_info=True)
            await self.send_text(f"恢复存档时出错：{exc}")
            return False, "恢复失败", 2

    async def _handle_保存(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        _ = user_id
        _ = user_name

        save_name = str(rest_input or "").strip()
        if not save_name:
            await self.send_text("请提供存档名称。用法：`/rg 保存 <存档名称>`")
            return False, "缺少存档名称", 2

        state_manager = GameStateManager()
        state = await state_manager.get_world(group_id)
        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            ok = await SaveManager().save_with_name(group_id, state.session, save_name)
            if not ok:
                await self.send_text("存档保存失败，请稍后重试。")
                return False, "保存失败", 2

            await self.send_text(f"已经替你把当前进度收好了，存档名是“{save_name}”。")
            return True, "存档已保存", 2
        except Exception as exc:
            logger.error("保存存档失败: %s", exc, exc_info=True)
            await self.send_text(f"保存存档时出错：{exc}")
            return False, "保存失败", 2
        finally:
            state.release_world()

    async def _handle_读取(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        _ = user_id
        _ = user_name

        save_name = str(rest_input or "").strip()
        if not save_name:
            await self.send_text("请提供存档名称。用法：`/rg 读取 <存档名称>`")
            return False, "缺少存档名称", 2

        try:
            session = await SaveManager().load_with_name(group_id, save_name)
            if not session:
                await self.send_text(f"未找到存档：{save_name}")
                return False, "无存档", 2
            if session.status == GameStatus.ENDED:
                await self.send_text(f"存档 {save_name} 已结束。")
                return False, "存档已结束", 2

            state_manager = GameStateManager()
            state = await state_manager.get_world_or_create(group_id)
            try:
                self.rehydrate_session_runtime(session, group_id)
                state.session = session
            finally:
                state.release_world()

            await self.send_text(
                f"**存档已读取**\n\n"
                f"存档名称：{save_name}\n"
                f"场景：{session.scene_name}\n"
                f"模式：{session.game_mode}\n"
                f"玩家数：{len(session.players)}\n\n"
                "使用 `/rg 状态` 查看详细信息。"
            )
            return True, "存档已读取", 2
        except Exception as exc:
            logger.error("读取存档失败: %s", exc, exc_info=True)
            await self.send_text(f"读取存档时出错：{exc}")
            return False, "读取失败", 2

    async def _handle_存档列表(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        _ = user_id
        _ = user_name
        _ = rest_input

        try:
            saves = [save for save in await SaveManager().list_saves() if save.get("group_id") == group_id]
            if not saves:
                await self.send_text("**存档列表**\n\n暂无存档。")
                return True, "存档列表已显示", 2

            lines = ["**存档列表**", ""]
            for index, save in enumerate(saves, start=1):
                name = save.get("name") or ("默认存档" if not save.get("is_named") else "未命名")
                lines.append(
                    f"{index}. {name}\n"
                    f"   场景：{save.get('scene_name', '未知')}\n"
                    f"   模式：{save.get('game_mode', '未知')}\n"
                    f"   状态：{save.get('status', 'unknown')}\n"
                    f"   时间：{save.get('saved_at', '未知时间')}"
                )

            await self.send_text("\n".join(lines))
            return True, "存档列表已显示", 2
        except Exception as exc:
            logger.error("查看存档列表失败: %s", exc, exc_info=True)
            await self.send_text(f"查看存档列表时出错：{exc}")
            return False, "查看失败", 2

    _handle_leave = _handle_离开
    _handle_status = _handle_状态
    _handle_rules = _handle_规则
    _handle_clues = _handle_线索
    _handle_hint = _handle_提示
    _handle_reason = _handle_推理
    _handle_record_rule = _handle_记录规则
    _handle_action = _handle_行动
    _handle_end = _handle_结束
    _handle_help = _handle_帮助
    _handle_scene = _handle_场景
    _handle_areas = _handle_区域
    _handle_plot = _handle_剧情
    _handle_story = _handle_剧情
    _handle_item = _handle_道具
    _handle_items = _handle_道具
    _handle_inventory = _handle_物品栏
    _handle_bag = _handle_背包
    _handle_continue = _handle_继续
    _handle_restore = _handle_恢复
    _handle_save = _handle_保存
    _handle_load = _handle_读取
    _handle_save_list = _handle_存档列表
    _handle_cleanup_saves = _handle_清理存档
