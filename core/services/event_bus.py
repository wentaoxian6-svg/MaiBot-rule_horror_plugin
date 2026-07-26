"""游戏事件总线：玩家行动 / NPC 事件 / 规则变异 的发布订阅。"""
from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
import asyncio
import logging

from ...common.models import JsonObject

logger = logging.getLogger(__name__)


@dataclass
class GameEvent:
    """一次可被其他玩家感知的游戏事件。"""
    event_type: str            # "player_action" / "npc_move" / "rule_mutation" / "item_pickup"
    group_id: str
    actor_id: str
    actor_name: str
    location: str
    description: str           # 主视角描述（给可见玩家）
    audible_description: str   # 声音描述（给可听玩家）
    visible_to: set[str] = field(default_factory=set)    # player_id
    audible_to: set[str] = field(default_factory=set)
    importance: str = "normal"  # "low" / "normal" / "high"
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> JsonObject:
        return {
            "event_type": self.event_type,
            "actor_id": self.actor_id,
            "actor_name": self.actor_name,
            "location": self.location,
            "description": self.description,
            "audible_description": self.audible_description,
            "visible_to": list(self.visible_to),
            "audible_to": list(self.audible_to),
            "importance": self.importance,
            "timestamp": self.timestamp.isoformat(),
        }


class EventBus:
    """单群内事件聚合 + 订阅分发。

    - 同一群组的事件进入同一聚合窗口（默认 3 秒）
    - 窗口结束时合并成一条消息发送
    - 支持 importance="high" 立即发送（如玩家死亡、规则变异）
    """

    def __init__(self, aggregate_window_seconds: float = 3.0) -> None:
        self._subscribers: dict[str, list[Callable[[GameEvent], Awaitable[None]]]] = defaultdict(list)
        self._aggregators: dict[str, asyncio.Task[None]] = {}
        self._pending: dict[str, deque[GameEvent]] = defaultdict(deque)
        self._aggregate_window = aggregate_window_seconds

    def subscribe(self, group_id: str, callback: Callable[[GameEvent], Awaitable[None]]) -> None:
        """订阅指定群组的事件。"""
        self._subscribers[group_id].append(callback)

    async def publish(self, event: GameEvent) -> None:
        """发布事件。importance="high" 立即发送，其他进入聚合窗口。"""
        if event.importance == "high":
            # 立即 flush 所有 pending，然后单独发送该事件
            await self._flush_group(event.group_id)
            await self._dispatch(event.group_id, event)
            return

        self._pending[event.group_id].append(event)
        self._schedule_flush(event.group_id)

    def _schedule_flush(self, group_id: str) -> None:
        """启动或续期聚合任务。"""
        existing = self._aggregators.get(group_id)
        if existing is not None and not existing.done():
            existing.cancel()

        async def _flush_after_delay() -> None:
            try:
                await asyncio.sleep(self._aggregate_window)
                await self._flush_group(group_id)
            except asyncio.CancelledError:
                pass

        self._aggregators[group_id] = asyncio.create_task(_flush_after_delay())

    async def _flush_group(self, group_id: str) -> None:
        """flush 指定群组的所有 pending 事件。"""
        pending = self._pending.pop(group_id, deque())
        if not pending:
            return
        # 按可见/可听集合分组，再合并文本
        merged = self._merge_events(list(pending))
        for event in merged:
            await self._dispatch(group_id, event)

    def _merge_events(self, events: list[GameEvent]) -> list[GameEvent]:
        """合并同 actor 同 location 的多个事件。P2 简化：直接返回，每个事件单独发。"""
        return events

    async def _dispatch(self, group_id: str, event: GameEvent) -> None:
        """把事件分发给该群的所有订阅器。"""
        for callback in self._subscribers.get(group_id, []):
            try:
                await callback(event)
            except Exception as exc:
                logger.error("EventBus 订阅器异常: %s", exc, exc_info=True)
