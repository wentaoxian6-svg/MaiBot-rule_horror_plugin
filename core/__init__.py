"""核心模块 - 重构后的规则怪谈插件核心组件"""
from __future__ import annotations

from .config import LLMConfig, PluginConfig, SaveConfig, Config, get_config, set_config
from .config.loader import load_config_from_file, reload_config
from .llm import LLMClient, LLMResponse, LLMError, PromptBuilder, get_default_max_tokens
from .game import GameStateManager, GameState, SaveManager, Player, GameSession, GameStatus, PlayerStatus
from .content import AsyncImageGenerator, TextFormatter

__all__ = [
    # 配置
    "LLMConfig",
    "PluginConfig",
    "SaveConfig",
    "Config",
    "get_config",
    "set_config",
    "load_config_from_file",
    "reload_config",
    # LLM
    "LLMClient",
    "LLMResponse",
    "LLMError",
    "PromptBuilder",
    "get_default_max_tokens",
    # 游戏管理
    "GameStateManager",
    "GameState",
    "SaveManager",
    "Player",
    "GameSession",
    "GameStatus",
    "PlayerStatus",
    # 内容生成
    "AsyncImageGenerator",
    "TextFormatter",
]
