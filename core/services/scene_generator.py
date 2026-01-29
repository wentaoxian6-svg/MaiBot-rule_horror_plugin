"""场景生成器 - 使用 LLM 生成沉浸式的规则怪谈场景"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from ..llm.client import LLMClient, LLMResponse

logger = logging.getLogger(__name__)


class SceneType(Enum):
    """场景类型枚举"""
    SUBWAY = "subway"
    HOSPITAL = "hospital"
    SCHOOL = "school"
    LIBRARY = "library"
    CONVENIENCE_STORE = "convenience_store"
    APARTMENT = "apartment"
    ELEVATOR = "elevator"
    PARKING_LOT = "parking_lot"
    OFFICE = "office"
    CUSTOM = "custom"


@dataclass
class SceneData:
    """场景数据类"""
    scene_name: str
    scene_type: SceneType
    background: str
    player_identity: str
    hidden_truth: str
    rules: list[dict[str, Any]]
    win_condition: str
    clues: list[dict[str, Any]]
    core_symbols: list[dict[str, Any]]
    horror_elements: list[str]
    atmosphere_description: str


class SceneGenerator:
    """场景生成器 - 使用 LLM 生成沉浸式的规则怪谈场景"""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()

    async def generate_scene(
        self,
        scene_type: Optional[SceneType] = None,
        custom_prompt: Optional[str] = None,
        game_mode: str = "单人",
    ) -> SceneData:
        """
        生成规则怪谈场景

        Args:
            scene_type: 场景类型，如果为 None 则随机选择
            custom_prompt: 自定义提示词，如果提供则覆盖 scene_type
            game_mode: 游戏模式（单人/多人）

        Returns:
            SceneData 对象
        """
        if custom_prompt:
            return await self._generate_from_custom_prompt(custom_prompt, game_mode)
        else:
            if scene_type is None:
                scene_type = self._random_scene_type()
            return await self._generate_from_type(scene_type, game_mode)

    async def _generate_from_type(
        self,
        scene_type: SceneType,
        game_mode: str,
    ) -> SceneData:
        """根据场景类型生成"""
        system_prompt = self._build_system_prompt(game_mode)
        user_prompt = self._build_type_prompt(scene_type)

        try:
            response = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.9,
                max_tokens=2000,
            )

            result = response.parse_json()
            return self._parse_scene_data(result, scene_type)

        except Exception as e:
            logger.error(f"生成场景失败: {e}")
            return self._get_fallback_scene(scene_type)

    async def _generate_from_custom_prompt(
        self,
        custom_prompt: str,
        game_mode: str,
    ) -> SceneData:
        """根据自定义提示词生成"""
        system_prompt = self._build_system_prompt(game_mode)
        user_prompt = f"""请根据以下描述生成规则怪谈场景：

{custom_prompt}

请按照要求的 JSON 格式返回。"""

        try:
            response = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.9,
                max_tokens=2000,
            )

            result = response.parse_json()
            return self._parse_scene_data(result, SceneType.CUSTOM)

        except Exception as e:
            logger.error(f"生成自定义场景失败: {e}")
            return self._get_fallback_scene(SceneType.CUSTOM)

    def _build_system_prompt(self, game_mode: str) -> str:
        """构建系统提示词"""
        return f"""你是一个规则怪谈游戏的场景生成器。你的任务是生成沉浸式的恐怖场景。

场景生成要求：
1. 生成一个日常场所作为场景（地铁站、医院、学校、图书馆、便利店、公寓、电梯、停车场、办公室等）
2. 创建一个看似正常的欢迎信息
3. 生成 3-5 条规则，其中至少有一条是错误的或陷阱
4. 包含微妙的异常暗示
5. 设计一个隐藏的真相
6. 提供通关条件
7. 生成线索和核心符号
8. 添加恐怖元素
9. 描述氛围

风格要求：
- 克系恐怖（Cosmic Horror）
- 新怪谈（New Weird）
- Liminal Space（阈限空间）
- 认知失调
- 渐进式异化

游戏模式：{game_mode}

请以 JSON 格式返回：
{{
    "scene_name": "场景名称",
    "background": "背景故事（200-300字）",
    "player_identity": "玩家身份描述",
    "hidden_truth": "隐藏的真相（100-150字）",
    "rules": [
        {{"id": 1, "text": "规则内容", "is_trap": false}},
        {{"id": 2, "text": "规则内容", "is_trap": true}}
    ],
    "win_condition": "通关条件",
    "clues": [
        {{"id": 1, "content": "线索内容", "location": "线索位置"}}
    ],
    "core_symbols": [
        {{"id": 1, "symbol": "符号名称", "meaning": "符号含义"}}
    ],
    "horror_elements": ["恐怖元素1", "恐怖元素2"],
    "atmosphere_description": "氛围描述（100-150字）"
}}

注意：
- 规则应该看起来合理但隐藏着危险
- 陷阱规则应该看起来像正常规则
- 线索应该帮助玩家发现真相
- 恐怖元素应该营造紧张和不安的氛围
- 氛围描述应该使用感官语言"""

    def _build_type_prompt(self, scene_type: SceneType) -> str:
        """构建场景类型提示词"""
        type_descriptions = {
            SceneType.SUBWAY: "地铁站 - 地下的交通枢纽，充满机械声和人流",
            SceneType.HOSPITAL: "医院 - 治疗疾病的场所，充满消毒水味和不安",
            SceneType.SCHOOL: "学校 - 学习的场所，充满回忆和诡异",
            SceneType.LIBRARY: "图书馆 - 知识的殿堂，安静而神秘",
            SceneType.CONVENIENCE_STORE: "便利店 - 24小时营业，深夜的孤岛",
            SceneType.APARTMENT: "公寓 - 居住的空间，私密而诡异",
            SceneType.ELEVATOR: "电梯 - 封闭的空间，移动的盒子",
            SceneType.PARKING_LOT: "停车场 - 停放车辆的地方，阴暗而空旷",
            SceneType.OFFICE: "办公室 - 工作的场所，重复而压抑",
        }

        description = type_descriptions.get(scene_type, "未知场景")
        return f"""请生成一个{description}的规则怪谈场景。

场景类型：{scene_type.value}

请按照要求的 JSON 格式返回。"""

    def _parse_scene_data(self, result: dict[str, Any], scene_type: SceneType) -> SceneData:
        """将解析结果转换为 SceneData 对象"""
        return SceneData(
            scene_name=result.get("scene_name", "未知场景"),
            scene_type=scene_type,
            background=result.get("background", ""),
            player_identity=result.get("player_identity", ""),
            hidden_truth=result.get("hidden_truth", ""),
            rules=result.get("rules", []),
            win_condition=result.get("win_condition", ""),
            clues=result.get("clues", []),
            core_symbols=result.get("core_symbols", []),
            horror_elements=result.get("horror_elements", []),
            atmosphere_description=result.get("atmosphere_description", ""),
        )

    def _get_fallback_scene(self, scene_type: SceneType) -> SceneData:
        """获取备用场景（当 LLM 调用失败时）"""
        return SceneData(
            scene_name=f"{scene_type.value}（备用场景）",
            scene_type=scene_type,
            background="一个神秘的场所，隐藏着未知的危险。",
            player_identity="你是一个误入此地的普通人。",
            hidden_truth="这里的一切都是虚假的，你被困在了一个循环中。",
            rules=[
                {"id": 1, "text": "不要相信任何红色的东西。", "is_trap": False},
                {"id": 2, "text": "当听到钟声时，立即停止移动。", "is_trap": False},
                {"id": 3, "text": "如果你看到镜子，不要看里面的倒影。", "is_trap": True},
            ],
            win_condition="找到出口并离开。",
            clues=[
                {"id": 1, "content": "墙上有一行字：红色是警告。", "location": "走廊"},
            ],
            core_symbols=[
                {"id": 1, "symbol": "红色", "meaning": "危险和警告"},
            ],
            horror_elements=["异常的寂静", "扭曲的空间"],
            atmosphere_description="空气中弥漫着不安的气息，仿佛有什么东西在注视着你。",
        )

    def _random_scene_type(self) -> SceneType:
        """随机选择场景类型"""
        import random
        return random.choice(list(SceneType))

    async def generate_progressive_reveal(
        self,
        scene_data: SceneData,
        stage: int = 1,
    ) -> str:
        """
        生成渐进式信息揭示

        Args:
            scene_data: 场景数据
            stage: 揭示阶段（1-3）

        Returns:
            揭示的文本
        """
        system_prompt = """你是一个规则怪谈游戏的渐进式信息揭示器。你的任务是逐步向玩家揭示场景的真相。

揭示阶段：
- 阶段1：给出入场和初步的剧情导入
- 阶段2：揭示部分规则和场景结构
- 阶段3：暗示隐藏的真相和恐怖元素

请根据阶段生成相应的文本，保持神秘感和恐怖氛围。"""

        if stage == 1:
            prompt = f"""场景名称：{scene_data.scene_name}

背景：{scene_data.background}

玩家身份：{scene_data.player_identity}

氛围：{scene_data.atmosphere_description}

请生成入场和初步的剧情导入（150-200字）。"""
        elif stage == 2:
            prompt = f"""场景名称：{scene_data.scene_name}

规则：
"""
            for rule in scene_data.rules[:2]:
                prompt += f"- {rule['text']}\n"

            prompt += f"""
场景结构：
"""
            for clue in scene_data.clues[:2]:
                prompt += f"- {clue['location']}: {clue['content']}\n"

            prompt += """
请生成规则和场景结构的揭示（150-200字）。"""
        else:
            prompt = f"""场景名称：{scene_data.scene_name}

隐藏的真相：{scene_data.hidden_truth}

恐怖元素：
"""
            for element in scene_data.horror_elements:
                prompt += f"- {element}\n"

            prompt += """
请生成隐藏真相和恐怖元素的暗示（150-200字）。"""

        try:
            response = await self.llm_client.call(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.8,
                max_tokens=300,
            )

            return response.content.strip()

        except Exception as e:
            logger.error(f"生成渐进式揭示失败: {e}")
            return "有些事情正在发生变化..."
