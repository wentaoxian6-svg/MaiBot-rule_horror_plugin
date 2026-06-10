"""业务服务层 - 封装核心业务逻辑"""
from __future__ import annotations

from .intent_parser import IntentParser, PlayerAction, ActionType
from .immersive_feedback import ImmersiveFeedback, FeedbackResponse, FeedbackType
from .scene_generator import SceneGenerator, SceneData, SceneType
from .multiplayer_contradiction import (
    MultiplayerContradictionSystem,
    PlayerRuleset,
)
from .horror_atmosphere import (
    HorrorAtmosphereEnhancer,
    AtmosphereEvent,
    AtmosphereIntensity,
)
from .action_processor import ActionProcessor, ActionResult
from .game_generator import GameGenerator
from .npc_simulator import NPCSimulator
from .ending_judge import EndingJudge, EndingResult, EndingType

__all__ = [
    "IntentParser",
    "PlayerAction",
    "ActionType",
    "ImmersiveFeedback",
    "FeedbackResponse",
    "FeedbackType",
    "SceneGenerator",
    "SceneData",
    "SceneType",
    "MultiplayerContradictionSystem",
    "PlayerRuleset",
    "HorrorAtmosphereEnhancer",
    "AtmosphereEvent",
    "AtmosphereIntensity",
    "ActionProcessor",
    "ActionResult",
    "GameGenerator",
    "NPCSimulator",
    "EndingJudge",
    "EndingResult",
    "EndingType",
]
