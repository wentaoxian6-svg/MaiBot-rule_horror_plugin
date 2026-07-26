"""业务服务层 - 封装核心业务逻辑"""
from __future__ import annotations

from .event_bus import EventBus, GameEvent
from .factories import (
    EnvironmentSystemFactory,
    EventBusFactory,
    NPCSimulatorFactory,
    RuleMutationSystemFactory,
    RuntimeFactories,
)
from .immersive_feedback import ImmersiveFeedback, FeedbackResponse, FeedbackType
from .action_processor import ActionProcessor, ActionResult
from .game_generator import GameGenerator
from .npc_simulator import NPCSimulator
from .ending_judge import EndingJudge, EndingResult, EndingType

__all__ = [
    "EventBus",
    "GameEvent",
    "ImmersiveFeedback",
    "FeedbackResponse",
    "FeedbackType",
    "ActionProcessor",
    "ActionResult",
    "GameGenerator",
    "NPCSimulator",
    "EndingJudge",
    "EndingResult",
    "EndingType",
    "EnvironmentSystemFactory",
    "EventBusFactory",
    "NPCSimulatorFactory",
    "RuleMutationSystemFactory",
    "RuntimeFactories",
]
