"""环境演化系统：生成并保存开局环境信息，并让环境随时间/理智值/事件演化。

环境状态 dict schema：
{
    "lighting": str,              # 光照描述
    "sounds": list[str],          # 声音列表（时段基底音 + 场景专属音叠加）
    "scene_sounds": list[str],   # 场景专属音快照（开局 LLM 生成，演化时保留）
    "smells": list[str],          # 气味列表
    "temperature": float,         # 温度（摄氏度），向时段目标温度收敛
    "base_temperature": float,    # 场景初始温度，作为时段目标温度的基准
    "atmosphere": str,            # 氛围描述
    "entropy_level": int,         # 混乱度
    "doors": list[dict],          # 门列表 [{"rooms": [A, B], "state": DoorState}]
    "lights": dict[str, str],     # 灯具 {"灯名": LightState.value}
    "room_graph": dict,           # 房间图
    "npcs": list[dict],           # NPC 列表
    ...
}
"""
from datetime import datetime
from enum import Enum
from typing import TypeAlias

import logging
import random

from ..common.constants import SanityThresholds, TimeThresholds
from ..core.game.models import GameSession, PlayerStatus
from ..core.llm.client import LLMClient

logger = logging.getLogger(__name__)

GameState: TypeAlias = dict[str, object]
EnvironmentData: TypeAlias = dict[str, object]


class DoorState(Enum):
    """门的状态"""

    CLOSED = "关闭"
    OPEN = "打开"
    LOCKED = "上锁"
    BROKEN = "损坏"


class LightState(Enum):
    """灯光状态"""

    OFF = "关闭"
    DIM = "昏暗"
    NORMAL = "正常"
    FLICKERING = "闪烁"
    BLOOD_RED = "血红色"


class EnvironmentEvolutionSystem:
    """生成并保存开局环境信息，并让环境随时间/理智值/事件演化。"""

    # 时段预设规则表：避免每次行动都调 LLM，直接按 elapsed_minutes 分段叠加
    # 时段划分：
    # - opening:    < MIDNIGHT(60)                    开场氛围
    # - midnight:   MIDNIGHT(60) <= t < DAWN(180)     深夜，灯光变暗、声音减少
    # - deep_night: DAWN(180) <= t < EARLY_MORNING(300)  午夜，氛围更压抑
    # - pre_dawn:   >= EARLY_MORNING(300)             黎明前，极度压抑
    # 字段语义：
    # - lighting/atmosphere: 时段层直接覆盖
    # - sounds: 仅作为基底层，与场景专属音（scene_sounds）叠加，不整体替换
    # - target_temperature: 该时段的目标温度（以 22℃ 室内基准计算），运行时按场景
    #   初始温度 base_temperature 平移：实际 target = base + (target_temperature - 22)
    # - scene_sound_distortion: 时段对场景专属音的扭曲前缀，越深夜扭曲越强
    TIME_PHASE_RULES: dict[str, dict[str, object]] = {
        "opening": {
            "lighting": "灯光正常，白炽灯管稳定亮着",
            "sounds": ["远处的键盘敲击声", "通风管的低频嗡鸣"],
            "atmosphere": "一切看起来正常，但有种说不清的违和感",
            "target_temperature": 22.0,
            "smells": ["淡淡的消毒水味"],
            "light_flicker_chance": 0.0,
            "scene_sound_distortion": "",
        },
        "midnight": {
            "lighting": "灯光开始闪烁，偶尔熄灭几秒",
            "sounds": ["不存在的脚步声", "远处门轴的吱呀声"],
            "atmosphere": "空气变得沉重，温度似乎下降了几度",
            "target_temperature": 19.0,
            "smells": ["霉味"],
            "light_flicker_chance": 0.1,
            "scene_sound_distortion": "远处传来的",
        },
        "deep_night": {
            "lighting": "灯光大部分时间熄灭，只剩应急灯的微弱红光",
            "sounds": ["墙壁深处的抓挠声", "自己心跳的回响"],
            "atmosphere": "黑暗像有实体，压在皮肤上",
            "target_temperature": 17.0,
            "smells": ["铁锈味"],
            "light_flicker_chance": 0.3,
            "scene_sound_distortion": "隐约的",
        },
        "pre_dawn": {
            "lighting": "黑暗中开始出现不自然的微光",
            "sounds": ["所有声音都消失了，只剩耳鸣", "似乎有人在耳边低语"],
            "atmosphere": "现实感开始瓦解，时间变得粘稠",
            "target_temperature": 18.0,
            "smells": ["潮湿气息"],
            "light_flicker_chance": 0.2,
            "scene_sound_distortion": "扭曲的",
        },
    }

    # 理智低下时追加的幻觉声音池
    HALLUCINATION_SOUNDS: tuple[str, ...] = (
        "墙壁里有人在叫你的名字",
        "身后的呼吸声，但转头什么也没有",
        "滴答声像是从自己颅骨里传来",
        "远方有人用你死去亲人的语调说话",
    )

    # 理智低下时追加的扭曲氛围描述池
    HALLUCINATION_ATMOSPHERE: tuple[str, ...] = (
        "墙角的阴影正以肉眼可见的速度蠕动",
        "地砖的纹路像是某种符号在缓慢重组",
        "镜子里的自己比眼前慢半拍",
        "天花板的污渍正拼出一张似曾相识的脸",
    )

    # 理智低下时追加的幻觉嗅觉池
    HALLUCINATION_SMELLS: tuple[str, ...] = (
        "腐臭味",
        "铁锈味",
        "烧焦味",
    )

    # 事件类型 -> 氛围修饰文本
    EVENT_ATMOSPHERE_PATCHES: dict[str, str] = {
        "violation": "空气中残留着违逆规则后的焦灼气息",
        "combat": "墙壁似乎在吸附着残留的血腥气",
        "discover_clue": "刚发现的线索让周围的一切显得更加意味深长",
    }

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
2. 温度：当前环境的摄氏度数值（float），结合场景合理设定，例如办公室空调 22.5、地下室 16.0、冬日清晨 -5.0
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
  "temperature": 22.5,
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
            # 把 LLM 生成的初始温度固化为场景基准温度，供时段目标温度计算使用
            result["base_temperature"] = result["temperature"]
            return result
        except Exception as exc:
            logger.error("[环境演化] 使用LLM生成环境失败: %s", exc)
            return None

    async def evolve(
        self,
        session: GameSession,
        elapsed_minutes: int,
        recent_events: list[dict[str, object]] | None = None,
    ) -> None:
        """环境演化入口：根据时间、理智值、近期事件更新 environment_state。

        在每次玩家行动前调用一次，使用预设规则表更新字段，避免每次行动都调 LLM。

        Args:
            session: 游戏会话
            elapsed_minutes: 自开局以来经过的分钟数
            recent_events: 近期事件列表，每个事件是 dict，含 ``type``/``description`` 等字段；
                支持 ``violation``/``combat``/``discover_clue`` 三类事件
        """
        recent_events = recent_events or []
        if not isinstance(session.environment_state, dict):
            # environment_state 不是 dict 说明开局初始化未完成，记录后跳过，不掩盖错误
            logger.warning("[环境演化] session.environment_state 不是 dict，跳过本次演化")
            return

        env_state = session.environment_state

        # 1) 随时间演化：按 elapsed_minutes 选择时段预设，覆盖 lighting/sounds/atmosphere
        phase_key = self._resolve_time_phase(elapsed_minutes)
        self._apply_time_phase(env_state, phase_key)

        # 2) 随理智值演化：任一存活玩家理智低于 MEDIUM 即追加幻觉元素
        self._apply_sanity_effects(session, env_state)

        # 3) 随事件演化：根据 recent_events 调整氛围
        self._apply_event_effects(env_state, recent_events)

        # 同步 time_manager 的 time_phase 字段，便于其他模块感知当前时段
        if isinstance(session.time_manager, dict):
            session.time_manager["time_phase"] = phase_key
            session.time_manager["elapsed_minutes"] = elapsed_minutes

        logger.debug("[环境演化] 完成，elapsed_minutes=%s, phase=%s", elapsed_minutes, phase_key)

    @staticmethod
    def _resolve_time_phase(elapsed_minutes: int) -> str:
        """根据 elapsed_minutes 对照 TimeThresholds 解析时段 key。"""
        if elapsed_minutes < TimeThresholds.MIDNIGHT:
            return "opening"
        if elapsed_minutes < TimeThresholds.DAWN:
            return "midnight"
        if elapsed_minutes < TimeThresholds.EARLY_MORNING:
            return "deep_night"
        return "pre_dawn"

    def _apply_time_phase(self, env_state: dict[str, object], phase_key: str) -> None:
        """按时段预设表演化 lighting/atmosphere/sounds/temperature/smells/lights。

        与旧版的区别：
        - sounds 不再整体替换为时段基底音，而是以时段基底音 + 场景专属音叠加，
          场景专属音按时段扭曲前缀降权保留。
        - temperature 不再每次累加 delta，而是向时段目标温度收敛。
        """
        rules = self.TIME_PHASE_RULES.get(phase_key)
        if not rules:
            return
        env_state["lighting"] = rules["lighting"]
        env_state["atmosphere"] = rules["atmosphere"]

        # 场景专属音保留：开局 LLM 生成的 sounds 在首次演化时快照到 scene_sounds
        # 后续演化以 scene_sounds 为场景层、TIME_PHASE_RULES.sounds 为时段基底叠加
        # 幻觉元素由 _apply_sanity_effects 追加
        if "scene_sounds" not in env_state:
            current_sounds = env_state["sounds"]  # 缺失直接抛 KeyError
            env_state["scene_sounds"] = (
                list(current_sounds) if isinstance(current_sounds, list) else []
            )
        scene_sounds = env_state["scene_sounds"]

        # 时段基底音（拷贝避免污染类级预设表）
        phase_sounds = rules["sounds"]
        phase_sounds_list = list(phase_sounds) if isinstance(phase_sounds, list) else []

        # 叠加场景专属音：按时段扭曲描述符降权，不整体替换
        scene_sound_distortion = rules["scene_sound_distortion"]
        merged_sounds = list(phase_sounds_list)
        for sound in scene_sounds:
            if sound in merged_sounds:
                continue
            if scene_sound_distortion:
                merged_sounds.append(f"{scene_sound_distortion}{sound}")
            else:
                merged_sounds.append(sound)
        env_state["sounds"] = merged_sounds

        # 温度演化：向时段目标温度收敛（target = base + delta），不再累加 delta
        # 避免多次行动后温度跌至零下几十度
        base_temp = env_state["base_temperature"]  # 缺失直接抛 KeyError
        # 时段目标温度 = 场景初始温度 + 时段相对 22℃ 基准的偏移
        target_temp = base_temp + (rules["target_temperature"] - 22.0)
        current_temp = env_state["temperature"]  # 缺失直接抛 KeyError
        env_state["temperature"] = current_temp + (target_temp - current_temp) * 0.5

        # 嗅觉演化：追加该时段的环境嗅觉（去重，避免重复堆叠）
        smells = env_state.get("smells")
        if not isinstance(smells, list):
            smells = []
            env_state["smells"] = smells
        phase_smells = rules["smells"]
        if isinstance(phase_smells, list):
            for smell in phase_smells:
                if smell not in smells:
                    smells.append(smell)

        # 灯光演化：按 light_flicker_chance 概率翻转灯状态（NORMAL <-> FLICKERING）
        light_flicker_chance = rules["light_flicker_chance"]
        lights = env_state.get("lights")
        if (
            isinstance(lights, dict)
            and isinstance(light_flicker_chance, (int, float))
            and light_flicker_chance > 0
        ):
            for light_id in list(lights.keys()):
                if random.random() < light_flicker_chance:
                    current = lights[light_id]
                    current_str = current.value if isinstance(current, LightState) else str(current)
                    if current_str == LightState.NORMAL.value:
                        lights[light_id] = LightState.FLICKERING.value
                    elif current_str == LightState.FLICKERING.value:
                        lights[light_id] = LightState.NORMAL.value

    def _apply_sanity_effects(self, session: GameSession, env_state: dict[str, object]) -> None:
        """玩家理智低于 SanityThresholds.MEDIUM 时追加幻觉元素（声音/嗅觉/氛围）。"""
        any_low_sanity = any(
            player.sanity < SanityThresholds.MEDIUM
            for player in session.players.values()
            if player.status == PlayerStatus.ALIVE
        )
        if not any_low_sanity:
            return

        # 追加幻觉声音（随机选取一条，去重避免多次演化后重复堆叠）
        sounds = env_state.get("sounds")
        if not isinstance(sounds, list):
            sounds = []
            env_state["sounds"] = sounds
        hallucination_sound = random.choice(self.HALLUCINATION_SOUNDS)
        if hallucination_sound not in sounds:
            sounds.append(hallucination_sound)

        # 追加幻觉嗅觉（随机选取一条，去重）
        smells = env_state.get("smells")
        if not isinstance(smells, list):
            smells = []
            env_state["smells"] = smells
        hallucination_smell = random.choice(self.HALLUCINATION_SMELLS)
        if hallucination_smell not in smells:
            smells.append(hallucination_smell)

        # 追加幻觉氛围描述（随机选取一条，仅在尚未追加过幻觉描述时追加，避免反复堆叠）
        atmosphere = str(env_state.get("atmosphere", "") or "")
        if not any(patch in atmosphere for patch in self.HALLUCINATION_ATMOSPHERE):
            hallucination = random.choice(self.HALLUCINATION_ATMOSPHERE)
            env_state["atmosphere"] = f"{atmosphere}。{hallucination}" if atmosphere else hallucination

    def _apply_event_effects(
        self,
        env_state: dict[str, object],
        recent_events: list[dict[str, object]],
    ) -> None:
        """根据 recent_events 中的违规/战斗/发现线索事件调整氛围。"""
        if not recent_events:
            return

        patches: list[str] = []
        for event in recent_events:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type", "") or "").strip().lower()
            if not event_type:
                continue
            patch = self.EVENT_ATMOSPHERE_PATCHES.get(event_type)
            if patch and patch not in patches:
                patches.append(patch)

        if not patches:
            return

        atmosphere = str(env_state.get("atmosphere", "") or "")
        # 仅追加尚未出现的修饰，避免反复堆叠
        new_patches = [p for p in patches if p not in atmosphere]
        if not new_patches:
            return
        suffix = "；".join(new_patches)
        env_state["atmosphere"] = f"{atmosphere}。{suffix}" if atmosphere else suffix

    @staticmethod
    def _default_environment_state() -> dict[str, object]:
        return {
            "lighting": "未特别记录",
            "temperature": 22.0,
            # 场景初始温度，作为时段目标温度计算的基准（target = base + 相对偏移）
            "base_temperature": 22.0,
            "sounds": [],
            "smells": [],
            "atmosphere": "未特别记录",
            "lights": {},
        }
