# pyright: reportImplicitRelativeImport=false
# pyright: reportIncompatibleMethodOverride=false
# pyright: reportAssignmentType=false
# pyright: reportUnannotatedClassAttribute=false
# pyright: reportMissingTypeArgument=false
# pyright: reportUnusedImport=false
# pyright: reportUndefinedVariable=false
# pyright: reportConstantRedefinition=false
# pyright: reportIncompatibleVariableOverride=false
# pyright: reportAttributeAccessIssue=false
# pyright: reportArgumentType=false
"""
规则怪谈插件

生成规则怪谈并进行互动游戏，支持LLM生成、提示、推理和多种结局判定
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any


from src.plugin_system import (
    BasePlugin,
    BaseCommand,
    register_plugin,
    ConfigField,
    PythonDependency,
)

from .core import (
    GameStateManager,
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
    IntentParser,
    ImmersiveFeedback,
    ActionProcessor,
    GameGenerator,
    EndingJudge,
)

# 导入原有系统（保持兼容性：这些模块在旧版/扩展玩法中用于环境演化、时间推进、规则变异、线索发现、多人协作等）
# 当前主流程未直接调用它们，但保留导入以便后续功能对接/兼容旧存档结构。
from .environment_evolution import EnvironmentEvolutionSystem  # noqa: F401
from .game_time_manager import GameTimeManager  # noqa: F401
from .environment_state import EnvironmentState  # noqa: F401
from .rule_mutation_system import RuleMutationSystem  # noqa: F401
from .clue_discovery_system import ClueDiscoverySystem  # noqa: F401
from .multiplayer_physics_system import MultiplayerPhysicsSystem  # noqa: F401
from .npc_system import NPCMemory, NPCAttitude



# 配置日志
logger = logging.getLogger(__name__)

# 目录配置
PLUGIN_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(PLUGIN_DIR, "data")
TEMP_IMAGES_DIR = os.path.join(DATA_DIR, "temp_images")


def _is_dir_writable(path: str) -> bool:
    """检查目录是否可写（Linux 上常见：插件目录只读导致写入失败）。"""
    try:
        os.makedirs(path, exist_ok=True)
        test_file = os.path.join(path, ".write_test")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(test_file)
        return True
    except Exception:
        return False


def _resolve_data_dir(plugin_dir: str) -> str:
    """解析数据目录。

    优先使用插件目录下的 `data/`；如果不可写（常见于 Linux/Docker 只读挂载），
    则回退到用户数据目录（XDG_DATA_HOME 或 `~/.local/share`）。
    """
    preferred = os.path.join(plugin_dir, "data")
    if _is_dir_writable(preferred):
        return preferred

    xdg_home = os.getenv("XDG_DATA_HOME")
    if xdg_home:
        base = os.path.join(xdg_home, "maibot")
    else:
        base = os.path.join(os.path.expanduser("~"), ".local", "share", "maibot")

    fallback = os.path.join(base, "rule_horror")
    os.makedirs(fallback, exist_ok=True)
    logger.warning(f"插件目录不可写，已回退数据目录到: {fallback}")
    return fallback


@register_plugin
class RuleHorrorPlugin(BasePlugin):

    """规则怪谈插件"""

    plugin_name: str = "rule_horror"
    enable_plugin: bool = True
    dependencies: list[str] = []
    python_dependencies: list[PythonDependency] = [
        PythonDependency(package_name="aiohttp"),
        PythonDependency(package_name="pydantic"),
        PythonDependency(package_name="tenacity"),
        PythonDependency(package_name="pyyaml"),
        PythonDependency(package_name="Pillow"),
    ]
    config_file_name: str = "config.toml"

    plugin_description: str = "生成规则怪谈并进行互动游戏。"
    plugin_version: str = "2.1.0"
    plugin_author: str = "岚影鸿夜"

    config_section_descriptions: dict[str, str] = {
        "plugin": "插件启用配置",
        "llm": "LLM API 配置",
        "environment": "环境演变系统配置",
        "save": "存档配置",
    }

    config_schema: dict[str, dict[str, ConfigField]] = {
        "plugin": {
            "enabled": ConfigField(
                type=bool,
                default=True,
                description="是否启用规则怪谈插件"
            ),
            "config_version": ConfigField(
                type=str,
                default="2.1.0",
                description="配置文件版本"
            ),
            "auto_save_interval": ConfigField(
                type=int,
                default=30,
                description="自动保存间隔(秒)"
            ),
            "enable_natural_language_action": ConfigField(
                type=bool,
                default=False,
                description="是否允许直接发送自然语言触发行动（建议关闭，使用 /rg 行动 更稳定）"
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
                default=0.8,
                description="生成随机性(0.0-1.0)"
            ),
            "max_concurrent": ConfigField(
                type=int,
                default=10,
                description="最大并发请求数"
            ),
        },
        "environment": {
            "enabled": ConfigField(
                type=bool,
                default=True,
                description="是否启用环境演变系统"
            ),
        },
        "save": {
            "batch_save_interval": ConfigField(
                type=int,
                default=30,
                description="批量保存间隔(秒)"
            ),
        },
    }

    def __init__(self, plugin_dir: str | None = None, plugin_config: dict | None = None, **kwargs):
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
        self._temp_images_dir: str = os.path.join(plugin_dir, "data", "temp_images")

    async def on_load(self) -> None:
        """插件加载时初始化"""
        # 使用实例的plugin_dir
        plugin_dir = self.plugin_dir

        # 解析数据目录（Linux 下可能出现插件目录只读的情况）
        data_dir = _resolve_data_dir(plugin_dir)
        temp_images_dir = os.path.join(data_dir, "temp_images")
        self._temp_images_dir = temp_images_dir

        # 确保目录存在
        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(temp_images_dir, exist_ok=True)

        
        # 加载配置文件
        config_path = os.path.join(plugin_dir, "config.toml")
        try:
            config = load_config_from_file(config_path)
            logger.info("配置文件加载成功")
            logger.info(f"LLM API URL: {config.llm.api_url}")
            logger.info(f"LLM 模型列表: {config.llm.model_list}")
            logger.info(f"LLM API Key: {config.llm.api_key[:20]}..." if config.llm.api_key else "未设置")
        except Exception as e:
            logger.error(f"配置文件加载失败: {e}")
        
        # 初始化核心组件
        self.state_manager = GameStateManager()
        self.save_manager = SaveManager(os.path.join(data_dir, "saves"))
        self.llm_client = LLMClient()
        self.image_generator = AsyncImageGenerator(self._temp_images_dir)

        # 启动管理器
        await self.state_manager.start()
        await self.save_manager.start()

        logger.info("规则怪谈插件已加载")

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

        logger.info("规则怪谈插件已卸载")

    def get_plugin_components(self) -> list[tuple]:
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


    def __init__(self, message, plugin_config=None):
        super().__init__(message, plugin_config)
        self._formatter = TextFormatter()
        self._intent_parser = IntentParser()
        self._feedback_system = ImmersiveFeedback()
        self._action_processor = ActionProcessor()
        self._game_generator: GameGenerator | None = None
        self._ending_judge = EndingJudge()
        
        # 获取临时图片目录（Linux 可能需要回退到用户目录）
        if plugin_config and 'plugin_dir' in plugin_config:
            plugin_dir = plugin_config['plugin_dir']
            data_dir = _resolve_data_dir(plugin_dir)
            self._temp_images_dir = os.path.join(data_dir, "temp_images")
        else:
            # 回退到全局变量
            self._temp_images_dir = TEMP_IMAGES_DIR

        
        # 确保目录存在
        os.makedirs(self._temp_images_dir, exist_ok=True)
        
    def _get_game_generator(self) -> GameGenerator:
        """获取或创建 GameGenerator（延迟初始化）"""
        if self._game_generator is None:
            self._game_generator = GameGenerator()
        return self._game_generator

    def _assign_multiplayer_identities(self, session: GameSession, player_order: list[str]) -> None:
        """把多人模式生成的身份信息分配到玩家对象上。

        说明：身份信息来自 `session.rule_network['multi_identity']['identities']`。
        """
        if session.game_mode != "多人":
            return

        mi = session.rule_network.get("multi_identity", {}) if isinstance(getattr(session, "rule_network", None), dict) else {}
        identities = mi.get("identities", []) if isinstance(mi, dict) else []
        if not isinstance(identities, list) or not identities:
            return

        # 补全顺序列表
        order = [str(x) for x in (player_order or []) if str(x)]
        for pid in session.players.keys():
            if pid not in order:
                order.append(pid)

        # 为每个玩家分配一个身份（按加入顺序；身份数量不足时循环使用）
        assigned: dict[str, str] = {}
        for i, pid in enumerate(order):
            p = session.players.get(pid)
            if not p:
                continue
            ident = identities[i % len(identities)] if identities else {}
            if not isinstance(ident, dict):
                continue

            p.identity = str(ident.get("identity_name") or "").strip() or None
            p.identity_description = str(ident.get("identity_description") or "").strip() or None
            ur = ident.get("unique_rules", [])
            p.unique_rules = ur if isinstance(ur, list) else []
            p.exclusive_info = str(ident.get("exclusive_info") or "").strip() or None

            if p.identity:
                assigned[pid] = p.identity

        # 写入会话环境状态（便于后续展示/调试）
        if isinstance(getattr(session, "environment_state", None), dict):
            session.environment_state.setdefault("multiplayer", {})
            if isinstance(session.environment_state.get("multiplayer"), dict):
                session.environment_state["multiplayer"]["assigned_identities"] = assigned


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
        """获取用户信息 (user_id, user_name)"""
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
                user_name = getattr(user_info, 'user_name', f'玩家{user_id}')
                return user_id, user_name
        
        # 回退到从 message 获取
        message_obj = getattr(self, 'message', None)
        if message_obj:
            user_info = getattr(message_obj, 'user_info', None)
            if user_info:
                user_id = str(getattr(user_info, 'user_id', 'unknown'))
                user_name = getattr(user_info, 'user_name', f'玩家{user_id}')
                return user_id, user_name
        
        return "unknown", "未知玩家"

    async def execute(self) -> tuple[bool, Optional[str], int]:
        """执行命令"""
        matched_groups = self.matched_groups or {}
        action = (matched_groups.get("action") or "").strip()
        rest_input = (matched_groups.get("rest") or "").strip()

        group_id = self._get_group_id()
        user_id, user_name = self._get_user_info()

        # 检查插件是否启用
        enabled = self.get_config("plugin.enabled", True)
        if not enabled:
            await self.send_text("规则怪谈插件已被禁用。")
            return False, "插件未启用", 2

        # 检查是否是命令格式
        if not action:
            # 沉浸式“结束”口令：不必输入 /rg，也不受 enable_natural_language_action 影响
            raw_text = (getattr(self.message, "text", "") or "").strip()
            norm = re.sub(r"\s+", "", raw_text)
            norm = re.sub(r"[，,。.!！？?；;:“”\"'‘’《》【】\[\]（）()\-—…·]", "", norm)
            if norm in {"结束", "结束游戏"}:

                state_manager = GameStateManager()
                state = await state_manager.get(group_id)
                if state and state.session:
                    return await self._handle_结束(group_id, user_id, user_name, "")

            # 非命令消息：默认不当作行动（避免误触发/剧透）。
            # 如需开启，可在配置中设置 plugin.enable_natural_language_action=true
            if self.get_config("plugin.enable_natural_language_action", False):
                return await self._handle_natural_input(group_id, user_id, user_name)
            return False, None, 0



        # 路由到对应处理器
        handler = getattr(self, f"_handle_{action}", None)
        if handler:
            return await handler(group_id, user_id, user_name, rest_input)

        await self.send_text("未知命令。请使用 `/rg 帮助` 查看可用命令。")
        return False, "未知命令", 2

    # ============== 自然语言输入处理 ==============

    async def _handle_natural_input(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
    ) -> tuple[bool, Optional[str], int]:
        """处理自然语言输入(统一使用ActionProcessor)"""
        state_manager = GameStateManager()
        state = await state_manager.get(group_id)

        if not state or not state.session:
            # 不在游戏中，忽略自然语言输入
            return False, None, 0

        player = state.session.players.get(user_id)
        if not player:
            return False, None, 0

        if player.status != PlayerStatus.ALIVE:
            return False, None, 0

        # 获取用户输入
        user_input = self.message.text
        logger.info(f"自然语言输入检查: {user_name} - {user_input}")

        # 简单的关键词过滤，避免不必要的LLM调用
        action_keywords = [
            "拿", "取", "放", "扔", "用", "打开", "关闭", "检查", "询问",
            "进入", "离开", "触摸", "推", "拉", "按", "转", "看", "听",
            "等待", "躲藏", "逃跑", "攻击", "交谈", "观察", "搜索", "移动",
            "前往", "返回", "调查", "寻找", "翻找", "使用", "吃", "探索",
            "喝", "睡", "休息", "歇息", "坐", "站", "走", "跑", "爬",
            "先", "然后", "接着", "再", "去", "来", "到", "在", "找"
        ]
        
        # 如果输入太短或不包含行动关键词，忽略
        if len(user_input) < 2:
            logger.debug(f"自然语言输入太短，忽略: {user_input}")
            return False, None, 0
            
        if not any(kw in user_input for kw in action_keywords):
            logger.info(f"自然语言输入不包含行动关键词，忽略: {user_input}")
            return False, None, 0

        # 判断是否是有效的游戏行动（可选的验证步骤）
        context = self._build_game_context(state, player)
        
        try:
            is_valid = await self._intent_parser.is_valid_action(user_input, context)
            logger.info(f"自然语言输入验证结果: {user_input[:30]}... -> {'有效' if is_valid else '无效'}")
        except Exception as e:
            logger.error(f"判断行动有效性失败: {e}")
            # 即使验证失败，也尝试处理（因为可能是物品使用或休息）
            is_valid = True

        if not is_valid:
            # 不是有效的游戏行动，忽略
            logger.info(f"自然语言输入被验证为无效，忽略: {user_input[:30]}...")
            return False, None, 0

        try:
            # 使用 ActionProcessor 处理行动（统一处理流程）
            logger.info(f"自然语言输入处理: {user_name} - {user_input}")
            
            result = await self._action_processor.process_action(
                action=user_input,
                player=player,
                session=state.session,
            )
            
            # 记录行动
            player.action_history.append({
                "action": user_input,
                "timestamp": datetime.now().isoformat(),
            })
            player.last_action_at = datetime.now()
            
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
                fatigue = player.fatigue
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
            action_image = await image_generator.generate_action_result_image(
                user_name=user_name,
                action=user_input,
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
                found_items=[c for c in result.discovered_clues],
                new_location=new_location,
                random_event=random_event,
            )
            
            # 发送图片
            with open(action_image, 'rb') as f:
                image_bytes = f.read()
            image_base64 = base64.b64encode(image_bytes).decode('ascii')
            await self.send_image(image_base64)
            
            # 如果玩家死亡
            if result.is_fatal or player.status != PlayerStatus.ALIVE:
                if player.sanity == 0:
                    # 理智崩坏：保持沉浸，不做 OOC 指令提示
                    await self.send_text("**……**\n\n你感到某种‘秩序’正在接纳你。")

                else:

                    await self.send_text(
                        "**💀 你已死亡！**\n\n"
                        f"违反的规则：{result.violated_rule or '未知'}\n\n"
                        "游戏结束。使用 `/rg 结束` 查看结局。"
                    )
            
            # 保存状态
            save_manager = SaveManager()
            await save_manager.schedule_save(group_id, state.session)
            
            return True, "行动已执行", 2
            
        except Exception as e:
            logger.error(f"处理自然语言输入失败: {e}", exc_info=True)
            await self.send_text(f"处理行动时出错：{e}")
            return False, "处理失败", 2

        finally:
            if state:
                state.release()

    def _build_game_context(self, state, player) -> dict[str, Any]:
        """构建游戏上下文

        注意：这里的 rules 应当是“玩家已知规则”，避免自然语言模块/提示系统拿到全规则导致剧透。
        """
        session = state.session
        env_state = session.environment_state if isinstance(getattr(session, "environment_state", None), dict) else {}

        # 归一化规则表
        all_rules: list[dict[str, Any]] = []
        for i, r in enumerate(session.rules or []):
            if isinstance(r, dict):
                rr = dict(r)
                rr.setdefault("original_index", i)
                all_rules.append(rr)
            else:
                all_rules.append({"text": str(r), "original_index": i})

        known_indices: list[int] = []
        if isinstance(env_state.get("known_rule_indices"), list):
            known_indices = [int(x) for x in env_state.get("known_rule_indices", []) if isinstance(x, int)]

        known_rules: list[dict[str, Any]] = []
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

        return {
            "scene_name": session.scene_name,
            "background": session.background,
            "rules": known_rules,
            "player_status": {
                "sanity": player.sanity,
                "health": player.health,
                "location": player.location,
            },
            "recent_actions": [a.get("action", "") for a in player.action_history[-5:]],
        }


    def _build_game_state_dict(self, state, player) -> dict[str, Any]:
        """构建游戏状态字典"""
        return {
            "scene_name": state.session.scene_name,
            "background": state.session.background,
            "player_status": {
                "sanity": player.sanity,
                "health": player.health,
                "location": player.location,
            },
        }

    def _apply_state_updates(self, player: Player, updates: dict[str, Any]) -> None:
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
        event_description: Optional[str],
    ) -> None:
        """调度延迟事件"""
        await asyncio.sleep(delay_seconds)

        state_manager = GameStateManager()
        state = await state_manager.get(group_id)

        if not state or not state.session:
            return

        player = state.session.players.get(user_id)
        if not player or player.status != PlayerStatus.ALIVE:
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

        # 发送反馈
        await self.send_text(feedback.content)

    async def _extract_rules_from_dialogue(
        self,
        npc_dialogue: str,
        all_rules: list[dict[str, Any]],
        npc_name: str,
    ) -> list[dict[str, Any]]:
        """从NPC对话中提取提到的规则。

        策略：
        - 先用 LLM 做“索引对齐 + 口吻改写”（便于和后台规则表对齐）。
        - 再用轻量启发式从口述里补全：
          - 把“非常像规则的句子”抓出来。
          - 能匹配到后台规则表的就补齐缺失索引。
          - 匹配不到但明显是规矩/警告的，也允许作为“口述规则”展示（不占用 original_index）。
        """
        if not npc_dialogue:
            return all_rules

        def _norm(s: str) -> str:
            s = str(s or "")
            s = re.sub(r"\s+", "", s)
            s = re.sub(r"[，,。.!！？?；;:“”\"'‘’《》【】\[\]（）()\-—…·]", "", s)
            return s

        def _sim(a: str, b: str) -> float:
            na, nb = _norm(a), _norm(b)
            if not na or not nb:
                return 0.0
            if na in nb or nb in na:
                return 1.0
            return float(SequenceMatcher(None, na, nb).ratio())

        def _looks_like_rule(line: str) -> bool:
            t = str(line or "").strip()
            if len(t) < 8:
                return False
            cues = ["不要", "别", "必须", "务必", "千万", "记住", "如果", "一旦", "立刻", "禁止", "不得", "否则"]
            return any(c in t for c in cues)

        # 先从口述里提取“候选规则句”
        raw = str(npc_dialogue)
        parts = [p.strip() for p in re.split(r"[\n。！？；]+", raw) if p.strip()]
        candidates = [p for p in parts if _looks_like_rule(p)]

        # 1) LLM对齐提取
        extracted_rules: list[dict[str, Any]] = []
        try:
            if all_rules:
                llm_client = LLMClient()

                rules_text = "\n".join(
                    [
                        f"{i+1}. {rule.get('text', rule.get('content', str(rule)))}"
                        for i, rule in enumerate(all_rules)
                    ]
                )

                system_prompt = f"""你是规则怪谈游戏的规则提取器。你需要分析{npc_name}的对话，找出其中提到的所有规则。

任务：
1. 仔细阅读NPC的对话内容，找出其中提到的所有注意事项、警告、规则
2. 对比所有规则列表，找出NPC明确提到、暗示或警告的规则
3. 对于每条被提到的规则，用NPC对话中的措辞重新表述（保持原意但使用NPC的语言风格）
4. 返回所有被提到的规则，不要遗漏

注意：
- 只返回JSON格式
- 即使NPC的措辞与规则原文不同，只要意思相同就应该提取
- 要提取所有被提到的规则，不要只提取一条
- 用NPC对话中的原话或类似风格重新表述规则"""

                user_prompt = f"""NPC对话：
{npc_dialogue}

所有规则：
{rules_text}

请分析NPC对话中提到的所有规则，返回JSON格式：
{{"mentioned_rules": [
    {{"original_index": 0, "text": "用NPC对话中的措辞重新表述的规则内容"}},
    {{"original_index": 2, "text": "另一条被提到的规则"}}
]}}"""

                response = await llm_client.call(
                    prompt=user_prompt,
                    system_prompt=system_prompt,
                    temperature=0.3,
                )

                data = response.parse_json()
                mentioned_rules = data.get("mentioned_rules", [])

                if mentioned_rules and isinstance(mentioned_rules, list):
                    for rule_data in mentioned_rules:
                        if isinstance(rule_data, dict):
                            original_index = rule_data.get("original_index")
                            text = str(rule_data.get("text", "") or "").strip()

                            if isinstance(original_index, int) and 0 <= original_index < len(all_rules):
                                if text:
                                    extracted_rules.append({"text": text, "original_index": original_index})
                                else:
                                    base = dict(all_rules[original_index]) if isinstance(all_rules[original_index], dict) else {"text": str(all_rules[original_index])}
                                    base.setdefault("original_index", original_index)
                                    extracted_rules.append(base)
                        elif isinstance(rule_data, int) and 0 <= rule_data < len(all_rules):
                            base = dict(all_rules[rule_data]) if isinstance(all_rules[rule_data], dict) else {"text": str(all_rules[rule_data])}
                            base.setdefault("original_index", rule_data)
                            extracted_rules.append(base)
        except Exception as e:
            logger.warning(f"LLM提取规则失败，将使用启发式兜底: {e}")

        # 2) 启发式补全：补齐漏掉的“后台规则表”索引
        extracted_idx: set[int] = set(
            [int(r.get("original_index")) for r in extracted_rules if isinstance(r, dict) and isinstance(r.get("original_index"), int)]
        )

        if all_rules and candidates:
            for i, rule in enumerate(all_rules):
                if i in extracted_idx:
                    continue
                rule_text = str(rule.get("text", rule.get("content", str(rule))) if isinstance(rule, dict) else str(rule))
                best = 0.0
                best_cand = ""
                for c in candidates:
                    s = _sim(c, rule_text)
                    if s > best:
                        best, best_cand = s, c
                if best >= 0.55:
                    extracted_rules.append({"text": best_cand.strip(), "original_index": i})
                    extracted_idx.add(i)

        # 3) 启发式补全：加入“口述规则”（匹配不到索引但像规矩/警告）
        #    这能覆盖你图里那种：NPC说了“别过夜/别理会”，但后台规则表暂时没有写进去的情况。
        if candidates:
            existing_texts = {_norm(r.get("text", "")) for r in extracted_rules if isinstance(r, dict)}
            for c in candidates:
                nc = _norm(c)
                if not nc or nc in existing_texts:
                    continue

                # 如果它与某条后台规则相似度很高，但那条规则已经提取了，就跳过（避免重复表述）
                if all_rules:
                    best = 0.0
                    for i, rule in enumerate(all_rules):
                        rule_text = str(rule.get("text", rule.get("content", str(rule))) if isinstance(rule, dict) else str(rule))
                        best = max(best, _sim(c, rule_text))
                    if best >= 0.80:
                        continue

                extracted_rules.append({"text": c.strip(), "original_index": None, "source": "npc_dialogue"})
                existing_texts.add(nc)

        if extracted_rules:
            # 稳定排序：先按索引，其次保持口述规则在后面
            def _sort_key(x: dict[str, Any]) -> tuple[int, int]:
                idx = x.get("original_index")
                return (0, int(idx)) if isinstance(idx, int) else (1, 10**9)

            extracted_rules.sort(key=_sort_key)
            logger.info(f"从NPC对话中提取到 {len(extracted_rules)} 条规则（含口述补全）")
            return extracted_rules

        # 如果还是没有提取到：返回所有规则（并补齐 original_index）
        normalized: list[dict[str, Any]] = []
        for i, r in enumerate(all_rules):
            if isinstance(r, dict):
                rr = dict(r)
                rr.setdefault("original_index", i)
                normalized.append(rr)
            else:
                normalized.append({"text": str(r), "original_index": i})
        return normalized


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
        if getattr(session, "game_mode", "单人") == "多人":
            plural_hint = "\n8. 多人模式：使用第二人称复数‘你们’，把玩家视作一行人\n"

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
            return f"你来到了{session.scene_name}。这里的气氛让你感到不安。"

    async def _handle_开始(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, Optional[str], int]:
        """处理开始游戏命令"""
        raw = (rest_input or "").strip()

        game_mode = "单人"
        multi_target: int | None = None
        multi_start = False

        if raw:
            m = re.match(r"^(单人|多人)\s*(.*)$", raw)
            if m:
                game_mode = m.group(1)
                tail = (m.group(2) or "").strip()
            else:
                game_mode = raw
                tail = ""

            if game_mode == "多人" and tail:
                if re.search(r"(开始|生成|确认|立即|立刻|start|go)", tail, flags=re.IGNORECASE):
                    multi_start = True

                m2 = re.search(r"(\d{1,2})", tail)
                if m2:
                    try:
                        n = int(m2.group(1))
                        if 2 <= n <= 4:
                            multi_target = n
                    except Exception:
                        pass

        if game_mode not in ["单人", "多人"]:
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
        if game_mode == "多人":
            state_manager = GameStateManager()
            state = await state_manager.get_or_create(group_id)
            try:
                sess = state.session
                lobby: GameSession | None = None
                if (
                    sess
                    and sess.game_mode == "多人"
                    and sess.status == GameStatus.WAITING
                    and isinstance(getattr(sess, "environment_state", None), dict)
                    and isinstance(sess.environment_state.get("lobby"), dict)
                ):
                    lobby = sess

                # 兼容：若磁盘上已有等待中的多人大厅存档但内存未加载，则先恢复到内存
                if (
                    lobby is None
                    and existing
                    and existing.game_mode == "多人"
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
                            "target_players": multi_target,
                            "created_at": datetime.now().isoformat(),
                        },
                        "lobby_player_order": [user_id],
                    }
                    lobby.add_player(Player(player_id=user_id, name=user_name))
                    state.session = lobby
                    await save_manager.save_immediately(group_id, lobby)

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

                env_state = lobby.environment_state
                lobby_meta = env_state.get("lobby", {}) if isinstance(env_state.get("lobby"), dict) else {}
                host_id = str(lobby_meta.get("host_id") or "")
                host_name = str(lobby_meta.get("host_name") or "房主")

                if not host_id:
                    lobby_meta["host_id"] = user_id
                    lobby_meta["host_name"] = user_name
                    env_state["lobby"] = lobby_meta
                    host_id = user_id
                    host_name = user_name

                if user_id != host_id:
                    await self.send_text(
                        f"当前已有多人大厅，由 {host_name} 创建。\n"
                        "请使用 `/rg 加入` 加入，等待房主开始生成。"
                    )
                    return False, "非房主", 2

                if multi_target is not None:
                    lobby_meta["target_players"] = multi_target
                    env_state["lobby"] = lobby_meta

                # 确保房主在大厅内
                if user_id not in lobby.players:
                    lobby.add_player(Player(player_id=user_id, name=user_name))

                order = env_state.get("lobby_player_order", [])
                if not isinstance(order, list):
                    order = []
                for pid in list(lobby.players.keys()):
                    if pid not in order:
                        order.append(pid)
                env_state["lobby_player_order"] = order

                cur = len(lobby.players)
                target = lobby_meta.get("target_players")
                target_disp = f"{int(target)}" if isinstance(target, int) else "?"
                players_disp = "、".join([p.name for p in lobby.players.values()]) if lobby.players else "（无）"

                if not multi_start:
                    await save_manager.save_immediately(group_id, lobby)
                    await self.send_text(
                        "**多人模式大厅**\n\n"
                        f"房主：{host_name}\n"
                        f"当前人数：{cur}/{target_disp}\n"
                        f"玩家：{players_disp}\n\n"
                        "等待其他玩家 `/rg 加入`。\n"
                        "房主发送 `/rg 开始 多人 开始` 开始生成。"
                    )
                    return True, "大厅状态", 2

                if cur < 2:
                    await self.send_text("多人模式至少需要 2 名玩家。请先让其他玩家使用 `/rg 加入`。")
                    return False, "人数不足", 2

                if isinstance(target, int) and cur < target:
                    await self.send_text(f"当前人数 {cur}/{target}，还未到齐。")
                    return False, "未到齐", 2

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
            player_count = len(lobby_players) if (game_mode == "多人") else None
            player_names = [n for _, n in lobby_players] if (game_mode == "多人") else None
            session = await self._get_game_generator().generate_game(
                group_id,
                game_mode,
                player_count=player_count,
                player_names=player_names,
            )
            session.status = GameStatus.ACTIVE

            # 添加玩家
            if game_mode == "单人":
                player = Player(player_id=user_id, name=user_name)
                session.add_player(player)
            else:
                for pid, name in lobby_players:
                    session.add_player(Player(player_id=pid, name=name))
                self._assign_multiplayer_identities(session, lobby_order or [pid for pid, _ in lobby_players])

            
            # 保存到状态管理器
            state_manager = GameStateManager()
            state = await state_manager.get_or_create(group_id)
            try:
                state.session = session
                
                # 保存存档
                await save_manager.save_immediately(group_id, session)
            finally:
                state.release()
            
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
            
            with open(scene_image, 'rb') as f:
                image_bytes = f.read()
            image_base64 = base64.b64encode(image_bytes).decode('ascii')
            await self.send_image(image_base64)
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

                    # 尝试给“单人模式”的开局玩家一个初始态度向量（多人模式在首次互动时再初始化）
                    if game_mode == "单人" and user_id:
                        memory.initialize_attitude_vector(user_id)

                        att = str(npc_guidance.get("npc_attitude", "") or "")
                        # 轻量映射：让初始“语气/态度”影响信任/怀疑
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

                
                with open(entrance_long_image, 'rb') as f:
                    image_bytes = f.read()
                image_base64 = base64.b64encode(image_bytes).decode('ascii')
                await self.send_image(image_base64)
                await asyncio.sleep(1.0)  # 间隔1秒
            
            # ③ 生成并发送规则图片
            guidance_method = npc_guidance.get("guidance_method", "rule_carrier") if npc_guidance else "rule_carrier"

            # “规则图”展示的是玩家当前已获得的信息（已知规则），而不是后台完整规则。
            # 这样 NPC 的态度/是否愿意多说，才能在玩法上形成可感知的动态变化。
            env_state = session.environment_state if isinstance(getattr(session, "environment_state", None), dict) else {}

            def _normalize_all_rules() -> list[dict[str, Any]]:
                out: list[dict[str, Any]] = []
                for i, r in enumerate(session.rules):
                    if isinstance(r, dict):
                        rr = dict(r)
                        rr.setdefault("original_index", i)
                        out.append(rr)
                    else:
                        out.append({"text": str(r), "original_index": i})
                return out

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

            dedup: list[dict[str, Any]] = []
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

            rules_image = await image_generator.generate_rules_image(

                rules_title=rules_title,
                rules=display_rules,
                win_condition=session.win_condition,
                game_mode=game_mode,
            )



            
            with open(rules_image, 'rb') as f:
                image_bytes = f.read()
            image_base64 = base64.b64encode(image_bytes).decode('ascii')
            await self.send_image(image_base64)
            await asyncio.sleep(1.0)  # 间隔1秒
            
            # ④ 生成并发送场景结构文字长图
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
                
                with open(scene_structure_image, 'rb') as f:
                    image_bytes = f.read()
                image_base64 = base64.b64encode(image_bytes).decode('ascii')
                await self.send_image(image_base64)
                await asyncio.sleep(0.5)  # 最后一张可以稍短
            
            # 发送文字说明
            if game_mode == "多人":
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
    ) -> tuple[bool, Optional[str], int]:
        """处理加入游戏命令"""
        state_manager = GameStateManager()
        state = await state_manager.get(group_id)

        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。请先使用 `/rg 开始` 开始游戏。")
            return False, "无游戏", 2

        try:
            session = state.session

            if session.game_mode != "多人":
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

            # 限制人数（最多4人）
            if len(session.players) >= 4:
                await self.send_text("大厅人数已满（最多4人）。")
                return False, "人数已满", 2

            # 创建新玩家
            player = Player(player_id=user_id, name=user_name)
            success = session.add_player(player)

            if not success:
                await self.send_text("大厅人数已满（最多4人）。")
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
            target = lobby_meta.get("target_players")
            target_disp = f"{int(target)}" if isinstance(target, int) else "?"
            cur = len(session.players)
            players_disp = "、".join([p.name for p in session.players.values()])

            # 保存大厅状态
            save_manager = SaveManager()
            await save_manager.save_immediately(group_id, session)

            await self.send_text(
                "**加入成功**\n\n"
                f"{user_name} 加入了大厅。\n"
                f"当前人数：{cur}/{target_disp}\n"
                f"玩家：{players_disp}\n\n"
                f"等待房主 {host_name} 开始生成：`/rg 开始 多人 开始`"
            )
            return True, "加入成功", 2

        finally:
            if state:
                state.release()


    async def _handle_离开(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, Optional[str], int]:
        """处理离开游戏命令"""
        state_manager = GameStateManager()
        state = await state_manager.get(group_id)

        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            if user_id not in state.session.players:
                await self.send_text("你不在当前游戏中。")
                return False, "不在游戏中", 2

            state.session.remove_player(user_id)
            await self.send_text(f"{user_name} 离开了游戏。")

            # 如果所有玩家都离开了，结束游戏
            if not state.session.players:
                state.session.status = GameStatus.ENDED
                await state_manager.remove(group_id)

            return True, "离开成功", 2
        finally:
            if state:
                state.release()

    async def _handle_状态(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, Optional[str], int]:
        """处理查看状态命令"""
        state_manager = GameStateManager()
        state = await state_manager.get(group_id)

        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            session = state.session
            title = session.scene_name
            if not title:
                if session.game_mode == "多人" and session.status == GameStatus.WAITING:
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
                status_emoji = "🟢" if player.status == PlayerStatus.ALIVE else "💀"
                status_text.append(
                    f"{status_emoji} {player.name} - 理智:{player.sanity}/100 体力:{player.health}/100"
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
    ) -> tuple[bool, Optional[str], int]:
        """处理查看规则命令"""
        state_manager = GameStateManager()
        state = await state_manager.get(group_id)

        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            session = state.session
            if not session.rules:
                await self.send_text("规则尚未生成。")
                return False, "无规则", 2

            env_state = session.environment_state if isinstance(getattr(session, "environment_state", None), dict) else {}

            # 归一化全规则表（用于索引取值）
            all_rules: list[dict[str, Any]] = []
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

            known_rules: list[dict[str, Any]] = []
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
                    "建议：先查看 `/rg 剧情` 的入场信息，或使用 `/rg 行动` 进行探索/礼貌询问。\n\n"
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
    ) -> tuple[bool, Optional[str], int]:
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

            def _normalize_rule_text(r: Any) -> str:
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

            selected_item: dict[str, Any] | None = None
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

            def _format_item(it: dict[str, Any]) -> str:
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
    ) -> tuple[bool, Optional[str], int]:
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

            await self.send_text(f"**{user_name} 的推理**\n\n{rest_input}\n\n推理已记录。")
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
    ) -> tuple[bool, Optional[str], int]:
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
                fatigue = player.fatigue
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
            action_image = await image_generator.generate_action_result_image(
                user_name=user_name,
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
                found_items=[c for c in result.discovered_clues],
                new_location=new_location,
                random_event=random_event,
            )
            
            # 发送图片
            with open(action_image, 'rb') as f:
                image_bytes = f.read()
            image_base64 = base64.b64encode(image_bytes).decode('ascii')
            await self.send_image(image_base64)
            
            # 如果玩家死亡
            if result.is_fatal or player.status != PlayerStatus.ALIVE:
                if player.sanity == 0:
                    await self.send_text("**……**\n\n你感到某种‘秩序’正在接纳你。")

                else:

                    await self.send_text(
                        "**💀 你已死亡！**\n\n"
                        f"违反的规则：{result.violated_rule or '未知'}\n\n"
                        "游戏结束。使用 `/rg 结束` 查看结局。"
                    )
            
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
    ) -> tuple[bool, Optional[str], int]:
        """处理结束游戏命令"""
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

            await self.send_text("正在判定结局...")
            
            # 判定结局
            ending = await self._ending_judge.judge_ending(
                session=session,
                player=player,
            )
            
            # 更新会话状态
            session.status = GameStatus.ENDED
            session.ended_at = datetime.now()
            
            # 生成结局图片
            # - 死亡/失败/强制结束（玩家主动结束且未通关）不展示“解释/推理分析/隐藏真相”
            forced_end = (player.status == PlayerStatus.ALIVE and not session.has_cleared)
            ending_type = str(getattr(ending, "ending_type", "") or "")
            hide_explain = forced_end or (ending_type == "failed") or (player.status != PlayerStatus.ALIVE)

            reasoning_analysis = "" if hide_explain else str(getattr(ending, "reasoning_analysis", "") or "")
            truth_revealed = False if hide_explain else bool(getattr(ending, "truth_revealed", False))
            hidden_truth = session.hidden_truth if truth_revealed else None

            image_generator = AsyncImageGenerator(self._temp_images_dir)
            ending_image = await image_generator.generate_ending_image(
                ending_title=ending.title,
                ending_description=ending.description,
                reasoning_analysis=reasoning_analysis,
                truth_revealed=truth_revealed,
                hidden_truth=hidden_truth,
                ending_type=ending.ending_type,
            )

            
            # 发送结局图片
            with open(ending_image, 'rb') as f:
                image_bytes = f.read()
            image_base64 = base64.b64encode(image_bytes).decode('ascii')
            await self.send_image(image_base64)
            
            # 清理状态
            await state_manager.remove(group_id)
            
            # 删除存档
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
    ) -> tuple[bool, Optional[str], int]:
        """处理帮助命令"""
        _ = rest_input
        help_text = (
            "**规则怪谈游戏帮助**\n\n"
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
            "- `/rg 离开` - 离开当前游戏\n"
            "- `/rg 状态` - 查看游戏状态和玩家信息\n"
            "- `/rg 剧情` - 查看剧情导入（重新发送入场/导入图）\n"
            "- `/rg 规则` - 查看当前规则\n"
            "- `/rg 场景` - 查看场景结构\n"
            "- `/rg 道具 [道具名称]` - 查看道具列表或道具详情\n"
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

    async def _handle_剧情(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, Optional[str], int]:
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
            with open(scene_image, 'rb') as f:
                image_bytes = f.read()
            image_base64 = base64.b64encode(image_bytes).decode('ascii')
            await self.send_image(image_base64)

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
                with open(entrance_long_image, 'rb') as f:
                    image_bytes = f.read()
                image_base64 = base64.b64encode(image_bytes).decode('ascii')
                await self.send_image(image_base64)

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
    ) -> tuple[bool, Optional[str], int]:
        """处理查看道具命令（物品栏别名，支持详情）"""
        return await self._handle_物品栏(group_id, user_id, user_name, rest_input)

    async def _handle_清理存档(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, Optional[str], int]:
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
    ) -> tuple[bool, Optional[str], int]:
        """处理查看场景命令"""
        state_manager = GameStateManager()
        state = await state_manager.get(group_id)

        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            session = state.session
            scene_structure = getattr(session, 'scene_structure', {}) or {}
            
            if not scene_structure:
                await self.send_text("场景结构尚未生成。")
                return False, "无场景结构", 2
            
            # 构建场景结构文本
            scene_text = [f"**{session.scene_name} - 场景结构**\n"]
            
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
            
        finally:
            if state:
                state.release()

    async def _handle_物品栏(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, Optional[str], int]:
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

            if not inventory:
                await self.send_text("**物品栏**\n\n你的背包是空的。")
                return True, "物品栏已显示", 2

            query = (rest_input or "").strip()
            if query:
                # 详情模式：根据名称模糊匹配
                matches: list[dict[str, Any]] = []
                for it in inventory:
                    if not isinstance(it, dict):
                        continue
                    name = str(it.get('name', ''))
                    if query in name:
                        matches.append(it)

                if not matches:
                    await self.send_text(f"未找到道具：{query}\n\n你可以使用 `/rg 道具` 查看道具列表。")
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
                    inventory_data=inventory,
                    player_name=user_name,
                    use_cache=True,
                )
                with open(inventory_image, 'rb') as f:
                    image_bytes = f.read()
                image_base64 = base64.b64encode(image_bytes).decode('ascii')
                await self.send_image(image_base64)
            except Exception as e:
                logger.debug(f"生成或发送道具图片失败，回退到文本: {e}")

            items_text = [f"**{user_name} 的物品栏**\n"]
            for i, item in enumerate(inventory, 1):
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
            await self.send_text("\n".join(items_text))
            return True, "物品栏已显示", 2

            
        finally:
            if state:
                state.release()

    async def _handle_背包(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, Optional[str], int]:
        """处理查看背包命令（物品栏别名）"""
        return await self._handle_物品栏(group_id, user_id, user_name, rest_input)

    async def _handle_继续(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, Optional[str], int]:
        """处理继续探索命令"""
        state_manager = GameStateManager()
        state = await state_manager.get(group_id)

        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            if not state.session.has_cleared:
                await self.send_text("你还未达成通关条件！请继续探索。")
                return False, "未通关", 2
            
            await self.send_text(
                "**继续探索**\n\n"
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
    ) -> tuple[bool, Optional[str], int]:
        """处理强制开始命令"""
        raw = (rest_input or "").strip()

        game_mode = "单人"
        multi_target: int | None = None

        if raw:
            m = re.match(r"^(单人|多人)\s*(.*)$", raw)
            if m:
                game_mode = m.group(1)
                tail = (m.group(2) or "").strip()
            else:
                game_mode = raw
                tail = ""

            if game_mode == "多人" and tail:
                m2 = re.search(r"(\d{1,2})", tail)
                if m2:
                    try:
                        n = int(m2.group(1))
                        if 2 <= n <= 4:
                            multi_target = n
                    except Exception:
                        pass

        if game_mode not in ["单人", "多人"]:
            await self.send_text("请指定游戏模式：`/rg 强制开始 单人` 或 `/rg 强制开始 多人`")
            return False, "缺少游戏模式", 2


        # 清理现有状态
        state_manager = GameStateManager()
        await state_manager.remove(group_id)
        
        # 删除现有存档
        save_manager = SaveManager()
        await save_manager.delete(group_id)

        # 多人模式：强制开始只负责“清档并创建大厅”，避免先生成再加人
        if game_mode == "多人":
            state = await state_manager.get_or_create(group_id)
            try:
                lobby = GameSession(group_id=group_id, game_mode="多人", status=GameStatus.WAITING)
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
            if game_mode == "单人":
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
            
            with open(scene_image, 'rb') as f:
                image_bytes = f.read()
            image_base64 = base64.b64encode(image_bytes).decode('ascii')
            await self.send_image(image_base64)
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
                    if game_mode == "单人" and user_id:
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


                
                with open(entrance_long_image, 'rb') as f:
                    image_bytes = f.read()
                image_base64 = base64.b64encode(image_bytes).decode('ascii')
                await self.send_image(image_base64)
                await asyncio.sleep(1.0)  # 间隔1秒
            
            # ③ 生成并发送规则图片
            guidance_method = npc_guidance.get("guidance_method", "rule_carrier") if npc_guidance else "rule_carrier"

            # “规则图”展示的是玩家当前已获得的信息（已知规则），而不是后台完整规则。
            env_state = session.environment_state if isinstance(getattr(session, "environment_state", None), dict) else {}

            def _normalize_all_rules() -> list[dict[str, Any]]:
                out: list[dict[str, Any]] = []
                for i, r in enumerate(session.rules):
                    if isinstance(r, dict):
                        rr = dict(r)
                        rr.setdefault("original_index", i)
                        out.append(rr)
                    else:
                        out.append({"text": str(r), "original_index": i})
                return out

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

            dedup: list[dict[str, Any]] = []
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

            rules_image = await image_generator.generate_rules_image(

                rules_title=rules_title,
                rules=display_rules,
                win_condition=session.win_condition,
                game_mode=game_mode,
                use_cache=False,  # 强制开始不使用缓存
            )



            
            with open(rules_image, 'rb') as f:
                image_bytes = f.read()
            image_base64 = base64.b64encode(image_bytes).decode('ascii')
            await self.send_image(image_base64)
            await asyncio.sleep(1.0)  # 间隔1秒
            
            # ④ 生成并发送场景结构文字长图
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
                
                with open(scene_structure_image, 'rb') as f:
                    image_bytes = f.read()
                image_base64 = base64.b64encode(image_bytes).decode('ascii')
                await self.send_image(image_base64)
                await asyncio.sleep(0.5)  # 最后一张可以稍短
            
            # 发送文字说明
            if game_mode == "多人":
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
    ) -> tuple[bool, Optional[str], int]:
        """处理恢复存档命令"""
        save_manager = SaveManager()
        
        try:
            session = await save_manager.load(group_id)
            
            if not session:
                await self.send_text("未找到存档。请使用 `/rg 开始` 开始新游戏。")
                return False, "无存档", 2
            
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
            
            await self.send_text(
                f"**存档已恢复**\n\n"
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
    ) -> tuple[bool, Optional[str], int]:
        """处理手动保存命令"""
        if not rest_input:
            await self.send_text("请提供存档名称。用法：`/rg 保存 <存档名称>`")
            return False, "缺少存档名称", 2

        state_manager = GameStateManager()
        state = await state_manager.get(group_id)

        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            save_name = rest_input.strip()
            save_manager = SaveManager()

            ok = await save_manager.save_with_name(group_id, state.session, save_name)
            if not ok:
                await self.send_text("存档保存失败，请稍后重试。")
                return False, "保存失败", 2

            await self.send_text(f"**存档已保存**\n\n存档名称：{save_name}")
            return True, "存档已保存", 2

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
    ) -> tuple[bool, Optional[str], int]:
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

            # 恢复到状态管理器
            state_manager = GameStateManager()
            state = await state_manager.get_or_create(group_id)
            try:
                state.session = session
            finally:
                state.release()

            await self.send_text(
                f"**存档已读取**\n\n"
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
    ) -> tuple[bool, Optional[str], int]:
        """处理查看存档列表命令"""
        save_manager = SaveManager()

        try:
            all_saves = await save_manager.list_saves()
            saves = [s for s in all_saves if s.get("group_id") == group_id]

            if not saves:
                await self.send_text("**存档列表**\n\n暂无存档。")
                return True, "存档列表已显示", 2

            saves_text = ["**存档列表**\n"]
            for i, s in enumerate(saves, 1):
                name = s.get("name") or ("默认存档" if not s.get("is_named") else "未命名")
                saved_at = str(s.get("saved_at", "未知时间"))
                saves_text.append(
                    f"{i}. {name}\n"
                    f"   场景：{s.get('scene_name', '未知')}\n"
                    f"   模式：{s.get('game_mode', '未知')}\n"
                    f"   状态：{s.get('status', 'unknown')}\n"
                    f"   时间：{saved_at}\n"
                )

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

