import logging
from datetime import datetime
from typing import TypeAlias

from ..core.llm.client import LLMClient

logger = logging.getLogger(__name__)

GameState: TypeAlias = dict[str, object]
EnvironmentData: TypeAlias = dict[str, object]


class EnvironmentEvolutionSystem:
    """生成并保存开局环境信息。"""

    def __init__(self, game_states: dict[str, GameState], llm_client: LLMClient | None = None):
        self.game_states = game_states
        self.llm_client = llm_client or LLMClient()

    async def initialize_environment(
        self,
        group_id: str,
        scene_type: str,
        player_identity: str,
        building_type: str,
        use_llm: bool = True,
        temperature: float = 0.7,
    ) -> EnvironmentData:
        game_state = self.game_states.get(group_id)
        if game_state is None:
            game_state = {}
            self.game_states[group_id] = game_state

        environment_data: EnvironmentData = {
            "npcs": [],
            "scene_type": scene_type,
            "building_type": building_type,
            "time": {
                "current_time": "开场时刻",
                "elapsed_minutes": 0,
                "last_update": datetime.now().isoformat(),
                "time_phase": "开场",
            },
            "environment_state": self._default_environment_state(),
            "active_events": [],
            "event_history": [],
            "npc_interactions": [],
            "environmental_changes": [],
            "identity_system": {
                "current_identity": player_identity,
                "identity_history": [player_identity],
                "access_permissions": {},
                "identity_guides": {},
            },
        }

        if use_llm:
            llm_environment = await self._generate_environment_with_llm(
                scene_type,
                building_type,
                player_identity,
                temperature,
            )
            if llm_environment:
                environment_data["environment_state"] = llm_environment

        game_state["environment_evolution"] = environment_data
        return environment_data

    async def _generate_environment_with_llm(
        self,
        scene_type: str,
        building_type: str,
        player_identity: str,
        temperature: float = 0.7,
    ) -> dict[str, object] | None:
        prompt = f"""
你是一位规则怪谈环境设计师。请为以下场景生成初始环境状态。

场景类型：{scene_type}
建筑类型：{building_type}
玩家身份：{player_identity}

环境设计要求：
1. 光线状况：由场景类型和此刻合理的时段决定，例如白炽灯管、午后阳光、应急灯或闪烁不定的灯光
2. 温度感受：描述具体体感，例如空调过冷、闷热、阴冷或暖气过足
3. 声音：列出 2-4 个具体声音，例如广播声、键盘敲击声、远处脚步声或通风管嗡鸣
4. 气味：列出 2-3 个具体气味，例如消毒水、饭菜、陈旧纸张或霉味
5. 整体氛围：用一句具体的话描述，不要只给单个形容词

设计要求：
- 时段由你根据场景自行决定，不必固定在深夜；日常场所的白天同样可以诡异
- 环境整体应大体正常，但有一两处说不上来的不对劲；不要堆砌恐怖词汇
- 环境细节可以克制地暗示隐藏真相或规则
- 不要使用 emoji

请仅返回 JSON：
{{
  "lighting": "光线状况",
  "temperature": "温度感受",
  "sounds": ["声音1", "声音2"],
  "smells": ["气味1", "气味2"],
  "atmosphere": "整体氛围描述"
}}"""
        try:
            response = await self.llm_client.call(
                prompt=prompt,
                temperature=temperature,
                max_tokens=2000,
            )
            result = response.parse_json()
            if not isinstance(result, dict):
                return None
            defaults = self._default_environment_state()
            for field, value in defaults.items():
                result.setdefault(field, value)
            return result
        except Exception as exc:
            logger.error("[环境演化] 使用LLM生成环境失败: %s", exc)
            return None

    @staticmethod
    def _default_environment_state() -> dict[str, object]:
        return {
            "lighting": "未特别记录",
            "temperature": "未特别记录",
            "sounds": [],
            "smells": [],
            "atmosphere": "未特别记录",
        }
