"""
游戏系统模块 - 环境、规则、房间级物理感知、NPC等系统
"""
from .environment_evolution import DoorState, EnvironmentEvolutionSystem, LightState
from .rule_mutation_system import RuleMutationSystem
from .npc_system import NPCMemory, NPCAttitude, NPC
from .room_topology import (
    SoundIntensity,
    WallMaterial,
    build_room_graph,
    can_hear_between_rooms,
    get_audible_npcs,
    get_intra_room_visibility,
    get_obstacles_for_room,
    get_visible_npcs,
    is_adjacent_room,
    is_same_room,
    normalize_rooms,
    shortest_room_distance,
)

__all__ = [
    "DoorState",
    "LightState",
    "EnvironmentEvolutionSystem",
    "RuleMutationSystem",
    "NPCMemory",
    "NPCAttitude",
    "NPC",
    "SoundIntensity",
    "WallMaterial",
    "normalize_rooms",
    "build_room_graph",
    "is_same_room",
    "is_adjacent_room",
    "shortest_room_distance",
    "can_hear_between_rooms",
    "get_intra_room_visibility",
    "get_obstacles_for_room",
    "get_visible_npcs",
    "get_audible_npcs",
]
