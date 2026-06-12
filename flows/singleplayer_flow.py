from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ..core import AsyncImageGenerator, GameStateManager, GameStatus, Player, SaveManager
from ..common import GameModes, JsonObject
from ..systems import DoorState, EnvironmentState, LightState

if TYPE_CHECKING:
    from ..commands.handler import RuleHorrorCommand
    from ..core import GameSession


logger = logging.getLogger(__name__)


class SingleplayerFlow:
    """单人模式流程编排。"""

    def __init__(self, command: RuleHorrorCommand) -> None:
        self.command = command

    async def handle_start(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
    ) -> tuple[bool, str | None, int]:
        save_manager = SaveManager()
        existing = await save_manager.load(group_id)
        if existing and existing.status == GameStatus.ACTIVE:
            await self.command.send_text(
                "**发现存档**\n\n"
                "该群组/用户已有未完成的游戏存档。\n"
                "请使用 `/rg 恢复` 恢复存档，或使用 `/rg 强制开始` 覆盖存档。"
            )
            return False, "存在存档", 2

        await self.command.send_text("正在生成规则怪谈，请稍候..")
        try:
            session = await self.command._get_game_generator().generate_game(group_id, GameModes.SINGLE.value)
            await self._activate_single_session(
                session=session,
                group_id=group_id,
                user_id=user_id,
                user_name=user_name,
                use_cache=True,
            )
            logger.info("单人模式开始成功: %s", group_id)
            return True, "游戏已开始", 2
        except Exception as exc:
            logger.error("单人模式开始失败: %s", exc, exc_info=True)
            await self.command.send_text(f"生成游戏失败：{exc}\n请稍后重试。")
            return False, "生成失败", 2

    async def handle_force_start(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
    ) -> tuple[bool, str | None, int]:
        state_manager = GameStateManager()
        await state_manager.remove(group_id)

        save_manager = SaveManager()
        await save_manager.mark_ended_and_cleanup(group_id)
        await save_manager.delete(group_id)

        await self.command.send_text("正在生成规则怪谈，请稍候..")
        try:
            session = await self.command._get_game_generator().generate_game(group_id, GameModes.SINGLE.value)
            await self._activate_single_session(
                session=session,
                group_id=group_id,
                user_id=user_id,
                user_name=user_name,
                use_cache=False,
            )
            logger.info("单人模式强制开始成功: %s", group_id)
            return True, "游戏已开始", 2
        except Exception as exc:
            logger.error("单人模式强制开始失败: %s", exc, exc_info=True)
            await self.command.send_text(f"生成游戏失败：{exc}\n请稍后重试。")
            return False, "生成失败", 2

    async def _activate_single_session(
        self,
        *,
        session: GameSession,
        group_id: str,
        user_id: str,
        user_name: str,
        use_cache: bool,
    ) -> None:
        session.status = GameStatus.ACTIVE
        session.add_player(Player(player_id=user_id, name=user_name))

        game_states: dict[str, JsonObject] = {group_id: {}}
        env_system = self.command._get_or_create_environment_system(game_states)
        session._environment_system = env_system

        try:
            await env_system.initialize_environment(
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
        env_snapshot = EnvironmentState()
        env_snapshot.set_door_state("entrance_door", DoorState.LOCKED)
        env_snapshot.set_light_state("main_hall_light", LightState.DIM)
        env_snapshot.set_temperature(18.0)
        env_snapshot.add_sound("远处的风声")
        env_snapshot.add_smell("陈旧的霉味")
        env_state["environment_snapshot"] = env_snapshot.to_dict()

        session.time_manager = {
            "current_time": 0,
            "time_description": "深夜（午夜时分，周围一片死寂）",
            "elapsed_minutes": 0,
        }

        rule_mutation = self.command._get_or_create_rule_mutation_system()
        session._rule_mutation_system = rule_mutation
        env_state["rule_mutations"] = []

        clue_system = self.command._get_or_create_clue_discovery_system()
        _ = clue_system
        env_state["discovered_clues"] = []

        state_manager = GameStateManager()
        state = await state_manager.get_or_create(group_id)
        try:
            state.session = session
            save_manager = SaveManager()
            await save_manager.save_immediately(group_id, session)
        finally:
            state.release()

        image_generator = self.command.get_image_generator()
        core_symbols = getattr(session, "core_symbols", None)
        scene_image = await image_generator.generate_scene_image(
            scene_name=session.scene_name,
            background=session.background,
            arrival_reason=session.player_identity,
            core_symbols=core_symbols,
            use_cache=use_cache,
        )
        await self.command._send_image_path(scene_image)
        await asyncio.sleep(1.0)

        entrance_description = await self.command._generate_entrance_description(session)
        if isinstance(getattr(session, "environment_state", None), dict):
            session.environment_state["entrance_description"] = entrance_description

        has_opening_guidance = self.command._has_opening_guidance(session)
        self.command._ensure_story_runtime(
            session,
            game_mode=GameModes.SINGLE.value,
            initial_player_id=user_id,
        )

        if has_opening_guidance:
            entrance_long_image = await image_generator.generate_entrance_long_image(
                scene_name=session.scene_name,
                entrance_description=entrance_description,
                npc_guidance=getattr(session, "npc_guidance", {}) or {},
                use_cache=use_cache,
            )
            await self.command._send_image_path(entrance_long_image)
            await asyncio.sleep(1.0)

        player = session.players.get(user_id)
        current_location = str(getattr(player, "location", "") or "").strip() if player else ""
        scene_overview = self.command._build_scene_overview_text(session, current_location=current_location)
        if scene_overview:
            scene_overview_image = await image_generator.generate_scene_overview_image(
                overview_title="此刻你能确认的情况",
                overview_text=scene_overview,
                use_cache=use_cache,
            )
            await self.command._send_image_path(scene_overview_image)
            await asyncio.sleep(1.0)

        await self.command._send_initial_rule_exposure(session, GameModes.SINGLE.value, [])
        await asyncio.sleep(1.0)

        await self.command.send_text(
            "故事已经开始。\n\n"
            "接下来直接用 `/rg 行动 <描述>` 往前走就行；如果想重新整理手头信息，可以用 `/rg 状态`、`/rg 规则`、`/rg 场景`。"
        )
