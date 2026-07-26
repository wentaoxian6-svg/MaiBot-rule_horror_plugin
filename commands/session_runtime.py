from __future__ import annotations

from typing import TYPE_CHECKING

from ..common import GameModes
from ..core import GameStatus, PlayerStatus

if TYPE_CHECKING:
    from ..core import GameSession


class SessionRuntimeMixin:
    """会话运行时恢复与绑定。"""

    @staticmethod
    def _build_environment_game_state(session: GameSession) -> dict[str, object]:
        """从已保存会话中恢复环境系统所需的最小状态。"""
        env_state = session.environment_state if isinstance(session.environment_state, dict) else {}
        environment_evolution = env_state.get("environment_evolution", {})
        if isinstance(environment_evolution, dict):
            return {"environment_evolution": environment_evolution}
        return {}

    def _bind_environment_runtime(self, session: GameSession, group_id: str) -> None:
        """重新绑定环境演化系统，避免恢复存档后相关逻辑静默失效。"""
        game_state = self._build_environment_game_state(session)
        env_system = self._get_or_create_environment_system({group_id: game_state})
        env_system.game_states[group_id] = game_state
        session._environment_system = env_system

    def _bind_rule_mutation_runtime(self, session: GameSession) -> None:
        """重新绑定规则变异系统。"""
        session._rule_mutation_system = self._get_or_create_rule_mutation_system()
        if not isinstance(session.environment_state, dict):
            session.environment_state = {}
        session.environment_state.setdefault("rule_mutations", [])
        session.environment_state.setdefault("discovered_clues", [])

    def _guess_initial_player_id(self, session: GameSession) -> str | None:
        """恢复 story runtime 时推断一个稳定的初始玩家。"""
        for player in session.players.values():
            if getattr(player, "status", None) == PlayerStatus.ALIVE:
                return player.player_id
        return next(iter(session.players.keys()), None)

    def rehydrate_session_runtime(self, session: GameSession, group_id: str) -> None:
        """在恢复/读取存档后重建运行时依赖。"""
        if not isinstance(session.environment_state, dict):
            session.environment_state = {}

        if not session.players:
            return

        if session.status != GameStatus.ACTIVE:
            if session.game_mode == GameModes.MULTI.value:
                session.environment_state.setdefault("lobby_player_order", list(session.players.keys()))
            return

        self._bind_environment_runtime(session, group_id)
        self._bind_rule_mutation_runtime(session)
        self._ensure_story_runtime(
            session,
            game_mode=session.game_mode,
            initial_player_id=self._guess_initial_player_id(session),
        )
