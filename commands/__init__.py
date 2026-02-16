"""
命令处理模块 - 命令路由和处理器
"""
from .router import (
    CommandRouter,
    create_default_router,
    get_handler_method_name,
)

__all__ = [
    "CommandRouter",
    "create_default_router",
    "get_handler_method_name",
]
