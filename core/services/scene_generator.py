"""场景生成服务

使用 LLM 生成“规则怪谈”场景数据（SceneData）：背景、规则、线索、核心象征符号等。

该文件曾被批量替换破坏（引号缺失、字符串不闭合、全角标点落入语法层），此处按原意重写并保持对外接口：
- SceneType
- SceneData
- SceneGenerator.generate_scene()
- SceneGenerator.generate_progressive_reveal()

注意：项目中另有更复杂的 `GameGenerator`；本模块作为可复用的“场景生成器”仍保持可用。
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..llm.client import LLMClient, get_default_max_tokens

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
    """场景数据"""

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
    """场景生成器"""

    def __init__(self, llm_client: LLMClient | None = None):
        self.llm_client: LLMClient = llm_client or LLMClient()

    async def generate_scene(
        self,
        scene_type: SceneType | None = None,
        custom_prompt: str | None = None,
        game_mode: str = "单人",
    ) -> SceneData:
        """生成规则怪谈场景"""

        if custom_prompt:
            return await self._generate_from_custom_prompt(custom_prompt, game_mode)

        if scene_type is None:
            scene_type = self._random_scene_type()

        return await self._generate_from_type(scene_type, game_mode)

    async def _generate_from_type(self, scene_type: SceneType, game_mode: str) -> SceneData:
        system_prompt = self._build_system_prompt(game_mode)
        user_prompt = self._build_type_prompt(scene_type)

        try:
            resp = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.9,
                max_tokens=get_default_max_tokens(),
            )
            data = resp.parse_json()
            return self._parse_scene_data(data, scene_type)
        except Exception as e:
            logger.error(f"生成场景失败: {e}", exc_info=True)
            return self._get_fallback_scene(scene_type)

    async def _generate_from_custom_prompt(self, custom_prompt: str, game_mode: str) -> SceneData:
        system_prompt = self._build_system_prompt(game_mode)
        user_prompt = (
            "请根据以下描述生成规则怪谈场景，并按要求返回 JSON：\n\n"
            f"{custom_prompt}\n"
        )

        try:
            resp = await self.llm_client.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.9,
                max_tokens=get_default_max_tokens(),
            )
            data = resp.parse_json()
            return self._parse_scene_data(data, SceneType.CUSTOM)
        except Exception as e:
            logger.error(f"生成自定义场景失败: {e}", exc_info=True)
            return self._get_fallback_scene(SceneType.CUSTOM)

    def _build_system_prompt(self, game_mode: str) -> str:
        """构建系统提示词"""

        return (
            "你是规则怪谈游戏的场景生成器。你需要生成一个日常场所中的诡异场景。\n\n"
            "要求：\n"
            "1) 场景要具体、日常，不要用‘废弃的/神秘的/阴森的’这种空泛形容\n"
            "2) 背景 200-300 字，平淡语气暗含不对劲\n"
            "3) 规则 3-6 条，至少 1 条是陷阱（is_trap=true）\n"
            "4) 隐藏真相 120-200 字，解释规则为何存在\n"
            "5) 提供通关条件 win_condition\n"
            "6) 线索 clues（1-3 条，包含 location）\n"
            "7) 核心象征符号 core_symbols（1-2 个）\n"
            "8) 给出恐怖元素列表 horror_elements（2-4 个短语）\n"
            "9) 给出氛围描述 atmosphere_description（80-150 字）\n\n"
            f"游戏模式: {game_mode}\n\n"
            "只返回 JSON（不要 markdown，不要其他文字）：\n"
            "{\n"
            '  "scene_name": "场景名称",\n'
            '  "background": "背景故事",\n'
            '  "player_identity": "玩家身份",\n'
            '  "hidden_truth": "隐藏真相",\n'
            '  "rules": [ {"id": 1, "text": "规则", "is_trap": false} ],\n'
            '  "win_condition": "通关条件",\n'
            '  "clues": [ {"id": 1, "content": "线索内容", "location": "位置"} ],\n'
            '  "core_symbols": [ {"id": 1, "symbol": "符号", "meaning": "含义"} ],\n'
            '  "horror_elements": ["元素1", "元素2"],\n'
            '  "atmosphere_description": "氛围描述"\n'
            "}"
        )

    def _build_type_prompt(self, scene_type: SceneType) -> str:
        """构建场景类型提示词"""

        descriptions = {
            SceneType.SUBWAY: "地铁站（地下交通枢纽，金属与回声）",
            SceneType.HOSPITAL: "医院（消毒水、值班、走廊与病房）",
            SceneType.SCHOOL: "学校（夜间教学楼、公告栏、铃声）",
            SceneType.LIBRARY: "图书馆（借阅规则、静默、书页声）",
            SceneType.CONVENIENCE_STORE: "24小时便利店（深夜灯光、货架、店员守则）",
            SceneType.APARTMENT: "老旧公寓（楼道、门牌号、物业通知）",
            SceneType.ELEVATOR: "电梯（封闭空间、楼层按钮、广播）",
            SceneType.PARKING_LOT: "地下停车场（指示牌、车位号、回声）",
            SceneType.OFFICE: "办公室（加班、门禁、打印机、会议室）",
        }

        desc = descriptions.get(scene_type, "自定义场景")
        return (
            f"请生成一个以‘{desc}’为核心的规则怪谈场景。\n"
            f"场景类型: {scene_type.value}\n"
            "按系统提示的 JSON 格式返回。"
        )

    def _parse_scene_data(self, data: dict[str, Any], scene_type: SceneType) -> SceneData:
        """解析 LLM JSON -> SceneData"""

        rules = data.get("rules", [])
        if not isinstance(rules, list):
            rules = []

        clues = data.get("clues", [])
        if not isinstance(clues, list):
            clues = []

        core_symbols = data.get("core_symbols", [])
        if not isinstance(core_symbols, list):
            core_symbols = []

        horror_elements = data.get("horror_elements", [])
        if not isinstance(horror_elements, list):
            horror_elements = []

        return SceneData(
            scene_name=str(data.get("scene_name", "未知场景") or "未知场景"),
            scene_type=scene_type,
            background=str(data.get("background", "") or ""),
            player_identity=str(data.get("player_identity", "") or ""),
            hidden_truth=str(data.get("hidden_truth", "") or ""),
            rules=rules,
            win_condition=str(data.get("win_condition", "") or ""),
            clues=clues,
            core_symbols=core_symbols[:2],
            horror_elements=[str(x) for x in horror_elements][:4],
            atmosphere_description=str(data.get("atmosphere_description", "") or ""),
        )

    def _get_fallback_scene(self, scene_type: SceneType) -> SceneData:
        """LLM 失败时的备用场景"""

        return SceneData(
            scene_name=f"{scene_type.value}（备用场景）",
            scene_type=scene_type,
            background="一个看似正常的地方，却有几条没人愿意解释清楚的规矩。",
            player_identity="你是一个误入此地的普通人。",
            hidden_truth="这里的秩序并非为了保护你，而是在保护某种不能被看见的东西。",
            rules=[
                {"id": 1, "text": "听到广播时，先停下三秒再行动。", "is_trap": False},
                {"id": 2, "text": "如果灯光闪烁，别去数它闪了几次。", "is_trap": True},
                {"id": 3, "text": "不要在镜面前停留超过十秒。", "is_trap": False},
            ],
            win_condition="找到出口并离开。",
            clues=[
                {"id": 1, "content": "墙上的通知单被反复撕贴，某一行字被涂得很黑。", "location": "走廊"}
            ],
            core_symbols=[
                {"id": 1, "symbol": "闪烁的灯", "meaning": "被篡改的规则与不稳定的现实"}
            ],
            horror_elements=["异常的安静", "不合逻辑的影子"],
            atmosphere_description="空气里像积着陈旧的灰，声音被压得很低。你每走一步，都像踩在某种不愿承认你存在的地面上。",
        )

    def _random_scene_type(self) -> SceneType:
        """随机选择一个非 CUSTOM 类型"""

        choices = [t for t in SceneType if t != SceneType.CUSTOM]
        return random.choice(choices)

    async def generate_progressive_reveal(self, scene_data: SceneData, stage: int = 1) -> str:
        """生成渐进式信息揭示（纯文本）"""

        s = max(1, min(3, int(stage)))
        system_prompt = (
            "你是规则怪谈游戏的渐进式信息揭示器。\n"
            "请根据阶段输出一段中文文本（150-220字），只输出纯文本。\n"
            "阶段：1=入场与初步暗示；2=规则与结构的提示；3=真相与恐怖元素的暗示。"
        )

        if s == 1:
            prompt = (
                f"场景名称: {scene_data.scene_name}\n"
                f"背景: {scene_data.background}\n"
                f"玩家身份: {scene_data.player_identity}\n"
                f"氛围: {scene_data.atmosphere_description}\n\n"
                "请输出阶段1揭示。"
            )
        elif s == 2:
            rules_preview = "\n".join(f"- {r.get('text', str(r))}" for r in scene_data.rules[:2])
            clues_preview = "\n".join(
                f"- {c.get('location', '某处')}: {c.get('content', str(c))}" for c in scene_data.clues[:2]
            )
            prompt = (
                f"场景名称: {scene_data.scene_name}\n\n"
                f"部分规则:\n{rules_preview if rules_preview else '（无）'}\n\n"
                f"部分线索/结构:\n{clues_preview if clues_preview else '（无）'}\n\n"
                "请输出阶段2揭示。"
            )
        else:
            elements = "\n".join(f"- {x}" for x in scene_data.horror_elements)
            prompt = (
                f"场景名称: {scene_data.scene_name}\n\n"
                f"隐藏真相: {scene_data.hidden_truth}\n\n"
                f"恐怖元素:\n{elements if elements else '（无）'}\n\n"
                "请输出阶段3揭示。"
            )

        try:
            resp = await self.llm_client.call(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.8,
                max_tokens=get_default_max_tokens(),
            )
            return resp.clean_content
        except Exception as e:
            logger.error(f"生成渐进式揭示失败: {e}", exc_info=True)
            return "有些事情正在发生变化，但你还说不清那是什么。"
