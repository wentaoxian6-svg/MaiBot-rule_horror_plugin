"""  # pyright: ignore[reportImportCycles]
命令路由器 - 将命令分发到对应的处理器
"""
from __future__ import annotations

from collections.abc import Awaitable
from typing import Callable

from ..common.constants import GameCommands




CommandHandler = Callable[
    [object, str, str, str, str],
    Awaitable[tuple[bool, str | None, int]]
]



class CommandRouter:
    """命令路由器

    负责将用户命令路由到对应的处理方法
    """

    def __init__(self):
        self._routes: dict[str, CommandHandler | str] = {}

    def register(self, command: str, handler: CommandHandler | str) -> None:
        """注册命令处理器

        Args:
            command: 命令名称
            handler: 处理器函数或处理器方法名
        """
        self._routes[command] = handler

    def route(self, command: str) -> CommandHandler | str | None:
        """路由命令到处理器

        Args:
            command: 命令名称

        Returns:
            处理器函数或方法名，如果未找到则返回None
        """
        return self._routes.get(command)

    def has_route(self, command: str) -> bool:
        """检查命令是否已注册

        Args:
            command: 命令名称

        Returns:
            是否已注册
        """
        return command in self._routes


def create_default_router() -> CommandRouter:
    """创建默认的命令路由器
    
    Returns:
        配置好的命令路由器
    """
    router = CommandRouter()
    
    # 注册所有命令（实际的处理器将在RuleHorrorCommand中定义）
    # 这里只是定义路由映射关系
    command_map = {
        GameCommands.START.value: "_handle_开始",
        GameCommands.FORCE_START.value: "_handle_强制开始",
        GameCommands.RESTORE.value: "_handle_恢复",
        GameCommands.SAVE.value: "_handle_保存",
        GameCommands.LOAD.value: "_handle_读取",
        GameCommands.SAVE_LIST.value: "_handle_存档列表",
        GameCommands.CLEAN_SAVES.value: "_handle_清理存档",
        GameCommands.JOIN.value: "_handle_加入",
        GameCommands.LEAVE.value: "_handle_离开",
        GameCommands.STATUS.value: "_handle_状态",
        GameCommands.PLOT.value: "_handle_剧情",
        GameCommands.RULES.value: "_handle_规则",
        GameCommands.SCENE.value: "_handle_场景",
        GameCommands.ITEMS.value: "_handle_道具",
        GameCommands.CLUES.value: "_handle_线索",
        GameCommands.HINT.value: "_handle_提示",
        GameCommands.REASON.value: "_handle_推理",
        GameCommands.ACTION.value: "_handle_行动",
        GameCommands.CONTINUE.value: "_handle_继续",
        GameCommands.END.value: "_handle_结束",
        GameCommands.HELP.value: "_handle_帮助",
        GameCommands.IDENTITY.value: "_handle_身份",
    }

    
    # 注册路由（实际处理器将通过方法名动态获取）
    for command, handler_name in command_map.items():
        # 这里存储处理器方法名，实际调用时会通过getattr获取
        router.register(command, handler_name)

    return router


def get_handler_method_name(command: str) -> str | None:
    """获取命令对应的处理器方法名
    
    Args:
        command: 命令名称
        
    Returns:
        处理器方法名，如果未找到则返回None
    """
    command_map = {
        GameCommands.START.value: "_handle_开始",
        GameCommands.FORCE_START.value: "_handle_强制开始",
        GameCommands.RESTORE.value: "_handle_恢复",
        GameCommands.SAVE.value: "_handle_保存",
        GameCommands.LOAD.value: "_handle_读取",
        GameCommands.SAVE_LIST.value: "_handle_存档列表",
        GameCommands.CLEAN_SAVES.value: "_handle_清理存档",
        GameCommands.JOIN.value: "_handle_加入",
        GameCommands.LEAVE.value: "_handle_离开",
        GameCommands.STATUS.value: "_handle_状态",
        GameCommands.PLOT.value: "_handle_剧情",
        GameCommands.RULES.value: "_handle_规则",
        GameCommands.SCENE.value: "_handle_场景",
        GameCommands.ITEMS.value: "_handle_道具",
        GameCommands.CLUES.value: "_handle_线索",
        GameCommands.HINT.value: "_handle_提示",
        GameCommands.REASON.value: "_handle_推理",
        GameCommands.ACTION.value: "_handle_行动",
        GameCommands.CONTINUE.value: "_handle_继续",
        GameCommands.END.value: "_handle_结束",
        GameCommands.HELP.value: "_handle_帮助",
        GameCommands.IDENTITY.value: "_handle_身份",
    }

    
    return command_map.get(command)
