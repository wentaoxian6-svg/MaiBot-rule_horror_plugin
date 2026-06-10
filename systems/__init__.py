"""
游戏系统模块 - 环境、时间、规则、线索、多人物理、NPC等系统
"""
from .environment_state import EnvironmentState, DoorState, LightState
from .environment_evolution import EnvironmentEvolutionSystem
from .game_time_manager import GameTimeManager
from .rule_mutation_system import RuleMutationSystem
from .clue_discovery_system import ClueDiscoverySystem
from .multiplayer_physics_system import MultiplayerPhysicsSystem
from .npc_system import NPCMemory, NPCAttitude, NPC
from .room_topology import (
    build_room_graph,
    can_hear_between_rooms,
    get_audible_npcs,
    get_visible_npcs,
    is_adjacent_room,
    is_same_room,
    normalize_rooms,
    shortest_room_distance,
)

__all__ = [
    "EnvironmentState",
    "DoorState",
    "LightState",
    "EnvironmentEvolutionSystem",
    "GameTimeManager",
    "RuleMutationSystem",
    "ClueDiscoverySystem",
    "MultiplayerPhysicsSystem",
    "NPCMemory",
    "NPCAttitude",
    "NPC",
    "normalize_rooms",
    "build_room_graph",
    "is_same_room",
    "is_adjacent_room",
    "shortest_room_distance",
    "can_hear_between_rooms",
    "get_visible_npcs",
    "get_audible_npcs",
]
