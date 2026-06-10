"""房间拓扑工具 - 基于场景结构推导房间级邻接关系。"""
from __future__ import annotations

from collections import deque
from collections.abc import Mapping


JsonObject = dict[str, object]
RoomGraph = dict[str, list[str]]


def normalize_rooms(scene_structure: Mapping[str, object] | None) -> list[str]:
    """从场景结构中提取房间 / 区域列表。"""
    if not isinstance(scene_structure, Mapping):
        return []

    rooms: list[str] = []
    floors = scene_structure.get("floors", [])
    if isinstance(floors, list):
        for floor in floors:
            if not isinstance(floor, Mapping):
                continue
            for key in ("areas", "rooms"):
                values = floor.get(key, [])
                if isinstance(values, list):
                    for item in values:
                        room_name = str(item).strip()
                        if room_name and room_name not in rooms:
                            rooms.append(room_name)

    special_areas = scene_structure.get("special_areas", [])
    if isinstance(special_areas, list):
        for item in special_areas:
            room_name = str(item).strip()
            if room_name and room_name not in rooms:
                rooms.append(room_name)

    return rooms


def _extract_connection_endpoints(connection: object) -> tuple[str, str] | None:
    if isinstance(connection, Mapping):
        left_keys = ("from", "source", "a", "start", "room_a")
        right_keys = ("to", "target", "b", "end", "room_b")
        left = next((str(connection.get(key, "")).strip() for key in left_keys if str(connection.get(key, "")).strip()), "")
        right = next((str(connection.get(key, "")).strip() for key in right_keys if str(connection.get(key, "")).strip()), "")
        if left and right:
            return left, right

    if isinstance(connection, str):
        for sep in ("->", "-", "—", "至", "到"):
            if sep in connection:
                left, right = [part.strip() for part in connection.split(sep, 1)]
                if left and right:
                    return left, right
    return None


def build_room_graph(scene_structure: Mapping[str, object] | None) -> RoomGraph:
    """构建房间邻接图。"""
    rooms = normalize_rooms(scene_structure)
    graph: RoomGraph = {room: [] for room in rooms}

    if isinstance(scene_structure, Mapping):
        floors = scene_structure.get("floors", [])
        if isinstance(floors, list):
            for floor in floors:
                if not isinstance(floor, Mapping):
                    continue
                raw_rooms = floor.get("areas", floor.get("rooms", []))
                floor_rooms = [str(item).strip() for item in raw_rooms if str(item).strip()] if isinstance(raw_rooms, list) else []
                for index in range(len(floor_rooms) - 1):
                    left = floor_rooms[index]
                    right = floor_rooms[index + 1]
                    _link_rooms(graph, left, right)

        connections = scene_structure.get("connections", [])
        if isinstance(connections, list):
            for connection in connections:
                endpoints = _extract_connection_endpoints(connection)
                if endpoints is None:
                    continue
                _link_rooms(graph, endpoints[0], endpoints[1])

    return graph


def _link_rooms(graph: RoomGraph, left: str, right: str) -> None:
    for room in (left, right):
        graph.setdefault(room, [])
    if right not in graph[left]:
        graph[left].append(right)
    if left not in graph[right]:
        graph[right].append(left)


def is_same_room(left: str, right: str) -> bool:
    return str(left).strip() != "" and str(left).strip() == str(right).strip()


def is_adjacent_room(graph: RoomGraph, left: str, right: str) -> bool:
    left_room = str(left).strip()
    right_room = str(right).strip()
    if not left_room or not right_room:
        return False
    return right_room in graph.get(left_room, [])


def shortest_room_distance(graph: RoomGraph, source: str, target: str) -> int | None:
    """返回房间级最短步数。"""
    source_room = str(source).strip()
    target_room = str(target).strip()
    if not source_room or not target_room:
        return None
    if source_room == target_room:
        return 0

    visited = {source_room}
    queue: deque[tuple[str, int]] = deque([(source_room, 0)])
    while queue:
        room, distance = queue.popleft()
        for neighbor in graph.get(room, []):
            if neighbor == target_room:
                return distance + 1
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, distance + 1))
    return None


def can_hear_between_rooms(graph: RoomGraph, source: str, target: str, hearing_radius: int = 1) -> bool:
    """判断两个房间之间是否能听到动静。"""
    distance = shortest_room_distance(graph, source, target)
    return distance is not None and distance <= max(0, int(hearing_radius))


def get_visible_npcs(npcs: list[JsonObject], player_location: str) -> list[JsonObject]:
    """获取与玩家同房间的 NPC 列表。"""
    location = str(player_location).strip()
    visible: list[JsonObject] = []
    for npc in npcs:
        npc_location = str(npc.get("current_location") or npc.get("location") or "").strip()
        if location and npc_location == location:
            visible.append(npc)
    return visible


def get_audible_npcs(
    graph: RoomGraph,
    npcs: list[JsonObject],
    player_location: str,
    hearing_radius: int = 1,
) -> list[JsonObject]:
    """获取在可听范围内、但不在同房间的 NPC 列表。"""
    audible: list[JsonObject] = []
    location = str(player_location).strip()
    for npc in npcs:
        npc_location = str(npc.get("current_location") or npc.get("location") or "").strip()
        if not npc_location or npc_location == location:
            continue
        if can_hear_between_rooms(graph, location, npc_location, hearing_radius=hearing_radius):
            audible.append(npc)
    return audible
