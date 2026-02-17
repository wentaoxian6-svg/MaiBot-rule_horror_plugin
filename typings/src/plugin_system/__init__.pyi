"""Plugin System 类型存根文件"""
from __future__ import annotations
from typing import Any, Callable, Optional, TypeVar, Generic, Type

T = TypeVar('T')

class ConfigField:
    """配置字段定义"""
    def __init__(
        self,
        type: Type[Any],
        default: Any = None,
        description: str = "",
        required: bool = False,
        **kwargs: Any
    ) -> None:
        self.type: Type[Any]
        self.default: Any
        self.description: str
        self.required: bool

class PythonDependency:
    """Python 依赖定义"""
    def __init__(
        self,
        package_name: str,
        version_spec: str = "",
        **kwargs: Any
    ) -> None:
        self.package_name: str
        self.version_spec: str

class BasePlugin:
    """插件基类"""
    plugin_name: str
    enable_plugin: bool
    dependencies: list[str]
    python_dependencies: list[PythonDependency]
    config_file_name: str
    plugin_description: str
    plugin_version: str
    plugin_author: str
    config_section_descriptions: dict[str, str]
    config_schema: dict[str, dict[str, ConfigField]]

    def __init__(
        self,
        plugin_dir: str | None = None,
        plugin_config: dict[str, Any] | None = None,
        **kwargs: Any
    ) -> None:
        self.plugin_dir: str
        self.plugin_config: dict[str, Any] | None

    async def on_load(self) -> None:
        """插件加载时调用"""
        ...

    async def on_unload(self) -> None:
        """插件卸载时调用"""
        ...

    def get_plugin_components(self) -> list[tuple[Any, Type[Any]]]:
        """获取插件组件"""
        ...

    def get_config(self, key: str, default: T | None = None) -> T | None:
        """获取配置值"""
        ...

class BaseCommand:
    """命令基类"""
    command_name: str
    command_description: str
    command_pattern: str
    command_help: str
    matched_groups: dict[str, str] | None
    message: Any
    chat_stream: Any

    def __init__(
        self,
        message: Any,
        plugin_config: dict[str, Any] | None = None
    ) -> None:
        self.message: Any
        self.plugin_config: dict[str, Any] | None

    async def execute(self) -> tuple[bool, Optional[str], int]:
        """执行命令"""
        ...

    async def send_text(self, text: str) -> bool:
        """发送文本消息"""
        ...

    async def send_image(self, image_base64: str) -> bool:
        """发送图片消息"""
        ...

def register_plugin(cls: Type[T]) -> Type[T]:
    """插件注册装饰器"""
    ...

# 导出
__all__ = [
    "BasePlugin",
    "BaseCommand",
    "ConfigField",
    "PythonDependency",
    "register_plugin",
]
