"""游戏状态管理器 - 线程安全的状态管理"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timedelta

from .models import GameSession, GameStatus

logger = logging.getLogger(__name__)


class GameState:
    """游戏状态封装"""
    def __init__(self, group_id: str):
        self.group_id: str = group_id
        self.session: GameSession | None = None
        self.last_accessed: datetime = datetime.now()
        self._lock: asyncio.Lock = asyncio.Lock()

    async def acquire(self) -> GameState:
        """获取锁"""
        await self._lock.acquire()
        self.last_accessed = datetime.now()
        return self

    def release(self) -> None:
        """释放锁"""
        if self._lock.locked():
            self._lock.release()

    async def __aenter__(self) -> GameState:
        return await self.acquire()

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()

    def is_active(self) -> bool:
        """检查游戏是否活跃"""
        return self.session is not None and self.session.status == GameStatus.ACTIVE

    def is_stale(self, timeout_minutes: int = 60) -> bool:
        """检查状态是否过期"""
        return datetime.now() - self.last_accessed > timedelta(minutes=timeout_minutes)


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
            stale_groups = [
                group_id for group_id, state in self._states.items()
                if state.is_stale()
            ]
            for group_id in stale_groups:
                state = self._states[group_id]
                if not state.is_active():
                    del self._states[group_id]
                    logger.info(f"清理过期状态: {group_id}")

    async def get_or_create(self, group_id: str, timeout: float = 5.0) -> GameState:
        """
        获取或创建游戏状态（带超时保护）

        Args:
            group_id: 群组/用户ID
            timeout: 超时时间（秒）

        Returns:
            GameState 对象（已加锁）

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
                
                # 在全局锁外获取状态锁
                await state.acquire()
                return state
        except asyncio.TimeoutError:
            logger.error(f"获取状态超时: {group_id}")
            raise RuntimeError(f"获取状态超时: {group_id}")

    async def get(self, group_id: str, timeout: float = 5.0) -> GameState | None:
        """
        获取游戏状态（如果不存在则返回 None）

        Args:
            group_id: 群组/用户ID
            timeout: 超时时间（秒）

        Returns:
            GameState 对象（已加锁）或 None

        Raises:
            RuntimeError: 获取状态超时
        """
        try:
            async with asyncio.timeout(timeout):
                async with self._global_lock:
                    state = self._states.get(group_id)

                if state:
                    await state.acquire()
                    return state
                return None
        except asyncio.TimeoutError:
            logger.error(f"获取状态超时: {group_id}")
            raise RuntimeError(f"获取状态超时: {group_id}")

    async def remove(self, group_id: str) -> bool:
        """移除游戏状态。

        说明：
        - 旧实现会在状态锁被持有时拒绝删除，导致像 `/rg 结束` 这类“在持锁处理器内清理状态”的场景无法真正结束游戏。
        - 这里改为**无条件移除**，让结束流程在同一临界区内完成；即便有协程仍持有该 `GameState` 引用，也只会影响当前协程的 `finally: state.release()`，不再允许后续请求重新获取该状态。

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
