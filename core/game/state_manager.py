"""游戏状态管理器 - 线程安全的状态管理"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timedelta

from ..services.factories import RuntimeFactories
from .models import GameSession, GameStatus

logger = logging.getLogger(__name__)


class GameState:
    """游戏状态封装"""
    def __init__(self, group_id: str):
        self.group_id: str = group_id
        self.session: GameSession | None = None
        self.last_accessed: datetime = datetime.now()
        # 玩家私有状态锁：保护 player.sanity/health/inventory/location/action_history
        self._player_locks: dict[str, asyncio.Lock] = {}
        # 玩家锁的最后访问时间，用于 TTL 清理（避免长运行累积孤儿锁）
        self._player_lock_access: dict[str, datetime] = {}
        # 共享世界锁：保护 NPC 位置、规则载体、环境演化、规则变异
        self._world_lock = asyncio.Lock()
        # 元数据锁：保护 players 字典本身的增删、status 切换
        self._meta_lock = asyncio.Lock()
        # NPC tick 后台任务及其间隔
        self._npc_tick_task: asyncio.Task[None] | None = None
        self._tick_interval_seconds: int = 60

    async def acquire_player(self, player_id: str) -> "GameState":
        """持玩家私有锁。可读 session，但写共享世界需另 acquire_world。"""
        lock = self._player_locks.setdefault(player_id, asyncio.Lock())
        # 记录访问时间，用于 TTL 清理判定；即便当前协程在 await lock.acquire() 上等待，
        # 也算一次活跃访问，避免清理误删正在排队的锁。
        self._player_lock_access[player_id] = datetime.now()
        await lock.acquire()
        self.last_accessed = datetime.now()
        return self

    def release_player(self, player_id: str) -> None:
        """释放玩家私有锁。

        若锁未被当前协程持有，release() 会抛 RuntimeError，让错误暴露
        （符合项目“不兜底”规范）。玩家锁不存在时直接返回。
        """
        lock = self._player_locks.get(player_id)
        if lock is None:
            return
        lock.release()

    async def acquire_world(self) -> "GameState":
        """持世界锁。用于 NPC 推进、规则变异、机关变更等共享写。"""
        await self._world_lock.acquire()
        self.last_accessed = datetime.now()
        return self

    def release_world(self) -> None:
        """释放世界锁。

        若锁未被当前协程持有，release() 会抛 RuntimeError，让错误暴露
        （符合项目“不兜底”规范）。
        """
        self._world_lock.release()

    async def snapshot(self) -> dict:
        """短持 meta_lock 复制一份只读快照，立即释放。

        用于在无锁的 LLM 判定阶段传入世界视图。
        返回格式：{"session": session.to_dict(), "players_snapshot": {pid: p.to_dict()}}
        若 session 为 None，返回空 dict。
        """
        async with self._meta_lock:
            if not self.session:
                return {}
            return {
                "session": self.session.to_dict(),
                "players_snapshot": {pid: p.to_dict() for pid, p in self.session.players.items()},
            }

    def _cleanup_stale_player_locks(self, ttl_minutes: int = 60) -> None:
        """清理超过 ttl_minutes 未访问且未被持有的玩家锁。

        在 GameStateManager._cleanup_stale_states 中被调用，避免长运行下
        _player_locks 累积孤儿锁（玩家离开后再也不来的场景）。
        同步函数，无 await，单线程 asyncio 下不会被协程打断。
        """
        now = datetime.now()
        stale_pids = [
            pid for pid, last_access in self._player_lock_access.items()
            if (now - last_access) > timedelta(minutes=ttl_minutes)
        ]
        for pid in stale_pids:
            lock = self._player_locks.get(pid)
            # 锁仍在持有时跳过，避免清理活跃协程的状态
            if lock is not None and not lock.locked():
                del self._player_locks[pid]
            # 访问时间记录一律清理：下次 acquire_player 会重建 _player_lock_access[pid]
            del self._player_lock_access[pid]

    def is_active(self) -> bool:
        """检查游戏是否活跃"""
        return self.session is not None and self.session.status == GameStatus.ACTIVE

    def is_stale(self, timeout_minutes: int = 60) -> bool:
        """检查状态是否过期"""
        return datetime.now() - self.last_accessed > timedelta(minutes=timeout_minutes)

    def start_npc_tick(self, factories: RuntimeFactories) -> None:
        """启动 NPC tick 循环。只在多人模式且 status=ACTIVE 时启动。

        Args:
            factories: 运行时工厂集合，提供 NPC 模拟器与事件总线实例。
                由 commands 层注入满足 RuntimeFactories Protocol 的对象
                （如 RuleHorrorCommand），core 层不再反向持有 commands 层引用。
        """
        if self._npc_tick_task is not None:
            return
        self._npc_tick_task = asyncio.create_task(self._npc_tick_loop(factories))

    async def stop_npc_tick(self) -> None:
        """停止 NPC tick 循环。"""
        if self._npc_tick_task is not None:
            self._npc_tick_task.cancel()
            try:
                await self._npc_tick_task
            except asyncio.CancelledError:
                pass
            self._npc_tick_task = None

    async def _npc_tick_loop(self, factories: RuntimeFactories) -> None:
        """NPC tick 循环：每 60 秒持世界锁推进 NPC，把事件发布到 event_bus。

        每次 tick 同时推进游戏内时间（tick 一次 = 游戏内 15 分钟），
        并在 NPC 推进后调用 EnvironmentEvolutionSystem.evolve() 让环境随之演化。

        Args:
            factories: 运行时工厂集合，用于获取 NPC 模拟器与事件总线
        """
        while True:
            try:
                await asyncio.sleep(self._tick_interval_seconds)
                if not self.is_active():
                    continue
                # 持世界锁推进 NPC
                await self.acquire_world()
                events: list = []
                try:
                    if self.session is None:
                        continue

                    # tick 一次推进游戏内 15 分钟，让 NPC 作息与环境演化都能基于新时间
                    if isinstance(self.session.time_manager, dict):
                        elapsed_minutes = int(self.session.time_manager.get("elapsed_minutes", 0) or 0) + 15
                        self.session.time_manager["elapsed_minutes"] = elapsed_minutes

                    npc_simulator = factories.get_or_create_npc_simulator()
                    events = await npc_simulator.tick(self.session)

                    # NPC tick 后调用环境演化，让环境与 NPC 同步推进
                    env_system = getattr(self.session, "_environment_system", None)
                    if env_system is not None:
                        elapsed_for_evolve = int(self.session.time_manager.get("elapsed_minutes", 0) or 0) if isinstance(self.session.time_manager, dict) else 0
                        recent_events: list = []
                        env_state = self.session.environment_state if isinstance(self.session.environment_state, dict) else {}
                        npc_runtime = env_state.get("npc_runtime", {})
                        if isinstance(npc_runtime, dict):
                            raw_events = npc_runtime.get("recent_events", [])
                            if isinstance(raw_events, list):
                                recent_events = raw_events
                        await env_system.evolve(self.session, elapsed_for_evolve, recent_events)
                finally:
                    self.release_world()
                # 发布事件（无锁）
                if events:
                    event_bus = factories.get_or_create_event_bus()
                    for event in events:
                        await event_bus.publish(event)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("NPC tick 失败: %s", exc, exc_info=True)


class GameStateManager:
    """游戏状态管理器 - 单例模式"""

    _instance: GameStateManager | None = None
    _lock: threading.Lock = threading.Lock()  # 使用threading.Lock而非asyncio.Lock

    def __new__(cls) -> GameStateManager:
        """单例模式（线程安全）"""
        if cls._instance is None:
            with cls._lock:
                # 双重检查锁定
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return

        self._initialized: bool = True
        self._states: dict[str, GameState] = {}
        self._global_lock: asyncio.Lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task[None] | None = None
        self._cleanup_interval: int = 300  # 5分钟清理一次

    async def start(self) -> None:
        """启动管理器"""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info("GameStateManager 已启动")

    async def stop(self) -> None:
        """停止管理器"""
        # 停止所有 NPC tick 任务
        for state in self._states.values():
            await state.stop_npc_tick()
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
            logger.info("GameStateManager 已停止")

    async def _cleanup_loop(self) -> None:
        """定期清理过期状态的循环"""
        while True:
            try:
                await asyncio.sleep(self._cleanup_interval)
                await self._cleanup_stale_states()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"清理状态时出错: {e}")

    async def _cleanup_stale_states(self) -> None:
        """清理过期状态"""
        async with self._global_lock:
            # 先清理每个 state 的过期玩家锁，避免长运行累积孤儿锁
            for state in self._states.values():
                state._cleanup_stale_player_locks()
            stale_groups = [
                group_id for group_id, state in self._states.items()
                if state.is_stale()
            ]
            for group_id in stale_groups:
                state = self._states[group_id]
                if not state.is_active():
                    del self._states[group_id]
                    logger.info(f"清理过期状态: {group_id}")

    async def get_world_or_create(self, group_id: str, timeout: float = 5.0) -> GameState:
        """持世界锁获取或创建游戏状态。

        与 get_world 类似，但若 state 不存在则创建新 GameState。
        用于初始化 session 的场景（开始游戏、恢复存档等），调用方拿到状态后会设置 state.session。

        注意：与 get_world 不同，本方法**不检查** session 是否为 None，
        返回的 state 可能 session=None（新创建的 state），由调用方负责设置。

        Args:
            group_id: 群组/用户ID
            timeout: 超时时间（秒）

        Returns:
            GameState 对象（已持世界锁，session 可能为 None）

        Raises:
            RuntimeError: 获取状态超时
        """
        try:
            async with asyncio.timeout(timeout):
                async with self._global_lock:
                    if group_id not in self._states:
                        self._states[group_id] = GameState(group_id)
                        logger.debug(f"创建新游戏状态: {group_id}")
                    state = self._states[group_id]

                # 在全局锁外获取世界锁
                await state.acquire_world()
                return state
        except asyncio.TimeoutError:
            logger.error(f"获取状态超时: {group_id}")
            raise RuntimeError(f"获取状态超时: {group_id}")

    async def get_for_player(self, group_id: str, player_id: str, timeout: float = 5.0) -> GameState | None:
        """持玩家私有锁（用于行动处理）。

        与 get_world() 类似，但调用 state.acquire_player(player_id)。
        若 session 为 None，返回 None 并释放锁。

        Args:
            group_id: 群组/用户ID
            player_id: 玩家ID
            timeout: 超时时间（秒）

        Returns:
            GameState 对象（已持玩家私有锁）或 None

        Raises:
            RuntimeError: 获取状态超时
        """
        try:
            async with asyncio.timeout(timeout):
                async with self._global_lock:
                    state = self._states.get(group_id)

                if state:
                    await state.acquire_player(player_id)
                    if state.session is None:
                        state.release_player(player_id)
                        return None
                    return state
                return None
        except asyncio.TimeoutError:
            logger.error(f"获取玩家状态超时: {group_id}/{player_id}")
            raise RuntimeError(f"获取玩家状态超时: {group_id}/{player_id}")

    async def get_world(self, group_id: str, timeout: float = 5.0) -> GameState | None:
        """持世界锁（用于 NPC tick / 规则变异）。

        调用 state.acquire_world()。若 session 为 None，返回 None 并释放锁。

        Args:
            group_id: 群组/用户ID
            timeout: 超时时间（秒）

        Returns:
            GameState 对象（已持世界锁）或 None

        Raises:
            RuntimeError: 获取状态超时
        """
        try:
            async with asyncio.timeout(timeout):
                async with self._global_lock:
                    state = self._states.get(group_id)

                if state:
                    await state.acquire_world()
                    if state.session is None:
                        state.release_world()
                        return None
                    return state
                return None
        except asyncio.TimeoutError:
            logger.error(f"获取世界状态超时: {group_id}")
            raise RuntimeError(f"获取世界状态超时: {group_id}")

    async def get_snapshot(self, group_id: str, timeout: float = 5.0) -> dict | None:
        """短锁获取只读快照，立即释放（用于 LLM 判定前的上下文读取）。

        返回 GameState.snapshot() 的结果。若 state 不存在或 session 为 None，返回 None。

        Args:
            group_id: 群组/用户ID
            timeout: 超时时间（秒）

        Returns:
            快照字典或 None

        Raises:
            RuntimeError: 获取快照超时
        """
        try:
            async with asyncio.timeout(timeout):
                async with self._global_lock:
                    state = self._states.get(group_id)

                if not state:
                    return None
                # snapshot 内部会短持 meta_lock，全局锁已在上方释放，不会阻塞其他群组
                return await state.snapshot()
        except asyncio.TimeoutError:
            logger.error(f"获取快照超时: {group_id}")
            raise RuntimeError(f"获取快照超时: {group_id}")

    async def remove(self, group_id: str) -> bool:
        """移除游戏状态。

        说明：
        - 旧实现会在状态锁被持有时拒绝删除，导致像 `/rg 结束` 这类“在持锁处理器内清理状态”的场景无法真正结束游戏。
        - 这里改为**无条件移除**，让结束流程在同一临界区内完成；即便有协程仍持有该 `GameState` 引用，也只会影响当前协程的 `finally: state.release_world()` 或 `state.release_player(pid)`，不再允许后续请求重新获取该状态。

        Args:
            group_id: 群组/用户ID

        Returns:
            是否成功移除
        """
        async with self._global_lock:
            if group_id in self._states:
                del self._states[group_id]
                logger.info(f"移除游戏状态: {group_id}")
                return True
        return False

    def get_active_games_count(self) -> int:
        """获取活跃游戏数量"""
        return sum(
            1 for state in self._states.values()
            if state.is_active()
        )

    def get_all_group_ids(self) -> list[str]:
        """获取所有群组ID"""
        return list(self._states.keys())
