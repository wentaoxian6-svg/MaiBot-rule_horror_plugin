"""
规则怪谈插件入口。

`plugin.py` 只保留插件生命周期、配置接线和命令装配；
具体命令处理、运行时辅助以及单人/多人流程分发均已拆到独立模块。
"""

from __future__ import annotations

import logging
import os
import re
import sys
import types
from pathlib import Path
from typing import Any

from maibot_sdk import Command, MaiBotPlugin


def _bootstrap_relative_imports() -> None:
    plugin_dir = Path(__file__).resolve().parent
    base = re.sub(r"[^0-9a-zA-Z_]", "_", plugin_dir.name)
    if not base:
        base = "_"
    if base[0].isdigit():
        base = f"_{base}"
    package_name = f"_maibot_plugin_{base}_{abs(hash(str(plugin_dir)))}"
    if package_name not in sys.modules:
        pkg = types.ModuleType(package_name)
        pkg.__path__ = [str(plugin_dir)]
        pkg.__file__ = str(plugin_dir / "__init__.py")
        pkg.__package__ = package_name
        sys.modules[package_name] = pkg
    globals()["__package__"] = package_name


_bootstrap_relative_imports()

from .commands.handler import RuleHorrorCommand
from .core import (
    AsyncImageGenerator,
    GameStateManager,
    LLMClient,
    SaveManager,
    apply_plugin_config_overrides,
    load_config_from_file,
)
from .common import (
    ConfigLoadError,
    DirectoryNames,
    ErrorMessages,
    FileNames,
    SuccessMessages,
    resolve_data_dir,
)
from .plugin_config import RuleHorrorPluginConfig


logger = logging.getLogger(__name__)
PLUGIN_DIR = os.path.dirname(__file__)


class RuleHorrorPlugin(MaiBotPlugin):
    """规则怪谈插件入口。"""

    config_model = RuleHorrorPluginConfig

    def __init__(self) -> None:
        super().__init__()
        self.state_manager: GameStateManager | None = None
        self.save_manager: SaveManager | None = None
        self.llm_client: LLMClient | None = None
        self.image_generator: AsyncImageGenerator | None = None
        self._temp_images_dir: str = ""

    async def on_load(self) -> None:
        """加载插件运行时资源并初始化核心服务。"""
        plugin_dir = PLUGIN_DIR
        data_dir = resolve_data_dir(plugin_dir, DirectoryNames.DATA)
        temp_images_dir = os.path.join(data_dir, DirectoryNames.TEMP_IMAGES)
        self._temp_images_dir = temp_images_dir

        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(temp_images_dir, exist_ok=True)

        try:
            load_config_from_file(os.path.join(plugin_dir, FileNames.CONFIG))
            apply_plugin_config_overrides(self.get_plugin_config_data())
        except Exception as exc:
            logger.error("%s: %s", ErrorMessages.CONFIG_LOAD_FAILED, exc)
            raise ConfigLoadError(f"{ErrorMessages.CONFIG_LOAD_FAILED}: {exc}") from exc

        self.state_manager = GameStateManager()
        self.save_manager = SaveManager(os.path.join(data_dir, DirectoryNames.SAVES))
        self.llm_client = LLMClient()
        self.image_generator = AsyncImageGenerator(self._temp_images_dir)

        await self.state_manager.start()
        await self.save_manager.start()
        logger.info(SuccessMessages.PLUGIN_LOADED)

    async def on_unload(self) -> None:
        """卸载插件并关闭核心服务。"""
        if self.save_manager:
            await self.save_manager.stop()
        if self.state_manager:
            await self.state_manager.stop()
        if self.llm_client:
            await self.llm_client.close()
        if self.image_generator:
            await self.image_generator.close()
        logger.info(SuccessMessages.PLUGIN_UNLOADED)

    async def on_config_update(self, scope: str, config_data: dict[str, object], version: str) -> None:
        """在配置更新后重新应用插件覆盖配置。"""
        del scope
        del config_data
        del version
        apply_plugin_config_overrides(self.get_plugin_config_data())

    async def _resolve_user_name(self, stream_id: str, user_id: str) -> str:
        """尽量从最近消息里解析用户昵称。"""
        if not stream_id or not user_id:
            return f"用户{user_id or '未知'}"

        try:
            recent = await self.ctx.message.get_recent(stream_id=stream_id, limit=10)
            messages = recent if isinstance(recent, list) else []
            for item in reversed(messages):
                if not isinstance(item, dict):
                    continue
                message_info = item.get("message_info", {})
                user_info = message_info.get("user_info", {}) if isinstance(message_info, dict) else {}
                if str(user_info.get("user_id", "") or "") != user_id:
                    continue
                for key in ("user_cardname", "user_nickname"):
                    value = str(user_info.get(key, "") or "").strip()
                    if value:
                        return value
        except Exception as exc:
            logger.debug("解析用户昵称失败: %s", exc)

        return f"用户{user_id}"

    @Command("rule_horror", description="规则怪谈游戏主命令", pattern=r"^/rg\s+(?P<action>\S+)(?:\s+(?P<rest>.+))?")
    async def handle_rule_horror(
        self,
        stream_id: str = "",
        group_id: str = "",
        user_id: str = "",
        matched_groups: dict[str, str] | None = None,
        text: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str | None, int]:
        """处理 `/rg` 主命令。"""
        del kwargs

        user_name = await self._resolve_user_name(stream_id, user_id)
        plugin_config: dict[str, object] = dict(self.get_plugin_config_data())
        plugin_config["plugin_dir"] = PLUGIN_DIR

        command = RuleHorrorCommand(
            plugin=self,
            stream_id=stream_id,
            group_id=group_id,
            user_id=user_id,
            user_name=user_name,
            raw_text=text,
            matched_groups=matched_groups,
            plugin_config=plugin_config,
        )
        return await command.execute()


def create_plugin() -> RuleHorrorPlugin:
    """创建插件实例。"""
    return RuleHorrorPlugin()
