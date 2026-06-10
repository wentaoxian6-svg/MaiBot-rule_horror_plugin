from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING

from ..core import AsyncImageGenerator, GameSession, GameStateManager, TextFormatter
from ..core.services import ActionProcessor, EndingJudge, GameGenerator, ImmersiveFeedback, NPCSimulator
from ..common import DirectoryNames, ErrorMessages, GameModes, resolve_data_dir
from ..flows.multiplayer_flow import MultiplayerFlow
from ..flows.singleplayer_flow import SingleplayerFlow
from ..systems import ClueDiscoverySystem, EnvironmentEvolutionSystem, GameTimeManager, MultiplayerPhysicsSystem, RuleMutationSystem
from .runtime_support import RuntimeSupportMixin
from .session_runtime import SessionRuntimeMixin
from .shared_handlers import SharedCommandHandlersMixin

if TYPE_CHECKING:
    from ..plugin import RuleHorrorPlugin

PLUGIN_DIR = os.path.dirname(os.path.dirname(__file__))


class RuleHorrorCommand(SessionRuntimeMixin, RuntimeSupportMixin, SharedCommandHandlersMixin):
    """规则怪谈命令处理器。"""

    command_name: str = "RuleHorrorCommand"
    command_description: str = "规则怪谈游戏：生成规则怪谈、加入/离开、提示、推理、行动、结束"
    command_pattern: str = r"^/rg\s+(?P<action>\S+)(?:\s+(?P<rest>.+))?"

    command_help: str = (
        "规则怪谈游戏：\n"
        "/rg 开始 单人 - 生成并开始单人游戏（自动加入）\n"
        "/rg 开始 多人 - 创建多人大厅（房主自动加入）\n"
        "/rg 开始 多人 开始 - 人数到齐后生成并开始多人游戏\n"
        "/rg 强制开始 单人/多人 - 覆盖存档并强制开始\n"
        "/rg 恢复 - 恢复默认存档\n"
        "/rg 保存 <存档名称> - 手动保存当前游戏状态\n"
        "/rg 读取 <存档名称> - 读取指定命名存档\n"
        "/rg 存档列表 - 查看所有存档\n"
        "/rg 清理存档 - 清理已结束存档与过期图片缓存\n"
        "/rg 加入 - 加入游戏（多人模式）\n"
        "/rg 离开 - 离开游戏\n"
        "/rg 状态 - 查看游戏状态\n"
        "/rg 剧情 - 查看剧情导入\n"
        "/rg 规则 - 查看你记录下来的规则\n"
        "/rg 场景 - 查看场景结构\n"
        "/rg 道具 [道具名称] - 查看道具列表或详情\n"
        "/rg 提示 <规则/线索> - 获取提示\n"
        "/rg 推理 <推理内容> - 记录推理\n"
        "/rg 记录规则 <规则内容> - 记录你推理出的规则\n"
        "/rg 行动 <行动描述> - 描述行动\n"
        "/rg 继续 - 通关后继续探索\n"
        "/rg 结束 - 结束游戏\n"
        "/rg 帮助 - 查看帮助"
    )

    def __init__(
        self,
        plugin: RuleHorrorPlugin,
        *,
        stream_id: str,
        group_id: str,
        user_id: str,
        user_name: str,
        raw_text: str = "",
        matched_groups: Mapping[str, str] | None = None,
        plugin_config: Mapping[str, object] | None = None,
    ) -> None:
        plugin_config_dict = dict(plugin_config) if plugin_config else {}
        self.plugin = plugin
        self.ctx = plugin.ctx
        self.stream_id = str(stream_id or "").strip()
        self.group_id = str(group_id or "").strip()
        self.user_id = str(user_id or "").strip()
        self.user_name = str(user_name or "").strip() or f"玩家{self.user_id or '未知'}"
        self.raw_text = str(raw_text or "")
        self.matched_groups = dict(matched_groups) if matched_groups else {}
        self.plugin_config = plugin_config_dict

        self._formatter = TextFormatter()
        self._feedback_system = ImmersiveFeedback()
        self._action_processor = ActionProcessor(
            message_sender=self.send_text,
            session_saver=self._schedule_session_save,
        )
        self._game_generator: GameGenerator | None = None
        self._ending_judge = EndingJudge()
        self._npc_simulator: NPCSimulator | None = None
        self._environment_system: EnvironmentEvolutionSystem | None = None
        self._game_time_manager: GameTimeManager | None = None
        self._rule_mutation_system: RuleMutationSystem | None = None
        self._clue_discovery_system: ClueDiscoverySystem | None = None
        self._multiplayer_physics_system: MultiplayerPhysicsSystem | None = None
        self._singleplayer_flow = SingleplayerFlow(self)
        self._multiplayer_flow = MultiplayerFlow(self)
        
        # 获取临时图片目录
        if plugin_config_dict and "plugin_dir" in plugin_config_dict:
            plugin_dir_raw = plugin_config_dict.get("plugin_dir")
            plugin_dir = plugin_dir_raw if isinstance(plugin_dir_raw, str) else PLUGIN_DIR
            data_dir = resolve_data_dir(plugin_dir, DirectoryNames.DATA)
            self._temp_images_dir = os.path.join(data_dir, DirectoryNames.TEMP_IMAGES)

        else:
            # Linux/容器下插件目录可能只读，这里也要走统一的数据目录解析
            data_dir = resolve_data_dir(PLUGIN_DIR, DirectoryNames.DATA)
            self._temp_images_dir = os.path.join(data_dir, DirectoryNames.TEMP_IMAGES)
        
        # 确保目录存在
        os.makedirs(self._temp_images_dir, exist_ok=True)

    async def send_text(self, content: str) -> bool:
        """发送文本消息。"""
        return await self.ctx.send.text(content, self.stream_id)

    async def send_image(self, image_base64: str) -> bool:
        """发送图片消息。"""
        return await self.ctx.send.image(image_base64, self.stream_id)

    def get_image_generator(self) -> AsyncImageGenerator:
        """获取插件级图片生成器，避免重复创建线程池。"""
        generator = self.plugin.image_generator
        if generator is None:
            generator = AsyncImageGenerator(self._temp_images_dir)
            self.plugin.image_generator = generator
        return generator

    async def _schedule_session_save(self, group_id: str, session: GameSession) -> None:
        """为异步后台任务提供统一存档入口。"""
        await self.plugin.save_manager.schedule_save(group_id, session)

    def get_config(self, key: str, default: object = None) -> object:
        """从当前插件配置中读取值。"""
        current: object = self.plugin.get_plugin_config_data()
        for part in str(key).split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    def _get_group_id(self) -> str:

        """获取群组/用户ID"""
        return self.group_id or self.user_id or "unknown"

    def _get_user_info(self) -> tuple[str, str]:
        """获取用户信息 (user_id, user_name)。
        """
        return self.user_id or "unknown", self.user_name or f"玩家{self.user_id or '未知'}"

    async def execute(self) -> tuple[bool, str | None, int]:
        """执行命令"""
        matched_groups = self.matched_groups or {}
        action = (matched_groups.get("action") or "").strip()
        rest_input = (matched_groups.get("rest") or "").strip()

        group_id = self._get_group_id()
        user_id, user_name = self._get_user_info()

        # 检查插件是否启用
        enabled = self.get_config("plugin.enabled", True)
        if not enabled:
            await self.send_text(ErrorMessages.PLUGIN_DISABLED)
            return False, "插件未启用", 2

        # 路由到对应处理器（优先走命令路由表，兜底允许 `_handle_{action}` 形式）
        from .router import get_handler_method_name
        handler_name = get_handler_method_name(action) or f"_handle_{action}"
        handler = getattr(self, handler_name, None)
        if handler:
            return await handler(group_id, user_id, user_name, rest_input)

        await self.send_text(ErrorMessages.UNKNOWN_COMMAND)
        return False, "未知命令", 2

    async def _handle_开始(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        """处理开始游戏命令。"""
        raw = (rest_input or "").strip()
        game_mode = raw or GameModes.SINGLE.value
        if raw:
            m = re.match(rf"^({GameModes.SINGLE.value}|{GameModes.MULTI.value})\s*(.*)$", raw)
            if m:
                game_mode = m.group(1)

        if game_mode not in [GameModes.SINGLE.value, GameModes.MULTI.value]:
            await self.send_text("请指定游戏模式：`/rg 开始 单人` 或 `/rg 开始 多人`")
            return False, "缺少游戏模式", 2

        if game_mode == GameModes.SINGLE.value:
            return await self._singleplayer_flow.handle_start(group_id, user_id, user_name)
        return await self._multiplayer_flow.handle_start(group_id, user_id, user_name, rest_input)

    async def _handle_加入(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        """处理多人加入命令。"""
        _ = rest_input
        return await self._multiplayer_flow.handle_join(group_id, user_id, user_name)

    async def _handle_身份(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        """处理查看身份命令（多人模式主动拉取身份信息）。"""
        _ = rest_input
        state_manager = GameStateManager()
        state = await state_manager.get(group_id)

        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            session = state.session
            if session.game_mode != GameModes.MULTI.value:
                await self.send_text("当前不是多人模式游戏，没有身份分配。")
                return False, "非多人模式", 2
        finally:
            state.release()

        return await self._multiplayer_flow.handle_identity(group_id, user_id, user_name)

    async def _handle_强制开始(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        """处理强制开始命令。"""
        raw = (rest_input or "").strip()
        game_mode = raw or GameModes.SINGLE.value
        if raw:
            m = re.match(rf"^({GameModes.SINGLE.value}|{GameModes.MULTI.value})\s*(.*)$", raw)
            if m:
                game_mode = m.group(1)

        if game_mode not in [GameModes.SINGLE.value, GameModes.MULTI.value]:
            await self.send_text("请指定游戏模式：`/rg 强制开始 单人` 或 `/rg 强制开始 多人`")
            return False, "缺少游戏模式", 2

        if game_mode == GameModes.SINGLE.value:
            return await self._singleplayer_flow.handle_force_start(group_id, user_id, user_name)
        return await self._multiplayer_flow.handle_force_start(group_id, user_id, user_name, rest_input)

    _handle_start = _handle_开始
    _handle_join = _handle_加入
    _handle_force_start = _handle_强制开始
    _handle_identity = _handle_身份
