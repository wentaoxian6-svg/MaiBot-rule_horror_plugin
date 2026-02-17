"""
自定义异常 - 提供清晰的错误类型，避免使用通用Exception
"""


class RuleHorrorError(Exception):
    """规则怪谈插件基础异常"""
    pass


class ConfigError(RuleHorrorError):
    """配置相关错误"""
    pass


class ConfigLoadError(ConfigError):
    """配置加载失败"""
    pass


class ConfigValidationError(ConfigError):
    """配置验证失败"""
    pass


class GameStateError(RuleHorrorError):
    """游戏状态相关错误"""
    pass


class GameNotFoundError(GameStateError):
    """游戏不存在"""
    pass


class GameAlreadyExistsError(GameStateError):
    """游戏已存在"""
    pass


class PlayerError(RuleHorrorError):
    """玩家相关错误"""
    pass


class PlayerNotFoundError(PlayerError):
    """玩家不存在"""
    pass


class PlayerAlreadyJoinedError(PlayerError):
    """玩家已加入"""
    pass


class PlayerNotInGameError(PlayerError):
    """玩家未加入游戏"""
    pass


class SaveError(RuleHorrorError):
    """存档相关错误"""
    pass


class SaveNotFoundError(SaveError):
    """存档不存在"""
    pass


class SaveLoadError(SaveError):
    """存档加载失败"""
    pass


class SaveWriteError(SaveError):
    """存档写入失败"""
    pass


class LLMError(RuleHorrorError):
    """LLM相关错误"""
    pass


class LLMRequestError(LLMError):
    """LLM请求失败"""
    pass


class LLMResponseError(LLMError):
    """LLM响应解析失败"""
    pass


class LLMTimeoutError(LLMError):
    """LLM请求超时"""
    pass


class ImageGenerationError(RuleHorrorError):
    """图片生成错误"""
    pass


class ValidationError(RuleHorrorError):
    """数据验证错误"""
    pass


class InvalidActionError(ValidationError):
    """无效的行动"""
    pass


class InvalidCommandError(ValidationError):
    """无效的命令"""
    pass
