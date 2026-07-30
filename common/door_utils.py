"""门状态查询工具 - 查询两个房间之间的门状态。

本模块为 action_processor 与 npc_simulator 共享的公共工具，合并两处重复实现，
并统一支持列表格式与旧版字典格式两种 doors 字段存储。
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..core.game.models import GameSession
    from ..systems.environment_evolution import DoorState


def get_door_state_between(
    session: GameSession,
    room_a: str,
    room_b: str,
) -> DoorState | None:
    """查询两个房间之间的门状态。

    从 ``session.environment_state.doors`` 查询连接 room_a 与 room_b 的门。
    支持两种 doors 字段格式：
    - 列表格式：``[{"rooms": ["A", "B"], "state": "关闭"}, ...]``
    - 字典格式：``{"A-B": "关闭", ...}``（旧版 EnvironmentState 序列化格式）

    Args:
        session: 游戏会话
        room_a: 房间 A 名称
        room_b: 房间 B 名称

    Returns:
        DoorState 枚举值；若无门或字段缺失则返回 None
    """
    # 延迟导入避免 common → systems 的循环依赖
    from ..systems.environment_evolution import DoorState

    env_state = session.environment_state if isinstance(session.environment_state, dict) else {}
    doors = env_state.get("doors", [])
    if not doors:
        return None

    ra = str(room_a or "").strip()
    rb = str(room_b or "").strip()
    if not ra or not rb or ra == rb:
        return None

    # 列表格式：[{"rooms": ["A", "B"], "state": "关闭"}, ...]
    if isinstance(doors, list):
        for door in doors:
            if not isinstance(door, dict):
                continue
            rooms = door.get("rooms", [])
            if not isinstance(rooms, list) or len(rooms) < 2:
                continue
            room_set = {str(r).strip() for r in rooms}
            if ra in room_set and rb in room_set:
                state_str = str(door.get("state", "")).strip()
                try:
                    return DoorState(state_str)
                except ValueError:
                    return None
        return None

    # 字典格式：{"A-B": "关闭", ...}（旧版兼容）
    if isinstance(doors, dict):
        for door_key, state_str in doors.items():
            parts = re.split(r"[-|,]", str(door_key))
            if len(parts) == 2:
                p1, p2 = parts[0].strip(), parts[1].strip()
                if {p1, p2} == {ra, rb}:
                    try:
                        return DoorState(str(state_str))
                    except ValueError:
                        return None
        return None

    return None
