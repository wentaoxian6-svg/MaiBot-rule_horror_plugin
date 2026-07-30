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
# 扩展后的房间图：在邻接表基础上额外携带 wall_materials 列表。
# wall_materials 为 list[list[str, str]]，每项形如 ["房间A|房间B", "wood"]，
# 双向存储（A|B 与 B|A 各存一份），值为 WallMaterial 的字符串值，便于 JSON 序列化。
RoomGraphWithMaterials = dict[str, object]


def _normalize_area(item: object) -> str:
    """从场景结构条目中归一化出房间/区域名称。

    统一处理 LLM 可能输出的两种形态：
    - 字符串：直接 strip 返回
    - 字典：按优先级从 ``name``/``title``/``location`` 键提取名称

    ``normalize_rooms``/``build_room_graph`` 以及 ``action_processor._infer_new_location``
    都应使用本函数，避免 ``str(dict)`` 产生 ``"{'name': '走廊'}"`` 这类污染房间名。

    Args:
        item: 场景结构中的区域条目（字符串或字典）

    Returns:
        归一化后的房间名称；无法提取时返回空字符串
    """
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, Mapping):
        for name_key in ("name", "title", "location"):
            raw = item.get(name_key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
    return ""


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
    """从场景结构中提取房间 / 区域列表。

    使用 ``_normalize_area`` 统一处理字符串与字典形态的区域条目，
    避免 LLM 输出 ``{"name": "走廊"}`` 时房间名变为 ``"{'name': '走廊'}"``。
    """
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
                        room_name = _normalize_area(item)
                        if room_name and room_name not in rooms:
                            rooms.append(room_name)

    special_areas = scene_structure.get("special_areas", [])
    if isinstance(special_areas, list):
        for item in special_areas:
            room_name = _normalize_area(item)
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
    - ``"wall_materials"`` → ``list[list[str, str]]``，
      每项形如 ``["房间A|房间B", "wood"]``，**双向存储**（``A|B`` 与 ``B|A`` 各存一份），
      值为 ``WallMaterial`` 的字符串值（如 ``"wood"``/``"concrete"``/``"glass"``），
      便于 JSON 序列化；调用方可走 ``get_wall_material`` 辅助函数获取枚举值。

    墙材质解析顺序：
    1. ``scene_structure["walls"]``：形如 ``[{"rooms": ["A", "B"], "material": "glass"}]``
    2. ``scene_structure["wall_materials"]``：同上结构（兼容字段名）
    3. 未匹配到的邻接关系默认使用配置项 ``npc_sim.default_wall_material``（默认 WOOD）
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
                # 使用 _normalize_area 统一处理字符串/字典形态的区域条目
                floor_rooms = [
                    _normalize_area(item)
                    for item in raw_rooms
                    if _normalize_area(item)
                ] if isinstance(raw_rooms, list) else []
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

    # 构建墙材质列表：遍历所有邻接关系，为每对相邻房间查询墙材质
    # 格式为 list[list[str, str]]，每项 ["房间A|房间B", "wood"]，双向存储
    wall_materials: list[list[str, str]] = []
    for room, neighbors in graph.items():
        if not isinstance(neighbors, list):
            # 此时 graph 中尚无非邻接表条目，防御性跳过
            continue
        for neighbor in neighbors:
            material = _resolve_wall_material(scene_structure, room, neighbor)
            # 双向存储，调用方查 A|B 或 B|A 均可命中
            wall_materials.append([f"{room}|{neighbor}", material.value])

    graph["wall_materials"] = wall_materials
    return graph


def _parse_wall_material_str(material_str: str) -> WallMaterial:
    """把材质字符串解析为 WallMaterial 枚举。

    支持模糊匹配（如 "wood"/"WOOD"/"木板"）与精确枚举值匹配，
    未命中时返回配置默认墙材质（默认 WOOD）。
    """
    raw = str(material_str or "").strip().lower()
    if "glass" in raw:
        return WallMaterial.GLASS
    if "wood" in raw:
        return WallMaterial.WOOD
    if "concrete" in raw:
        return WallMaterial.CONCRETE
    # 未知材质字符串：按枚举值精确匹配
    for member in WallMaterial:
        if member.value == raw:
            return member
    # 未命中时使用配置默认墙材质（不再硬编码 CONCRETE）
    return _get_default_wall_material()


def _get_default_wall_material() -> WallMaterial:
    """从配置读取默认墙材质；配置未声明该字段时使用 WOOD。

    对应配置项 ``npc_sim.default_wall_material``（字符串，如 ``"wood"``）。
    若运行时配置 schema 尚未包含该字段，``getattr`` 返回 ``"wood"``。
    """
    from ..core.config import get_config

    material_str = getattr(get_config().npc_sim, "default_wall_material", "wood")
    return _parse_wall_material_str(material_str)


def _get_default_hearing_radius() -> float:
    """从配置读取默认听力半径；配置缺失时返回 1.0。"""
    from ..core.config import get_config

    return float(getattr(get_config().npc_sim, "room_hearing_radius", 1) or 1)


def _resolve_wall_material(
    scene_structure: Mapping[str, object] | None,
    room_a: str,
    room_b: str,
) -> WallMaterial:
    """从场景结构解析两房间之间的墙材质，缺失时使用配置默认值（默认 WOOD）。

    支持的字段格式（按优先级）：
    1. ``scene_structure["walls"]``：``[{"rooms": ["A", "B"], "material": "glass|wood|concrete"}]``
    2. ``scene_structure["wall_materials"]``：同上结构（字段名兼容）

    Args:
        scene_structure: 场景结构字典
        room_a: 房间 A 名称
        room_b: 房间 B 名称

    Returns:
        对应的 WallMaterial 枚举；未匹配到时返回配置默认墙材质（默认 WOOD）
    """
    if not isinstance(scene_structure, Mapping):
        return _get_default_wall_material()

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
            # 使用 _normalize_area 统一处理 rooms 列表中的字符串/字典形态
            rooms_str = [_normalize_area(item) for item in rooms if _normalize_area(item)]
            # 双向匹配：A-B 或 B-A 都算命中
            if room_a in rooms_str and room_b in rooms_str and room_a != room_b:
                material_str = str(wall.get("material", "")).strip()
                return _parse_wall_material_str(material_str)

    return _get_default_wall_material()


def get_wall_material(
    room_graph: Mapping[str, object],
    room_a: str,
    room_b: str,
) -> WallMaterial:
    """从房间图查询两房间之间的墙材质。

    支持新旧两种 ``wall_materials`` 存储格式：
    - 新格式（list）：``[["房间A|房间B", "wood"], ...]``，双向存储
    - 旧格式（dict）：``{(room_a, room_b): WallMaterial, ...}``（兼容旧内存缓存）

    查询顺序：
    1. 正向 key ``"room_a|room_b"`` 命中
    2. 反向 key ``"room_b|room_a"`` 命中
    3. 未命中时返回配置默认墙材质（默认 WOOD）

    Args:
        room_graph: ``build_room_graph`` 返回的房间图
        room_a: 房间 A 名称
        room_b: 房间 B 名称

    Returns:
        对应的 WallMaterial 枚举
    """
    wall_materials = room_graph.get("wall_materials")
    if wall_materials is None:
        return _get_default_wall_material()

    pair_key = f"{room_a}|{room_b}"
    reverse_key = f"{room_b}|{room_a}"

    # 新格式：list[list[str, str]]
    if isinstance(wall_materials, list):
        for item in wall_materials:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                key = str(item[0])
                if key == pair_key or key == reverse_key:
                    return _parse_wall_material_str(str(item[1]))
        return _get_default_wall_material()

    # 旧格式兼容：dict with tuple/string keys（含 JSON 反序列化后的字符串 key）
    if isinstance(wall_materials, Mapping):
        for key in [(room_a, room_b), (room_b, room_a), pair_key, reverse_key]:
            if key in wall_materials:
                material = wall_materials[key]
                if isinstance(material, WallMaterial):
                    return material
                if isinstance(material, str):
                    return _parse_wall_material_str(material)
        return _get_default_wall_material()

    return _get_default_wall_material()


def normalize_wall_materials_format(wall_materials: object) -> list[list[str, str]]:
    """把任意格式的 wall_materials 规范化为 ``list[list[str, str]]`` 格式。

    用于存档保存/加载时确保格式一致，兼容：
    - 新格式：``list[list[str, str]]``
    - 旧格式：``dict[tuple[str, str], WallMaterial]``（JSON 会把 tuple key 转为字符串）
    - 旧格式 JSON 反序列化后：``dict[str, str]``（key 形如 ``"(A, B)"``）

    Args:
        wall_materials: 任意格式的 wall_materials 数据

    Returns:
        规范化后的 ``list[list[str, str]]``，每项 ``["房间A|房间B", "wood"]``
    """
    # 已是新格式：list，验证并清理
    if isinstance(wall_materials, list):
        result: list[list[str, str]] = []
        for item in wall_materials:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                key = str(item[0])
                val = item[1]
                if isinstance(val, WallMaterial):
                    val_str = val.value
                else:
                    val_str = str(val)
                result.append([key, val_str])
        return result

    # 旧格式：dict，转换为 list
    if isinstance(wall_materials, Mapping):
        result = []
        for key, val in wall_materials.items():
            if isinstance(key, tuple):
                key_str = "|".join(str(k) for k in key)
            else:
                key_str = str(key)
            if isinstance(val, WallMaterial):
                val_str = val.value
            else:
                val_str = str(val)
            result.append([key_str, val_str])
        return result

    return []


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
    hearing_radius: float | None = None,
    *,
    door_state: DoorState | None = None,
    sound_intensity: SoundIntensity | None = None,
    wall_material: WallMaterial | None = None,
) -> float:
    """计算两个房间之间的听力质量 ∈ [0, 1]。

    使用乘性衰减模型，禁止半径向下取整归零（修复旧 bug：半径 1 × 0.3 = 0）。

    传播模型：
    1. 基础听力半径由 ``hearing_radius`` 给出（``None`` 时从配置
       ``npc_sim.room_hearing_radius`` 读取，默认 1.0）。
    2. 声源强度修正：LOUD +1，QUIET -1（加性，不取整）。
    3. 门状态修正：CLOSED/LOCKED/BROKEN 时乘以 0.5 衰减系数。
    4. 墙体材质修正：按穿透系数（WOOD=0.6 / CONCRETE=0.3 / GLASS=0.8）乘性缩放。
       ``wall_material`` 为 ``None`` 时从配置默认墙材质读取（默认 WOOD）。
    5. 距离衰减：``quality = (radius * transmission * door_factor) / distance``，
       最终 clamp 到 [0, 1]。

    Args:
        graph: 房间邻接图
        source: 声源所在房间
        target: 听者所在房间
        hearing_radius: 基础听力半径；``None`` 时从配置读取
        door_state: 门状态（可选，影响衰减系数）
        sound_intensity: 声源强度（可选，影响有效半径）
        wall_material: 墙体材质（可选，``None`` 时用配置默认）

    Returns:
        听力质量 ∈ [0, 1]：0.0 表示不可听，1.0 表示满质量。
        旧调用方以 ``if can_hear_between_rooms(...)`` 判断时，0.0 为 False，
        任意正值均为 True，向后兼容。
    """
    # 1. 基础半径（float，不取整）
    if hearing_radius is None:
        hearing_radius = _get_default_hearing_radius()
    radius = max(0.0, float(hearing_radius))

    # 2. 声源强度修正（加性，不取整）
    if sound_intensity is not None:
        radius = max(0.0, radius + _SOUND_INTENSITY_RADIUS_DELTA.get(sound_intensity, 0))

    # 3. 门状态修正：关闭/上锁/损坏时乘性衰减系数 0.5（不取整）
    door_factor = 1.0
    if door_state is not None and door_state in (DoorState.CLOSED, DoorState.LOCKED, DoorState.BROKEN):
        door_factor = 0.5

    # 4. 墙体材质修正：按穿透系数乘性缩放（不取整）
    # wall_material 为 None 时用配置默认墙材质（默认 WOOD）
    effective_material = wall_material if wall_material is not None else _get_default_wall_material()
    transmission = _WALL_MATERIAL_TRANSMISSION.get(effective_material, 1.0)

    # 5. 距离衰减
    if is_same_room(source, target):
        # 同房间：满质量，不受墙材质/门影响
        return 1.0

    distance = shortest_room_distance(graph, source, target)
    if distance is None or distance <= 0:
        # 不可达或同房间（同房间已在上方处理）
        return 0.0

    # 乘性衰减：quality = (radius * transmission * door_factor) / distance
    # 示例：radius=1, transmission=0.6 (WOOD), door_factor=1.0, distance=1 → 0.6
    #       radius=1, transmission=0.3 (CONCRETE), door_factor=0.5, distance=1 → 0.15
    quality = (radius * transmission * door_factor) / distance
    return max(0.0, min(1.0, quality))


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
    hearing_radius: float | None = None,
    *,
    session: object | None = None,
    door_state: DoorState | None = None,
    sound_intensity: SoundIntensity | None = None,
    wall_material: WallMaterial | None = None,
) -> list[JsonObject]:
    """获取在可听范围内、但不在同房间的 NPC 列表。

    与 ``_build_context`` 中 ``audible_events``/``audible_players`` 两个入口保持一致：
    ``wall_material``/``door_state``/``sound_intensity`` 未传入时，逐 NPC 解析
    玩家与 NPC 之间的墙材质/门状态，并从 NPC 的 ``last_action`` 推断声源强度，
    避免三入口因参数缺失导致结果不一致。

    Args:
        graph: 房间邻接图
        npcs: NPC 列表
        player_location: 玩家所在房间
        hearing_radius: 基础听力半径；``None`` 时从配置读取
        session: 游戏会话；传入后用于 ``door_state=None`` 时逐 NPC 解析门状态
        door_state: 门状态（可选，``None`` 且 ``session`` 传入时逐 NPC 解析）
        sound_intensity: 声源强度（可选，``None`` 时从 NPC ``last_action`` 推断）
        wall_material: 墙体材质（可选，``None`` 时从房间图解析）
    """
    # 延迟导入避免 common → systems 的循环依赖
    from ..common.door_utils import get_door_state_between
    from ..common.sound_utils import infer_sound_intensity

    audible: list[JsonObject] = []
    location = str(player_location).strip()
    for npc in npcs:
        npc_location = str(npc.get("current_location") or npc.get("location") or "").strip()
        if not npc_location or npc_location == location:
            continue
        # 统一墙材质参数：未传入时从房间图解析，与 audible_events/audible_players 保持一致
        effective_wall_material = wall_material
        if effective_wall_material is None:
            effective_wall_material = get_wall_material(graph, location, npc_location)
        # 统一门状态参数：未传入且 session 可用时逐 NPC 解析
        effective_door_state = door_state
        if effective_door_state is None and session is not None:
            effective_door_state = get_door_state_between(session, location, npc_location)
        # 统一声源强度参数：未传入时从 NPC 最近行动推断
        effective_sound_intensity = sound_intensity
        if effective_sound_intensity is None:
            npc_last_action = str(npc.get("last_action", "") or "")
            effective_sound_intensity = infer_sound_intensity(npc_last_action)
        quality = can_hear_between_rooms(
            graph,
            location,
            npc_location,
            hearing_radius=hearing_radius,
            door_state=effective_door_state,
            sound_intensity=effective_sound_intensity,
            wall_material=effective_wall_material,
        )
        if quality > 0:
            audible.append(npc)
    return audible


# ---------------------------------------------------------------------------
# Task 3: 房间级距离衰减与双人协作机制
#
# 原 ``multiplayer_physics_system.py`` 已不存在，以下能力直接在 ``room_topology.py``
# 实现，供 ``action_processor``（机关判定）与 ``npc_simulator``（感知计算）后续接入。
# 本任务仅暴露函数，不修改 ``action_processor.py`` / ``npc_simulator.py``。
# ---------------------------------------------------------------------------


# 房间级距离衰减系数表：基于最短步数的乘性衰减
# distance=0（同房间）→ 1.0；distance=1（相邻）→ 0.7；distance=2 → 0.4；distance=3 → 0.15
_ROOM_DISTANCE_DECAY_TABLE: dict[int, float] = {
    0: 1.0,
    1: 0.7,
    2: 0.4,
    3: 0.15,
}


def get_room_distance_decay(
    graph: RoomGraph,
    source: str,
    target: str,
    *,
    max_effective_distance: int = 3,
) -> float:
    """计算房间级距离衰减系数 ∈ [0, 1]。

    基于房间深度（最短步数）的乘性衰减，用于：
    - 行动效果衰减（如远程交互/远程支援的效力随距离递减）
    - PvP 威胁感知（距离越远，威胁感越低）
    - NPC 搜寻范围（距离越远，NPC 找到目标概率越低）

    衰减表（``_ROOM_DISTANCE_DECAY_TABLE``）：
    - distance=0（同房间）：1.0
    - distance=1（相邻）：0.7
    - distance=2：0.4
    - distance=3：0.15
    - distance>=max_effective_distance：0.0（超出有效范围）

    Args:
        graph: 房间邻接图
        source: 源房间
        target: 目标房间
        max_effective_distance: 最大有效距离，超过则返回 0.0

    Returns:
        衰减系数 ∈ [0, 1]
    """
    distance = shortest_room_distance(graph, source, target)
    if distance is None:
        return 0.0
    if distance >= max_effective_distance:
        return 0.0
    return _ROOM_DISTANCE_DECAY_TABLE.get(distance, 0.0)


def get_same_room_players(
    players_locations: Mapping[str, str],
    room: str,
) -> list[str]:
    """获取当前位于指定房间内的所有玩家 ID。

    用于双人机关判定：调用方传入 ``{player_id: location}`` 映射与目标房间，
    返回当前在该房间内的玩家 ID 列表。

    Args:
        players_locations: 玩家 ID → 当前房间位置的映射
        room: 目标房间名称

    Returns:
        在该房间内的玩家 ID 列表（按输入顺序）
    """
    room_normalized = str(room).strip()
    if not room_normalized:
        return []
    return [
        pid for pid, loc in players_locations.items()
        if str(loc).strip() == room_normalized
    ]


def is_dual_player_coop_eligible(
    players_locations: Mapping[str, str],
    room: str,
    *,
    min_players: int = 2,
) -> bool:
    """检测指定房间是否满足双人协作触发条件。

    当房间内存活玩家数 >= ``min_players``（默认 2）时返回 True，
    用于双人机关（如需要两人同时在场才能开启的密门、电梯、机关门等）。

    Args:
        players_locations: 玩家 ID → 当前房间位置的映射
        room: 目标房间名称
        min_players: 触发协作所需的最少玩家数

    Returns:
        是否满足协作触发条件
    """
    present = get_same_room_players(players_locations, room)
    return len(present) >= min_players


def get_coop_action_bonus(
    players_locations: Mapping[str, str],
    room: str,
    *,
    max_bonus: float = 0.3,
    min_players: int = 2,
) -> float:
    """计算同房间双人协作的行动加成系数 ∈ [0, max_bonus]。

    当房间内玩家数 >= ``min_players`` 时给予加成，用于：
    - 提升双人协作行动的成功率（如合力推门、互相协助）
    - 降低恐惧/压力影响（同伴在身边的安抚效果）

    加成公式：
    - 2 人：``max_bonus``（默认 0.3）
    - 3+ 人：``max_bonus * 1.5``（上限 ``max_bonus * 1.5``）
    - 不足 ``min_players``：0.0

    Args:
        players_locations: 玩家 ID → 当前房间位置的映射
        room: 目标房间名称
        max_bonus: 最大加成系数
        min_players: 触发加成的最少玩家数

    Returns:
        协作加成系数 ∈ [0, max_bonus * 1.5]
    """
    present = get_same_room_players(players_locations, room)
    count = len(present)
    if count < min_players:
        return 0.0
    if count <= 2:
        return max_bonus
    # 3+ 人时加成提升 50%，但不超过 max_bonus * 1.5
    return min(max_bonus * 1.5, max_bonus * 1.5)


def get_room_depth_factor(
    graph: RoomGraph,
    room: str,
    *,
    reference_rooms: list[str] | None = None,
    max_depth: int = 5,
) -> float:
    """计算房间深度因子 ∈ [0, 1]。

    基于从参考房间（默认为所有度数为 1 的"入口"房间）到目标房间的最短路径长度，
    衡量"偏僻程度"。深度越大（越偏僻），因子越接近 1.0，用于：
    - 提升 NPC 在偏远房间的搜寻难度
    - 增加偏远房间的恐怖事件触发概率
    - 降低偏远房间的被发现概率

    Args:
        graph: 房间邻接图
        room: 目标房间
        reference_rooms: 参考入口房间列表；``None`` 时自动选取度数为 1 的房间
        max_depth: 最大有效深度，超过则返回 1.0

    Returns:
        深度因子 ∈ [0, 1]，0 表示入口房间，1 表示深度 >= max_depth
    """
    target = str(room).strip()
    if not target:
        return 0.0

    # 未指定参考房间时，自动选取度数为 1 的"入口"房间
    if reference_rooms is None:
        reference_rooms = [
            r for r, neighbors in graph.items()
            if isinstance(neighbors, list) and len(neighbors) <= 1 and r != "wall_materials"
        ]

    if not reference_rooms:
        return 0.0

    # 取从任一入口到目标房间的最短距离
    min_distance: int | None = None
    for ref in reference_rooms:
        ref_room = str(ref).strip()
        if not ref_room or ref_room == target:
            min_distance = 0
            break
        dist = shortest_room_distance(graph, ref_room, target)
        if dist is not None:
            if min_distance is None or dist < min_distance:
                min_distance = dist

    if min_distance is None:
        # 不可达，视为最深
        return 1.0
    if min_distance >= max_depth:
        return 1.0
    return min_distance / max_depth
