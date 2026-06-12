from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from ..common import GameModes
from ..core import (
    GameStateManager,
    GameStatus,
    LLMClient,
    PlayerStatus,
    SaveManager,
    get_default_max_tokens,
)

logger = logging.getLogger(__name__)


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
        state = await state_manager.get(group_id)

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
            state.release()

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
        state = await state_manager.get(group_id)
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
            state.release()

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
        state = await state_manager.get(group_id)
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
            state.release()

    async def _handle_线索(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        state_manager = GameStateManager()
        state = await state_manager.get(group_id)
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
            state.release()

    async def _handle_提示(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        hint_type = (rest_input or "规则").strip()

        state_manager = GameStateManager()
        state = await state_manager.get(group_id)
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
            want_clue = "线索" in hint_type
            hint_mode = "clue" if want_clue else "rule"

            all_rules = [self._extract_rule_text(rule) for rule in (session.rules or [])]
            all_rules = [text for text in all_rules if text]
            player_rules = self._get_player_rules_for_display(session, player)

            target_rule_index_1b: int | None = None
            target_rule_text = ""
            if not want_clue:
                match = re.search(r"(\d{1,2})", hint_type)
                if match:
                    try:
                        index = int(match.group(1))
                        if 1 <= index <= len(player_rules):
                            target_rule_index_1b = index
                    except Exception:
                        target_rule_index_1b = None

                if target_rule_index_1b is None and player_rules:
                    target_rule_index_1b = len(player_rules)

                if target_rule_index_1b is not None and 1 <= target_rule_index_1b <= len(player_rules):
                    target_rule_text = player_rules[target_rule_index_1b - 1]

            inventory = getattr(player, "inventory", []) or []
            clue_query = hint_type.replace("线索", "", 1).strip() if want_clue else ""
            selected_item: dict[str, Any] | None = None
            if want_clue and inventory:
                if clue_query:
                    for item in inventory:
                        if isinstance(item, dict) and clue_query in str(item.get("name", "") or ""):
                            selected_item = item
                            break
                if selected_item is None:
                    for item in reversed(inventory):
                        if isinstance(item, dict) and bool(item.get("is_key_item", False)):
                            selected_item = item
                            break
                if selected_item is None:
                    for item in reversed(inventory):
                        if isinstance(item, dict) and str(item.get("name", "") or "").strip():
                            selected_item = item
                            break

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

            selected_item_text = format_item(selected_item) if isinstance(selected_item, dict) else ""
            rules_block = "\n".join(f"{index + 1}. {text}" for index, text in enumerate(all_rules))
            known_rules_block = "\n".join(f"- {text}" for text in player_rules) if player_rules else "（暂无）"

            inventory_lines: list[str] = []
            for item in inventory:
                if isinstance(item, dict) and str(item.get("name", "") or "").strip():
                    inventory_lines.append(format_item(item))
                elif item:
                    inventory_lines.append(str(item))
            inventory_block = "\n".join(f"- {text}" for text in inventory_lines) if inventory_lines else "（空）"

            system_prompt = (
                "你是规则怪谈游戏的提示生成器。你知道后台完整规则与隐藏真相，但必须严格控制剧透。\n"
                "硬性要求：\n"
                "1) 只输出 JSON，不要 markdown，不要多余文字。\n"
                "2) 禁止直接复述或泄露隐藏真相；禁止给出完整答案。\n"
                "3) 提示要可执行，必须给出一个低风险下一步建议。\n"
                "4) kind 必须与请求一致：rule 或 clue。\n"
                "5) rule 模式只能围绕一条玩家已知规则；clue 模式只能围绕一个物品。\n\n"
                "输出 JSON：\n"
                '- rule：{"kind":"rule","rule_index":1,"hint":"...","next_action":"..."}\n'
                '- clue：{"kind":"clue","item":"...","hint":"...","next_action":"..."}'
            )

            user_prompt = (
                f"本次提示类型(kind)：{hint_mode}\n"
                f"场景：{session.scene_name}\n"
                f"背景：{session.background}\n"
                f"通关条件：{session.win_condition}\n"
                f"隐藏真相（仅供内部推理，禁止输出）：{session.hidden_truth}\n\n"
                f"完整规则表：\n{rules_block if rules_block else '（无）'}\n\n"
                f"玩家规则笔记：\n{known_rules_block}\n\n"
                f"玩家状态：理智{player.sanity}/100 体力{player.health}/100 位置:{player.location}\n\n"
                f"背包物品：\n{inventory_block}\n\n"
            )
            if hint_mode == "rule":
                if target_rule_text:
                    user_prompt += (
                        f"本次必须点评的规则笔记编号：{target_rule_index_1b or 1}\n"
                        f"规则内容：{target_rule_text}\n"
                    )
                else:
                    user_prompt += "玩家当前没有规则笔记，请给出如何低风险获得规则信息的提示。\n"
            elif selected_item_text:
                user_prompt += f"本次必须提示的物品：{selected_item_text}\n"
            else:
                user_prompt += "玩家背包为空，请给出如何低风险获得线索的提示。\n"

            hint_text = ""
            next_action = ""
            try:
                response = await LLMClient().call(
                    prompt=user_prompt,
                    system_prompt=system_prompt,
                    temperature=0.4,
                    max_tokens=min(600, get_default_max_tokens()),
                )
                data = response.parse_json()
                if isinstance(data, dict):
                    hint_text = str(data.get("hint", "") or "").strip()
                    next_action = str(data.get("next_action", "") or "").strip()
                    hidden_truth = str(getattr(session, "hidden_truth", "") or "")
                    hidden_truth_probe = hidden_truth[:20].strip() if hidden_truth else ""
                    if hidden_truth_probe and (hidden_truth_probe in hint_text or hidden_truth_probe in next_action):
                        logger.warning("LLM 提示疑似泄露隐藏真相片段，已回退到兜底提示")
                        hint_text = ""
                        next_action = ""
            except Exception as exc:
                logger.warning("LLM 提示生成失败，将使用兜底提示: %s", exc)

            if not hint_text:
                if want_clue:
                    hint_text = (
                        "先别急着做高风险动作。优先检查书写痕迹、标签、票据、墙面告示，"
                        "再观察这些信息与当前场景、身份任务是否存在对应关系。"
                    )
                elif player_rules:
                    hint_text = (
                        "把这条规则拆成三个部分再验证：触发条件、必须动作、禁止动作。"
                        "先用最小代价确认它的触发条件是否真的出现。"
                    )
                else:
                    hint_text = (
                        "你现在更需要先获得信息，而不是直接冒险。"
                        "尝试观察 NPC 行为、搜索当前位置，或检查任何像告示、值班记录、便签的载体。"
                    )

            hint_message = f"{hint_text}\n\n下一步建议：{next_action}" if next_action else hint_text
            await SaveManager().schedule_save(group_id, session)
            await self.send_text(f"**提示（你还剩{player.hint_count}次）**\n\n{hint_message}")
            return True, "提示已发送", 2
        except Exception as exc:
            logger.error("生成提示失败: %s", exc, exc_info=True)
            await self.send_text(f"生成提示时出错：{exc}")
            return False, "生成失败", 2
        finally:
            state.release()

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
        state = await state_manager.get(group_id)
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
            state.release()

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
        state = await state_manager.get(group_id)
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
            state.release()

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
        state = await state_manager.get(group_id)
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

            self._ensure_story_runtime(
                session,
                game_mode=getattr(session, "game_mode", None),
                initial_player_id=user_id,
            )
            result = await self._action_processor.process_action(
                action=action_text,
                player=player,
                session=session,
                group_id=group_id,
            )

            now = datetime.now()
            player.action_history.append({"action": action_text, "timestamp": now.isoformat()})
            player.last_action_at = now

            discovered_carriers = self._discover_rule_carriers_for_player(session, player, action_text)

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
                    await self._get_or_create_npc_simulator().simulate_after_action(
                        session,
                        player,
                        action_text,
                        action_payload,
                    )
                    npc_perception = self._get_or_create_npc_simulator().build_perception_for_player(session, player)
                except Exception as exc:
                    logger.warning("NPC 模拟执行失败: %s", exc)

            if not session.has_cleared:
                has_cleared = await self._ending_judge.check_win_condition(session=session, player=player)
                if has_cleared:
                    session.has_cleared = True

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
                    "你已经碰到了离开的条件。\n\n"
                    "如果还想继续深挖，就用 `/rg 继续`；如果准备收束这一局，就用 `/rg 结束`。"
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
        except Exception as exc:
            logger.error("处理行动失败: %s", exc, exc_info=True)
            await self.send_text(f"处理行动时出错：{exc}")
            return False, "处理失败", 2
        finally:
            state.release()

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
        state = await state_manager.get(group_id)
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
            await self._send_image_path(ending_image)

            await state_manager.remove(group_id)
            await SaveManager().delete(group_id)

            logger.info("游戏结束: %s, 结局: %s", group_id, ending.ending_type)
            return True, "游戏已结束", 2
        except Exception as exc:
            logger.error("判定结局失败: %s", exc, exc_info=True)
            await self.send_text(f"判定结局时出错：{exc}")
            return False, "判定失败", 2
        finally:
            state.release()

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
            "- `/rg 场景` - 回看你对场景的整体印象\n"
            "- `/rg 道具 [名称]` - 查看道具列表或详情\n"
            "- `/rg 线索 [名称]` - 查看已整理出的线索\n"
            "- `/rg 提示 <规则/线索>` - 获取非剧透提示\n"
            "- `/rg 推理 <内容>` - 记录推理\n"
            "- `/rg 行动 <描述>` - 推进行动\n"
            "- `/rg 继续` - 达成通关后继续探索\n"
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
        state = await state_manager.get(group_id)
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
                arrival_reason=session.player_identity,
                core_symbols=getattr(session, "core_symbols", None),
                use_cache=True,
            )
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
                await self._send_image_path(entrance_long_image)

            return True, "剧情已显示", 2
        except Exception as exc:
            logger.error("显示剧情失败: %s", exc, exc_info=True)
            await self.send_text(f"显示剧情时出错：{exc}")
            return False, "显示失败", 2
        finally:
            state.release()

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
        state = await state_manager.get(group_id)
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
            state.release()

    async def _handle_物品栏(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        state_manager = GameStateManager()
        state = await state_manager.get(group_id)
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
            state.release()

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
        state = await state_manager.get(group_id)
        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            if not await self._ensure_active_game_session(state.session):
                return False, "游戏未开始", 2

            if not state.session.has_cleared:
                await self.send_text("你还未达成通关条件，请继续探索。")
                return False, "未通关", 2

            await self.send_text(
                "你已经摸到了离开的路，但故事还没有被你看完。\n\n"
                "如果你还想继续追下去，就继续用 `/rg 行动 <行动描述>` 往前走。"
            )
            return True, "继续探索", 2
        finally:
            state.release()

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
            state = await state_manager.get_or_create(group_id)
            try:
                self.rehydrate_session_runtime(session, group_id)
                state.session = session
            finally:
                state.release()

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
        state = await state_manager.get(group_id)
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
            state.release()

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
            state = await state_manager.get_or_create(group_id)
            try:
                self.rehydrate_session_runtime(session, group_id)
                state.session = session
            finally:
                state.release()

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
