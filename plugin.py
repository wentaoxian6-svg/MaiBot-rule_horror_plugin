"""
规则怪谈插件 - 重构版本

架构改进：
1. 职责分离：将游戏逻辑、LLM调用、图片生成、存档管理拆分到独立模块
2. 状态管理：使用 GameStateManager 替代全局变量，支持线程安全
3. 性能优化：LLMClient 使用连接池，AsyncImageGenerator 使用线程池
4. 批量保存：SaveManager 支持批量写入，减少磁盘IO
5. 类型安全：统一使用 Python 3.10+ 类型注解
6. 错误处理：完善的异常处理和重试机制
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
from datetime import datetime
from typing import Any, Optional

from src.plugin_system import (
    BasePlugin,
    BaseCommand,
    register_plugin,
    ActionInfo,
    CommandInfo,
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
    get_config,
    load_config_from_file,
)

from .core.services import (
    IntentParser,
    ImmersiveFeedback,
    ActionProcessor,
    GameGenerator,
    EndingJudge,
)

# 导入原有系统（保持不变）
from .environment_evolution import EnvironmentEvolutionSystem
from .game_time_manager import GameTimeManager
from .environment_state import EnvironmentState
from .rule_mutation_system import RuleMutationSystem
from .clue_discovery_system import ClueDiscoverySystem
from .multiplayer_physics_system import MultiplayerPhysicsSystem

# 配置日志
logger = logging.getLogger(__name__)

# 目录配置
PLUGIN_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(PLUGIN_DIR, "data")
TEMP_IMAGES_DIR = os.path.join(DATA_DIR, "temp_images")
TEMP_IMAGES_DIR = os.path.join(DATA_DIR, "temp_images")


@register_plugin
class RuleHorrorPlugin(BasePlugin):
    """规则怪谈插件 - 重构版本"""

    plugin_name = "rule_horror"
    enable_plugin = True
    dependencies: list[str] = []
    python_dependencies: list[PythonDependency] = [
        PythonDependency(package_name="aiohttp"),
        PythonDependency(package_name="pydantic"),
        PythonDependency(package_name="tenacity"),
        PythonDependency(package_name="pyyaml"),
        PythonDependency(package_name="Pillow"),
    ]
    config_file_name = "config.toml"

    plugin_description = "生成规则怪谈并进行互动游戏（重构版本）。"
    plugin_version = "2.1.0"
    plugin_author = "岚影鸿夜"

    config_section_descriptions = {
        "plugin": "插件启用配置",
        "llm": "LLM API 配置",
        "environment": "环境演变系统配置",
        "save": "存档配置",
    }

    config_schema = {
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
                default=["deepseek-ai/DeepSeek-V3"],
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

    def __init__(self, plugin_dir: Optional[str] = None, plugin_config: Optional[dict] = None, **kwargs):
        # 确保plugin_dir被传递给父类
        if plugin_dir is None:
            plugin_dir = PLUGIN_DIR
        super().__init__(plugin_dir=plugin_dir, **kwargs)
        self.plugin_dir = plugin_dir
        self.plugin_config = plugin_config
        self.state_manager: Optional[GameStateManager] = None
        self.save_manager: Optional[SaveManager] = None
        self.llm_client: Optional[LLMClient] = None
        self.image_generator: Optional[AsyncImageGenerator] = None

    async def on_load(self) -> None:
        """插件加载时初始化"""
        # 使用实例的plugin_dir
        plugin_dir = self.plugin_dir
        data_dir = os.path.join(plugin_dir, "data")
        temp_images_dir = os.path.join(data_dir, "temp_images")
        
        # 确保目录存在
        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(temp_images_dir, exist_ok=True)
        
        # 加载配置文件
        config_path = os.path.join(plugin_dir, "config.toml")
        try:
            load_config_from_file(config_path)
            logger.info("配置文件加载成功")
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

        logger.info("规则怪谈插件已加载（重构版本）")

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

    def get_plugin_components(self) -> list:
        """注册命令组件"""
        return [
            (RuleHorrorCommand.get_command_info(), RuleHorrorCommand),
        ]
class RuleHorrorCommand(BaseCommand):
    """规则怪谈命令处理器"""

    command_name = "RuleHorrorCommand"
    command_description = "规则怪谈游戏：生成规则怪谈、加入/离开、提示、推理、行动、结束"
    command_pattern = r"^/rg\s+(?P<action>\S+)(?:\s+(?P<rest>.+))?"

    command_help = (
        "规则怪谈游戏：\n"
        "/rg 开始 单人/多人 - 开始新游戏\n"
        "/rg 加入 - 加入游戏（多人模式）\n"
        "/rg 离开 - 离开游戏\n"
        "/rg 状态 - 查看游戏状态\n"
        "/rg 规则 - 查看当前规则\n"
        "/rg 提示 <规则/线索> - 获取提示\n"
        "/rg 推理 <推理内容> - 记录推理\n"
        "/rg 行动 <行动描述> - 描述行动\n"
        "/rg 结束 - 结束游戏\n"
        "/rg 帮助 - 查看帮助"
    )

    def __init__(self, message, plugin_config=None):
        super().__init__(message, plugin_config)
        self._formatter = TextFormatter()
        self._intent_parser = IntentParser()
        self._feedback_system = ImmersiveFeedback()
        self._action_processor = ActionProcessor()
        self._game_generator = GameGenerator()
        self._ending_judge = EndingJudge()
        
        # 获取临时图片目录
        if plugin_config and 'plugin_dir' in plugin_config:
            plugin_dir = plugin_config['plugin_dir']
            data_dir = os.path.join(plugin_dir, "data")
            self._temp_images_dir = os.path.join(data_dir, "temp_images")
        else:
            # 回退到全局变量
            self._temp_images_dir = TEMP_IMAGES_DIR
        
        # 确保目录存在
        os.makedirs(self._temp_images_dir, exist_ok=True)

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
        message_obj = getattr(self, 'message', None)
        if message_obj:
            user_info = getattr(message_obj, 'user_info', None)
            if user_info:
                user_id = str(getattr(user_info, 'user_id', 'unknown'))
                user_name = getattr(user_info, 'user_name', '未知玩家')
                return user_id, user_name
        return "unknown", "未知玩家"

    async def execute(self) -> tuple[bool, Optional[str], int]:
        """执行命令"""
        matched_groups = self.matched_groups or {}
        action = matched_groups.get("action", "").strip()
        rest_input = matched_groups.get("rest", "").strip()

        group_id = self._get_group_id()
        user_id, user_name = self._get_user_info()

        # 检查插件是否启用
        enabled = self.get_config("plugin.enabled", True)
        if not enabled:
            await self.send_text("规则怪谈插件已被禁用。")
            return False, "插件未启用", 2

        # 检查是否是命令格式
        if not action:
            # 不是命令格式，尝试处理自然语言输入
            return await self._handle_natural_input(group_id, user_id, user_name)

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

        # 简单的关键词过滤，避免不必要的LLM调用
        action_keywords = [
            "拿", "取", "放", "扔", "用", "打开", "关闭", "检查", "询问",
            "进入", "离开", "触摸", "推", "拉", "按", "转", "看", "听",
            "等待", "躲藏", "逃跑", "攻击", "交谈", "观察", "搜索", "移动",
            "前往", "返回", "调查", "寻找", "翻找", "使用", "吃", "探索",
            "喝", "睡", "休息", "歇息", "坐", "站"  # 添加物品使用和休息关键词
        ]
        
        # 如果输入太短或不包含行动关键词，忽略
        if len(user_input) < 2 or not any(kw in user_input for kw in action_keywords):
            return False, None, 0

        # 判断是否是有效的游戏行动（可选的验证步骤）
        context = self._build_game_context(state, player)
        
        try:
            is_valid = await self._intent_parser.is_valid_action(user_input, context)
        except Exception as e:
            logger.error(f"判断行动有效性失败: {e}")
            # 即使验证失败，也尝试处理（因为可能是物品使用或休息）
            is_valid = True

        if not is_valid:
            # 不是有效的游戏行动，忽略
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
        """构建游戏上下文"""
        return {
            "scene_name": state.session.scene_name,
            "background": state.session.background,
            "rules": state.session.rules,
            "player_status": {
                "sanity": player.sanity,
                "health": player.health,
                "location": player.location,
            },
            "recent_actions": [
                a.get("action", "") for a in player.action_history[-5:]
            ],
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

    # ============== 命令处理器==============

    async def _handle_开始(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        rest_input: str,
    ) -> tuple[bool, Optional[str], int]:
        """处理开始游戏命令"""
        game_mode = rest_input.strip() if rest_input else "单人"
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

        await self.send_text("正在生成规则怪谈，请稍候..")

        try:
            # 生成游戏
            session = await self._game_generator.generate_game(group_id, game_mode)
            session.status = GameStatus.ACTIVE
            
            # 单人模式自动添加玩家
            if game_mode == "单人":
                player = Player(player_id=user_id, name=user_name)
                session.add_player(player)
            
            # 保存到状态管理器
            state_manager = GameStateManager()
            state = await state_manager.get_or_create(group_id)
            try:
                state.session = session
                
                # 保存存档
                await save_manager.save_immediately(group_id, session)
            finally:
                state.release()
            
            # 生成剧情导入图片（使用增强版）
            image_generator = AsyncImageGenerator(self._temp_images_dir)
            
            # 获取核心象征符号（如果有）
            core_symbols = getattr(session, 'core_symbols', None)
            
            # 生成剧情导入图片
            scene_image = await image_generator.generate_scene_image(
                scene_name=session.scene_name,
                background=session.background,
                arrival_reason=session.player_identity,
                core_symbols=core_symbols,
            )
            
            # 发送剧情导入图片
            with open(scene_image, 'rb') as f:
                image_bytes = f.read()
            image_base64 = base64.b64encode(image_bytes).decode('ascii')
            await self.send_image(image_base64)
            
            await asyncio.sleep(0.5)
            
            # 生成场景结构文字长图
            scene_structure = session.scene_structure or {}
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
            
            # 发送场景结构文字长图
            with open(scene_structure_image, 'rb') as f:
                image_bytes = f.read()
            image_base64 = base64.b64encode(image_bytes).decode('ascii')
            await self.send_image(image_base64)
            
            await asyncio.sleep(0.5)
            
            # 生成规则图片
            rules_image = await image_generator.generate_rules_image(
                rules_title=f"{session.scene_name} - 规则",
                rules=session.rules,
                win_condition=session.win_condition,
                game_mode=game_mode,
            )
            
            # 发送规则图片
            with open(rules_image, 'rb') as f:
                image_bytes = f.read()
            image_base64 = base64.b64encode(image_bytes).decode('ascii')
            await self.send_image(image_base64)
            
            await asyncio.sleep(0.5)
            
            # 发送文字说明
            if game_mode == "多人":
                await self.send_text(
                    f"**游戏已开始！**\n\n"
                    f"模式：{game_mode}\n"
                    f"场景：{session.scene_name}\n\n"
                    f"其他玩家请使用 `/rg 加入` 加入游戏。\n"
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
            # 检查玩家是否已在游戏中
            if user_id in state.session.players:
                await self.send_text("你已经在游戏中了。")
                return False, "已在游戏中", 2

            # 创建新玩家
            player = Player(player_id=user_id, name=user_name)
            success = state.session.add_player(player)

            if success:
                await self.send_text(f"{user_name} 加入了游戏！")
                return True, "加入成功", 2
            else:
                await self.send_text("游戏人数已满（最多4人）。")
                return False, "人数已满", 2
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
            status_text = [
                f"**游戏状态：{session.scene_name}**",
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

            rules_text = [f"**{session.scene_name} - 规则**", ""]
            for i, rule in enumerate(session.rules, 1):
                rule_text = rule.get("text", rule.get("content", str(rule)))
                rules_text.append(f"{i}. {rule_text}")

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
        """处理获取提示命令"""
        hint_type = rest_input if rest_input else "规则"

        state_manager = GameStateManager()
        state = await state_manager.get(group_id)

        if not state or not state.session:
            await self.send_text("当前没有正在进行的游戏。")
            return False, "无游戏", 2

        try:
            session = state.session

            if session.hint_count <= 0:
                await self.send_text("你的提示次数已用完！")
                return False, "无提示次数", 2

            # 减少提示次数
            session.hint_count -= 1

            # 调用 LLM 生成提示
            llm_client = LLMClient()
            
            system_prompt = """你是规则怪谈游戏的提示系统。你需要给玩家提供有用但不直接揭示答案的提示。

提示原则：
1. 不要直接说出答案
2. 给出方向性的引导
3. 提示规则之间的矛盾
4. 暗示需要注意的细节

返回JSON格式：
{
    "hint": "提示内容（100-200字）"
}"""

            user_prompt = f"""场景：{session.scene_name}

规则：
{chr(10).join(f"{i+1}. {r.get('text', str(r))}" for i, r in enumerate(session.rules))}

隐藏真相：{session.hidden_truth}

玩家请求：{hint_type}提示

请生成提示。"""

            response = await llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.7,
                max_tokens=400,
            )
            
            result = response.parse_json()
            hint = result.get("hint", "仔细观察规则之间的矛盾..")
            
            # 保存状态
            save_manager = SaveManager()
            await save_manager.schedule_save(group_id, session)
            
            await self.send_text(
                f"**提示（剩余{session.hint_count}次）**\n\n{hint}"
            )
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
            
            # 更新会话状?            session.status = GameStatus.ENDED
            session.ended_at = datetime.now()
            
            # 生成结局图片（使用增强版?            image_generator = AsyncImageGenerator(self._temp_images_dir)
            ending_image = await image_generator.generate_ending_image(
                ending_title=ending.title,
                ending_description=ending.description,
                reasoning_analysis=ending.reasoning_analysis,
                truth_revealed=ending.truth_revealed,
                hidden_truth=session.hidden_truth if ending.truth_revealed else None,
                ending_type=ending.ending_type,
            )
            
            # 发送结局图片
            with open(ending_image, 'rb') as f:
                image_bytes = f.read()
            image_base64 = base64.b64encode(image_bytes).decode('ascii')
            await self.send_image(image_base64)
            
            # 清理状?            await state_manager.remove(group_id)
            
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
        help_text = (
            "**规则怪谈游戏帮助（重构版本）**\n\n"
            "**命令列表**\n"
            "- `/rg 开始 单人/多人` - 开始新游戏\n"
            "- `/rg 加入` - 加入当前游戏（多人模式）\n"
            "- `/rg 离开` - 离开当前游戏\n"
            "- `/rg 状态` - 查看游戏状态和玩家信息\n"
            "- `/rg 规则` - 查看当前规则\n"
            "- `/rg 提示 <规则/线索>` - 获取提示（剩余3次）\n"
            "- `/rg 推理 <推理内容>` - 记录你的推理\n"
            "- `/rg 行动 <行动描述>` - 描述你的行动\n"
            "- `/rg 结束` - 结束游戏\n"
            "- `/rg 帮助` - 查看帮助\n\n"
            "**重构改进**\n"
            "- 使用连接池优化LLM 调用性能\n"
            "- 使用线程池优化图片生成\n"
            "- 批量保存减少磁盘IO\n"
            "- 线程安全的状态管理\n"
            "- 完善的错误处理和重试机制"
        )
        await self.send_text(help_text)
        return True, "帮助已发送", 2

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
            scene_structure = session.scene_structure or {}
            
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
                    floor_name = floor.get('name', '未知楼层')
                    rooms = floor.get('rooms', [])
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
            
            # 构建物品栏文?            items_text = [f"**{user_name} 的物品栏**\n"]
            
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
        game_mode = rest_input.strip() if rest_input else "单人"
        if game_mode not in ["单人", "多人"]:
            await self.send_text("请指定游戏模式：`/rg 强制开始 单人` 或 `/rg 强制开始 多人`")
            return False, "缺少游戏模式", 2

        # 清理现有状?        state_manager = GameStateManager()
        await state_manager.remove(group_id)
        
        # 删除现有存档
        save_manager = SaveManager()
        await save_manager.delete(group_id)

        await self.send_text("正在生成规则怪谈，请稍候..")

        try:
            # 生成游戏
            session = await self._game_generator.generate_game(group_id, game_mode)
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
            
            # 生成剧情导入图片（使用增强版）
            image_generator = AsyncImageGenerator(self._temp_images_dir)
            
            # 获取核心象征符号（如果有）
            core_symbols = getattr(session, 'core_symbols', None)
            
            # 生成剧情导入图片
            scene_image = await image_generator.generate_scene_image(
                scene_name=session.scene_name,
                background=session.background,
                arrival_reason=session.player_identity,
                core_symbols=core_symbols,
            )
            
            # 发送剧情导入图片
            with open(scene_image, 'rb') as f:
                image_bytes = f.read()
            image_base64 = base64.b64encode(image_bytes).decode('ascii')
            await self.send_image(image_base64)
            
            await asyncio.sleep(0.5)
            
            # 生成场景结构文字长图
            scene_structure = session.scene_structure or {}
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
            
            # 发送场景结构文字长图
            with open(scene_structure_image, 'rb') as f:
                image_bytes = f.read()
            image_base64 = base64.b64encode(image_bytes).decode('ascii')
            await self.send_image(image_base64)
            
            await asyncio.sleep(0.5)
            
            # 生成规则图片
            rules_image = await image_generator.generate_rules_image(
                rules_title=f"{session.scene_name} - 规则",
                rules=session.rules,
                win_condition=session.win_condition,
                game_mode=game_mode,
            )
            
            # 发送规则图片
            with open(rules_image, 'rb') as f:
                image_bytes = f.read()
            image_base64 = base64.b64encode(image_bytes).decode('ascii')
            await self.send_image(image_base64)
            
            await asyncio.sleep(0.5)
            
            # 发送文字说明
            if game_mode == "多人":
                await self.send_text(
                    f"**游戏已开始！**\n\n"
                    f"模式：{game_mode}\n"
                    f"场景：{session.scene_name}\n\n"
                    f"其他玩家请使用 `/rg 加入` 加入游戏。\n"
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
            logger.error(f"强制开始游戏失? {e}", exc_info=True)
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
            
            # 使用自定义名称保?            custom_group_id = f"{group_id}_{save_name}"
            await save_manager.save_immediately(custom_group_id, state.session)
            
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
        custom_group_id = f"{group_id}_{save_name}"
        save_manager = SaveManager()
        
        try:
            session = await save_manager.load(custom_group_id)
            
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
            # 获取所有存档文?            import os
            save_dir = save_manager.save_dir
            
            if not os.path.exists(save_dir):
                await self.send_text("**存档列表**\n\n暂无存档。")
                return True, "存档列表已显示", 2
            
            # 查找所有相关存档
            saves = []
            for filename in os.listdir(save_dir):
                if filename.startswith(f"{group_id}") and filename.endswith(".json"):
                    save_path = os.path.join(save_dir, filename)
                    try:
                        session = await save_manager.load(filename[:-5])  # 去掉 .json
                        if session:
                            save_name = filename[:-5].replace(f"{group_id}_", "")
                            if save_name == group_id:
                                save_name = "默认存档"
                            
                            saves.append({
                                "name": save_name,
                                "scene": session.scene_name,
                                "mode": session.game_mode,
                                "status": session.status.value,
                                "created_at": getattr(session, 'created_at', None),
                            })
                    except Exception as e:
                        logger.warning(f"读取存档失败: {filename}, {e}")
                        continue
            
            if not saves:
                await self.send_text("**存档列表**\n\n暂无存档。")
                return True, "存档列表已显示", 2
            
            # 构建存档列表文本
            saves_text = ["**存档列表**\n"]
            for i, save in enumerate(saves, 1):
                created_at = save.get('created_at')
                time_str = created_at.strftime("%Y-%m-%d %H:%M") if created_at else "未知时间"
                
                saves_text.append(
                    f"{i}. {save['name']}\n"
                    f"   场景：{save['scene']}\n"
                    f"   模式：{save['mode']}\n"
                    f"   状态：{save['status']}\n"
                    f"   时间：{time_str}\n"
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
    _handle_inventory = _handle_物品栏
    _handle_bag = _handle_背包
    _handle_continue = _handle_继续
    _handle_force_start = _handle_强制开始
    _handle_restore = _handle_恢复
    _handle_save = _handle_保存
    _handle_load = _handle_读取
    _handle_save_list = _handle_存档列表
