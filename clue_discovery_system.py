# pyright: reportDeprecated=false
# pyright: reportUnknownVariableType=false
# pyright: reportUnknownMemberType=false
# pyright: reportMissingParameterType=false
# pyright: reportAny=false

"""
渐进式线索发现系统
将线索拆分为观察、推理、NPC三类，增强探索过程
"""

from typing import Any
from enum import Enum
from dataclasses import dataclass


class ClueType(Enum):
    """线索类型"""
    OBSERVATION = "观察线索"
    INFERENCE = "推理线索"
    NPC = "NPC线索"
    ENVIRONMENT = "环境线索"
    ITEM = "物品线索"


class ClueDifficulty(Enum):
    """线索难度"""
    EASY = "简单"
    MEDIUM = "中等"
    HARD = "困难"
    EXPERT = "专家"


@dataclass
class Clue:
    """线索"""
    clue_id: str = ""
    clue_type: ClueType = ClueType.OBSERVATION
    title: str = ""
    description: str = ""
    location: str = ""
    difficulty: ClueDifficulty = ClueDifficulty.EASY
    required_items: list[str] | None = None
    required_actions: list[str] | None = None
    required_clues: list[str] | None = None
    hint: str = ""
    is_discovered: bool = False
    discovered_by: str | None = None
    discovered_at: int | None = None

    def __post_init__(self):
        if self.required_items is None:
            self.required_items = []
        if self.required_actions is None:
            self.required_actions = []
        if self.required_clues is None:
            self.required_clues = []


@dataclass
class ObservationClue:
    """观察线索"""
    clue_id: str = ""
    clue_type: ClueType = ClueType.OBSERVATION
    title: str = ""
    description: str = ""
    location: str = ""
    difficulty: ClueDifficulty = ClueDifficulty.EASY
    required_items: list[str] | None = None
    required_actions: list[str] | None = None
    required_clues: list[str] | None = None
    hint: str = ""
    is_discovered: bool = False
    discovered_by: str | None = None
    discovered_at: int | None = None
    observation_method: str = "仔细观察"
    observation_target: str = "未知目标"
    visibility_condition: str = "光线充足"

    def __post_init__(self):
        if self.required_items is None:
            self.required_items = []
        if self.required_actions is None:
            self.required_actions = []
        if self.required_clues is None:
            self.required_clues = []


@dataclass
class InferenceClue:
    """推理线索"""
    clue_id: str = ""
    clue_type: ClueType = ClueType.INFERENCE
    title: str = ""
    description: str = ""
    location: str = ""
    difficulty: ClueDifficulty = ClueDifficulty.MEDIUM
    required_items: list[str] | None = None
    required_actions: list[str] | None = None
    required_clues: list[str] | None = None
    hint: str = ""
    is_discovered: bool = False
    discovered_by: str | None = None
    discovered_at: int | None = None
    required_knowledge: list[str] | None = None
    inference_steps: list[str] | None = None

    def __post_init__(self):
        if self.required_items is None:
            self.required_items = []
        if self.required_actions is None:
            self.required_actions = []
        if self.required_clues is None:
            self.required_clues = []
        if self.required_knowledge is None:
            self.required_knowledge = []
        if self.inference_steps is None:
            self.inference_steps = []


@dataclass
class NPCclue:
    """NPC线索"""
    clue_id: str = ""
    clue_type: ClueType = ClueType.NPC
    title: str = ""
    description: str = ""
    location: str = ""
    difficulty: ClueDifficulty = ClueDifficulty.MEDIUM
    required_items: list[str] | None = None
    required_actions: list[str] | None = None
    required_clues: list[str] | None = None
    hint: str = ""
    is_discovered: bool = False
    discovered_by: str | None = None
    discovered_at: int | None = None
    required_npc: str = "未知NPC"
    required_attitude: str = "中立"
    dialogue_trigger: str = "询问"

    def __post_init__(self):
        if self.required_items is None:
            self.required_items = []
        if self.required_actions is None:
            self.required_actions = []
        if self.required_clues is None:
            self.required_clues = []


class ClueDiscoverySystem:
    """线索发现系统

    负责管理渐进式线索发现，确保：
    - 线索分为观察、推理、NPC三类
    - 玩家需要通过检查动作发现线索
    - 线索有难度分级
    - 线索之间有依赖关系
    - 支持线索提示系统
    """

    def __init__(self):
        self.clues: dict[str, Clue] = {}
        self.discovered_clues: set[str] = set()
        self.player_progress: dict[str, dict[str, Any]] = {}
        self.location_clues: dict[str, list[str]] = {}
        self.item_clues: dict[str, list[str]] = {}
        self.npc_clues: dict[str, list[str]] = {}
    
    def add_clue(self, clue: Clue):
        """添加线索"""
        self.clues[clue.clue_id] = clue
        
        if clue.location not in self.location_clues:
            self.location_clues[clue.location] = []
        self.location_clues[clue.location].append(clue.clue_id)
        
        if clue.required_items:
            for item in clue.required_items:
                if item not in self.item_clues:
                    self.item_clues[item] = []
                self.item_clues[item].append(clue.clue_id)

        # 检查是否为NPC线索（支持继承检查或clue_type检查）
        is_npc_clue = isinstance(clue, NPCclue) or clue.clue_type == ClueType.NPC
        if is_npc_clue:
            # 优先使用NPCclue的required_npc属性，否则尝试从description推断
            npc_id = getattr(clue, 'required_npc', None)
            if npc_id is None and isinstance(clue, NPCclue):
                npc_id = "未知NPC"
            if npc_id:
                if npc_id not in self.npc_clues:
                    self.npc_clues[npc_id] = []
                self.npc_clues[npc_id].append(clue.clue_id)
    
    def discover_clue(self, clue_id: str, player_id: str,
                     _discovery_method: str, game_time: int) -> bool:
        """发现线索
        
        Args:
            clue_id: 线索ID
            player_id: 玩家ID
            discovery_method: 发现方法
            game_time: 游戏时间
        
        Returns:
            是否成功发现
        """
        if clue_id not in self.clues:
            return False
        
        clue = self.clues[clue_id]
        
        if clue_id in self.discovered_clues:
            return False
        
        if not self._check_discovery_conditions(clue, player_id):
            return False
        
        clue.is_discovered = True
        clue.discovered_by = player_id
        clue.discovered_at = game_time
        self.discovered_clues.add(clue_id)
        
        if player_id not in self.player_progress:
            self.player_progress[player_id] = {
                "discovered_clues": [],
                "total_clues_discovered": 0
            }
        
        self.player_progress[player_id]["discovered_clues"].append(clue_id)
        self.player_progress[player_id]["total_clues_discovered"] += 1
        
        return True
    
    def _check_discovery_conditions(self, clue: Clue, player_id: str) -> bool:
        """检查发现条件"""
        player_progress = self.player_progress.get(player_id, {})
        discovered_clues = player_progress.get("discovered_clues", [])
        
        if clue.required_clues:
            for required_clue_id in clue.required_clues:
                if required_clue_id not in discovered_clues:
                    return False
        
        return True
    
    def get_available_clues(self, player_id: str, location: str,
                            inventory: list[str], _game_state: dict[str, Any]) -> list[Clue]:
        """获取可发现的线索
        
        Args:
            player_id: 玩家ID
            location: 当前位置
            inventory: 物品清单
            game_state: 游戏状态
        
        Returns:
            可发现的线索列表
        """
        available_clues = []
        
        location_clue_ids = self.location_clues.get(location, [])
        
        for clue_id in location_clue_ids:
            if clue_id in self.discovered_clues:
                continue
            
            clue = self.clues[clue_id]
            
            if not self._check_discovery_conditions(clue, player_id):
                continue
            
            if clue.required_items:
                has_required_items = any(
                    item in inventory for item in clue.required_items
                )
                if not has_required_items:
                    continue
            
            available_clues.append(clue)
        
        return available_clues
    
    def get_clue_hint(self, clue_id: str, player_id: str) -> str | None:
        """获取线索提示
        
        Args:
            clue_id: 线索ID
            player_id: 玩家ID
        
        Returns:
            提示文本
        """
        if clue_id not in self.clues:
            return None
        
        clue = self.clues[clue_id]
        
        if clue_id in self.discovered_clues:
            return "你已经发现了这个线索。"
        
        if not self._check_discovery_conditions(clue, player_id):
            return "你还不能发现这个线索，需要先发现其他线索。"
        
        return clue.hint
    
    def get_discovered_clues(self, player_id: str) -> list[Clue]:
        """获取已发现的线索

        Args:
            player_id: 玩家ID

        Returns:
            已发现的线索列表
        """
        discovered = []
        for clue_id in self.discovered_clues:
            clue = self.clues.get(clue_id)
            if clue and clue.discovered_by == player_id:
                discovered.append(clue)
        return discovered

    def get_clue_by_id(self, clue_id: str) -> Clue | None:
        """根据ID获取线索"""
        return self.clues.get(clue_id)

    def get_clues_by_type(self, clue_type: ClueType) -> list[Clue]:
        """根据类型获取线索"""
        return [clue for clue in self.clues.values() if clue.clue_type == clue_type]

    def get_clues_by_location(self, location: str) -> list[Clue]:
        """根据位置获取线索"""
        clue_ids = self.location_clues.get(location, [])
        return [self.clues[cid] for cid in clue_ids if cid in self.clues]

    def get_player_progress(self, player_id: str) -> dict[str, Any]:
        """获取玩家进度"""
        return self.player_progress.get(player_id, {
            "discovered_clues": [],
            "total_clues_discovered": 0
        })
    
    def calculate_discovery_rate(self, player_id: str) -> float:
        """计算发现率
        
        Args:
            player_id: 玩家ID
        
        Returns:
            发现率（0-1）
        """
        total_clues = len(self.clues)
        if total_clues == 0:
            return 0.0
        
        player_progress = self.player_progress.get(player_id, {})
        discovered_count = player_progress.get("total_clues_discovered", 0)
        
        return discovered_count / total_clues
    
    def generate_observation_prompt(self, clue: ObservationClue) -> str:
        """生成观察提示
        
        Args:
            clue: 观察线索
        
        Returns:
            观察提示文本
        """
        return f"""
你可以尝试{clue.observation_method}来发现线索：
- 目标：{clue.observation_target}
- 位置：{clue.location}
- 难度：{clue.difficulty.value}

提示：{clue.hint}
"""
    
    def generate_inference_prompt(self, clue: InferenceClue,
                                  _discovered_clues: list[Clue]) -> str:
        """生成推理提示
        
        Args:
            clue: 推理线索
            discovered_clues: 已发现的线索
        
        Returns:
            推理提示文本
        """
        prompt = f"""
你可以通过推理来发现线索：
- 标题：{clue.title}
- 难度：{clue.difficulty.value}

推理步骤：
"""
        if clue.inference_steps:
            for i, step in enumerate(clue.inference_steps, 1):
                prompt += f"{i}. {step}\n"
        
        prompt += f"\n提示：{clue.hint}"
        
        return prompt
    
    def generate_npc_prompt(self, clue: NPCclue) -> str:
        """生成NPC互动提示
        
        Args:
            clue: NPC线索
        
        Returns:
            NPC互动提示文本
        """
        return f"""
你可以通过与NPC互动来发现线索：
- 目标NPC：{clue.required_npc}
- 要求态度：{clue.required_attitude}
- 触发对话：{clue.dialogue_trigger}
- 位置：{clue.location}
- 难度：{clue.difficulty.value}

提示：{clue.hint}
"""
    
    def to_dict(self) -> dict[str, Any]:
        """序列化为字典"""
        return {
            "clues": {
                clue_id: {
                    "clue_id": clue.clue_id,
                    "clue_type": clue.clue_type.value,
                    "title": clue.title,
                    "description": clue.description,
                    "location": clue.location,
                    "difficulty": clue.difficulty.value,
                    "required_items": clue.required_items,
                    "required_actions": clue.required_actions,
                    "required_clues": clue.required_clues,
                    "hint": clue.hint,
                    "is_discovered": clue.is_discovered,
                    "discovered_by": clue.discovered_by,
                    "discovered_at": clue.discovered_at
                }
                for clue_id, clue in self.clues.items()
            },
            "discovered_clues": list(self.discovered_clues),
            "player_progress": self.player_progress,
            "location_clues": self.location_clues,
            "item_clues": self.item_clues,
            "npc_clues": self.npc_clues
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ClueDiscoverySystem":
        """从字典反序列化"""
        system = cls()
        
        clues_data = data.get("clues", {})
        for clue_id, clue_data in clues_data.items():
            clue_type = ClueType(clue_data["clue_type"])
            difficulty = ClueDifficulty(clue_data["difficulty"])
            
            clue = Clue(
                clue_id=clue_data["clue_id"],
                clue_type=clue_type,
                title=clue_data["title"],
                description=clue_data["description"],
                location=clue_data["location"],
                difficulty=difficulty,
                required_items=clue_data["required_items"],
                required_actions=clue_data["required_actions"],
                required_clues=clue_data["required_clues"],
                hint=clue_data["hint"],
                is_discovered=clue_data["is_discovered"],
                discovered_by=clue_data["discovered_by"],
                discovered_at=clue_data["discovered_at"]
            )
            
            system.clues[clue_id] = clue
        
        system.discovered_clues = set(data.get("discovered_clues", []))
        system.player_progress = data.get("player_progress", {})
        system.location_clues = data.get("location_clues", {})
        system.item_clues = data.get("item_clues", {})
        system.npc_clues = data.get("npc_clues", {})
        
        return system


def create_default_clues(_scene_name: str) -> list[Clue]:
    """创建默认线索"""
    clues = []
    
    observation_clue = ObservationClue(
        clue_id="obs_001",
        clue_type=ClueType.OBSERVATION,
        title="墙上的奇怪符号",
        description="墙上刻着一些奇怪的符号，似乎暗示着什么。",
        location="走廊",
        difficulty=ClueDifficulty.EASY,
        required_items=[],
        required_actions=["检查墙壁"],
        required_clues=[],
        hint="仔细观察墙上的裂缝和划痕",
        observation_method="仔细观察",
        observation_target="墙壁",
        visibility_condition="光线充足"
    )
    clues.append(observation_clue)
    
    inference_clue = InferenceClue(
        clue_id="inf_001",
        clue_type=ClueType.INFERENCE,
        title="规则的含义",
        description="通过分析规则的矛盾之处，发现隐藏的真相。",
        location="任意",
        difficulty=ClueDifficulty.MEDIUM,
        required_items=[],
        required_actions=["推理"],
        required_clues=["obs_001"],
        hint="注意规则中的矛盾和例外",
        required_knowledge=["规则内容"],
        inference_steps=[
            "对比不同规则的表述",
            "找出规则中的矛盾点",
            "思考规则制定者的意图",
            "推导出隐藏的真相"
        ]
    )
    clues.append(inference_clue)
    
    return clues
