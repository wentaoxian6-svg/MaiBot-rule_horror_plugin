"""
规则怪谈插件

生成规则怪谈并进行互动游戏，支持LLM生成、提示、推理和多种结局判定
"""

import asyncio
import base64
import logging
import os
import re
from datetime import datetime
from collections.abc import Mapping
from typing import cast, TYPE_CHECKING

from src.plugin_system import (  # pyright: ignore[reportImplicitRelativeImport]

    BasePlugin,
    BaseAction,
    BaseCommand,
    BaseEventHandler,
    BaseTool,
    register_plugin,
    ConfigField,
    ConfigSection,
    ActionInfo,
    CommandInfo,
    EventHandlerInfo,
    ToolInfo,
    PythonDependency,
)



if TYPE_CHECKING:
    from src.chat.message_receive.message import MessageRecv  # pyright: ignore[reportImplicitRelativeImport]



from .core import (

    GameStateManager,
    GameState,
    SaveManager,
    LLMClient,
    AsyncImageGenerator,
    TextFormatter,
    Player,
    GameSession,
    GameStatus,
    PlayerStatus,
    load_config_from_file,
    get_default_max_tokens,
)


from .core.services import (
    ImmersiveFeedback,
    ActionProcessor,
    GameGenerator,
    EndingJudge,
)

# 导入通用模块
from .common import (
    GameModes,
    ErrorMessages,
    SuccessMessages,
    DirectoryNames,
    FileNames,
    ConfigDefaults,
    resolve_data_dir,
    ConfigLoadError,
    GameNotFoundError,
    PlayerNotInGameError,
    GameContextDict,
    RuleDict,
    PlayerStatusDict,
    StateUpdatesDict,
    JsonObject,
    JsonValue,
)

# 导入辅助模块
from .helpers import assign_multiplayer_identities

# 导入系统模块（保持兼容性）
from .systems import (
    EnvironmentEvolutionSystem,
    GameTimeManager,
    EnvironmentState,
    DoorState,
    LightState,
    RuleMutationSystem,
    ClueDiscoverySystem,
    MultiplayerPhysicsSystem,
    NPCMemory,
    NPCAttitude,
    NPC,
)


# 配置日志
logger = logging.getLogger(__name__)

# 目录配置
PLUGIN_DIR = os.path.dirname(__file__)


@register_plugin

class RuleHorrorPlugin(BasePlugin):

    """规则怪谈插件"""

    plugin_name: str = "rule_horror"
    _ENABLE_PLUGIN: bool = True
    _DEPENDENCIES: list[str] = []
    _PYTHON_DEPENDENCIES: list[PythonDependency] = [
        PythonDependency(package_name="aiohttp"),
        PythonDependency(package_name="pydantic"),
        PythonDependency(package_name="tenacity"),
        PythonDependency(package_name="pyyaml"),
        PythonDependency(package_name="Pillow"),
    ]
    _CONFIG_FILE_NAME: str = FileNames.CONFIG


    config_section_descriptions: dict[str, str | ConfigSection] = {

        "plugin": "插件启用配置",
        "llm": "LLM API 配置",
        "environment": "环境演变系统配置",
        "save": "存档配置",
    }

    _CONFIG_SCHEMA: dict[str, dict[str, ConfigField] | str] = {


        "plugin": {
            "enabled": ConfigField(
                type=bool,
                default=True,
                description="是否启用规则怪谈插件"
            ),
            "config_version": ConfigField(
                type=str,
                default="2.2.0",
                description="配置文件版本"
            ),
            "auto_save_interval": ConfigField(
                type=int,
                default=ConfigDefaults.AUTO_SAVE_INTERVAL,
                description="自动保存间隔(秒)"
            ),
        },

        "llm": {
            "api_url": ConfigField(
                type=str,
                default="https://rinkoai.com/v1/chat/completions",
                description="LLM API 地址"
            ),
            "api_key": ConfigField(
                type=str,
                default="",
                description="LLM API 密钥"
            ),
            "model_list": ConfigField(
                type=list,
                default=["gemini-2.5-flash"],
                description="LLM模型列表"
            ),
            "temperature": ConfigField(
                type=float,
                default=ConfigDefaults.TEMPERATURE,
                description="生成随机性(0.0-1.0)"
            ),
            "max_concurrent": ConfigField(
                type=int,
                default=ConfigDefaults.MAX_CONCURRENT_REQUESTS,
                description="最大并发请求数"
            ),
        },
        "save": {
            "batch_save_interval": ConfigField(
                type=int,
                default=ConfigDefaults.BATCH_SAVE_INTERVAL,
                description="批量保存间隔(秒)"
            ),
        },
    }

    @property
    def enable_plugin(self) -> bool:
        return self._ENABLE_PLUGIN

    @enable_plugin.setter
    def enable_plugin(self, value: bool) -> None:
        self._ENABLE_PLUGIN = value

    @property
    def dependencies(self) -> list[str]:
        return self._DEPENDENCIES

    @property
    def python_dependencies(self) -> list[PythonDependency]:
        return self._PYTHON_DEPENDENCIES

    @property
    def config_file_name(self) -> str:
        return self._CONFIG_FILE_NAME

    @property
    def config_schema(self) -> dict[str, dict[str, ConfigField] | str]:
        return self._CONFIG_SCHEMA

    def __init__(

        self,
        plugin_dir: str | None = None,
        plugin_config: Mapping[str, object] | None = None,
        **kwargs: object,
    ) -> None:

        # 确保plugin_dir被传递给父类
        if plugin_dir is None:
            plugin_dir = PLUGIN_DIR
        super().__init__(plugin_dir=plugin_dir, **kwargs)
        self.plugin_dir = plugin_dir
        self.plugin_config = plugin_config
        self.state_manager: GameStateManager | None = None
        self.save_manager: SaveManager | None = None
        self.llm_client: LLMClient | None = None
        self.image_generator: AsyncImageGenerator | None = None
        self._temp_images_dir: str = ""

    async def on_load(self) -> None:
        """插件加载时初始化"""
        plugin_dir = self.plugin_dir

        # 解析数据目录（Linux 下可能出现插件目录只读的情况）
        data_dir = resolve_data_dir(plugin_dir, DirectoryNames.DATA)
        temp_images_dir = os.path.join(data_dir, DirectoryNames.TEMP_IMAGES)
        self._temp_images_dir = temp_images_dir

        # 确保目录存在
        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(temp_images_dir, exist_ok=True)

        # 加载配置文件
        config_path = os.path.join(plugin_dir, FileNames.CONFIG)
        try:
            config = load_config_from_file(config_path)
            logger.info(SuccessMessages.CONFIG_LOADED)
            logger.info(f"LLM API URL: {config.llm.api_url}")
            logger.info(f"LLM 模型列表: {config.llm.model_list}")
            if config.llm.api_key:
                logger.info(f"LLM API Key: {config.llm.api_key[:20]}...")
        except Exception as e:
            logger.error(f"{ErrorMessages.CONFIG_LOAD_FAILED}: {e}")
            raise ConfigLoadError(f"{ErrorMessages.CONFIG_LOAD_FAILED}: {e}") from e
        
        # 初始化核心组件
        self.state_manager = GameStateManager()
        self.save_manager = SaveManager(os.path.join(data_dir, DirectoryNames.SAVES))
        self.llm_client = LLMClient()
        self.image_generator = AsyncImageGenerator(self._temp_images_dir)

        # 启动管理器
        await self.state_manager.start()
        await self.save_manager.start()

        logger.info(SuccessMessages.PLUGIN_LOADED)

    async def on_unload(self) -> None:
        """插件卸载时清理"""
        if self.save_manager:
            await self.save_manager.stop()
        if self.state_manager:
            await self.state_manager.stop()
        if self.llm_client:
            await self.llm_client.close()
        if self.image_generator:
            await self.image_generator.close()

        logger.info(SuccessMessages.PLUGIN_UNLOADED)

    def get_plugin_components(
        self,
    ) -> list[
        tuple[ActionInfo, type[BaseAction]]
        | tuple[CommandInfo, type[BaseCommand]]
        | tuple[EventHandlerInfo, type[BaseEventHandler]]
        | tuple[ToolInfo, type[BaseTool]]
    ]:

        """注册命令组件"""
        return [
            (RuleHorrorCommand.get_command_info(), RuleHorrorCommand),
        ]



class RuleHorrorCommand(BaseCommand):
    """规则怪谈命令处理器"""

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
        "/rg 规则 - 查看当前规则\n"
        "/rg 场景 - 查看场景结构\n"
        "/rg 道具 [道具名称] - 查看道具列表或详情\n"
        "/rg 提示 <规则/线索> - 获取提示\n"
        "/rg 推理 <推理内容> - 记录推理\n"
        "/rg 行动 <行动描述> - 描述行动\n"
        "/rg 继续 - 通关后继续探索\n"
        "/rg 结束 - 结束游戏\n"
        "/rg 帮助 - 查看帮助"
    )


    def __init__(self, message: "MessageRecv", plugin_config: Mapping[str, object] | None = None) -> None:

        plugin_config_dict = dict(plugin_config) if plugin_config else None
        super().__init__(message, plugin_config_dict)

        self._formatter = TextFormatter()
        self._feedback_system = ImmersiveFeedback()
        self._action_processor = ActionProcessor()
        self._game_generator: GameGenerator | None = None
        self._ending_judge = EndingJudge()
        self._environment_system: EnvironmentEvolutionSystem | None = None
        self._game_time_manager: GameTimeManager | None = None
        self._rule_mutation_system: RuleMutationSystem | None = None
        self._clue_discovery_system: ClueDiscoverySystem | None = None
        self._multiplayer_physics_system: MultiplayerPhysicsSystem | None = None
        
        # 获取临时图片目录
        if plugin_config_dict and "plugin_dir" in plugin_config_dict:
            plugin_dir_raw = plugin_config_dict.get("plugin_dir")
            plugin_dir = plugin_dir_raw if isinstance(plugin_dir_raw, str) else PLUGIN_DIR
            data_dir = resolve_data_dir(plugin_dir, DirectoryNames.DATA)
            self._temp_images_dir = os.path.join(data_dir, DirectoryNames.TEMP_IMAGES)

        else:
            # 回退：使用当前目录
            self._temp_images_dir = os.path.join(PLUGIN_DIR, DirectoryNames.DATA, DirectoryNames.TEMP_IMAGES)
        
        # 确保目录存在
        os.makedirs(self._temp_images_dir, exist_ok=True)
        
    def _get_game_generator(self) -> GameGenerator:
        """获取或创建 GameGenerator（延迟初始化）"""
        if self._game_generator is None:
            self._game_generator = GameGenerator()
        return self._game_generator

    def _get_or_create_environment_system(self, game_states: dict[str, JsonObject]) -> EnvironmentEvolutionSystem:
        """获取或创建环境演化系统（延迟初始化）"""
        if self._environment_system is None:
            self._environment_system = EnvironmentEvolutionSystem(game_states)
        return self._environment_system

    def _get_or_create_game_time_manager(self) -> GameTimeManager:
        """获取或创建游戏时间管理器（延迟初始化）"""
        if self._game_time_manager is None:
            self._game_time_manager = GameTimeManager()
        return self._game_time_manager

    def _get_or_create_rule_mutation_system(self) -> RuleMutationSystem:
        """获取或创建规则变异系统（延迟初始化）"""
        if self._rule_mutation_system is None:
            self._rule_mutation_system = RuleMutationSystem()
            # 注册默认变异条件
            from .systems.rule_mutation_system import create_default_mutation_conditions
            default_conditions = create_default_mutation_conditions()
            for condition in default_conditions:
                self._rule_mutation_system.add_condition(condition)
            logger.info(f"已注册 {len(default_conditions)} 个默认规则变异条件")
        return self._rule_mutation_system

    def _get_or_create_clue_discovery_system(self) -> ClueDiscoverySystem:
        """获取或创建线索发现系统（延迟初始化）"""
        if self._clue_discovery_system is None:
            self._clue_discovery_system = ClueDiscoverySystem()
        return self._clue_discovery_system

    def _get_or_create_multiplayer_physics_system(self) -> MultiplayerPhysicsSystem:
        """获取或创建多人物理系统（延迟初始化）"""
        if self._multiplayer_physics_system is None:
            self._multiplayer_physics_system = MultiplayerPhysicsSystem()
        return self._multiplayer_physics_system


    async def _send_private_text(self, target_user_id: str, target_user_name: str, content: str) -> bool:
        """向指定用户发起私聊并发送文本。"""
        try:
            from maim_message import UserInfo
            from src.chat.message_receive.chat_stream import get_chat_manager  # pyright: ignore[reportImplicitRelativeImport]
            from src.plugin_system.apis import send_api  # pyright: ignore[reportImplicitRelativeImport]


            chat_stream = getattr(self.message, "chat_stream", None)
            platform = str(getattr(chat_stream, "platform", "qq") or "qq")

            uid = str(target_user_id or "").strip()
            if not uid:
                return False

            nickname = str(target_user_name or "").strip()
            user_info = UserInfo(user_id=uid, user_nickname=nickname, platform=platform)

            cm = get_chat_manager()
            private_stream = await cm.get_or_create_stream(platform=platform, user_info=user_info, group_info=None)
            return await send_api.text_to_stream(text=content, stream_id=private_stream.stream_id)
        except Exception as e:
            logger.error(f"发送私聊消息失败: {e}")
            return False


    def _build_player_private_brief(self, session: GameSession, player: Player) -> str:
        """构造多人模式私聊身份与规则文本。"""
        lines: list[str] = []
        scene = str(getattr(session, "scene_name", "") or "").strip()
        if scene:
            lines.append(f"场景：{scene}")

        if player.identity:
            lines.append(f"你的身份：{player.identity}")
        if player.identity_description:
            lines.append(f"身份简介：{player.identity_description}")

        # 个人规则（只展示文本，不展示真假与隐藏含义）
        ur_texts: list[str] = []
        for r in (player.unique_rules or []):
            if isinstance(r, dict):
                t = str(r.get("text", "") or "").strip()
            else:
                t = str(r or "").strip()
            if t:
                ur_texts.append(t)
        if ur_texts:
            lines.append("")
            lines.append("个人规则：")
            for i, t in enumerate(ur_texts, start=1):
                lines.append(f"{i}. {t}")

        # 共同规则
        mi = session.rule_network.get("multi_identity", {}) if isinstance(getattr(session, "rule_network", None), dict) else {}
        common = mi.get("common_rules", []) if isinstance(mi, dict) else []
        common_texts: list[str] = []
        if isinstance(common, list):
            for r in common:
                if isinstance(r, dict):
                    t = str(r.get("text", "") or "").strip()
                else:
                    t = str(r or "").strip()
                if t:
                    common_texts.append(t)
        if common_texts:
            lines.append("")
            lines.append("共同规则：")
            for i, t in enumerate(common_texts, start=1):
                lines.append(f"{i}. {t}")

        if player.exclusive_info:
            lines.append("")
            lines.append("独有信息：")
            lines.append(str(player.exclusive_info))

        return "\n".join(lines).strip() or "身份信息生成失败。"


    async def _send_multiplayer_private_infos(self, session: GameSession, lobby_players: list[tuple[str, str]], group_id: str | None = None) -> None:
        """多人模式：把身份与个人规则通过私聊发送给每位玩家。
        
        Args:
            session: 游戏会话
            lobby_players: 大厅玩家列表
            group_id: 群组ID（私聊失败时用于发送到群聊兜底）
        """
        failed_players: list[tuple[str, str, str]] = []  # (pid, name, content)
        
        try:
            # 使用 lobby_players 的名字更可信（来自当前群聊上下文）
            name_by_id: dict[str, str] = {str(pid): str(name) for pid, name in (lobby_players or []) if str(pid)}

            for pid, p in (session.players or {}).items():
                target_name = name_by_id.get(str(pid), p.name)
                content = self._build_player_private_brief(session, p)
                ok = await self._send_private_text(str(pid), str(target_name or ""), content)
                if not ok:
                    logger.warning(f"向玩家 {pid} 私聊发送身份信息失败，将尝试群聊兜底")
                    failed_players.append((str(pid), str(target_name or ""), content))
                await asyncio.sleep(0.2)
            
            # 私聊失败的玩家，通过群聊发送（@玩家并告知身份）
            if failed_players and group_id:
                await self._send_identity_to_group(failed_players, group_id)
                
        except Exception as e:
            logger.error(f"多人模式私聊下发失败: {e}")
    
    async def _send_identity_to_group(self, players: list[tuple[str, str, str]], group_id: str) -> None:
        """将身份信息发送到群聊（私聊失败时的兜底方案）
        
        Args:
            players: (player_id, player_name, identity_content) 列表
            group_id: 群组ID
        """
        try:
            for pid, name, content in players:
                # 构建群聊消息，@玩家并附上身份信息
                message = f"【@{name} 的身份信息】（私聊发送失败，请在群聊中查看）\n\n{content}\n\n---\n请妥善保管你的身份信息，不要向其他玩家透露！"
                await self.send_text(message)
                await asyncio.sleep(0.3)
        except Exception as e:
            logger.error(f"群聊兜底发送身份信息失败: {e}")


    def _get_group_id(self) -> str:

        """获取群组/用户ID"""
        chat_stream = getattr(self, 'chat_stream', None)
        if chat_stream is None:
            message_obj = getattr(self, 'message', None)
            if message_obj:
                chat_stream = getattr(message_obj, 'chat_stream', None)

        if chat_stream:
            group_info = getattr(chat_stream, 'group_info', None)
            if group_info:
                return str(group_info.group_id)
            user_info = getattr(chat_stream, 'user_info', None)
            if user_info:
                return str(user_info.user_id)

        return "unknown"

    def _get_user_info(self) -> tuple[str, str]:
        """获取用户信息 (user_id, user_name)。

        兼容说明：
        - MaiBot 的 `UserInfo` 标准字段是 `user_nickname`（见 typings）。
        - 部分适配器可能仍提供 `user_name`，这里做兼容回退。
        """
        def _pick_name(user_info: object, user_id: str) -> str:
            nickname = getattr(user_info, 'user_nickname', '')
            if isinstance(nickname, str) and nickname.strip():
                return nickname.strip()

            legacy = getattr(user_info, 'user_name', '')
            if isinstance(legacy, str) and legacy.strip():
                return legacy.strip()

            return f"玩家{user_id}"

        # 首先尝试从 chat_stream 获取
        chat_stream = getattr(self, 'chat_stream', None)
        if chat_stream is None:
            message_obj = getattr(self, 'message', None)
            if message_obj:
                chat_stream = getattr(message_obj, 'chat_stream', None)

        if chat_stream:
            user_info = getattr(chat_stream, 'user_info', None)
            if user_info:
                user_id = str(getattr(user_info, 'user_id', 'unknown'))
                return user_id, _pick_name(user_info, user_id)

        # 回退到从 message 获取
        message_obj = getattr(self, 'message', None)
        if message_obj:
            user_info = getattr(message_obj, 'user_info', None)
            if user_info:
                user_id = str(getattr(user_info, 'user_id', 'unknown'))
                return user_id, _pick_name(user_info, user_id)

        return "unknown", "未知玩家"


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
        from .commands.router import get_handler_method_name
        handler_name = get_handler_method_name(action) or f"_handle_{action}"
        handler = getattr(self, handler_name, None)
        if handler:
            return await handler(group_id, user_id, user_name, rest_input)

        await self.send_text(ErrorMessages.UNKNOWN_COMMAND)
        return False, "未知命令", 2

    async def _get_game_session(self, group_id: str) -> GameSession:
        """获取游戏会话，如果不存在则抛出 GameNotFoundError"""
        state_manager = GameStateManager()
        state = await state_manager.get(group_id)
        if not state or not state.session:
            raise GameNotFoundError("当前没有正在进行的游戏")
        return state.session

    def _get_player_or_raise(self, session: GameSession, user_id: str) -> Player:
        """获取玩家，如果不在游戏中则抛出 PlayerNotInGameError"""
        player = session.players.get(user_id)
        if not player:
            raise PlayerNotInGameError("你不在当前游戏中")
        return player

    def _fatigue_level_from_health(self, health: int) -> str:
        """根据体力值推导疲劳等级（用于展示）。"""
        if health <= 0:
            return "极度"
        if health <= 25:
            return "严重"
        if health <= 50:
            return "中度"
        if health <= 75:
            return "轻微"
        return "无"

    def _normalize_rules_list(self, rules: list[RuleDict | Mapping[str, JsonValue] | str]) -> list[RuleDict]:

        """归一化规则列表，确保每条规则都有 original_index。
        
        Args:
            rules: 原始规则列表
            
        Returns:
            归一化后的规则列表
        """
        normalized: list[RuleDict] = []
        for i, r in enumerate(rules):
            if isinstance(r, dict):
                text = str(r.get("text", r.get("content", str(r))) or "").strip()

                oi_raw = r.get("original_index", i)
                if isinstance(oi_raw, int):
                    original_index: int | None = oi_raw
                elif oi_raw is None:
                    original_index = None
                elif isinstance(oi_raw, float) and oi_raw.is_integer():
                    original_index = int(oi_raw)
                else:
                    original_index = i

                rule_dict: RuleDict = {
                    "text": text,
                    "original_index": original_index,
                }
                if "source" in r:
                    rule_dict["source"] = str(r["source"])
                normalized.append(rule_dict)
            else:
                normalized.append({
                    "text": str(r or "").strip(),
                    "original_index": i,
                })
        return normalized


    def _normalize_rule_text_for_dedup(self, text: str) -> str:
        """归一化规则文本用于去重（移除空白和标点）。
        
        Args:
            text: 原始文本
            
        Returns:
            归一化后的文本
        """
        text = re.sub(r"\s+", "", str(text or ""))
        text = re.sub(r"[，,。.!！？?；;:\"'《》【】\[\]（）()\-—…·]", "", text)

        return text

    def _deduplicate_rules(self, rules: list[RuleDict]) -> list[RuleDict]:
        """去重规则列表，保留带 original_index 的版本。
        
        Args:
            rules: 规则列表
            
        Returns:
            去重后的规则列表
        """
        dedup: list[RuleDict] = []
        seen: dict[str, int] = {}
        
        for r in rules:
            rr: RuleDict
            if isinstance(r, dict):
                rr = r
            else:
                rr = {"text": str(r), "original_index": None}

            txt = str(rr.get("text", rr.get("content", str(rr))) or "").strip()
            if not txt:
                continue

            key = self._normalize_rule_text_for_dedup(txt)
            if not key:
                continue

            if key in seen:
                # 如果已存在，优先保留带 original_index 的版本
                pi = seen[key]
                prev = dedup[pi]
                if not isinstance(prev.get("original_index"), int) and isinstance(rr.get("original_index"), int):
                    dedup[pi] = rr
                continue

            seen[key] = len(dedup)
            dedup.append(rr)

        
        return dedup

    async def _update_environment_async(
        self,
        env_system: EnvironmentEvolutionSystem,
        group_id: str,
        player_actions: list[str],
        player_locations: list[str],
        api_url: str,
        api_key: str,
        model_list: list[str],
        current_model_index: int,
        temperature: float,
    ) -> None:
        """异步更新环境演化系统（非阻塞调用）
        
        这个方法用于在玩家行动后更新环境状态，包括：
        - NPC行为和位置变化
        - 环境氛围变化
        - 时间流逝效果
        - 可能出现的异常现象
        
        注意：这是一个异步方法，使用 asyncio.create_task() 调用，不会阻塞主流程。
        即使更新失败也不会影响游戏主流程。
        """
        try:
            logger.debug(f"开始更新环境演化系统: {group_id}")
            
            # 调用环境演化系统的更新方法
            await env_system.update_environment(
                group_id=group_id,
                player_actions=player_actions,
                player_locations=player_locations,
                api_url=api_url,
                api_key=api_key,
                model_list=model_list,
                current_model_index=current_model_index,
                temperature=temperature,
            )
            
            logger.debug(f"环境演化系统更新完成: {group_id}")
            
        except Exception as e:
            # 记录错误但不影响主流程
            logger.warning(f"环境演化系统更新失败（非阻塞）: {e}")

    async def _send_image_path(self, image_path: str) -> None:
        """读取图片文件并发送（base64）。"""
        try:
            with open(image_path, "rb") as f:
                image_bytes = f.read()
            image_base64 = base64.b64encode(image_bytes).decode("ascii")
            await self.send_image(image_base64)
        except FileNotFoundError:
            logger.error(f"图片文件不存在: {image_path}")
            await self.send_text("图片生成失败，请稍后重试。")
        except Exception as e:
            logger.error(f"发送图片失败: {e}")
            await self.send_text("发送图片时出错，请稍后重试。")





    def _build_game_context(self, session: GameSession, player: Player) -> GameContextDict:

        """构建游戏上下文

        注意：这里的 rules 应当是“玩家已知规则”，避免自然语言模块/提示系统拿到全规则导致剧透。
        """
        env_state = session.environment_state if isinstance(getattr(session, "environment_state", None), dict) else {}


        # 归一化规则表
        all_rules: list[RuleDict] = []
        for i, r in enumerate(session.rules or []):
            if isinstance(r, dict):
                rr: RuleDict = {"text": str(r.get("text", "")), "original_index": i}
                all_rules.append(rr)
            else:
                all_rules.append({"text": str(r), "original_index": i})

        known_indices: list[int] = []
        if isinstance(env_state.get("known_rule_indices"), list):
            known_indices = [int(x) for x in env_state.get("known_rule_indices", []) if isinstance(x, int)]

        known_rules: list[RuleDict] = []
        if known_indices:
            for idx in sorted(set(known_indices)):
                if 0 <= idx < len(all_rules):
                    known_rules.append(all_rules[idx])

        # 合并口述补全规则（不占用 original_index）
        extra = env_state.get("known_rule_texts_extra", []) if isinstance(env_state, dict) else []
        if isinstance(extra, list):
            for t in extra:
                tt = str(t).strip()
                if tt:
                    known_rules.append({"text": tt, "original_index": None, "source": "npc_dialogue"})

        player_status: PlayerStatusDict = {
            "sanity": player.sanity,
            "health": player.health,
            "location": player.location,
        }

        # 构建全局世界状态信息（用于多人模式共享信息）
        world_flags_info = ""
        if session.world_flags:
            world_flags_parts = ["【全局世界状态】"]
            for key, value in session.world_flags.items():
                world_flags_parts.append(f"- {key}: {value}")
            world_flags_info = "\n".join(world_flags_parts)

        # 构建其他玩家状态信息（用于多人模式）
        other_players_info = ""
        if session.game_mode == "多人" and len(session.players) > 1:
            other_players_parts = ["【其他玩家状态】"]
            for pid, p in session.players.items():
                if pid != player.player_id and p.status == PlayerStatus.ALIVE:
                    other_players_parts.append(
                        f"- {p.name}: 理智{p.sanity}/生命{p.health}/位置:{p.location}"
                    )
            if len(other_players_parts) > 1:
                other_players_info = "\n".join(other_players_parts)

        result: GameContextDict = {
            "scene_name": session.scene_name,
            "background": session.background,
            "rules": known_rules,
            "player_status": player_status,
            "recent_actions": [a.get("action", "") for a in player.action_history[-5:]],
        }

        # 将全局状态注入到背景故事中
        if world_flags_info or other_players_info:
            extra_info = []
            if world_flags_info:
                extra_info.append(world_flags_info)
            if other_players_info:
                extra_info.append(other_players_info)
            result["background"] = result["background"] + "\n\n" + "\n\n".join(extra_info)

        return result


    def _build_game_state_dict(self, state: GameState, player: Player) -> JsonObject:

        """构建游戏状态字典"""
        return {
            "scene_name": state.session.scene_name if state.session else "",
            "background": state.session.background if state.session else "",
            "player_status": {
                "sanity": player.sanity,
                "health": player.health,
                "location": player.location,
            },
        }

    def _apply_state_updates(self, player: Player, updates: StateUpdatesDict) -> None:

        """应用状态更新"""
        if "sanity" in updates:
            player.sanity = max(0, min(100, player.sanity + updates["sanity"]))
        if "health" in updates:
            player.health = max(0, min(100, player.health + updates["health"]))
        if "location" in updates:
            player.location = updates["location"]

    async def _schedule_delayed_event(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        delay_seconds: int,
        event_description: str | None,
    ) -> None:
        """调度延迟事件"""
        await asyncio.sleep(delay_seconds)

        state_manager = GameStateManager()
        state = await state_manager.get(group_id)

        if not state or not state.session:
            return

        # 验证玩家是否仍在游戏中且存活
        player = state.session.players.get(user_id)
        if not player:
            logger.debug(f"延迟事件：玩家 {user_name}({user_id}) 已不在游戏中")
            return
        if player.status != PlayerStatus.ALIVE:
            logger.debug(f"延迟事件：玩家 {user_name}({user_id}) 已死亡，跳过延迟事件")
            return

        # 生成延迟反馈
        game_state = self._build_game_state_dict(state, player)
        feedback = await self._feedback_system.generate_delayed_feedback(
            original_action={"description": event_description or "之前的行动"},
            game_state=game_state,
        )

        # 应用状态更新
        if feedback.should_update_state:
            self._apply_state_updates(player, feedback.state_updates)

        # 发送个性化反馈
        personalized_content = f"**{user_name}** {feedback.content}"
        await self.send_text(personalized_content)

    async def _extract_rules_from_dialogue(
        self,
        npc_dialogue: str,
        all_rules: list[RuleDict],
        npc_name: str,
    ) -> list[RuleDict]:
        """从NPC对话中提取提到的规则。

        新策略：
        - 将所有后台规则和NPC入场对话传给LLM
        - LLM判断哪些规则在对话中被明确提到或暗示
        - 只返回被提到的规则，隐藏未被提到的规则
        - 使用NPC对话中的措辞重新表述规则
        """
        if not npc_dialogue or not all_rules:
            return []

        extracted_rules: list[RuleDict] = []

        try:
            llm_client = LLMClient()

            # 构建规则列表文本
            rules_text = "\n".join(
                [
                    f"{i}. {rule.get('text', rule.get('content', str(rule)))}"
                    for i, rule in enumerate(all_rules)
                ]
            )

            system_prompt = f"""你是规则怪谈游戏的规则提取专家。你的任务是分析NPC的入场对话，提取NPC明确提到的所有规则。

什么是规则：
- 必须包含明确的指令、警告、禁止事项或必须遵守的行为
- 例如："千万别..."、"一定要..."、"如果...就..."、"绝对不要..."
- 单纯的陈述、描述、背景介绍不是规则

任务说明：
1. 仔细阅读NPC的入场对话
2. 对比所有后台规则列表，找出被明确提到或暗示的规则
3. **只提取真正的规则**，不要提取陈述性内容
4. **注意**：NPC用"比如"、"例如"等词举例说明时，这只是对一条规则的具体说明，不是多条规则
5. **没有被提到的后台规则不要返回**

判断标准：
- 后台规则：NPC直接说出或暗示了后台规则表中的某条规则（即使措辞不同但意思相同）
- 口述规则：NPC明确提到的其他规则性内容（包含指令、警告、禁止事项）

输出格式要求：
- 只返回JSON格式
- mentioned_rule_indices: 后台规则表中被明确提到的规则索引列表
- rules_text: 对应规则的文本（用NPC对话中的措辞重新表述，保持完整，不要拆分举例部分）
- extra_rules: NPC口述的其他规则列表（真正的规则，不是陈述）

示例：
NPC说："如果深水区漂着不属于馆里的东西，比如红色的小鞋子，千万别下水去捞"
→ 这是一条规则（"比如红色的小鞋子"是举例，不是另一条规则）

NPC说："这馆里的水，一到晚上就变得沉"
→ 这不是规则，只是陈述背景

示例输出：
{{"mentioned_rule_indices": [0], "rules_text": ["如果深水区漂着不属于馆里的东西，比如红色的小鞋子，千万别下水去捞"], "extra_rules": ["要是听见水底下有人喊你名字，哪怕那是你妈的声音，也绝对不要低头看水面"]}}"""

            user_prompt = f"""NPC名称：{npc_name}

NPC入场对话：
{npc_dialogue}

所有后台规则（共{len(all_rules)}条）：
{rules_text}

请分析：
1. 哪些后台规则在NPC对话中被明确提到或暗示？（只返回真正被提到的）
2. NPC还明确提到了哪些其他规则？（只提取包含指令/警告/禁止事项的，不要提取陈述性内容）
3. 注意区分举例说明和多条规则

返回JSON格式。"""

            response = await llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.2,  # 低温度确保准确性
            )

            data = cast(JsonObject, response.parse_json())

            # 1. 处理后台规则表中被提到的规则
            mentioned_indices = data.get("mentioned_rule_indices", [])
            rules_text_list = data.get("rules_text", [])

            if mentioned_indices and isinstance(mentioned_indices, list):
                for idx, rule_text in zip(mentioned_indices, rules_text_list):
                    if isinstance(idx, int) and 0 <= idx < len(all_rules):
                        # 使用NPC的措辞或原始规则文本
                        display_text = str(rule_text or "").strip() if rule_text else all_rules[idx].get('text', str(all_rules[idx]))
                        base = dict(all_rules[idx]) if isinstance(all_rules[idx], dict) else {"text": str(all_rules[idx])}
                        base["text"] = display_text
                        base["original_index"] = idx
                        extracted_rules.append(base)
                        logger.debug(f"提取规则[{idx}]: {display_text[:50]}...")

            # 2. 处理NPC口述的其他规则（不在后台规则表中）
            extra_rules = data.get("extra_rules", [])
            if extra_rules and isinstance(extra_rules, list):
                for extra_rule in extra_rules:
                    if extra_rule and isinstance(extra_rule, str):
                        extracted_rules.append({
                            "text": extra_rule.strip(),
                            "original_index": None,
                            "source": "npc_dialogue"
                        })
                        logger.debug(f"提取口述规则: {extra_rule[:50]}...")

            logger.info(f"LLM从NPC对话中提取到 {len(extracted_rules)} 条规则（含{len(extra_rules) if extra_rules else 0}条口述规则）")

        except Exception as e:
            logger.warning(f"LLM提取规则失败: {e}，将返回所有规则")
            # 兜底：返回所有规则
            for i, r in enumerate(all_rules):
                if isinstance(r, dict):
                    rr = dict(r)
                    rr.setdefault("original_index", i)
                    extracted_rules.append(rr)
                else:
                    extracted_rules.append({"text": str(r), "original_index": i})

        return extracted_rules


    # ============== 命令处理器==============
    
    async def _generate_entrance_description(
        self,
        session: GameSession
    ) -> str:
        """生成入场描述
        
        Args:
            session: 游戏会话
        
        Returns:
            入场描述文本
        """
        llm_client = LLMClient()

        plural_hint = ""
        default_entrance = f"你来到了{session.scene_name}。这里的气氛让你感到不安。"
        if getattr(session, "game_mode", GameModes.SINGLE.value) == GameModes.MULTI.value:
            plural_hint = "\n8. 必须使用第二人称复数'你们'，禁止出现'你'、'你的'等单数表述\n9. 描述一行人一起进入场景，而不是单独一人\n"
            default_entrance = f"你们来到了{session.scene_name}。这里的气氛让你们感到不安。"

        system_prompt = f"""你是规则怪谈游戏的入场描述生成器。你需要生成玩家进入场景时的描述。

入场描述要求：
1. 描述玩家如何到达这个场景
2. 描述玩家进入场景时的第一印象
3. 描述玩家进入时的感受
4. 描述环境的初始状态
5. 使用感官细节（视觉、听觉、嗅觉、触觉）
6. 营造紧张和不安的氛围
7. 长度：150-200字{plural_hint}

返回纯文本，不要JSON格式。"""


        user_prompt = f"""场景名称：{session.scene_name}

背景：{session.background}

玩家身份：{session.player_identity}

请生成入场描述。"""

        try:
            response = await llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.8,
            )

            return response.clean_content
        except Exception as e:
            logger.error(f"生成入场描述失败: {e}")
            # 返回默认描述
            return default_entrance

    async def _handle_开始(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        """处理开始游戏命令"""
        raw = (rest_input or "").strip()

        game_mode = GameModes.SINGLE.value
        multi_start = False

        if raw:
            modes_pattern = rf"^({GameModes.SINGLE.value}|{GameModes.MULTI.value})\s*(.*)$"
            m = re.match(modes_pattern, raw)
            if m:
                game_mode = m.group(1)
                tail = (m.group(2) or "").strip()
            else:
                game_mode = raw
                tail = ""

            if game_mode == GameModes.MULTI.value and tail:
                if re.search(r"(开始|生成|确认|立即|立刻|start|go)", tail, flags=re.IGNORECASE):
                    multi_start = True

        if game_mode not in [GameModes.SINGLE.value, GameModes.MULTI.value]:
            await self.send_text("请指定游戏模式：`/rg 开始 单人` 或 `/rg 开始 多人`")
            return False, "缺少游戏模式", 2



        save_manager = SaveManager()
        existing = await save_manager.load(group_id)
        if existing and existing.status == GameStatus.ACTIVE:
            await self.send_text(
                "**发现存档**\n\n"
                "该群组/用户已有未完成的游戏存档。\n"
                "请使用 `/rg 恢复` 恢复存档，或使用 `/rg 强制开始` 覆盖存档。"
            )
            return False, "存在存档", 2

        lobby_players: list[tuple[str, str]] = []
        lobby_order: list[str] = []

        # 多人模式：先创建/进入大厅（WAITING），等人数确认后再生成
        if game_mode == GameModes.MULTI.value:
            state_manager = GameStateManager()
            state = await state_manager.get_or_create(group_id)
            try:
                sess = state.session
                lobby: GameSession | None = None
                if (
                    sess
                    and sess.game_mode == GameModes.MULTI.value
                    and sess.status == GameStatus.WAITING
                    and isinstance(getattr(sess, "environment_state", None), dict)
                    and isinstance(sess.environment_state.get("lobby"), dict)
                ):
                    lobby = sess

                # 兼容：若磁盘上已有等待中的多人大厅存档但内存未加载，则先恢复到内存
                if (
                    lobby is None
                    and existing
                    and existing.game_mode == GameModes.MULTI.value
                    and existing.status == GameStatus.WAITING
                    and isinstance(getattr(existing, "environment_state", None), dict)
                    and isinstance(existing.environment_state.get("lobby"), dict)
                ):
                    lobby = existing
                    state.session = lobby

                if lobby is None:
                    lobby = GameSession(group_id=group_id, game_mode="多人", status=GameStatus.WAITING)

                    lobby.environment_state = {
                        "lobby": {
                            "host_id": user_id,
                            "host_name": user_name,
                            "created_at": datetime.now().isoformat(),
                        },
                        "lobby_player_order": [user_id],
                    }
                    lobby.add_player(Player(player_id=user_id, name=user_name))
                    state.session = lobby
                    await save_manager.save_immediately(group_id, lobby)

                    await self.send_text(
                        "**多人模式大厅已创建**\n\n"
                        f"房主：{user_name}\n"
                        f"当前人数：1/5\n\n"
                        "其他玩家请发送 `/rg 加入` 加入。\n"
                        "房主在人数到齐后发送 `/rg 开始 多人 开始` 生成开局。"
                    )
                    return True, "大厅已创建", 2

                # 任何人发送 /rg 开始 多人（不带"开始"），都可以创建新大厅覆盖旧的
                if not multi_start:
                    # 创建新大厅，覆盖旧的
                    lobby = GameSession(group_id=group_id, game_mode="多人", status=GameStatus.WAITING)
                    lobby.environment_state = {
                        "lobby": {
                            "host_id": user_id,
                            "host_name": user_name,
                            "created_at": datetime.now().isoformat(),
                        },
                        "lobby_player_order": [user_id],
                    }
                    lobby.add_player(Player(player_id=user_id, name=user_name))
                    state.session = lobby
                    await save_manager.save_immediately(group_id, lobby)
                    await self.send_text(
                        "**多人模式大厅已创建**\n\n"
                        f"房主：{user_name}\n"
                        f"当前人数：1/5\n\n"
                        "其他玩家请发送 `/rg 加入` 加入。\n"
                        "房主在人数到齐后发送 `/rg 开始 多人 开始` 生成开局。"
                    )
                    return True, "大厅已创建", 2

                # 发送 /rg 开始 多人 开始，继续现有大厅
                env_state = lobby.environment_state
                lobby_meta = env_state.get("lobby", {}) if isinstance(env_state.get("lobby"), dict) else {}
                host_id = str(lobby_meta.get("host_id") or "")
                host_name = str(lobby_meta.get("host_name") or "房主")

                if user_id != host_id:
                    await self.send_text(
                        f"当前已有多人大厅，由 {host_name} 创建。\n"
                        "请使用 `/rg 加入` 加入，等待房主开始生成。"
                    )
                    return False, "非房主", 2

                # multi_start 为 True，继续原有逻辑准备开始游戏
                order = env_state.get("lobby_player_order", [])
                if not isinstance(order, list):
                    order = []
                for pid in list(lobby.players.keys()):
                    if pid not in order:
                        order.append(pid)
                env_state["lobby_player_order"] = order

                cur = len(lobby.players)
                players_disp = "、".join([p.name for p in lobby.players.values()]) if lobby.players else "（无）"

                if cur < 2:
                    await self.send_text("多人模式至少需要 2 名玩家。请先让其他玩家使用 `/rg 加入`。")
                    return False, "人数不足", 2

                # 快照：用于生成（避免长时间持锁）
                lobby_order = list(order)
                lobby_players = [(pid, lobby.players[pid].name) for pid in lobby_order if pid in lobby.players]
                known_pids = {pid for pid, _ in lobby_players}
                for pid, p in lobby.players.items():
                    if pid not in known_pids:
                        lobby_players.append((pid, p.name))
                        lobby_order.append(pid)

            finally:
                state.release()

        await self.send_text("正在生成规则怪谈，请稍候..")

        try:
            # 生成游戏
            player_count = len(lobby_players) if (game_mode == GameModes.MULTI.value) else None
            player_names = [n for _, n in lobby_players] if (game_mode == GameModes.MULTI.value) else None
            player_ids = [pid for pid, _ in lobby_players] if (game_mode == GameModes.MULTI.value) else None
            session = await self._get_game_generator().generate_game(
                group_id,
                game_mode,
                player_count=player_count,
                player_names=player_names,
                player_ids=player_ids,
            )

            session.status = GameStatus.ACTIVE

            # 添加玩家
            if game_mode == GameModes.SINGLE.value:
                player = Player(player_id=user_id, name=user_name)
                session.add_player(player)
            else:
                for pid, name in lobby_players:
                    session.add_player(Player(player_id=pid, name=name))
                assign_multiplayer_identities(session, lobby_order or [pid for pid, _ in lobby_players])

            # 初始化环境演化系统
            game_states: dict[str, JsonObject] = {group_id: {}}
            env_system = self._get_or_create_environment_system(game_states)
            session._environment_system = env_system  # 保存到session供后续使用
            
            # 调用环境演化系统的初始化
            try:
                await env_system.initialize_environment(
                    group_id=group_id,
                    scene_type=session.scene_name,
                    player_identity=session.player_identity,
                    building_type=getattr(session, 'scene_structure', {}).get('building_type', '未知建筑') if isinstance(getattr(session, 'scene_structure', {}), dict) else '未知建筑'
                )
                logger.info(f"环境演化系统初始化完成: {group_id}")
            except Exception as e:
                logger.error(f"环境演化系统初始化失败: {e}")
            
            # 初始化环境状态（保留原有快照系统）
            if not isinstance(getattr(session, 'environment_state', None), dict):
                session.environment_state = {}
            
            env_state = session.environment_state
            
            # 初始化环境状态快照
            env_snapshot = EnvironmentState()
            # 设置初始环境状态
            env_snapshot.set_door_state("entrance_door", DoorState.LOCKED)
            env_snapshot.set_light_state("main_hall_light", LightState.DIM)
            env_snapshot.set_temperature(18.0)
            env_snapshot.add_sound("远处的风声")
            env_snapshot.add_smell("陈旧的霉味")
            env_state["environment_snapshot"] = env_snapshot.to_dict()
            
            # 初始化游戏时间管理器
            time_manager = self._get_or_create_game_time_manager()
            env_state["game_time"] = {
                "current_time": 0,
                "time_description": "深夜（午夜时分，周围一片死寂）",
                "elapsed_minutes": 0
            }
            
            # 初始化规则变异系统
            rule_mutation = self._get_or_create_rule_mutation_system()
            session._rule_mutation_system = rule_mutation  # 保存到session供action_processor使用
            env_state["rule_mutations"] = []
            
            # 初始化线索发现系统
            clue_system = self._get_or_create_clue_discovery_system()
            env_state["discovered_clues"] = []
            
            # 多人模式：初始化物理系统
            if game_mode == GameModes.MULTI.value:
                physics_system = self._get_or_create_multiplayer_physics_system()
                for pid, name in lobby_players:
                    physics_system.register_player(pid, name)
                env_state["physics_state"] = physics_system.to_dict()
            
            # 保存到状态管理器
            state_manager = GameStateManager()
            state = await state_manager.get_or_create(group_id)
            try:
                state.session = session
                
                # 保存存档
                await save_manager.save_immediately(group_id, session)
            finally:
                state.release()
            
            # 多人模式：身份与个人规则通过私聊下发
            if game_mode == GameModes.MULTI.value:
                await self._send_multiplayer_private_infos(session, lobby_players, group_id)

            # 生成图片
            image_generator = AsyncImageGenerator(self._temp_images_dir)

            
            # 获取核心象征符号（如果有）
            core_symbols = getattr(session, 'core_symbols', None)
            
            # ① 生成并发送剧情导入图片
            scene_image = await image_generator.generate_scene_image(
                scene_name=session.scene_name,
                background=session.background,
                arrival_reason=session.player_identity,
                core_symbols=core_symbols,
            )
            await self._send_image_path(scene_image)
            await asyncio.sleep(1.0)  # 间隔1秒
            
            # ② 生成入场描述
            entrance_description = await self._generate_entrance_description(session)
            # 记录入场描述，供 `/rg 剧情` 重发（尽量避免重复LLM调用）
            if isinstance(getattr(session, 'environment_state', None), dict):
                session.environment_state["entrance_description"] = entrance_description
            
            # ② 生成并发送入场长图（入场+NPC引导）
            npc_guidance = getattr(session, 'npc_guidance', {}) or {}

            # 初始化 NPC 与“已知规则”状态：让后续行动可以基于态度/记忆动态演化，而不是硬编码结论
            if isinstance(getattr(session, 'environment_state', None), dict) and npc_guidance:
                env_state = session.environment_state
                env_state.setdefault("npcs", [])
                env_state.setdefault("known_rule_indices", [])

                npc_name = npc_guidance.get("npc_name", "NPC")

                # 推断更像人类的初始NPC位置：优先前台/柜台/收银台等
                scene_structure = getattr(session, "scene_structure", {}) or {}
                areas: list[str] = []
                for fl in scene_structure.get("floors", []) or []:
                    if isinstance(fl, dict):
                        areas.extend([str(x) for x in (fl.get("areas") or fl.get("rooms") or [])])
                areas.extend([str(x) for x in (scene_structure.get("special_areas") or [])])

                prefer = ["柜台", "收银", "前台", "服务台", "接待", "值班室", "大厅", "入口", "门口"]
                npc_location = None
                for kw in prefer:
                    hit = next((a for a in areas if kw in a), None)
                    if hit:
                        npc_location = hit
                        break
                npc_location = npc_location or (areas[0] if areas else session.scene_name or "起始位置")

                # 只在第一次初始化 NPC 列表（避免覆盖存档/后续演化）
                if not env_state.get("npcs"):
                    memory = NPCMemory()
                    initial_attitude = NPCAttitude.NEUTRAL

                    # 尝试给“单人模式”的开局玩家一个初始态度向量（多人模式在首次互动时再初始化）
                    if game_mode == GameModes.SINGLE.value and user_id:
                        memory.initialize_attitude_vector(user_id)


                        att = str(npc_guidance.get("npc_attitude", "") or "")
                        # 轻量映射：让初始"语气/态度"影响信任/怀疑
                        if any(k in att for k in ["友好", "温和", "热情"]):
                            memory.update_attitude_vector(user_id, affection_delta=10, trust_delta=10)
                            initial_attitude = NPCAttitude.FRIENDLY
                        elif any(k in att for k in ["警告", "严厉", "冷淡", "不耐烦"]):
                            memory.update_attitude_vector(user_id, suspicion_delta=15, trust_delta=-5)
                            initial_attitude = NPCAttitude.SUSPICIOUS
                        elif any(k in att for k in ["敌对", "威胁"]):
                            memory.update_attitude_vector(user_id, hostility_delta=25, trust_delta=-15)
                            initial_attitude = NPCAttitude.HOSTILE
                        
                        memory.player_attitudes[user_id] = initial_attitude

                    # 使用 NPC 类创建 NPC 实例
                    guide_npc = NPC(
                        npc_id="guide_0",
                        name=npc_name,
                        role=str(npc_guidance.get("npc_role", "")),
                        personality="",
                        initial_location=npc_location
                    )
                    guide_npc.memory = memory
                    guide_npc.current_location = npc_location
                    guide_npc.danger_level = "低"
                    
                    env_state["npcs"] = [guide_npc.to_dict()]

                # 兼容旧字段：给上下文/旧逻辑一个“在场NPC摘要”
                env_state["npcs_present"] = [
                    {
                        "name": npc_name,
                        "role": npc_guidance.get("npc_role", ""),
                        "attitude": npc_guidance.get("npc_attitude", ""),
                        "location": npc_location,
                    }
                ]


            if npc_guidance:
                entrance_long_image = await image_generator.generate_entrance_long_image(
                    scene_name=session.scene_name,
                    entrance_description=entrance_description,
                    npc_guidance=npc_guidance,
                )
                await self._send_image_path(entrance_long_image)
                await asyncio.sleep(1.0)  # 间隔1秒
            
            # ③ 生成并发送规则图片
            guidance_method = npc_guidance.get("guidance_method", "rule_carrier") if npc_guidance else "rule_carrier"

            # “规则图”展示的是玩家当前已获得的信息（已知规则），而不是后台完整规则。
            # 这样 NPC 的态度/是否愿意多说，才能在玩法上形成可感知的动态变化。
            env_state = session.environment_state if isinstance(getattr(session, "environment_state", None), dict) else {}

            def _normalize_all_rules() -> list[RuleDict]:
                return self._normalize_rules_list(session.rules)


            if guidance_method == "natural_language" and npc_guidance:
                npc_name = npc_guidance.get("npc_name", "NPC")
                npc_attitude = str(npc_guidance.get("npc_attitude", "警告") or "").strip()

                # 标题可读化：避免出现“警告且疲惫/严厉并慌张”这种像状态描述的拼接
                def _title_att(att: str) -> str:
                    att = str(att or "").strip()
                    for sep in ["且", "并", "但是", "而且", "同时", "，", ",", "。", "；", ";", "、", "/"]:
                        if sep in att:
                            att = att.split(sep, 1)[0].strip()
                    allow = {"警告", "提醒", "告诫", "忠告", "提示", "指示", "劝告"}
                    return att if att in allow else (att if len(att) <= 4 and att else "提醒")

                rules_title = f"{npc_name}的{_title_att(npc_attitude)}"

                npc_dialogue = str(npc_guidance.get("npc_dialogue", "") or "")
                display_rules = await self._extract_rules_from_dialogue(npc_dialogue, _normalize_all_rules(), npc_name)

                known = [int(r.get("original_index")) for r in display_rules if isinstance(r, dict) and isinstance(r.get("original_index"), int)]
                env_state["known_rule_indices"] = sorted(set(known))

                extra_texts = [str(r.get("text", "")).strip() for r in display_rules if isinstance(r, dict) and not isinstance(r.get("original_index"), int)]
                if extra_texts:
                    env_state["known_rule_texts_extra"] = sorted(set([x for x in extra_texts if x]))
                else:
                    env_state.pop("known_rule_texts_extra", None)
            else:
                rules_title = npc_guidance.get("rule_carrier_title", f"{session.scene_name} - 规则") if npc_guidance else f"{session.scene_name} - 规则"
                display_rules = _normalize_all_rules()
                env_state["known_rule_indices"] = [int(r.get("original_index", 0)) for r in display_rules]
                env_state.pop("known_rule_texts_extra", None)

            
            # 把“口述补全规则”也合并到规则图展示里（这些可能不在后台规则表，但玩家确实已经听到）
            if isinstance(env_state, dict) and isinstance(env_state.get("known_rule_texts_extra"), list):
                extra = [str(x).strip() for x in env_state.get("known_rule_texts_extra", []) if str(x).strip()]
                if extra:
                    has_extra_in_display = any(
                        isinstance(r, dict) and not isinstance(r.get("original_index"), int)
                        for r in (display_rules or [])
                    )
                    if not has_extra_in_display:
                        display_rules = list(display_rules) + [{"text": t, "original_index": None, "source": "npc_dialogue"} for t in extra]

            # 规则展示去重：同一句话只保留一条（优先保留带 original_index 的版本）
            def _norm_rule_text(t: str) -> str:
                t = re.sub(r"\s+", "", str(t or ""))
                t = re.sub(r"[，,。.!！？?；;:“”\"'‘’《》【】\[\]（）()\-—…·]", "", t)
                return t

            dedup: list[RuleDict] = []

            seen: dict[str, int] = {}
            for r in (display_rules or []):
                if not isinstance(r, dict):
                    r = {"text": str(r)}
                txt = str(r.get("text", r.get("content", str(r))) or "").strip()
                if not txt:
                    continue
                key = _norm_rule_text(txt)
                if not key:
                    continue
                if key in seen:
                    pi = seen[key]
                    prev = dedup[pi]
                    if not isinstance(prev.get("original_index"), int) and isinstance(r.get("original_index"), int):
                        dedup[pi] = r
                    continue
                seen[key] = len(dedup)
                dedup.append(r)
            display_rules = dedup

            # 生成规则图片（所有模式都显示）
            rules_image = await image_generator.generate_rules_image(
                rules_title=rules_title,
                rules=display_rules,
                win_condition=session.win_condition,
                game_mode=game_mode,
            )
            await self._send_image_path(rules_image)
            await asyncio.sleep(1.0)  # 间隔1秒

            
            # ④ 生成并发送场景结构文字长图（所有模式都显示）
            scene_structure = getattr(session, 'scene_structure', {}) or {}
            if scene_structure:
                building_type = scene_structure.get('building_type', '未知建筑')
                overall_layout = scene_structure.get('overall_layout', '未知布局')
                floors = scene_structure.get('floors', [])
                connections = scene_structure.get('connections', [])
                special_areas = scene_structure.get('special_areas', [])
                
                scene_structure_image = await image_generator.generate_scene_structure_text_image(
                    building_type=building_type,
                    overall_layout=overall_layout,
                    floors=floors,
                    connections=connections,
                    special_areas=special_areas,
                )
                await self._send_image_path(scene_structure_image)
                await asyncio.sleep(0.5)  # 最后一张可以稍短

            
            # 发送文字说明
            if game_mode == GameModes.MULTI.value:
                players_disp = "、".join([p.name for p in session.players.values()]) if session.players else "（无）"
                await self.send_text(
                    f"**游戏已开始！**\n\n"
                    f"模式：{game_mode}\n"
                    f"场景：{session.scene_name}\n"
                    f"玩家：{players_disp}\n\n"
                    f"`/rg 行动 <描述>` - 进行行动\n"
                    f"`/rg 推理 <内容>` - 记录推理\n"
                    f"`/rg 状态` - 查看状态"
                )
            else:
                await self.send_text(
                    f"**游戏已开始！**\n\n"
                    f"模式：{game_mode}\n"
                    f"场景：{session.scene_name}\n\n"
                    f"`/rg 行动 <描述>` - 进行行动\n"
                    f"`/rg 推理 <内容>` - 记录推理\n"
                    f"`/rg 状态` - 查看状态"
                )


            
            logger.info(f"游戏开始成功: {group_id}, 模式: {game_mode}")
            return True, "游戏已开始", 2
            
        except Exception as e:
            logger.error(f"开始游戏失败: {e}", exc_info=True)
            await self.send_text(f"生成游戏失败：{e}\n请稍后重试。")
            return False, "生成失败", 2

    async def _handle_加入(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        """处理加入游戏命令"""
        state_manager = GameStateManager()
        state = await state_manager.get(group_id)

        if not state or not state.session:
            raise GameNotFoundError("当前没有正在进行的游戏。请先使用 `/rg 开始` 开始游戏")

        try:
            session = state.session

            if session.game_mode != GameModes.MULTI.value:
                await self.send_text("当前不是多人模式游戏。")
                return False, "非多人模式", 2

            # 只允许在大厅阶段加入
            if session.status != GameStatus.WAITING:
                await self.send_text("游戏已经开始，无法中途加入。")
                return False, "已开始", 2

            # 检查玩家是否已在游戏中
            if user_id in session.players:
                await self.send_text("你已经在大厅里了。")
                return False, "已在大厅", 2

            # 限制人数（最多5人）
            if len(session.players) >= 5:
                await self.send_text("大厅人数已满（最多5人）。")
                return False, "人数已满", 2

            # 创建新玩家
            player = Player(player_id=user_id, name=user_name)
            success = session.add_player(player)

            if not success:
                await self.send_text("大厅人数已满（最多5人）。")
                return False, "人数已满", 2

            # 维护加入顺序
            env_state = session.environment_state if isinstance(getattr(session, "environment_state", None), dict) else {}
            order = env_state.get("lobby_player_order", [])
            if not isinstance(order, list):
                order = []
            if user_id not in order:
                order.append(user_id)
            env_state["lobby_player_order"] = order
            session.environment_state = env_state

            lobby_meta = env_state.get("lobby", {}) if isinstance(env_state.get("lobby"), dict) else {}
            host_name = str(lobby_meta.get("host_name") or "房主")
            cur = len(session.players)
            players_disp = "、".join([p.name for p in session.players.values()])

            # 保存大厅状态
            save_manager = SaveManager()
            await save_manager.save_immediately(group_id, session)

            await self.send_text(
                "**加入成功**\n\n"
                f"{user_name} 加入了大厅。\n"
                f"当前人数：{cur}/5\n"
                f"玩家：{players_disp}\n\n"
                f"等待房主 {host_name} 开始生成：`/rg 开始 多人 开始`"
            )
            return True, "加入成功", 2

        except GameNotFoundError as e:
            await self.send_text(str(e))
            return False, "无游戏", 2
        finally:
            if state:
                state.release()


    async def _handle_离开(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        """处理离开游戏命令"""
        state_manager = GameStateManager()
        state = await state_manager.get(group_id)

        try:
            if not state or not state.session:
                raise GameNotFoundError("当前没有正在进行的游戏")

            session = state.session
            if user_id not in session.players:
                raise PlayerNotInGameError("你不在当前游戏中")

            session.remove_player(user_id)
            await self.send_text(f"{user_name} 离开了游戏。")

            # 如果所有玩家都离开了，结束游戏
            if not session.players:
                session.status = GameStatus.ENDED
                await state_manager.remove(group_id)

            return True, "离开成功", 2
        except GameNotFoundError as e:
            await self.send_text(str(e))
            return False, "无游戏", 2
        except PlayerNotInGameError as e:
            await self.send_text(str(e))
            return False, "不在游戏中", 2
        finally:
            if state:
                state.release()

    async def _handle_状态(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        """处理查看状态命令"""
        _ = rest_input  # 状态命令不需要额外参数
        state_manager = GameStateManager()
        state = await state_manager.get(group_id)

        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            session = state.session
            title = session.scene_name
            if not title:
                if session.game_mode == GameModes.MULTI.value and session.status == GameStatus.WAITING:
                    title = "多人大厅"
                else:
                    title = "未生成"

            status_text = [
                f"**游戏状态：{title}**",
                f"模式：{session.game_mode}",
                f"状态：{session.status.value}",
                "",
                "**玩家列表**",
            ]

            for pid, player in session.players.items():
                # 标记当前玩家
                is_current = (str(pid) == str(user_id))
                current_marker = "【你】" if is_current else ""
                status_emoji = "🟢" if player.status == PlayerStatus.ALIVE else "💀"
                # 获取疲劳值，如果没有则根据体力值计算
                fatigue = getattr(player, 'fatigue', None)
                if fatigue is None:
                    # 根据体力值推导疲劳等级
                    if player.health >= 80:
                        fatigue = "无"
                    elif player.health >= 60:
                        fatigue = "轻微"
                    elif player.health >= 40:
                        fatigue = "中度"
                    elif player.health >= 20:
                        fatigue = "严重"
                    else:
                        fatigue = "极度"
                status_text.append(
                    f"{status_emoji} {player.name}{current_marker} - 理智:{player.sanity}/100 体力:{player.health}/100 受伤:{player.injury}"
                )
                status_text.append(
                    f"   状态:{player.state} 情绪:{player.emotion} 疲劳:{fatigue}"
                )
                status_text.append(
                    f"   恐惧:{player.fear_level}/100 焦虑:{player.anxiety_level}/100 压力:{player.stress_level}/100"
                )

            await self.send_text("\n".join(status_text))
            return True, "状态已显示", 2
        finally:
            if state:
                state.release()

    async def _handle_规则(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        """处理查看规则命令"""
        _ = rest_input
        state_manager = GameStateManager()
        state = await state_manager.get(group_id)

        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            session = state.session
            player = session.players.get(user_id)
            if not player:
                await self.send_text("你不在游戏中。")
                return False, "不在游戏中", 2

            # 使用玩家实际名称（从player对象获取更可靠）
            display_name = player.name or user_name or "玩家"

            # 多人模式：显示个人规则 + 共同规则
            if session.game_mode == GameModes.MULTI.value:
                rules_text = [f"**{session.scene_name} - {display_name}的规则**", ""]
                
                # 个人规则
                if player.unique_rules:
                    rules_text.append("【个人规则】")
                    for i, r in enumerate(player.unique_rules, 1):
                        if isinstance(r, dict):
                            text = str(r.get("text", "")).strip()
                        else:
                            text = str(r).strip()
                        if text:
                            rules_text.append(f"{i}. {text}")
                    rules_text.append("")
                
                # 共同规则
                mi = session.rule_network.get("multi_identity", {}) if isinstance(getattr(session, "rule_network", None), dict) else {}
                common = mi.get("common_rules", []) if isinstance(mi, dict) else []
                if common:
                    rules_text.append("【共同规则】")
                    for i, r in enumerate(common, 1):
                        if isinstance(r, dict):
                            text = str(r.get("text", "")).strip()
                        else:
                            text = str(r).strip()
                        if text:
                            rules_text.append(f"{i}. {text}")
                    rules_text.append("")
                
                if len(rules_text) <= 2:  # 只有标题
                    rules_text.append("你还没有获得任何规则。")
                
                rules_text.append(f"**通关条件**：{session.win_condition}")
                await self.send_text("\n".join(rules_text))
                return True, "规则已显示", 2

            # 单人模式：使用原有逻辑
            if not session.rules:
                await self.send_text("规则尚未生成。")
                return False, "无规则", 2

            env_state = session.environment_state if isinstance(getattr(session, "environment_state", None), dict) else {}

            # 归一化全规则表（用于索引取值）
            all_rules: list[RuleDict] = []
            for i, r in enumerate(session.rules):
                if isinstance(r, dict):
                    rr = dict(r)
                    rr.setdefault("original_index", i)
                    all_rules.append(rr)
                else:
                    all_rules.append({"text": str(r), "original_index": i})

            known_indices: list[int] = []
            if isinstance(env_state.get("known_rule_indices"), list):
                known_indices = [int(x) for x in env_state.get("known_rule_indices", []) if isinstance(x, int)]

            known_rules: list[RuleDict] = []
            for idx in sorted(set(known_indices)):
                if 0 <= idx < len(all_rules):
                    known_rules.append(all_rules[idx])

            extra_texts: list[str] = []
            if isinstance(env_state.get("known_rule_texts_extra"), list):
                extra_texts = [str(x).strip() for x in env_state.get("known_rule_texts_extra", []) if str(x).strip()]

            if not known_rules and not extra_texts:
                await self.send_text(
                    f"**{session.scene_name} - 已知规则**\n\n"
                    "你目前还没有获得任何明确规则。\n"
                    "建议：先查看 `/rg 剧情` 的入场信息，或使用 `/rg 行动` 进行探索/礼貌询问。\n"
                    "使用 `/rg 状态` 随时查看当前状态。\n\n"
                    f"**通关条件**：{session.win_condition}"
                )
                return True, "规则已显示", 2

            rules_text = [f"**{session.scene_name} - 已知规则**", ""]

            def _norm_rule_text(t: str) -> str:
                t = re.sub(r"\s+", "", str(t or ""))
                t = re.sub(r"[，,。.!！？?；;:“”\"'‘’《》【】\[\]（）()\-—…·]", "", t)
                return t

            seen_text: set[str] = set()

            idx_num = 1
            for rule in known_rules:
                text = str(rule.get("text", rule.get("content", str(rule))) if isinstance(rule, dict) else str(rule)).strip()
                key = _norm_rule_text(text)
                if text and key and key not in seen_text:
                    rules_text.append(f"{idx_num}. {text}")
                    idx_num += 1
                    seen_text.add(key)

            for t in extra_texts:
                key = _norm_rule_text(t)
                if t and key and key not in seen_text:
                    rules_text.append(f"{idx_num}. {t}")
                    idx_num += 1
                    seen_text.add(key)


            rules_text.extend(["", f"**通关条件**：{session.win_condition}"])

            await self.send_text("\n".join(rules_text))
            return True, "规则已显示", 2

        finally:
            if state:
                state.release()

    async def _handle_提示(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        """处理获取提示命令

        目标：让 LLM 基于“完整规则 + 隐藏真相”生成**有限度**提示，但输出必须非剧透。
        - 规则提示：只点评一条规则的措辞陷阱/关键字/边界条件，并给一个可验证建议。
        - 线索提示：只针对背包中某个物品，提示它可能与什么相关，并给下一步行动建议。
        """
        hint_type = (rest_input or "规则").strip()

        state_manager = GameStateManager()
        state = await state_manager.get(group_id)

        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            session = state.session
            player = session.players.get(user_id)
            if not player:
                await self.send_text("你不在游戏中。")
                return False, "不在游戏中", 2

            if session.hint_count <= 0:
                await self.send_text("你的提示次数已用完！")
                return False, "无提示次数", 2

            # 减少提示次数
            session.hint_count -= 1

            want_clue = "线索" in hint_type
            hint_mode = "clue" if want_clue else "rule"

            # 基于当前“玩家已知规则”做轻量提示控制：LLM 可用全规则推理，但输出尽量围绕已知信息
            env_state = session.environment_state if isinstance(getattr(session, "environment_state", None), dict) else {}
            known_indices: list[int] = []
            if isinstance(env_state.get("known_rule_indices"), list):
                known_indices = [int(x) for x in env_state.get("known_rule_indices", []) if isinstance(x, int)]

            extra_texts: list[str] = []
            if isinstance(env_state.get("known_rule_texts_extra"), list):
                extra_texts = [str(x).strip() for x in env_state.get("known_rule_texts_extra", []) if str(x).strip()]

            def _normalize_rule_text(r: RuleDict | str) -> str:

                if isinstance(r, dict):
                    return str(r.get("text", r.get("content", "")) or "").strip()
                return str(r or "").strip()

            all_rules: list[str] = [_normalize_rule_text(r) for r in (session.rules or [])]

            # ===== 规则提示：确定“只点评哪一条” =====
            target_rule_index_1b: int | None = None
            target_rule_text = ""

            if not want_clue:
                # 允许：/rg 提示 规则3  /  /rg 提示 3
                m = re.search(r"(\d{1,2})", hint_type)
                if m:
                    try:
                        n = int(m.group(1))
                        if 1 <= n <= len(all_rules):
                            target_rule_index_1b = n
                    except Exception:
                        target_rule_index_1b = None

                # 未指定就从已知规则里挑一条（更符合“验证规则”的体感）
                if target_rule_index_1b is None and known_indices:
                    idx0 = sorted(set([i for i in known_indices if 0 <= i < len(all_rules)]))[-1]
                    target_rule_index_1b = idx0 + 1

                if target_rule_index_1b is not None and 1 <= target_rule_index_1b <= len(all_rules):
                    target_rule_text = all_rules[target_rule_index_1b - 1]
                elif extra_texts:
                    # 只有口述规则但没有索引时，仍可点评一句“口述规矩”
                    target_rule_text = extra_texts[0]

            # ===== 线索提示：确定“只提示哪一个物品” =====
            inventory = getattr(player, "inventory", []) or []
            clue_query = ""
            if want_clue:
                # 支持：/rg 提示 线索 钥匙
                clue_query = hint_type.replace("线索", "", 1).strip()

            selected_item: JsonObject | None = None

            if want_clue and inventory:
                # 1) 名称匹配优先
                if clue_query:
                    for it in inventory:
                        if isinstance(it, dict) and clue_query in str(it.get("name", "") or ""):
                            selected_item = it
                            break

                # 2) 关键物品优先
                if selected_item is None:
                    for it in reversed(inventory):
                        if isinstance(it, dict) and bool(it.get("is_key_item", False)):
                            selected_item = it
                            break

                # 3) 否则选最近获得的一个
                if selected_item is None:
                    for it in reversed(inventory):
                        if isinstance(it, dict) and str(it.get("name", "") or "").strip():
                            selected_item = it
                            break

            def _format_item(it: Mapping[str, JsonValue]) -> str:

                name = str(it.get("name", "") or "").strip()
                desc = str(it.get("description", "") or "").strip()
                oh = str(it.get("observation_hint", "") or "").strip()
                is_key = bool(it.get("is_key_item", False))
                parts = [name]
                if is_key:
                    parts.append("关键")
                if desc:
                    parts.append(f"描述:{desc}")
                if oh:
                    parts.append(f"观察提示:{oh}")
                return " | ".join(parts)

            selected_item_text = _format_item(selected_item) if (selected_item and isinstance(selected_item, dict)) else ""

            # 组装 LLM 输入（包含完整规则与隐藏真相，但强制非剧透输出）
            rules_block = "\n".join([f"{i+1}. {t}" for i, t in enumerate(all_rules) if t])

            known_rules: list[str] = []
            for idx in sorted(set([i for i in known_indices if isinstance(i, int)])):
                if 0 <= idx < len(all_rules):
                    t = all_rules[idx]
                    if t:
                        known_rules.append(t)
            known_rules.extend([t for t in extra_texts if t])
            known_rules_block = "\n".join([f"- {t}" for t in known_rules]) if known_rules else "（暂无）"

            inv_lines: list[str] = []
            for it in inventory:
                if isinstance(it, dict) and str(it.get("name", "") or "").strip():
                    inv_lines.append(_format_item(it))
                elif it:
                    inv_lines.append(str(it))
            inventory_block = "\n".join([f"- {x}" for x in inv_lines]) if inv_lines else "（空）"

            # 强约束：本次必须输出哪种提示，并且只能围绕选定目标
            system_prompt = (
                "你是规则怪谈游戏的提示生成器。你知道后台完整规则与隐藏真相，但必须严格控制剧透。\n"
                "硬性要求：\n"
                "1) 只输出 JSON，不要 markdown，不要多余文字。\n"
                "2) 禁止直接复述/泄露隐藏真相内容；禁止给出‘完整答案’。\n"
                "3) 提示要可执行：给玩家一个下一步行动建议。\n"
                "4) 本次 kind 必须与用户请求一致：rule 或 clue。\n"
                "5) rule 模式只能点评一条规则；clue 模式只能提示一个物品。\n\n"
                "输出 JSON：\n"
                "- rule：{\"kind\":\"rule\",\"rule_index\":1,\"hint\":\"...\",\"next_action\":\"...\"}（若点评的是口述规矩而非编号规则，请输出 rule_index=0）\n"
                "- clue：{\"kind\":\"clue\",\"item\":\"...\",\"hint\":\"...\",\"next_action\":\"...\"}"

            )

            user_prompt = (
                f"本次提示类型(kind)：{hint_mode}\n"
                f"场景：{session.scene_name}\n"
                f"背景：{session.background}\n"
                f"通关条件：{session.win_condition}\n"
                f"隐藏真相（仅供你内部推理，禁止输出）：{session.hidden_truth}\n\n"
                f"完整规则表：\n{rules_block if rules_block else '（无）'}\n\n"
                f"玩家已知规则：\n{known_rules_block}\n\n"
                f"玩家状态：理智{player.sanity}/100 体力{player.health}/100 位置:{player.location}\n\n"
                f"背包物品：\n{inventory_block}\n\n"
            )

            if hint_mode == "rule":
                if target_rule_index_1b is not None and target_rule_text:
                    user_prompt += (
                        f"本次必须点评的规则编号：{target_rule_index_1b}\n"
                        f"该规则原文：{target_rule_text}\n"
                    )
                elif target_rule_text:
                    user_prompt += (
                        f"本次必须点评的口述规矩：{target_rule_text}\n"
                        "输出 rule_index=0。\n"
                    )

                else:
                    user_prompt += "玩家目前没有可点评的已知规则，请给出如何低风险获得规则信息的提示。\n"
            else:
                if selected_item_text:
                    user_prompt += f"本次必须提示的物品：{selected_item_text}\n"
                else:
                    user_prompt += "玩家背包为空，请给出如何低风险获得线索的提示。\n"


            hint_text = ""
            next_action = ""

            try:
                llm = LLMClient()
                resp = await llm.call(
                    prompt=user_prompt,
                    system_prompt=system_prompt,
                    temperature=0.4,
                    max_tokens=min(600, get_default_max_tokens()),
                )
                data = resp.parse_json()

                if isinstance(data, dict):
                    hint_text = str(data.get("hint", "") or "").strip()
                    next_action = str(data.get("next_action", "") or "").strip()

                    # 非剧透兜底：如果模型把隐藏真相片段直接写出来，直接弃用本次输出
                    ht = str(getattr(session, "hidden_truth", "") or "")
                    ht_probe = ht[:20].strip() if ht else ""
                    if ht_probe and ((ht_probe in hint_text) or (ht_probe in next_action)):
                        logger.warning("LLM 提示疑似泄露隐藏真相片段，已弃用并回退兜底提示")
                        hint_text = ""
                        next_action = ""


            except Exception as e:
                logger.warning(f"LLM 提示生成失败，将使用兜底提示: {e}")

            # 兜底：保留原先的提示策略（不依赖 LLM）
            if not hint_text:
                total_rules = len(session.rules or [])
                have_unknown = bool(total_rules and len(set(known_indices)) < total_rules)
                if want_clue:
                    hint_text = (
                        "先别做高风险动作。用低风险的方式‘确认事实’：\n"
                        "- 观察/检查可写字的东西（告示、标签、票据、墙面痕迹）\n"
                        "- 询问在场NPC‘这里有什么禁忌’，注意对方态度变化\n"
                        "- 用 `/rg 道具` 复盘你拿到的物品是否暗示线索"
                    )
                else:
                    npc_name = "NPC"
                    if isinstance(getattr(session, "npc_guidance", None), dict):
                        npc_name = str(session.npc_guidance.get("npc_name", "NPC") or "NPC")
                    if have_unknown:
                        hint_text = (
                            "你直觉觉得‘规矩’还没说完。\n"
                            f"尝试在同一地点礼貌地询问{npc_name}，或寻找规则载体（告示/收据/标签）。\n"
                            "注意：你越礼貌、越像在确认而非逼问，越可能得到更多信息。"
                        )
                    else:
                        hint_text = (
                            "把你已知的规则逐条对照现场：哪些能被立刻验证？\n"
                            "用最小代价做试探（例如观察、短暂停留、轻触），再决定是否行动。"
                        )

            # 拼接下一步建议（可选）
            if next_action:
                hint = f"{hint_text}\n\n下一步建议：{next_action}"
            else:
                hint = hint_text

            # 保存状态
            save_manager = SaveManager()
            await save_manager.schedule_save(group_id, session)

            await self.send_text(f"**提示（剩余{session.hint_count}次）**\n\n{hint}")
            return True, "提示已发送", 2

        except Exception as e:
            logger.error(f"生成提示失败: {e}", exc_info=True)
            await self.send_text(f"生成提示时出错：{e}")
            return False, "生成失败", 2
        finally:
            if state:
                state.release()


    async def _handle_推理(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        """处理推理命令"""
        if not rest_input:
            await self.send_text("请提供推理内容。用法：`/rg 推理 <推理内容>`")
            return False, "缺少推理内容", 2

        state_manager = GameStateManager()
        state = await state_manager.get(group_id)

        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            player = state.session.players.get(user_id)
            if not player:
                await self.send_text("你不在游戏中。")
                return False, "不在游戏中", 2

            if player.status != PlayerStatus.ALIVE:
                await self.send_text("你已经死亡，无法进行推理。")
                return False, "已死亡", 2

            # 记录推理
            player.reasoning_history.append(rest_input)

            display_name = player.name or user_name or "玩家"
            await self.send_text(f"**{display_name} 的推理**\n\n{rest_input}\n\n推理已记录。")

            return True, "推理已记录", 2
        finally:
            if state:
                state.release()

    async def _handle_行动(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        """处理行动命令"""
        if not rest_input:
            await self.send_text("请提供行动描述。用法：`/rg 行动 <行动描述>`")
            return False, "缺少行动描述", 2

        state_manager = GameStateManager()
        state = await state_manager.get(group_id)

        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            player = state.session.players.get(user_id)
            if not player:
                await self.send_text("你不在游戏中。")
                return False, "不在游戏中", 2

            if player.status != PlayerStatus.ALIVE:
                await self.send_text("你已经死亡，无法行动。")
                return False, "已死亡", 2

            # 如果当前会话里保存的名字像是 QQ 号/回退名，则用本次消息解析到的昵称刷新（提升观感）
            try:
                bad_name = False
                if not isinstance(player.name, str) or not player.name.strip():
                    bad_name = True
                else:
                    pn = player.name.strip()
                    if pn == user_id or pn == f"玩家{user_id}" or pn.isdigit():
                        bad_name = True
                if bad_name and isinstance(user_name, str) and user_name.strip() and not user_name.strip().isdigit():
                    player.name = user_name.strip()
            except Exception:
                pass

            # 处理行动
            result = await self._action_processor.process_action(
                action=rest_input,
                player=player,
                session=state.session,
            )
            
            # 记录行动
            player.action_history.append({
                "action": rest_input,
                "timestamp": datetime.now().isoformat(),
            })
            player.last_action_at = datetime.now()

            
            # 更新游戏时间
            env_state = state.session.environment_state if isinstance(getattr(state.session, 'environment_state', None), dict) else {}
            game_time = env_state.get("game_time", {})
            elapsed_minutes = int(game_time.get("elapsed_minutes", 0)) + 5  # 每次行动推进5分钟
            game_time["elapsed_minutes"] = elapsed_minutes
            
            # 更新时间描述
            if elapsed_minutes < 60:
                game_time["time_description"] = "深夜（午夜时分，周围一片死寂）"
            elif elapsed_minutes < 180:
                game_time["time_description"] = "凌晨（黎明前的黑暗，空气中弥漫着不安）"
            else:
                game_time["time_description"] = "黎明（东方泛起鱼肚白，但黑暗仍未完全消散）"
            
            env_state["game_time"] = game_time
            state.session.environment_state = env_state
            
            # 检查线索发现
            if result.discovered_clues:
                from .systems.clue_discovery_system import Clue, ClueType
                clue_system = self._get_or_create_clue_discovery_system()
                for idx, clue_text in enumerate(result.discovered_clues):
                    clue_id = f"clue_{len(env_state.get('discovered_clues', [])) + idx}"
                    # 先添加线索
                    clue = Clue(
                        clue_id=clue_id,
                        clue_type=ClueType.OBSERVATION,
                        title=clue_text[:50] if len(clue_text) > 50 else clue_text,
                        description=clue_text,
                        location=getattr(player, 'location', '未知'),
                    )
                    clue_system.add_clue(clue)
                    # 再标记为已发现
                    clue_system.discover_clue(
                        clue_id=clue_id,
                        player_id=user_id,
                        _discovery_method="行动发现",
                        game_time=elapsed_minutes
                    )
                env_state["discovered_clues"] = list(clue_system.discovered_clues)
            
            # 检查规则变异条件
            if result.is_key_item:
                rule_mutation = self._get_or_create_rule_mutation_system()
                env_state["rule_mutations"] = env_state.get("rule_mutations", []) + [{
                    "triggered_at": elapsed_minutes,
                    "triggered_by": rest_input[:100],
                    "reason": "发现关键物品"
                }]
            
            # 检查是否达成通关条件
            if not state.session.has_cleared:
                has_cleared = await self._ending_judge.check_win_condition(
                    session=state.session,
                    player=player,
                )
                if has_cleared:
                    state.session.has_cleared = True
                    await self.send_text(
                        "**🎉 恭喜！你已达成通关条件！**\n\n"
                        "你可以选择：\n"
                        "- `/rg 继续` - 继续探索，寻找完美结局\n"
                        "- `/rg 结束` - 结束游戏，查看结局"
                    )
            
            # 获取玩家状态信息（用于图片生成）
            injury = "无伤"
            fatigue = "正常"
            state_desc = "正常"
            emotion = "平静"
            fear_level = 0
            anxiety_level = 0
            stress_level = 0
            new_location = None
            random_event = None
            
            # 从player的额外数据中获取（如果有）
            if hasattr(player, 'injury'):
                injury = player.injury
            if hasattr(player, 'fatigue'):
                fatigue = str(player.fatigue)

            if hasattr(player, 'state'):
                state_desc = player.state
            if hasattr(player, 'emotion'):
                emotion = player.emotion
            if hasattr(player, 'fear_level'):
                fear_level = player.fear_level
            if hasattr(player, 'anxiety_level'):
                anxiety_level = player.anxiety_level
            if hasattr(player, 'stress_level'):
                stress_level = player.stress_level
            if hasattr(player, 'location'):
                new_location = player.location
            
            # 生成行动结果图片（增强版，支持理智崩坏效果）
            image_generator = AsyncImageGenerator(self._temp_images_dir)
            display_name = player.name or user_name or "玩家"
            action_image = await image_generator.generate_action_result_image(
                user_name=display_name,
                action=rest_input,
                is_dead=(player.status != PlayerStatus.ALIVE),

                scene_description=result.description,
                action_feedback="",
                health=player.health,
                injury=injury,
                fatigue=fatigue,
                sanity=player.sanity,
                state=state_desc,
                emotion=emotion,
                fear_level=fear_level,
                anxiety_level=anxiety_level,
                stress_level=stress_level,
                found_items=result.found_items or [],
                found_clues=result.discovered_clues or [],
                new_location=new_location,
                random_event=random_event,
            )
            await self._send_image_path(action_image)
            
            # 如果玩家死亡
            if result.is_fatal or player.status != PlayerStatus.ALIVE:
                if player.sanity == 0:
                    await self.send_text("**……**\n\n你感到某种'秩序'正在接纳你。")
                else:
                    await self.send_text(
                        "**💀 你已死亡！**\n\n"
                        f"违反的规则：{result.violated_rule or '未知'}\n\n"
                    )
                
                # 检查是否所有玩家都已死亡
                all_dead = all(p.status != PlayerStatus.ALIVE for p in state.session.players.values())

                
                # 单人模式死亡，或多人模式所有玩家死亡：自动结束游戏
                if len(session.players) == 1 or all_dead:
                    if len(session.players) > 1 and all_dead:
                        await self.send_text("全员已死亡，正在判定【总结局】...")
                    else:
                        await self.send_text("正在判定结局...")
                    # 释放状态锁，避免死锁
                    if state:
                        state.release()
                        state = None
                    # 自动触发结局判定
                    return await self._handle_结束(group_id, user_id, user_name, "")

            
            # 更新环境演化系统（非阻塞，失败不影响主流程）
            try:
                session = state.session
                env_system = getattr(session, '_environment_system', None)
                if env_system and hasattr(env_system, 'update_environment'):
                    # 收集所有玩家的行动和位置
                    player_actions = [a.get("action", "") for a in player.action_history[-5:]]
                    player_locations = [p.location for p in session.players.values()]
                    
                    # 获取配置
                    config = self.plugin_config or {}
                    llm_config = config.get('llm', {})
                    api_url = llm_config.get('api_url', '')
                    api_key = llm_config.get('api_key', '')
                    model_list = llm_config.get('model_list', [])
                    temperature = llm_config.get('temperature', 0.8)
                    
                    # 异步更新环境（不等待结果，避免阻塞）
                    asyncio.create_task(
                        self._update_environment_async(
                            env_system, group_id, player_actions, player_locations,
                            api_url, api_key, model_list, 0, temperature
                        )
                    )
            except Exception as e:
                logger.warning(f"环境演化系统更新失败: {e}")
            
            # 保存状态
            save_manager = SaveManager()
            await save_manager.schedule_save(group_id, state.session)
            
            return True, "行动已执行", 2
            
        except Exception as e:
            logger.error(f"处理行动失败: {e}", exc_info=True)
            await self.send_text(f"处理行动时出错：{e}")
            return False, "处理失败", 2

        finally:
            if state:
                state.release()

    async def _handle_结束(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        """处理结束游戏命令

        约定：
        - 单人模式：生成该玩家结局。
        - 多人模式：任意玩家触发一次即可**结束整局**，只生成一次“总结局 + 真相”。
        """
        _ = rest_input  # 结束命令不需要额外参数
        state_manager = GameStateManager()
        state = await state_manager.get(group_id)

        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        def _build_aggregate_player(session: GameSession) -> Player:
            """构造一个用于多人总结局判定的“聚合玩家”。"""
            players = list(session.players.values())
            if not players:
                return Player(player_id="__group__", name="全体玩家")

            alive_any = any(p.status == PlayerStatus.ALIVE for p in players)
            n = max(1, len(players))

            # 合并行动（按时间排序，保留最近一段）
            merged_actions: list[JsonObject] = []
            for p in players:
                for a in (p.action_history or []):
                    if isinstance(a, dict):
                        act = str(a.get("action", "") or "").strip()
                        if act:
                            merged_actions.append({
                                "action": f"{p.name}: {act}",
                                "timestamp": str(a.get("timestamp", "") or ""),
                            })
            merged_actions.sort(key=lambda x: str(x.get("timestamp", "")))
            merged_actions = merged_actions[-25:]

            # 合并推理（保留最近一段）
            merged_reasoning: list[str] = []
            for p in sorted(players, key=lambda x: x.joined_at):
                for r in (p.reasoning_history or [])[-8:]:
                    rr = str(r or "").strip()
                    if rr:
                        merged_reasoning.append(f"{p.name}: {rr}")
            merged_reasoning = merged_reasoning[-25:]

            # 线索/物品：EndingJudge 只识别 type=="clue"，这里做一次兼容
            merged_inventory: list[JsonObject] = []
            for p in players:
                for item in (p.inventory or []):
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name", "") or "").strip()
                    itype = str(item.get("type", "") or "").strip()
                    if not name:
                        continue
                    if itype in {"clue", "线索", "Clue", "线索物品"}:
                        merged_inventory.append({"name": name, "type": "clue"})

            agg = Player(player_id="__group__", name="全体玩家")
            agg.status = PlayerStatus.ALIVE if alive_any else PlayerStatus.DEAD
            agg.health = int(sum(max(0, int(p.health)) for p in players) / n)
            agg.sanity = int(sum(max(0, int(p.sanity)) for p in players) / n)
            agg.fear_level = int(sum(max(0, int(getattr(p, "fear_level", 0))) for p in players) / n)
            agg.anxiety_level = int(sum(max(0, int(getattr(p, "anxiety_level", 0))) for p in players) / n)
            agg.stress_level = int(sum(max(0, int(getattr(p, "stress_level", 0))) for p in players) / n)
            agg.fatigue = int(sum(max(0, int(getattr(p, "fatigue", 0))) for p in players) / n)
            agg.location = " / ".join(sorted({str(getattr(p, "location", "") or "") for p in players if getattr(p, "location", None)})) or (session.scene_name or "未知")
            agg.inventory = merged_inventory
            agg.reasoning_history = merged_reasoning
            agg.action_history = merged_actions
            return agg

        try:
            session = state.session

            # 已结束则不再重复生成结局
            if session.status == GameStatus.ENDED:
                await self.send_text("游戏已经结束。请使用 `/rg 开始` 开始新游戏。")
                return False, "已结束", 2

            caller = session.players.get(user_id)
            if not caller:
                await self.send_text("你不在游戏中。")
                return False, "不在游戏中", 2

            is_multi = (session.game_mode == GameModes.MULTI.value and len(session.players) > 1)

            display_name = caller.name or user_name or "玩家"
            if is_multi:
                await self.send_text("正在判定【总结局】...")
                ending = await self._ending_judge.judge_group_ending(session=session)
            else:
                await self.send_text(f"{display_name}，正在判定结局...")
                ending = await self._ending_judge.judge_ending(
                    session=session,
                    player=caller,
                )


            # 更新会话状态
            session.status = GameStatus.ENDED
            session.ended_at = datetime.now()

            # 生成结局图片
            ending_type = str(getattr(ending, "ending_type", "") or "")

            # 强制结束（未通关时主动结束）：隐藏推理分析，但始终显示真相
            forced_end = (not session.has_cleared)
            hide_reasoning = forced_end
            reasoning_analysis = "" if hide_reasoning else str(getattr(ending, "reasoning_analysis", "") or "")

            # 多人模式：附加玩家结局概览（不算“逐人结算”，只是总览）
            if is_multi and not hide_reasoning:
                parts = ["【玩家结局概览】"]
                for p in sorted(session.players.values(), key=lambda x: x.joined_at):
                    st = "存活" if p.status == PlayerStatus.ALIVE else "死亡"
                    parts.append(f"- {p.name}: {st}（理智{p.sanity}/体力{p.health}）")
                overview = "\n".join(parts)
                reasoning_analysis = (reasoning_analysis + "\n\n" + overview).strip() if reasoning_analysis else overview

            truth_revealed = True
            hidden_truth = (f"真相：{session.hidden_truth}" if session.hidden_truth else "真相：未知")

            ending_title = str(getattr(ending, "title", "") or "未知结局")

            image_generator = AsyncImageGenerator(self._temp_images_dir)

            ending_image = await image_generator.generate_ending_image(
                ending_title=ending_title,
                ending_description=str(getattr(ending, "description", "") or ""),
                reasoning_analysis=reasoning_analysis,
                truth_revealed=truth_revealed,
                hidden_truth=hidden_truth,
                ending_type=ending_type,
            )
            await self._send_image_path(ending_image)

            # 清理状态与存档（结束整局）
            await state_manager.remove(group_id)
            save_manager = SaveManager()
            await save_manager.delete(group_id)

            logger.info(f"游戏结束: {group_id}, 结局: {ending.ending_type}")
            return True, "游戏已结束", 2

        except Exception as e:
            logger.error(f"判定结局失败: {e}", exc_info=True)
            await self.send_text(f"判定结局时出错：{e}")
            return False, "判定失败", 2
        finally:
            if state:
                state.release()


    async def _handle_帮助(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        """处理帮助命令"""
        _ = group_id
        _ = user_id
        _ = rest_input
        display_name = user_name or "玩家"
        help_text = (
            f"**{display_name}，欢迎使用规则怪谈游戏帮助**\n\n"
            "**命令列表**\n"
            "- `/rg 开始 单人` - 生成并开始单人游戏（自动加入）\n"
            "- `/rg 开始 多人` - 创建多人大厅（房主自动加入）\n"
            "- `/rg 开始 多人 开始` - 人数到齐后生成并开始多人游戏\n"
            "- `/rg 强制开始 单人/多人` - 覆盖存档并强制开始\n"
            "- `/rg 恢复` - 恢复默认存档\n"
            "- `/rg 保存 <存档名称>` - 手动保存当前游戏状态\n"
            "- `/rg 读取 <存档名称>` - 读取指定命名存档\n"
            "- `/rg 存档列表` - 查看当前群组/用户的存档\n"
            "- `/rg 清理存档` - 清理已结束的存档与过期图片缓存\n"
            "- `/rg 加入` - 加入当前游戏（多人模式）\n"
            "- `/rg 身份` - 查看你的身份信息（多人模式，私聊失败时发送到群聊）\n"
            "- `/rg 离开` - 离开当前游戏\n"
            "- `/rg 状态` - 查看游戏状态和玩家信息\n"
            "- `/rg 剧情` - 查看剧情导入（重新发送入场/导入图）\n"
            "- `/rg 规则` - 查看当前规则\n"
            "- `/rg 场景` - 查看场景结构\n"
            "- `/rg 道具 [道具名称]` - 查看道具列表或道具详情\n"
            "- `/rg 线索 [线索名称]` - 查看已知线索列表或详情\n"
            "- `/rg 提示 <规则/线索>` - 获取提示（默认3次）\n"
            "- `/rg 推理 <推理内容>` - 记录你的推理\n"
            "- `/rg 行动 <行动描述>` - 描述你的行动\n"
            "- `/rg 继续` - 达成通关后继续探索（追求完美结局）\n"
            "- `/rg 结束` - 结束游戏并判定结局\n"
            "- `/rg 帮助` - 查看帮助\n\n"
            "**提示**\n"
            "- 为避免误触发与剧透，建议用 `/rg 行动 <行动描述>` 推进游戏。"
        )
        await self.send_text(help_text)
        return True, "帮助已发送", 2

    async def _handle_身份(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        """处理查看身份命令（多人模式主动拉取身份信息）"""
        _ = rest_input

        state_manager = GameStateManager()
        state = await state_manager.get(group_id)

        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            session = state.session

            # 检查是否为多人模式
            if session.game_mode != GameModes.MULTI.value:
                await self.send_text("当前不是多人模式游戏，没有身份分配。")
                return False, "非多人模式", 2

            # 检查玩家是否在游戏中
            player = session.players.get(user_id)
            if not player:
                await self.send_text("你还没有加入游戏。请使用 `/rg 加入` 加入游戏。")
                return False, "未加入", 2

            # 检查玩家是否有身份信息
            if not player.identity:
                await self.send_text("你还没有被分配身份。请等待游戏开始后再查看。")
                return False, "无身份", 2

            # 构建身份信息文本
            content = self._build_player_private_brief(session, player)

            # 尝试私聊发送
            ok = await self._send_private_text(user_id, user_name, content)
            
            if ok:
                await self.send_text(f"{user_name}，你的身份信息已通过私聊发送，请查看。")
            else:
                # 私聊失败，发送到群聊兜底
                await self.send_text(
                    f"【@{user_name} 的身份信息】（私聊发送失败）\n\n{content}\n\n---\n请妥善保管你的身份信息，不要向其他玩家透露！"
                )

            return True, "身份已发送", 2

        except Exception as e:
            logger.error(f"查看身份失败: {e}")
            await self.send_text("查看身份时发生错误，请稍后重试。")
            return False, "错误", 2
        finally:
            if state:
                state.release()

    async def _handle_剧情(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        """处理查看剧情导入命令（重发导入/入场信息）"""
        _ = rest_input
        _ = user_name

        state_manager = GameStateManager()
        state = await state_manager.get(group_id)

        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            session = state.session
            image_generator = AsyncImageGenerator(self._temp_images_dir)

            # ① 剧情导入图（缓存）
            core_symbols = getattr(session, 'core_symbols', None)
            scene_image = await image_generator.generate_scene_image(
                scene_name=session.scene_name,
                background=session.background,
                arrival_reason=session.player_identity,
                core_symbols=core_symbols,
                use_cache=True,
            )
            await self._send_image_path(scene_image)

            # ② 入场长图（如果可用）
            entrance_description = None
            if session.environment_state and isinstance(session.environment_state, dict):
                entrance_description = session.environment_state.get("entrance_description")

            npc_guidance = getattr(session, 'npc_guidance', {}) or {}
            if entrance_description and npc_guidance:
                entrance_long_image = await image_generator.generate_entrance_long_image(
                    scene_name=session.scene_name,
                    entrance_description=str(entrance_description),
                    npc_guidance=npc_guidance,
                    use_cache=True,
                )
                await self._send_image_path(entrance_long_image)

            return True, "剧情已显示", 2

        except Exception as e:
            logger.error(f"显示剧情失败: {e}", exc_info=True)
            await self.send_text(f"显示剧情时出错：{e}")
            return False, "显示失败", 2

        finally:
            if state:
                state.release()

    async def _handle_道具(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        """处理查看道具命令（物品栏别名，支持详情）"""
        return await self._handle_物品栏(group_id, user_id, user_name, rest_input)

    async def _handle_清理存档(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        """处理清理存档命令：清理已结束存档 + 过期图片缓存"""
        _ = user_id
        _ = user_name
        _ = rest_input

        save_manager = SaveManager()
        try:
            cleaned_saves = await save_manager.cleanup_ended_saves(group_id)
        except Exception as e:
            logger.error(f"清理已结束存档失败: {e}", exc_info=True)
            cleaned_saves = 0

        # 清理图片缓存：只删除过期文件，避免误删仍在使用的缓存
        cleaned_images = 0
        try:
            import time
            from pathlib import Path

            now = time.time()
            max_age_days = 30
            cutoff = now - max_age_days * 86400

            temp_dir = Path(self._temp_images_dir)
            if temp_dir.exists():
                for p in temp_dir.rglob("*"):
                    if not p.is_file():
                        continue
                    # 保留缓存索引
                    if p.name == "cache_index.json":
                        continue
                    try:
                        if p.stat().st_mtime < cutoff:
                            p.unlink()
                            cleaned_images += 1
                    except Exception:
                        continue
        except Exception as e:
            logger.error(f"清理图片缓存失败: {e}", exc_info=True)

        await self.send_text(
            "**清理完成**\n\n"
            f"- 已清理已结束存档：{cleaned_saves} 个\n"
            f"- 已清理过期图片缓存：{cleaned_images} 个（>30天）"
        )
        return True, "清理完成", 2

    async def _handle_场景(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        """处理查看场景命令"""
        state_manager = GameStateManager()
        state = await state_manager.get(group_id)

        try:
            if not state or not state.session:
                raise GameNotFoundError("当前没有正在进行的游戏")

            session = state.session

            # 检查用户是否有权限查看场景（必须是游戏中的玩家）
            if user_id not in session.players:
                await self.send_text(
                    f"{user_name}，你无法查看场景。\n"
                    "只有当前游戏的参与者才能查看场景信息。"
                )
                return False, "无权限", 2

            player = session.players[user_id]
            scene_structure = getattr(session, 'scene_structure', {}) or {}

            if not scene_structure:
                await self.send_text("场景结构尚未生成。")
                return False, "无场景结构", 2

            # 构建场景结构文本
            scene_text = [f"**{user_name} 的场景探索 - {session.scene_name}**\n"]
            scene_text.append(f"当前位置：{player.location}\n")
            
            building_type = scene_structure.get('building_type', '未知建筑')
            scene_text.append(f"**建筑类型**: {building_type}\n")
            
            overall_layout = scene_structure.get('overall_layout', '未知布局')
            scene_text.append(f"**总体布局**: {overall_layout}\n")
            
            floors = scene_structure.get('floors', [])
            if floors:
                scene_text.append("**楼层布局**:")
                for floor in floors:
                    # 兼容旧结构：{'floor': '一层', 'areas': [...]}
                    floor_name = floor.get('floor') or floor.get('name') or '未知楼层'
                    rooms = floor.get('areas') or floor.get('rooms') or []
                    scene_text.append(f"\n{floor_name}:")
                    for room in rooms:
                        scene_text.append(f"  - {room}")

            
            connections = scene_structure.get('connections', [])
            if connections:
                scene_text.append("\n**连接通道**:")
                for conn in connections:
                    scene_text.append(f"  - {conn}")
            
            special_areas = scene_structure.get('special_areas', [])
            if special_areas:
                scene_text.append("\n**特殊区域**:")
                for area in special_areas:
                    scene_text.append(f"  - {area}")
            
            await self.send_text("\n".join(scene_text))
            return True, "场景已显示", 2

        except GameNotFoundError as e:
            await self.send_text(str(e))
            return False, "无游戏", 2
        finally:
            if state:
                state.release()

    async def _handle_物品栏(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        """处理查看物品栏命令"""
        state_manager = GameStateManager()
        state = await state_manager.get(group_id)

        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            player = state.session.players.get(user_id)
            if not player:
                await self.send_text("你不在游戏中。")
                return False, "不在游戏中", 2

            inventory = getattr(player, 'inventory', [])

            def _is_clue_item(it: JsonObject) -> bool:
                # 线索默认会以 type="clue" 写入背包；也兼容中文/其他写法
                t = str(it.get("type", "") or "").strip().lower()
                if t in {"clue", "clues", "线索"}:
                    return True
                if "clue" in t or "线索" in t:
                    return True
                return False

            items: list[JsonObject] = []
            clues: list[JsonObject] = []
            for it in inventory:
                if not isinstance(it, dict):
                    continue
                (clues if _is_clue_item(it) else items).append(it)

            if not items:
                # “道具/物品栏”默认只展示可用道具，线索单独用 /rg 线索 查看
                if clues:
                    await self.send_text("**道具**\n\n你目前没有可用道具（背包里只有线索）。\n\n使用 `/rg 线索` 查看已知线索。")
                else:
                    await self.send_text("**道具**\n\n你的背包是空的。")
                return True, "道具已显示", 2


            query = (rest_input or "").strip()
            if query:
                # 详情模式：根据名称模糊匹配
                matches: list[JsonObject] = []

                # 详情模式：只在“道具/物品”里查，不把线索混进来
                for it in items:
                    name = str(it.get('name', ''))
                    if query in name:
                        matches.append(it)


                if not matches:
                    # 如果只在“线索”里找得到，明确告诉玩家去用 /rg 线索
                    clue_names = [str(c.get('name', '')) for c in clues if isinstance(c, dict)]
                    if any(query in n for n in clue_names if n):
                        await self.send_text(f"`{query}` 看起来是一条线索，不是道具。\n\n使用 `/rg 线索` 查看已知线索。")
                        return False, "这是线索", 2

                    await self.send_text(f"未找到道具：{query}\n\n你可以使用 `/rg 道具` 查看道具列表，或用 `/rg 线索` 查看线索。")
                    return False, "未找到道具", 2


                if len(matches) > 3:
                    await self.send_text(f"匹配到多个道具（{len(matches)}个），请提供更精确的名称。")
                    return False, "匹配过多", 2

                lines = [f"**道具详情**\n"]
                for it in matches:
                    name = it.get('name', '未知')
                    item_type = it.get('type', '物品')
                    desc = it.get('description', '')
                    hint = it.get('observation_hint', '')
                    is_key = it.get('is_key_item', False)

                    lines.append(f"- 名称：{name}{'（关键物品）' if is_key else ''}")
                    lines.append(f"  类型：{item_type}")
                    if desc:
                        lines.append(f"  描述：{desc}")
                    if hint:
                        lines.append(f"  观察提示：{hint}")
                    lines.append("")

                await self.send_text("\n".join(lines).strip())
                return True, "道具详情已显示", 2

            # 列表模式：优先发送道具清单图片（失败则降级文本）
            try:
                image_generator = AsyncImageGenerator(self._temp_images_dir)
                inventory_image = await image_generator.generate_inventory_image(
                    inventory_data=items,
                    player_name=user_name,
                    title="道具",
                    use_cache=True,
                )

                await self._send_image_path(inventory_image)
            except Exception as e:
                logger.debug(f"生成或发送道具图片失败，回退到文本: {e}")

            items_text = [f"**{user_name} 的道具**\n"]
            for i, item in enumerate(items, 1):

                if isinstance(item, dict):
                    item_name = item.get('name', '未知物品')
                    item_desc = item.get('description', '')
                    is_key = item.get('is_key_item', False)

                    key_marker = " 🔑" if is_key else ""
                    items_text.append(f"{i}. {item_name}{key_marker}")
                    if item_desc:
                        items_text.append(f"   {item_desc}")
                else:
                    items_text.append(f"{i}. {item}")

            items_text.append("\n使用 `/rg 道具 <名称>` 查看详情。")
            items_text.append("使用 `/rg 线索` 查看已知线索。")

            await self.send_text("\n".join(items_text))
            return True, "物品栏已显示", 2

            
        finally:
            if state:
                state.release()

    async def _handle_线索(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        """处理查看已知线索命令（与道具分离）"""
        state_manager = GameStateManager()
        state = await state_manager.get(group_id)

        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            player = state.session.players.get(user_id)
            if not player:
                await self.send_text("你不在游戏中。")
                return False, "不在游戏中", 2

            inventory = getattr(player, 'inventory', [])

            def _is_clue_item(it: JsonObject) -> bool:
                t = str(it.get("type", "") or "").strip().lower()
                if t in {"clue", "clues", "线索"}:
                    return True
                if "clue" in t or "线索" in t:
                    return True
                return False

            clues: list[JsonObject] = [it for it in inventory if isinstance(it, dict) and _is_clue_item(it)]

            if not clues:
                await self.send_text("**已知线索**\n\n你目前还没有发现任何线索。")
                return True, "线索已显示", 2

            query = (rest_input or "").strip()
            if query:
                matches: list[JsonObject] = []
                for it in clues:
                    name = str(it.get('name', ''))
                    if query in name:
                        matches.append(it)

                if not matches:
                    await self.send_text(f"未找到线索：{query}\n\n你可以使用 `/rg 线索` 查看线索列表。")
                    return False, "未找到线索", 2

                if len(matches) > 5:
                    await self.send_text(f"匹配到多个线索（{len(matches)}条），请提供更精确的名称。")
                    return False, "匹配过多", 2

                lines = ["**线索详情**\n"]
                for it in matches:
                    name = it.get('name', '未知')
                    desc = it.get('description', '')
                    lines.append(f"- {name}")
                    if desc:
                        lines.append(f"  {desc}")
                    lines.append("")

                await self.send_text("\n".join(lines).strip())
                return True, "线索详情已显示", 2

            # 列表模式：优先发送线索清单图片（失败则降级文本）
            try:
                image_generator = AsyncImageGenerator(self._temp_images_dir)
                clue_image = await image_generator.generate_inventory_image(
                    inventory_data=clues,
                    player_name=user_name,
                    title="已知线索",
                    use_cache=True,
                )
                await self._send_image_path(clue_image)
            except Exception as e:
                logger.debug(f"生成或发送线索图片失败，回退到文本: {e}")

            lines = [f"**{user_name} 的已知线索**\n"]
            for i, it in enumerate(clues, 1):
                name = it.get('name', '未知线索')
                desc = it.get('description', '')
                lines.append(f"{i}. {name}")
                if desc:
                    lines.append(f"   {desc}")
            lines.append("\n使用 `/rg 线索 <名称>` 查看详情。")
            await self.send_text("\n".join(lines))
            return True, "线索已显示", 2

        finally:
            if state:
                state.release()

    async def _handle_背包(

        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        """处理查看背包命令（物品栏别名）"""
        return await self._handle_物品栏(group_id, user_id, user_name, rest_input)

    async def _handle_继续(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        """处理继续探索命令"""
        _ = rest_input  # 继续命令不需要额外参数
        state_manager = GameStateManager()
        state = await state_manager.get(group_id)

        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            session = state.session
            player = session.players.get(user_id)
            if not player:
                await self.send_text("你不在游戏中。")
                return False, "不在游戏中", 2

            if not session.has_cleared:
                await self.send_text(f"{player.name}，你还未达成通关条件！请继续探索。")
                return False, "未通关", 2
            
            await self.send_text(
                f"**{player.name} 继续探索**\n\n"
                "你已达成通关条件，但仍可以继续探索以寻找完美结局。\n"
                "使用 `/rg 行动 <行动描述>` 继续你的探索。"
            )
            return True, "继续探索", 2
            
        finally:
            if state:
                state.release()

    async def _handle_强制开始(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        """处理强制开始命令"""
        raw = (rest_input or "").strip()

        game_mode = GameModes.SINGLE.value
        multi_target: int | None = None

        if raw:
            modes_pattern = rf"^({GameModes.SINGLE.value}|{GameModes.MULTI.value})\s*(.*)$"
            m = re.match(modes_pattern, raw)
            if m:
                game_mode = m.group(1)
                tail = (m.group(2) or "").strip()
            else:
                game_mode = raw
                tail = ""

            if game_mode == GameModes.MULTI.value and tail:
                m2 = re.search(r"(\d{1,2})", tail)
                if m2:
                    try:
                        n = int(m2.group(1))
                        if 2 <= n <= 4:
                            multi_target = n
                    except Exception:
                        pass

        if game_mode not in [GameModes.SINGLE.value, GameModes.MULTI.value]:
            await self.send_text("请指定游戏模式：`/rg 强制开始 单人` 或 `/rg 强制开始 多人`")
            return False, "缺少游戏模式", 2


        # 清理现有状态
        state_manager = GameStateManager()
        await state_manager.remove(group_id)

        # 标记现有存档为结束并清理相关图片
        save_manager = SaveManager()
        await save_manager.mark_ended_and_cleanup(group_id)

        # 删除现有存档
        await save_manager.delete(group_id)

        # 多人模式：强制开始只负责“清档并创建大厅”，避免先生成再加人
        if game_mode == GameModes.MULTI.value:
            state = await state_manager.get_or_create(group_id)
            try:
                lobby = GameSession(group_id=group_id, game_mode=GameModes.MULTI.value, status=GameStatus.WAITING)
                lobby.environment_state = {
                    "lobby": {
                        "host_id": user_id,
                        "host_name": user_name,
                        "target_players": multi_target,
                        "created_at": datetime.now().isoformat(),
                    },
                    "lobby_player_order": [user_id],
                }
                lobby.add_player(Player(player_id=user_id, name=user_name))
                state.session = lobby
                await save_manager.save_immediately(group_id, lobby)
            finally:
                state.release()


            target_txt = f"{multi_target}人" if multi_target else "未指定人数"
            await self.send_text(
                "**多人模式大厅已创建**\n\n"
                f"房主：{user_name}\n"
                f"目标人数：{target_txt}\n"
                "当前人数：1\n\n"
                "其他玩家请发送 `/rg 加入` 加入。\n"
                "房主在人数到齐后发送 `/rg 开始 多人 开始` 生成开局。"
            )
            return True, "大厅已创建", 2

        await self.send_text("正在生成规则怪谈，请稍候..")

        try:
            # 生成游戏
            session = await self._get_game_generator().generate_game(group_id, game_mode)
            session.status = GameStatus.ACTIVE

            # 单人模式自动添加玩家
            if game_mode == GameModes.SINGLE.value:
                player = Player(player_id=user_id, name=user_name)
                session.add_player(player)

            
            # 保存到状态管理器
            state = await state_manager.get_or_create(group_id)
            try:
                state.session = session
                
                # 保存存档
                await save_manager.save_immediately(group_id, session)
            finally:
                state.release()
            
            # 生成图片（强制开始不使用缓存）
            image_generator = AsyncImageGenerator(self._temp_images_dir)
            
            # 获取核心象征符号（如果有）
            core_symbols = getattr(session, 'core_symbols', None)
            
            # ① 生成并发送剧情导入图片
            scene_image = await image_generator.generate_scene_image(
                scene_name=session.scene_name,
                background=session.background,
                arrival_reason=session.player_identity,
                core_symbols=core_symbols,
                use_cache=False,  # 强制开始不使用缓存
            )
            await self._send_image_path(scene_image)
            await asyncio.sleep(1.0)  # 间隔1秒
            
            # ② 生成入场描述
            entrance_description = await self._generate_entrance_description(session)
            # 记录入场描述，供 `/rg 剧情` 重发（尽量避免重复LLM调用）
            if isinstance(getattr(session, 'environment_state', None), dict):
                session.environment_state["entrance_description"] = entrance_description
            
            # ② 生成并发送入场长图（入场+NPC引导）
            npc_guidance = getattr(session, 'npc_guidance', {}) or {}

            # 初始化 NPC 与“已知规则”状态：让后续行动可以基于态度/记忆动态演化，而不是硬编码结论
            if isinstance(getattr(session, 'environment_state', None), dict) and npc_guidance:
                env_state = session.environment_state
                env_state.setdefault("npcs", [])
                env_state.setdefault("known_rule_indices", [])

                npc_name = npc_guidance.get("npc_name", "NPC")

                scene_structure = getattr(session, "scene_structure", {}) or {}
                areas: list[str] = []
                for fl in scene_structure.get("floors", []) or []:
                    if isinstance(fl, dict):
                        areas.extend([str(x) for x in (fl.get("areas") or fl.get("rooms") or [])])
                areas.extend([str(x) for x in (scene_structure.get("special_areas") or [])])

                prefer = ["柜台", "收银", "前台", "服务台", "接待", "值班室", "大厅", "入口", "门口"]
                npc_location = None
                for kw in prefer:
                    hit = next((a for a in areas if kw in a), None)
                    if hit:
                        npc_location = hit
                        break
                npc_location = npc_location or (areas[0] if areas else session.scene_name or "起始位置")

                if not env_state.get("npcs"):
                    memory = NPCMemory()
                    if game_mode == GameModes.SINGLE.value and user_id:
                        memory.initialize_attitude_vector(user_id)

                        att = str(npc_guidance.get("npc_attitude", "") or "")
                        if any(k in att for k in ["友好", "温和", "热情"]):
                            memory.update_attitude_vector(user_id, affection_delta=10, trust_delta=10)
                        elif any(k in att for k in ["警告", "严厉", "冷淡", "不耐烦"]):
                            memory.update_attitude_vector(user_id, suspicion_delta=15, trust_delta=-5)
                        elif any(k in att for k in ["敌对", "威胁"]):
                            memory.update_attitude_vector(user_id, hostility_delta=25, trust_delta=-15)

                    env_state["npcs"] = [
                        {
                            "npc_id": "guide_0",
                            "name": npc_name,
                            "role": npc_guidance.get("npc_role", ""),
                            "personality": "",
                            "danger_level": "低",
                            "home_location": npc_location,
                            "current_location": npc_location,
                            "can_speak": True,
                            "memory": memory.to_dict(),
                        }
                    ]

                env_state["npcs_present"] = [
                    {
                        "name": npc_name,
                        "role": npc_guidance.get("npc_role", ""),
                        "attitude": npc_guidance.get("npc_attitude", ""),
                        "location": npc_location,
                    }
                ]


            if npc_guidance:
                entrance_long_image = await image_generator.generate_entrance_long_image(
                    scene_name=session.scene_name,
                    entrance_description=entrance_description,
                    npc_guidance=npc_guidance,
                    use_cache=False,  # 强制开始不使用缓存
                )
                await self._send_image_path(entrance_long_image)
                await asyncio.sleep(1.0)  # 间隔1秒
            
            # ③ 生成并发送规则图片
            guidance_method = npc_guidance.get("guidance_method", "rule_carrier") if npc_guidance else "rule_carrier"

            # “规则图”展示的是玩家当前已获得的信息（已知规则），而不是后台完整规则。
            env_state = session.environment_state if isinstance(getattr(session, "environment_state", None), dict) else {}

            def _normalize_all_rules() -> list[RuleDict]:
                return self._normalize_rules_list(session.rules)


            if guidance_method == "natural_language" and npc_guidance:
                npc_name = npc_guidance.get("npc_name", "NPC")
                npc_attitude = str(npc_guidance.get("npc_attitude", "警告") or "").strip()

                def _title_att(att: str) -> str:
                    att = str(att or "").strip()
                    for sep in ["且", "并", "但是", "而且", "同时", "，", ",", "。", "；", ";", "、", "/"]:
                        if sep in att:
                            att = att.split(sep, 1)[0].strip()
                    allow = {"警告", "提醒", "告诫", "忠告", "提示", "指示", "劝告"}
                    return att if att in allow else (att if len(att) <= 4 and att else "提醒")

                rules_title = f"{npc_name}的{_title_att(npc_attitude)}"

                npc_dialogue = str(npc_guidance.get("npc_dialogue", "") or "")
                display_rules = await self._extract_rules_from_dialogue(npc_dialogue, _normalize_all_rules(), npc_name)

                known = [int(r.get("original_index")) for r in display_rules if isinstance(r, dict) and isinstance(r.get("original_index"), int)]
                env_state["known_rule_indices"] = sorted(set(known))

                extra_texts = [str(r.get("text", "")).strip() for r in display_rules if isinstance(r, dict) and not isinstance(r.get("original_index"), int)]
                if extra_texts:
                    env_state["known_rule_texts_extra"] = sorted(set([x for x in extra_texts if x]))
                else:
                    env_state.pop("known_rule_texts_extra", None)
            else:
                rules_title = npc_guidance.get("rule_carrier_title", f"{session.scene_name} - 规则") if npc_guidance else f"{session.scene_name} - 规则"
                display_rules = _normalize_all_rules()
                env_state["known_rule_indices"] = [int(r.get("original_index", 0)) for r in display_rules]
                env_state.pop("known_rule_texts_extra", None)

            
            if isinstance(env_state, dict) and isinstance(env_state.get("known_rule_texts_extra"), list):
                extra = [str(x).strip() for x in env_state.get("known_rule_texts_extra", []) if str(x).strip()]
                if extra:
                    has_extra_in_display = any(
                        isinstance(r, dict) and not isinstance(r.get("original_index"), int)
                        for r in (display_rules or [])
                    )
                    if not has_extra_in_display:
                        display_rules = list(display_rules) + [{"text": t, "original_index": None, "source": "npc_dialogue"} for t in extra]

            def _norm_rule_text(t: str) -> str:
                t = re.sub(r"\s+", "", str(t or ""))
                t = re.sub(r"[，,。.!！？?；;:“”\"'‘’《》【】\[\]（）()\-—…·]", "", t)
                return t

            dedup: list[RuleDict] = []
            seen: dict[str, int] = {}

            for r in (display_rules or []):
                if not isinstance(r, dict):
                    r = {"text": str(r)}
                txt = str(r.get("text", r.get("content", str(r))) or "").strip()
                if not txt:
                    continue
                key = _norm_rule_text(txt)
                if not key:
                    continue
                if key in seen:
                    pi = seen[key]
                    prev = dedup[pi]
                    if not isinstance(prev.get("original_index"), int) and isinstance(r.get("original_index"), int):
                        dedup[pi] = r
                    continue
                seen[key] = len(dedup)
                dedup.append(r)
            display_rules = dedup

            if game_mode != GameModes.MULTI.value:
                rules_image = await image_generator.generate_rules_image(
                    rules_title=rules_title,
                    rules=display_rules,
                    win_condition=session.win_condition,
                    game_mode=game_mode,
                    use_cache=False,  # 强制开始不使用缓存
                )
                await self._send_image_path(rules_image)
                await asyncio.sleep(1.0)  # 间隔1秒

            
            # ④ 生成并发送场景结构文字长图
            if game_mode != GameModes.MULTI.value:
                scene_structure = getattr(session, 'scene_structure', {}) or {}
                if scene_structure:
                    building_type = scene_structure.get('building_type', '未知建筑')
                    overall_layout = scene_structure.get('overall_layout', '未知布局')
                    floors = scene_structure.get('floors', [])
                    connections = scene_structure.get('connections', [])
                    special_areas = scene_structure.get('special_areas', [])
                    
                    scene_structure_image = await image_generator.generate_scene_structure_text_image(
                        building_type=building_type,
                        overall_layout=overall_layout,
                        floors=floors,
                        connections=connections,
                        special_areas=special_areas,
                        use_cache=False,  # 强制开始不使用缓存
                    )
                    await self._send_image_path(scene_structure_image)
                    await asyncio.sleep(0.5)  # 最后一张可以稍短

            
            # 发送文字说明
            if game_mode == GameModes.MULTI.value:
                players_disp = "、".join([p.name for p in session.players.values()]) if session.players else "（无）"
                await self.send_text(
                    f"**游戏已开始！**\n\n"
                    f"模式：{game_mode}\n"
                    f"场景：{session.scene_name}\n"
                    f"玩家：{players_disp}\n\n"
                    f"使用 `/rg 行动 <行动描述>` 进行行动。"
                )
            else:
                await self.send_text(
                    f"**游戏已开始！**\n\n"
                    f"模式：{game_mode}\n"
                    f"场景：{session.scene_name}\n\n"
                    f"使用 `/rg 行动 <行动描述>` 进行行动。\n"
                    f"使用 `/rg 推理 <推理内容>` 记录推理。"
                )


            
            logger.info(f"强制开始游戏成功: {group_id}, 模式: {game_mode}")
            return True, "游戏已开始", 2
            
        except Exception as e:
            logger.error(f"强制开始游戏失败: {e}", exc_info=True)
            await self.send_text(f"生成游戏失败：{e}\n请稍后重试。")
            return False, "生成失败", 2

    async def _handle_恢复(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        """处理恢复存档命令"""
        _ = rest_input  # 恢复命令不需要额外参数
        save_manager = SaveManager()
        
        try:
            session = await save_manager.load(group_id)
            
            if not session:
                await self.send_text("未找到存档。请使用 `/rg 开始` 开始新游戏。")
                return False, "无存档", 2
            
            # 检查玩家是否在游戏中（多人模式）
            if session.game_mode == GameModes.MULTI.value:
                player = session.players.get(user_id)
                if not player:
                    await self.send_text(f"{user_name}，你没有参与这个游戏。无法恢复。")
                    return False, "不在游戏中", 2
            
            if session.status == GameStatus.ENDED:
                await self.send_text("该存档已结束。请使用 `/rg 开始` 开始新游戏。")
                return False, "存档已结束", 2
            
            # 恢复到状态管理器
            state_manager = GameStateManager()
            state = await state_manager.get_or_create(group_id)
            try:
                state.session = session
            finally:
                state.release()
            
            # 使用用户名称个性化消息
            display_name = user_name or "玩家"
            await self.send_text(
                f"**存档已恢复**\n\n"
                f"欢迎回来，{display_name}！\n"
                f"场景：{session.scene_name}\n"
                f"模式：{session.game_mode}\n"
                f"玩家数：{len(session.players)}\n\n"
                f"使用 `/rg 状态` 查看详细信息。"
            )
            return True, "存档已恢复", 2
            
        except Exception as e:
            logger.error(f"恢复存档失败: {e}", exc_info=True)
            await self.send_text(f"恢复存档时出错：{e}")
            return False, "恢复失败", 2

    async def _handle_保存(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        """处理手动保存命令"""
        if not rest_input:
            await self.send_text("请提供存档名称。用法：`/rg 保存 <存档名称>`")
            return False, "缺少存档名称", 2

        state_manager = GameStateManager()
        state = await state_manager.get(group_id)

        try:
            if not state or not state.session:
                raise GameNotFoundError("当前没有正在进行的游戏")

            # 检查用户是否有权限保存存档（必须是游戏中的玩家）
            if user_id not in state.session.players:
                await self.send_text(
                    f"{user_name}，你无法保存存档。\n"
                    "只有当前游戏的参与者才能保存存档。"
                )
                return False, "无权限", 2

            save_name = rest_input.strip()
            save_manager = SaveManager()

            ok = await save_manager.save_with_name(group_id, state.session, save_name)
            if not ok:
                await self.send_text("存档保存失败，请稍后重试。")
                return False, "保存失败", 2

            await self.send_text(f"**{user_name}，存档已保存**\n\n存档名称：{save_name}")
            return True, "存档已保存", 2
        except GameNotFoundError as e:
            await self.send_text(str(e))
            return False, "无游戏", 2

        except Exception as e:
            logger.error(f"保存存档失败: {e}", exc_info=True)
            await self.send_text(f"保存存档时出错：{e}")
            return False, "保存失败", 2
        finally:
            if state:
                state.release()


    async def _handle_读取(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        """处理读取存档命令"""
        if not rest_input:
            await self.send_text("请提供存档名称。用法：`/rg 读取 <存档名称>`")
            return False, "缺少存档名称", 2

        save_name = rest_input.strip()
        save_manager = SaveManager()

        try:
            session = await save_manager.load_with_name(group_id, save_name)

            if not session:
                await self.send_text(f"未找到存档：{save_name}")
                return False, "无存档", 2

            if session.status == GameStatus.ENDED:
                await self.send_text(f"存档 {save_name} 已结束。")
                return False, "存档已结束", 2

            # 检查用户是否有权限读取该存档（必须是存档中的玩家）
            if user_id not in session.players:
                await self.send_text(
                    f"{user_name}，你无法读取存档 {save_name}。\n"
                    "只有该存档的参与者才能读取此存档。"
                )
                return False, "无权限", 2

            # 恢复到状态管理器
            state_manager = GameStateManager()
            state = await state_manager.get_or_create(group_id)
            try:
                state.session = session
            finally:
                state.release()

            await self.send_text(
                f"**{user_name}，存档已读取**\n\n"
                f"存档名称：{save_name}\n"
                f"场景：{session.scene_name}\n"
                f"模式：{session.game_mode}\n"
                f"玩家数：{len(session.players)}\n\n"
                f"使用 `/rg 状态` 查看详细信息。"
            )
            return True, "存档已读取", 2

        except Exception as e:
            logger.error(f"读取存档失败: {e}", exc_info=True)
            await self.send_text(f"读取存档时出错：{e}")
            return False, "读取失败", 2


    async def _handle_存档列表(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, str | None, int]:
        """处理查看存档列表命令"""
        _ = rest_input
        save_manager = SaveManager()

        try:
            all_saves = await save_manager.list_saves()
            saves = [s for s in all_saves if s.get("group_id") == group_id]

            if not saves:
                await self.send_text(f"**{user_name} 的存档列表**\n\n暂无存档。")
                return True, "存档列表已显示", 2

            saves_text = [f"**{user_name} 的存档列表**\n"]
            for i, s in enumerate(saves, 1):
                name = s.get("name") or ("默认存档" if not s.get("is_named") else "未命名")
                saved_at = str(s.get("saved_at", "未知时间"))
                player_count = s.get("player_count", 0)
                player_names = s.get("player_names", [])
                player_ids = s.get("player_ids", [])

                # 检查当前用户是否参与过该存档
                user_in_save = user_id in player_ids
                marker = "👤 " if user_in_save else ""

                player_info = f"{player_count}人"
                if player_names:
                    player_info = "、".join(player_names[:4])
                    if len(player_names) > 4:
                        player_info += f" 等{player_count}人"

                saves_text.append(
                    f"{i}. {marker}{name}\n"
                    f"   场景：{s.get('scene_name', '未知')}\n"
                    f"   模式：{s.get('game_mode', '未知')}\n"
                    f"   玩家：{player_info}\n"
                    f"   状态：{s.get('status', 'unknown')}\n"
                    f"   时间：{saved_at}\n"
                )

            # 添加图例说明
            if any(user_id in s.get("player_ids", []) for s in saves):
                saves_text.append("\n👤 标记表示你参与过的存档")

            await self.send_text("\n".join(saves_text))
            return True, "存档列表已显示", 2

        except Exception as e:
            logger.error(f"查看存档列表失败: {e}", exc_info=True)
            await self.send_text(f"查看存档列表时出错：{e}")
            return False, "查看失败", 2


    # 别名处理
    _handle_start = _handle_开始
    _handle_join = _handle_加入
    _handle_leave = _handle_离开
    _handle_status = _handle_状态
    _handle_rules = _handle_规则
    _handle_hint = _handle_提示
    _handle_clue = _handle_线索
    _handle_clues = _handle_线索
    _handle_reason = _handle_推理

    _handle_action = _handle_行动
    _handle_end = _handle_结束
    _handle_help = _handle_帮助
    _handle_scene = _handle_场景
    _handle_plot = _handle_剧情
    _handle_story = _handle_剧情
    _handle_item = _handle_道具
    _handle_items = _handle_道具
    _handle_inventory = _handle_物品栏
    _handle_bag = _handle_背包
    _handle_continue = _handle_继续
    _handle_force_start = _handle_强制开始
    _handle_restore = _handle_恢复
    _handle_save = _handle_保存
    _handle_load = _handle_读取
    _handle_save_list = _handle_存档列表
    _handle_cleanup_saves = _handle_清理存档

