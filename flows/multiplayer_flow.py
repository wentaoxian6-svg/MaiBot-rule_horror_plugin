from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING

from ..common import GameModes, JsonObject
from ..core import GameSession, GameStateManager, GameStatus, Player, SaveManager
from ..helpers import assign_multiplayer_identities

if TYPE_CHECKING:
    from ..commands.handler import RuleHorrorCommand


logger = logging.getLogger(__name__)


class MultiplayerFlow:
    """多人模式流程编排。"""

    def __init__(self, command: RuleHorrorCommand) -> None:
        self.command = command

    async def handle_start(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        save_manager = SaveManager()
        existing = await save_manager.load(group_id)
        multi_start = False
        tail = ""

        raw = (rest_input or "").strip()
        if raw:
            m = re.match(rf"^({GameModes.MULTI.value})\s*(.*)$", raw)
            if m:
                tail = (m.group(2) or "").strip()
            if tail and re.search(r"(开始|生成|确认|立即|立刻|start|go)", tail, flags=re.IGNORECASE):
                multi_start = True

        state_manager = GameStateManager()
        state = await state_manager.get_world_or_create(group_id)
        lobby_players: list[tuple[str, str]] = []
        lobby_order: list[str] = []

        try:
            sess = state.session
            if sess and sess.game_mode == GameModes.MULTI.value and sess.status == GameStatus.ACTIVE:
                await self.command.send_text(
                    "当前已有正在进行的多人游戏。\n"
                    "请继续游玩，或使用 `/rg 结束` 结束后再重新开始。"
                )
                return False, "游戏进行中", 2

            lobby: GameSession | None = None
            if (
                sess
                and sess.game_mode == GameModes.MULTI.value
                and sess.status == GameStatus.WAITING
                and isinstance(getattr(sess, "environment_state", None), dict)
                and isinstance(sess.environment_state.get("lobby"), dict)
            ):
                lobby = sess

            if (
                lobby is None
                and existing
                and existing.game_mode == GameModes.MULTI.value
                and existing.status == GameStatus.WAITING
                and isinstance(getattr(existing, "environment_state", None), dict)
                and isinstance(existing.environment_state.get("lobby"), dict)
            ):
                lobby = existing
                state.session = lobby

            if lobby is None and existing and existing.status == GameStatus.ACTIVE:
                await self.command.send_text(
                    "**发现存档**\n\n"
                    "该群组已有未完成的多人游戏存档。\n"
                    "请使用 `/rg 恢复` 恢复存档，或使用 `/rg 强制开始 多人` 覆盖存档。"
                )
                return False, "存在存档", 2

            if lobby is None:
                lobby = self._create_lobby(group_id, user_id, user_name)
                state.session = lobby
                await save_manager.save_immediately(group_id, lobby)
                await self._send_lobby_created(user_name)
                return True, "大厅已创建", 2

            if not multi_start:
                lobby_meta = lobby.environment_state.get("lobby", {}) if isinstance(lobby.environment_state, dict) else {}
                host_name = str(lobby_meta.get("host_name") or "房主")
                players_disp = "、".join(player.name for player in lobby.players.values()) or "（无）"
                if user_id in lobby.players:
                    await self.command.send_text(
                        "**多人模式大厅已存在**\n\n"
                        f"房主：{host_name}\n"
                        f"当前人数：{len(lobby.players)}/5\n"
                        f"玩家：{players_disp}\n\n"
                        "等待房主发送 `/rg 开始 多人 开始` 生成开局。"
                    )
                    return True, "大厅已存在", 2

                await self.command.send_text(
                    "**多人模式大厅已存在**\n\n"
                    f"房主：{host_name}\n"
                    f"当前人数：{len(lobby.players)}/5\n"
                    f"玩家：{players_disp}\n\n"
                    "请先发送 `/rg 加入` 加入大厅。"
                )
                return False, "大厅已存在", 2

            env_state = lobby.environment_state
            lobby_meta = env_state.get("lobby", {}) if isinstance(env_state.get("lobby"), dict) else {}
            host_id = str(lobby_meta.get("host_id") or "")
            host_name = str(lobby_meta.get("host_name") or "房主")

            if user_id != host_id:
                await self.command.send_text(
                    f"当前已有多人大厅，由 {host_name} 创建。\n"
                    "请使用 `/rg 加入` 加入，等待房主开始生成。"
                )
                return False, "非房主", 2

            order = env_state.get("lobby_player_order", [])
            if not isinstance(order, list):
                order = []
            for pid in list(lobby.players.keys()):
                if pid not in order:
                    order.append(pid)
            env_state["lobby_player_order"] = order

            if len(lobby.players) < 2:
                await self.command.send_text("多人模式至少需要 2 名玩家。请先让其他玩家使用 `/rg 加入`。")
                return False, "人数不足", 2

            target_players = lobby_meta.get("target_players")
            if isinstance(target_players, int) and target_players > 0 and len(lobby.players) < target_players:
                await self.command.send_text(
                    f"这个大厅的目标人数是 {target_players} 人，目前只有 {len(lobby.players)} 人。\n"
                    "请等人数到齐后再发送 `/rg 开始 多人 开始`。"
                )
                return False, "人数未到齐", 2

            lobby_order = list(order)
            lobby_players = [(pid, lobby.players[pid].name) for pid in lobby_order if pid in lobby.players]
            known_pids = {pid for pid, _ in lobby_players}
            for pid, player in lobby.players.items():
                if pid not in known_pids:
                    lobby_players.append((pid, player.name))
                    lobby_order.append(pid)
        finally:
            state.release_world()

        lock = self.command.plugin.get_generation_lock(group_id)
        if lock.locked():
            await self.command.send_text("当前规则怪谈仍在生成中，请勿重复发送开始命令。")
            return False, "正在生成", 2

        async with lock:
            await self.command.send_text(
                "正在生成规则怪谈，请稍候。\n"
                "完成后会依次发送三张图：背景与身份、开场场景与对话、你们的目标，它们都属于同一局；QQ 预览若显示不全，请点开原图查看。"
            )

            try:
                session = await self.command._get_game_generator().generate_game(
                    group_id,
                    GameModes.MULTI.value,
                    player_count=len(lobby_players),
                    player_names=[name for _, name in lobby_players],
                    player_ids=[pid for pid, _ in lobby_players],
                )
                await self._activate_multiplayer_session(
                    session=session,
                    group_id=group_id,
                    lobby_players=lobby_players,
                    lobby_order=lobby_order,
                    use_cache=True,
                )
                logger.info("多人模式开始成功: %s", group_id)
                return True, "游戏已开始", 2
            except Exception as exc:
                logger.error("多人模式开始失败: %s", exc, exc_info=True)
                await self.command.send_text(f"生成游戏失败：{exc}\n请稍后重试。")
                return False, "生成失败", 2

    async def handle_force_start(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        multi_target: int | None = None
        raw = (rest_input or "").strip()
        if raw:
            m = re.match(rf"^({GameModes.MULTI.value})\s*(.*)$", raw)
            tail = (m.group(2) or "").strip() if m else ""
            if tail:
                m2 = re.search(r"(\d{1,2})", tail)
                if m2:
                    try:
                        n = int(m2.group(1))
                        if 2 <= n <= 5:
                            multi_target = n
                    except Exception:
                        pass

        state_manager = GameStateManager()
        await state_manager.remove(group_id)

        save_manager = SaveManager()
        await save_manager.mark_ended_and_cleanup(group_id)
        await save_manager.delete(group_id)

        state = await state_manager.get_world_or_create(group_id)
        try:
            lobby = self._create_lobby(group_id, user_id, user_name, target_players=multi_target)
            state.session = lobby
            await save_manager.save_immediately(group_id, lobby)
        finally:
            state.release_world()

        target_txt = f"{multi_target}人" if multi_target else "未指定人数"
        await self.command.send_text(
            "**多人模式大厅已创建**\n\n"
            f"房主：{user_name}\n"
            f"目标人数：{target_txt}\n"
            "当前人数：1\n\n"
            "其他玩家请发送 `/rg 加入` 加入。\n"
            "房主在人数到齐后发送 `/rg 开始 多人 开始` 生成开局。"
        )
        return True, "大厅已创建", 2

    async def handle_join(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
    ) -> tuple[bool, str | None, int]:
        state_manager = GameStateManager()
        state = await state_manager.get_world(group_id)
        if not state:
            restored_session = await SaveManager().load(group_id)
            if not restored_session:
                await self.command.send_text("当前没有正在进行的游戏。请先使用 `/rg 开始` 开始游戏")
                return False, "无游戏", 2
            state = await state_manager.get_world_or_create(group_id)
            self.command.rehydrate_session_runtime(restored_session, group_id)
            state.session = restored_session

        try:
            session = state.session
            if session.game_mode != GameModes.MULTI.value:
                await self.command.send_text("当前不是多人模式游戏。")
                return False, "非多人模式", 2
            if session.status != GameStatus.WAITING:
                await self.command.send_text("游戏已经开始，无法中途加入。")
                return False, "已开始", 2
            if user_id in session.players:
                await self.command.send_text("你已经在大厅里了。")
                return False, "已在大厅", 2
            if len(session.players) >= 5:
                await self.command.send_text("大厅人数已满（最多5人）。")
                return False, "人数已满", 2

            player = Player(player_id=user_id, name=user_name)
            success = session.add_player(player)
            if not success:
                await self.command.send_text("大厅人数已满（最多5人）。")
                return False, "人数已满", 2

            env_state = session.environment_state if isinstance(getattr(session, "environment_state", None), dict) else {}
            order = env_state.get("lobby_player_order", [])
            if not isinstance(order, list):
                order = []
            if user_id not in order:
                order.append(user_id)
            env_state["lobby_player_order"] = order
            session.environment_state = env_state

            lobby_meta = env_state.get("lobby", {}) if isinstance(env_state.get("lobby"), dict) else {}
            host_name = str(lobby_meta.get("host_name") or "房主")
            players_disp = "、".join([p.name for p in session.players.values()])

            save_manager = SaveManager()
            await save_manager.save_immediately(group_id, session)

            await self.command.send_text(
                "**加入成功**\n\n"
                f"{user_name} 加入了大厅。\n"
                f"当前人数：{len(session.players)}/5\n"
                f"玩家：{players_disp}\n\n"
                f"等待房主 {host_name} 开始生成：`/rg 开始 多人 开始`"
            )
            return True, "加入成功", 2
        finally:
            state.release_world()

    async def handle_identity(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
    ) -> tuple[bool, str | None, int]:
        state_manager = GameStateManager()
        state = await state_manager.get_world(group_id)
        if not state or not state.session:
            await self.command.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            session = state.session
            player = session.players.get(user_id)
            if not player:
                await self.command.send_text("你还没有加入游戏。请使用 `/rg 加入` 加入游戏。")
                return False, "未加入", 2
            if not player.identity:
                await self.command.send_text("你还没有被分配身份。请等待游戏开始后再查看。")
                return False, "无身份", 2

            content = self.command._build_player_private_brief(session, player)
            ok = await self.command._send_private_text(user_id, user_name, content)
            if ok:
                await self.command.send_text(f"{user_name}，你的身份信息已通过私聊发送，请查看。")
            else:
                await self.command.send_text(
                    f"{user_name}，私聊发送失败。为避免身份泄露，群内不会展示身份正文。"
                    "请先添加机器人好友或检查私聊权限，然后重新使用 `/rg 身份` 获取。"
                )
            return True, "身份已发送", 2
        finally:
            state.release_world()

    async def _activate_multiplayer_session(
        self,
        *,
        session: GameSession,
        group_id: str,
        lobby_players: list[tuple[str, str]],
        lobby_order: list[str],
        use_cache: bool,
    ) -> None:
        session.status = GameStatus.ACTIVE
        for pid, name in lobby_players:
            session.add_player(Player(player_id=pid, name=name))
        assign_multiplayer_identities(session, lobby_order or [pid for pid, _ in lobby_players])

        game_states: dict[str, JsonObject] = {
            group_id: {
                "scene": session.scene_name,
                "building_type": (
                    getattr(session, "scene_structure", {}).get("building_type", "未知建筑")
                    if isinstance(getattr(session, "scene_structure", {}), dict)
                    else "未知建筑"
                ),
            }
        }
        env_system = self.command._get_or_create_environment_system(game_states)
        session._environment_system = env_system
        environment_evolution: JsonObject | None = None
        try:
            environment_evolution = await env_system.initialize_environment(
                group_id=group_id,
                scene_type=session.scene_name,
                player_identity=session.player_identity,
                building_type=(
                    getattr(session, "scene_structure", {}).get("building_type", "未知建筑")
                    if isinstance(getattr(session, "scene_structure", {}), dict)
                    else "未知建筑"
                ),
            )
            logger.info("环境演化系统初始化完成: %s", group_id)
        except Exception as exc:
            logger.error("环境演化系统初始化失败: %s", exc)

        if not isinstance(getattr(session, "environment_state", None), dict):
            session.environment_state = {}
        env_state = session.environment_state
        if isinstance(environment_evolution, dict) and environment_evolution:
            env_state["environment_evolution"] = environment_evolution
        env_state["environment_snapshot"] = {
            "doors": {},
            "items": {},
            "lights": {},
            "walls": {},
            "floors": {},
            "objects": {},
            "atmosphere": {},
            "sounds": [],
            "smells": [],
            "temperature": 20.0,
            "humidity": 50.0,
            "entropy_level": 0.0,
        }
        session.time_manager = {
            "current_time": 0,
            "time_description": "开场时刻",
            "elapsed_minutes": 0,
        }
        session._rule_mutation_system = self.command._get_or_create_rule_mutation_system()
        env_state["rule_mutations"] = []
        env_state["discovered_clues"] = []

        # 房间级模型：player.location 即为权威位置，不再需要坐标级物理系统初始化

        state_manager = GameStateManager()
        state = await state_manager.get_world_or_create(group_id)
        try:
            state.session = session
            # 启动 NPC tick（多人模式专属）
            state.start_npc_tick(self.command)
            save_manager = SaveManager()
            await save_manager.save_immediately(group_id, session)
        finally:
            state.release_world()

        await self.command._send_multiplayer_private_infos(session, lobby_players, group_id)

        image_generator = self.command.get_image_generator()
        core_symbols = getattr(session, "core_symbols", None)
        scene_image = await image_generator.generate_scene_image(
            scene_name=session.scene_name,
            background=session.background,
            player_identity=session.player_identity,
            core_symbols=core_symbols,
            use_cache=use_cache,
        )
        session.image_paths.append(scene_image)
        await self.command._send_image_path(scene_image)
        await asyncio.sleep(1.0)

        entrance_description = await self.command._generate_entrance_description(session)
        if isinstance(getattr(session, "environment_state", None), dict):
            session.environment_state["entrance_description"] = entrance_description

        has_opening_guidance = self.command._has_opening_guidance(session)
        self.command._ensure_story_runtime(session, game_mode=GameModes.MULTI.value, initial_player_id=lobby_players[0][0] if lobby_players else None)

        if has_opening_guidance:
            entrance_long_image = await image_generator.generate_entrance_long_image(
                scene_name=session.scene_name,
                entrance_description=entrance_description,
                npc_guidance=getattr(session, "npc_guidance", {}) or {},
                use_cache=use_cache,
            )
            session.image_paths.append(entrance_long_image)
            await self.command._send_image_path(entrance_long_image)
            await asyncio.sleep(1.0)

        await self.command._send_initial_rule_exposure(session, GameModes.MULTI.value, lobby_players)
        await asyncio.sleep(1.0)

        players_disp = "、".join([p.name for p in session.players.values()]) if session.players else "（无）"
        await self.command.send_text(
            f"{players_disp} 都已经被卷进了《{session.scene_name}》。\n\n"
            "接下来直接用 `/rg 行动 <描述>` 推进；如果想重新确认局势，可以随时看 `/rg 状态`、`/rg 规则`、`/rg 场景`。"
        )

    def _create_lobby(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        *,
        target_players: int | None = None,
    ) -> GameSession:
        lobby = GameSession(group_id=group_id, game_mode=GameModes.MULTI.value, status=GameStatus.WAITING)
        lobby.environment_state = {
            "lobby": {
                "host_id": user_id,
                "host_name": user_name,
                "target_players": target_players,
                "created_at": datetime.now().isoformat(),
            },
            "lobby_player_order": [user_id],
        }
        lobby.add_player(Player(player_id=user_id, name=user_name))
        return lobby

    async def _send_lobby_created(self, user_name: str) -> None:
        await self.command.send_text(
            "**多人模式大厅已创建**\n\n"
            f"房主：{user_name}\n"
            "当前人数：1/5\n\n"
            "其他玩家请发送 `/rg 加入` 加入。\n"
            "房主在人数到齐后发送 `/rg 开始 多人 开始` 生成开局。"
        )
