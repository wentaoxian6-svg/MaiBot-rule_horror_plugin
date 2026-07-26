"""房间拓扑工具 - 基于场景结构推导房间级邻接关系。

本模块是物理空间感知的唯一权威：玩家可见性、声音传播、PVP 距离衰减
全部基于房间级模型（同房间/相邻房间/更远），不再使用坐标级实现。
"""
from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from enum import Enum

from .environment_evolution import DoorState


JsonObject = dict[str, object]
RoomGraph = dict[str, list[str]]
# 扩展后的房间图：在邻接表基础上额外携带 wall_materials 字典。
# wall_materials 的 key 为 (room_a, room_b) 元组，双向存储；值为 WallMaterial 枚举。
RoomGraphWithMaterials = dict[str, object]


class SoundIntensity(Enum):
    """声源强度档位。

    不同档位对应不同的听力半径修正：
    - LOUD：喊叫/尖叫/重物坠落，传播更远
    - NORMAL：普通对话/常规动作
    - QUIET：耳语/蹑手蹑脚，传播很近
    """
    LOUD = "loud"
    NORMAL = "normal"
    QUIET = "quiet"


class WallMaterial(Enum):
    """墙体材质档位。

    不同材质对声音的吸收率不同（数值为衰减系数，0=完全吸收，1=完全不吸收）：
    - CONCRETE：混凝土，吸收率高
    - WOOD：木板，吸收率中
    - GLASS：玻璃，吸收率低
    """
    CONCRETE = "concrete"
    WOOD = "wood"
    GLASS = "glass"


# 声源强度对应的听力半径修正（正值扩大范围，负值缩小）
_SOUND_INTENSITY_RADIUS_DELTA: dict[SoundIntensity, int] = {
    SoundIntensity.LOUD: 1,
    SoundIntensity.NORMAL: 0,
    SoundIntensity.QUIET: -1,
}

# 墙体材质对应的声音穿透系数（0=完全吸收，1=完全不吸收）
_WALL_MATERIAL_TRANSMISSION: dict[WallMaterial, float] = {
    WallMaterial.CONCRETE: 0.3,
    WallMaterial.WOOD: 0.6,
    WallMaterial.GLASS: 0.8,
}


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


def build_room_graph(scene_structure: Mapping[str, object] | None) -> RoomGraphWithMaterials:
    """构建房间邻接图，并附带墙材质信息。

    返回结构：
    - 房间名 → 相邻房间名列表（邻接表，向后兼容旧调用方）
    - ``"wall_materials"`` → ``dict[tuple[str, str], WallMaterial]``，
      key 为 ``(room_a, room_b)`` 元组且**双向存储**（A-B 与 B-A 都各存一份），
      调用方查询时可直接取 ``(room_a, room_b)``，也可走 ``get_wall_material`` 辅助函数。

    墙材质解析顺序：
    1. ``scene_structure["walls"]``：形如 ``[{"rooms": ["A", "B"], "material": "glass"}]``
    2. ``scene_structure["wall_materials"]``：同上结构（兼容字段名）
    3. 未匹配到的邻接关系默认 ``WallMaterial.CONCRETE``
    """
    rooms = normalize_rooms(scene_structure)
    graph: RoomGraphWithMaterials = {room: [] for room in rooms}

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

    # 构建墙材质字典：遍历所有邻接关系，为每对相邻房间查询墙材质
    wall_materials: dict[tuple[str, str], WallMaterial] = {}
    for room, neighbors in graph.items():
        if not isinstance(neighbors, list):
            # 此时 graph 中尚无非邻接表条目，防御性跳过
            continue
        for neighbor in neighbors:
            material = _resolve_wall_material(scene_structure, room, neighbor)
            # 双向存储，调用方查 (A, B) 或 (B, A) 均可命中
            wall_materials[(room, neighbor)] = material

    graph["wall_materials"] = wall_materials
    return graph


def _resolve_wall_material(
    scene_structure: Mapping[str, object] | None,
    room_a: str,
    room_b: str,
) -> WallMaterial:
    """从场景结构解析两房间之间的墙材质，缺失时默认混凝土。

    支持的字段格式（按优先级）：
    1. ``scene_structure["walls"]``：``[{"rooms": ["A", "B"], "material": "glass|wood|concrete"}]``
    2. ``scene_structure["wall_materials"]``：同上结构（字段名兼容）

    Args:
        scene_structure: 场景结构字典
        room_a: 房间 A 名称
        room_b: 房间 B 名称

    Returns:
        对应的 WallMaterial 枚举；未匹配到时返回 CONCRETE
    """
    if not isinstance(scene_structure, Mapping):
        return WallMaterial.CONCRETE

    # 兼容 walls 与 wall_materials 两个字段名，均为列表结构
    for field_name in ("walls", "wall_materials"):
        walls = scene_structure.get(field_name, [])
        if not isinstance(walls, list):
            continue
        for wall in walls:
            if not isinstance(wall, Mapping):
                continue
            rooms = wall.get("rooms", [])
            if not isinstance(rooms, list):
                continue
            rooms_str = [str(item).strip() for item in rooms if str(item).strip()]
            # 双向匹配：A-B 或 B-A 都算命中
            if room_a in rooms_str and room_b in rooms_str and room_a != room_b:
                material_str = str(wall.get("material", "concrete")).strip().lower()
                if "glass" in material_str:
                    return WallMaterial.GLASS
                if "wood" in material_str:
                    return WallMaterial.WOOD
                if "concrete" in material_str:
                    return WallMaterial.CONCRETE
                # 未知材质字符串：按枚举值精确匹配，仍未命中则回落到 CONCRETE
                for member in WallMaterial:
                    if member.value == material_str:
                        return member
                return WallMaterial.CONCRETE

    return WallMaterial.CONCRETE


def get_wall_material(
    room_graph: Mapping[str, object],
    room_a: str,
    room_b: str,
) -> WallMaterial:
    """从房间图查询两房间之间的墙材质。

    查询顺序：
    1. ``(room_a, room_b)`` 直接命中
    2. ``(room_b, room_a)`` 反向命中
    3. 未命中时返回 ``WallMaterial.CONCRETE`` 作为默认值

    Args:
        room_graph: ``build_room_graph`` 返回的房间图
        room_a: 房间 A 名称
        room_b: 房间 B 名称

    Returns:
        对应的 WallMaterial 枚举
    """
    wall_materials = room_graph.get("wall_materials", {})
    if isinstance(wall_materials, Mapping):
        if (room_a, room_b) in wall_materials:
            material = wall_materials[(room_a, room_b)]
            if isinstance(material, WallMaterial):
                return material
        if (room_b, room_a) in wall_materials:
            material = wall_materials[(room_b, room_a)]
            if isinstance(material, WallMaterial):
                return material
    return WallMaterial.CONCRETE


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


def find_shortest_path(room_graph: RoomGraph, start: str, end: str) -> list[str]:
    """用 BFS 查找从 start 到 end 的最短路径（含 start 与 end）。

    返回空列表表示不可达；start == end 时返回 [start]。
    与 ``shortest_room_distance`` 共用同一套 BFS 思路，但额外回溯节点路径，
    供移动校验等需要"下一节点"的场景使用。
    """
    start_room = str(start).strip()
    end_room = str(end).strip()
    if not start_room or not end_room:
        return []
    if start_room == end_room:
        return [start_room]

    visited = {start_room}
    queue: deque[tuple[str, list[str]]] = deque([(start_room, [start_room])])
    while queue:
        current, path = queue.popleft()
        for neighbor in room_graph.get(current, []):
            if neighbor == end_room:
                return path + [end_room]
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, path + [neighbor]))
    return []


def can_hear_between_rooms(
    graph: RoomGraph,
    source: str,
    target: str,
    hearing_radius: int = 1,
    *,
    door_state: DoorState | None = None,
    sound_intensity: SoundIntensity | None = None,
    wall_material: WallMaterial | None = None,
) -> bool:
    """判断两个房间之间是否能听到动静。

    房间级声音传播模型：
    1. 基础听力半径由 hearing_radius 给出（默认 1，表示相邻房间可听）。
    2. 声源强度修正：LOUD +1，QUIET -1。
    3. 门状态修正：CLOSED/LOCKED/BROKEN 时半径减半（向下取整）。
    4. 墙体材质修正：按声音穿透系数等比例缩放最终半径（向下取整）。

    Args:
        graph: 房间邻接图
        source: 声源所在房间
        target: 听者所在房间
        hearing_radius: 基础听力半径
        door_state: 门状态（可选，影响半径）
        sound_intensity: 声源强度（可选，影响半径）
        wall_material: 墙体材质（可选，影响衰减）

    Returns:
        是否能听到
    """
    # 1. 基础半径
    radius = max(0, int(hearing_radius))

    # 2. 声源强度修正
    if sound_intensity is not None:
        radius += _SOUND_INTENSITY_RADIUS_DELTA.get(sound_intensity, 0)

    # 3. 门状态修正：关闭/上锁/损坏时半径减半（向下取整，至少 0）
    if door_state is not None and door_state in (DoorState.CLOSED, DoorState.LOCKED, DoorState.BROKEN):
        radius = radius // 2

    # 4. 墙体材质修正：按穿透系数缩放（向下取整，至少 0）
    if wall_material is not None:
        transmission = _WALL_MATERIAL_TRANSMISSION.get(wall_material, 1.0)
        radius = max(0, int(radius * transmission))

    if radius <= 0:
        # 半径归零后只能听到同房间内的声音
        return is_same_room(source, target)

    distance = shortest_room_distance(graph, source, target)
    return distance is not None and distance <= radius


def get_intra_room_visibility(
    obstacles: list[str] | None,
    observer_action: str = "",
) -> float:
    """同房间内基于障碍物列表评估可见度。

    房间级模型不模拟坐标，但可通过障碍物列表（如"屏风"、"货架"、"墙角"）
    给出一个粗略的可见度修正系数，调用方据此降低"看到对方细节"的概率。

    Args:
        obstacles: 房间内障碍物名称列表
        observer_action: 观察者当前行动（用于判断是否主动观察，预留扩展）

    Returns:
        可见度系数（0.2-1.0，1.0 表示无遮挡）
    """
    _ = observer_action  # 预留：未来可根据观察者动作（如"窥视"）进一步修正
    if not obstacles:
        return 1.0
    # 每个障碍物降低 0.2 可见度，最低 0.2
    return max(0.2, 1.0 - 0.2 * len(obstacles))


def get_obstacles_for_room(
    environment_state: Mapping[str, object] | None,
    room_name: str,
) -> list[str]:
    """从环境状态提取指定房间的可遮挡物列表。

    用于为 ``get_visible_npcs`` 构造 obstacles 参数：扫描 environment_state 的
    ``objects`` 字段，筛选出位于目标房间且属于家具类（柜/床/窗帘/桌/沙发/箱子/屏风）
    的物件名称，使"躲柜子/床底/窗帘后"具备真实潜行意义。

    Args:
        environment_state: 环境状态字典（含可选 ``objects`` 字段）
        room_name: 房间名称

    Returns:
        该房间内的可遮挡物名称列表；无匹配时返回空列表
    """
    obstacles: list[str] = []
    if not isinstance(environment_state, Mapping):
        return obstacles
    room = str(room_name).strip()
    objects = environment_state.get("objects", [])
    if isinstance(objects, list):
        for obj in objects:
            if not isinstance(obj, Mapping):
                continue
            obj_room = obj.get("room") or obj.get("location")
            if str(obj_room).strip() != room:
                continue
            obj_type = str(obj.get("type", "") or obj.get("name", "")).strip()
            # 筛选可遮挡物：家具类物件
            if obj_type and any(kw in obj_type for kw in ("柜", "床", "窗帘", "桌", "沙发", "箱子", "屏风")):
                obstacles.append(obj_type)
    # 预留：也可从 room_graph 的 obstacles key 读取（若有）
    return obstacles


def get_visible_npcs(
    npcs: list[JsonObject],
    player_location: str,
    obstacles: list[str] | None = None,
) -> list[JsonObject]:
    """获取与玩家同房间的 NPC 列表，考虑同房间遮挡。

    房间级模型不模拟坐标，但可通过 obstacles（障碍物名称列表，如"屏风"、"衣柜"）
    调用 ``get_intra_room_visibility`` 评估可见度系数，据此决定 NPC 是否可见及
    可见程度标注。

    Args:
        npcs: NPC 列表
        player_location: 玩家所在房间
        obstacles: 同房间内障碍物名称列表（可选）。传入时启用遮挡判定：
            可见度系数 < 0.5 视为不可见（跳过）；0.5-0.8 标注"模糊"；>= 0.8 标注"清晰"。
            未传入时（None）保持向后兼容，默认清晰可见。

    Returns:
        同房间可见的 NPC 列表（返回 NPC 副本，附带 ``visibility`` 字段标注可见程度）
    """
    location = str(player_location).strip()
    visible: list[JsonObject] = []
    for npc in npcs:
        npc_location = str(npc.get("current_location") or npc.get("location") or "").strip()
        if not location or npc_location != location:
            continue
        # 同房间 NPC：接入遮挡判定
        if obstacles:
            visibility_coef = get_intra_room_visibility(obstacles)
            if visibility_coef < 0.5:
                # 遮挡严重，视为不可见
                continue
            npc_copy = dict(npc)
            if visibility_coef < 0.8:
                npc_copy["visibility"] = "模糊"
            else:
                npc_copy["visibility"] = "清晰"
            visible.append(npc_copy)
        else:
            # 无 obstacles：默认清晰可见（向后兼容）
            npc_copy = dict(npc)
            npc_copy["visibility"] = "清晰"
            visible.append(npc_copy)
    return visible


def get_audible_npcs(
    graph: RoomGraph,
    npcs: list[JsonObject],
    player_location: str,
    hearing_radius: int = 1,
    *,
    door_state: DoorState | None = None,
    sound_intensity: SoundIntensity | None = None,
    wall_material: WallMaterial | None = None,
) -> list[JsonObject]:
    """获取在可听范围内、但不在同房间的 NPC 列表。

    Args:
        graph: 房间邻接图
        npcs: NPC 列表
        player_location: 玩家所在房间
        hearing_radius: 基础听力半径
        door_state: 门状态（可选）
        sound_intensity: 声源强度（可选）
        wall_material: 墙体材质（可选）
    """
    audible: list[JsonObject] = []
    location = str(player_location).strip()
    for npc in npcs:
        npc_location = str(npc.get("current_location") or npc.get("location") or "").strip()
        if not npc_location or npc_location == location:
            continue
        if can_hear_between_rooms(
            graph,
            location,
            npc_location,
            hearing_radius=hearing_radius,
            door_state=door_state,
            sound_intensity=sound_intensity,
            wall_material=wall_material,
        ):
            audible.append(npc)
    return audible
