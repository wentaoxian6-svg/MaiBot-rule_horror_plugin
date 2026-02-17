"""
多人协作物理存在感系统
增强多人模式下的实体交互和协作解谜
"""

import random
from enum import Enum
from dataclasses import dataclass
import math
from typing import TypeAlias

# 类型定义
PlayerDict: TypeAlias = dict[str, "str | dict | list | bool | None"]
MechanismDict: TypeAlias = dict[str, "str | dict | list | int | bool | None"]
PhysicsDict: TypeAlias = dict[str, "dict | float | None"]


class Direction(Enum):
    """方向"""
    NORTH = "北"
    SOUTH = "南"
    EAST = "东"
    WEST = "西"
    NORTHEAST = "东北"
    NORTHWEST = "西北"
    SOUTHEAST = "东南"
    SOUTHWEST = "西南"


@dataclass
class Position:
    """位置"""
    x: float
    y: float
    z: float = 0.0
    
    def distance_to(self, other: 'Position') -> float:
        """计算到另一个位置的距离"""
        return math.sqrt(
            (self.x - other.x) ** 2 +
            (self.y - other.y) ** 2 +
            (self.z - other.z) ** 2
        )
    
    def direction_to(self, other: 'Position') -> Direction:
        """计算到另一个位置的方向"""
        dx = other.x - self.x
        dy = other.y - self.y
        
        angle = math.atan2(dy, dx)
        degrees = math.degrees(angle)
        
        if -22.5 <= degrees < 22.5:
            return Direction.EAST
        elif 22.5 <= degrees < 67.5:
            return Direction.SOUTHEAST
        elif 67.5 <= degrees < 112.5:
            return Direction.SOUTH
        elif 112.5 <= degrees < 157.5:
            return Direction.SOUTHWEST
        elif 157.5 <= degrees <= 180 or -180 <= degrees < -157.5:
            return Direction.WEST
        elif -157.5 <= degrees < -112.5:
            return Direction.NORTHWEST
        elif -112.5 <= degrees < -67.5:
            return Direction.NORTH
        else:
            return Direction.NORTHEAST


@dataclass
class PlayerState:
    """玩家状态"""
    player_id: str
    name: str
    position: Position
    location: str  # 当前位置名称（如："大厅", "走廊", "101房间"）
    facing_direction: Direction
    is_alive: bool
    is_visible: bool
    is_speaking: bool
    current_action: str | None
    inventory: list[dict[str, object]]  # 物品列表，与 Player 类保持一致
    
    def can_see(self, other: 'PlayerState', max_distance: float = 10.0,
                view_angle: float = 120.0) -> bool:
        """判断是否能看到另一个玩家
        
        Args:
            other: 另一个玩家
            max_distance: 最大可见距离
            view_angle: 视野角度（度）
        
        Returns:
            是否能看到
        """
        if not other.is_visible or not other.is_alive:
            return False
        
        distance = self.position.distance_to(other.position)
        if distance > max_distance:
            return False
        
        direction_to_other = self.position.direction_to(other.position)
        
        direction_order = [
            Direction.EAST, Direction.SOUTHEAST, Direction.SOUTH,
            Direction.SOUTHWEST, Direction.WEST, Direction.NORTHWEST,
            Direction.NORTH, Direction.NORTHEAST
        ]
        
        try:
            self_index = direction_order.index(self.facing_direction)
            other_index = direction_order.index(direction_to_other)
            
            angle_diff = abs(self_index - other_index)
            if angle_diff > 4:
                angle_diff = 8 - angle_diff
            
            return angle_diff * 45 <= view_angle / 2
        except ValueError:
            return True
    
    def can_hear(self, other: 'PlayerState', max_distance: float = 20.0) -> bool:
        """判断是否能听到另一个玩家
        
        Args:
            other: 另一个玩家
            max_distance: 最大可听距离
        
        Returns:
            是否能听到
        """
        if not other.is_alive:
            return False
        
        distance = self.position.distance_to(other.position)
        return distance <= max_distance
    
    def get_hearing_quality(self, other: 'PlayerState', 
                            max_distance: float = 20.0) -> float:
        """获取听到的质量
        
        Args:
            other: 另一个玩家
            max_distance: 最大可听距离
        
        Returns:
            听到的质量（0-1）
        """
        if not self.can_hear(other, max_distance):
            return 0.0
        
        distance = self.position.distance_to(other.position)
        quality = 1.0 - (distance / max_distance)
        return max(0.0, quality)


@dataclass
class Mechanism:
    """机关"""
    mechanism_id: str
    name: str
    location: Position
    required_players: int
    current_players: set[str]
    is_activated: bool
    activation_time: int | None
    description: str
    
    def add_player(self, player_id: str) -> bool:
        """添加玩家
        
        Args:
            player_id: 玩家ID
        
        Returns:
            是否成功添加
        """
        if len(self.current_players) >= self.required_players:
            return False
        
        self.current_players.add(player_id)
        
        if len(self.current_players) >= self.required_players:
            self.is_activated = True
        
        return True
    
    def remove_player(self, player_id: str) -> bool:
        """移除玩家
        
        Args:
            player_id: 玩家ID
        
        Returns:
            是否成功移除
        """
        if player_id not in self.current_players:
            return False
        
        self.current_players.remove(player_id)
        
        if len(self.current_players) < self.required_players:
            self.is_activated = False
            self.activation_time = None
        
        return True
    
    def is_complete(self) -> bool:
        """判断是否完成"""
        return self.is_activated
    
    def get_progress(self) -> float:
        """获取进度"""
        if self.required_players == 0:
            return 1.0
        return len(self.current_players) / self.required_players


class MultiplayerPhysicsSystem:
    """多人协作物理存在感系统
    
    负责管理多人模式下的物理交互，确保：
    - 视线系统：玩家只能看到朝向范围内的其他玩家
    - 距离衰减：对话内容根据距离远近显示完整度
    - 物理协作：需要多人同时操作的机关
    - 背后偷袭机制
    """
    
    def __init__(self):
        self.players: dict[str, PlayerState] = {}
        self.mechanisms: dict[str, Mechanism] = {}
        self.player_positions: dict[str, Position] = {}
        self.location_players: dict[str, set[str]] = {}  # 位置索引：快速查找某位置的所有玩家
        self.max_view_distance: float = 10.0
        self.max_hear_distance: float = 20.0
        self.view_angle: float = 120.0
    
    def add_player(self, player_id: str, name: str, position: Position,
                   location: str = "未知位置",
                   facing_direction: Direction = Direction.NORTH) -> PlayerState:
        """添加玩家

        Args:
            player_id: 玩家ID
            name: 玩家名称
            position: 坐标位置
            location: 位置名称（如："大厅", "走廊"）
            facing_direction: 朝向

        Returns:
            玩家状态
        """
        player_state = PlayerState(
            player_id=player_id,
            name=name,
            position=position,
            location=location,
            facing_direction=facing_direction,
            is_alive=True,
            is_visible=True,
            is_speaking=False,
            current_action=None,
            inventory=[]
        )

        self.players[player_id] = player_state
        self.player_positions[player_id] = position

        # 更新位置索引
        self._update_player_location_index(player_id, location)

        return player_state

    def _update_player_location_index(self, player_id: str, new_location: str, old_location: str | None = None):
        """更新玩家位置索引

        Args:
            player_id: 玩家ID
            new_location: 新位置
            old_location: 旧位置（如果有）
        """
        # 从旧位置移除
        if old_location and old_location in self.location_players:
            self.location_players[old_location].discard(player_id)
            # 如果该位置没有玩家了，清理空集合
            if not self.location_players[old_location]:
                del self.location_players[old_location]

        # 添加到新位置
        if new_location not in self.location_players:
            self.location_players[new_location] = set()
        self.location_players[new_location].add(player_id)

    def register_player(self, player_id: str, name: str, initial_location: str = "初始位置") -> PlayerState:
        """注册玩家（简化接口，用于游戏开始时注册玩家）

        Args:
            player_id: 玩家ID
            name: 玩家名称
            initial_location: 初始位置名称（默认"初始位置"，应由游戏场景指定）

        Returns:
            玩家状态
        """
        # 随机分配初始坐标（在10x10的范围内）
        position = Position(
            x=random.uniform(-5, 5),
            y=random.uniform(-5, 5),
            z=0.0
        )
        return self.add_player(player_id, name, position, initial_location)
    
    def remove_player(self, player_id: str):
        """移除玩家"""
        if player_id in self.players:
            del self.players[player_id]
        if player_id in self.player_positions:
            del self.player_positions[player_id]
    
    def update_player_position(self, player_id: str, position: Position,
                                location: str | None = None) -> bool:
        """更新玩家位置

        Args:
            player_id: 玩家ID
            position: 新坐标位置
            location: 新位置名称（如果提供则更新）

        Returns:
            是否更新成功
        """
        if player_id not in self.players:
            return False

        old_location = self.players[player_id].location

        self.players[player_id].position = position
        self.player_positions[player_id] = position

        # 如果提供了新位置，更新位置索引
        if location is not None and location != old_location:
            self.players[player_id].location = location
            self._update_player_location_index(player_id, location, old_location)

        return True

    def move_player_to_location(self, player_id: str, location: str,
                                 position: Position | None = None) -> bool:
        """将玩家移动到指定位置

        Args:
            player_id: 玩家ID
            location: 目标位置名称
            position: 目标坐标（可选，不提供则保持原坐标）

        Returns:
            是否移动成功
        """
        if player_id not in self.players:
            return False

        old_location = self.players[player_id].location

        # 更新位置名称
        self.players[player_id].location = location

        # 如果提供了新坐标，更新坐标
        if position is not None:
            self.players[player_id].position = position
            self.player_positions[player_id] = position

        # 更新位置索引
        self._update_player_location_index(player_id, location, old_location)

        return True
    
    def update_player_facing(self, player_id: str, direction: Direction):
        """更新玩家朝向"""
        if player_id in self.players:
            self.players[player_id].facing_direction = direction
    
    def set_player_visibility(self, player_id: str, is_visible: bool):
        """设置玩家可见性"""
        if player_id in self.players:
            self.players[player_id].is_visible = is_visible
    
    def set_player_speaking(self, player_id: str, is_speaking: bool):
        """设置玩家是否在说话"""
        if player_id in self.players:
            self.players[player_id].is_speaking = is_speaking

    def add_item_to_inventory(self, player_id: str, item: dict[str, object]) -> bool:
        """添加物品到玩家库存

        Args:
            player_id: 玩家ID
            item: 物品字典（应包含 name, description 等字段）

        Returns:
            是否成功添加
        """
        if player_id not in self.players:
            return False

        # 检查是否已存在同名物品
        item_name = str(item.get("name", ""))
        if item_name and not any(str(i.get("name", "")) == item_name for i in self.players[player_id].inventory):
            self.players[player_id].inventory.append(item)
            return True
        return False

    def remove_item_from_inventory(self, player_id: str, item_name: str) -> bool:
        """从玩家库存移除物品

        Args:
            player_id: 玩家ID
            item_name: 物品名称

        Returns:
            是否成功移除
        """
        if player_id not in self.players:
            return False

        # 查找并移除指定名称的物品
        for i, item in enumerate(self.players[player_id].inventory):
            if str(item.get("name", "")) == item_name:
                self.players[player_id].inventory.pop(i)
                return True
        return False

    def get_player_inventory(self, player_id: str) -> list[dict[str, object]]:
        """获取玩家库存

        Args:
            player_id: 玩家ID

        Returns:
            物品列表（深拷贝）
        """
        if player_id not in self.players:
            return []

        return [item.copy() for item in self.players[player_id].inventory]

    def has_item(self, player_id: str, item_name: str) -> bool:
        """检查玩家是否有指定物品

        Args:
            player_id: 玩家ID
            item_name: 物品名称

        Returns:
            是否有该物品
        """
        if player_id not in self.players:
            return False

        return any(str(item.get("name", "")) == item_name for item in self.players[player_id].inventory)

    def get_visible_players(self, player_id: str) -> list[PlayerState]:
        """获取可见的玩家
        
        Args:
            player_id: 玩家ID
        
        Returns:
            可见的玩家列表
        """
        if player_id not in self.players:
            return []
        
        observer = self.players[player_id]
        visible_players = []
        
        for other_id, other_player in self.players.items():
            if other_id == player_id:
                continue
            
            if observer.can_see(other_player, self.max_view_distance, self.view_angle):
                visible_players.append(other_player)
        
        return visible_players
    
    def get_audible_players(self, player_id: str) -> list[tuple[PlayerState, float]]:
        """获取可听到的玩家
        
        Args:
            player_id: 玩家ID
        
        Returns:
            可听到的玩家列表及其听到的质量
        """
        if player_id not in self.players:
            return []
        
        observer = self.players[player_id]
        audible_players = []
        
        for other_id, other_player in self.players.items():
            if other_id == player_id:
                continue
            
            quality = observer.get_hearing_quality(other_player, self.max_hear_distance)
            if quality > 0:
                audible_players.append((other_player, quality))
        
        return audible_players
    
    def process_dialogue(self, speaker_id: str, dialogue: str) -> dict[str, str]:
        """处理对话，根据距离衰减
        
        Args:
            speaker_id: 说话者ID
            dialogue: 对话内容
        
        Returns:
            每个玩家听到的对话内容
        """
        if speaker_id not in self.players:
            return {}
        
        speaker = self.players[speaker_id]
        result = {}
        
        for listener_id, listener in self.players.items():
            if listener_id == speaker_id:
                result[listener_id] = dialogue
                continue
            
            quality = listener.get_hearing_quality(speaker, self.max_hear_distance)
            
            if quality >= 0.8:
                result[listener_id] = dialogue
            elif quality >= 0.5:
                result[listener_id] = self._truncate_dialogue(dialogue, 0.7)
            elif quality >= 0.3:
                result[listener_id] = self._truncate_dialogue(dialogue, 0.4)
            else:
                result[listener_id] = self._truncate_dialogue(dialogue, 0.2)
        
        return result
    
    def _truncate_dialogue(self, dialogue: str, ratio: float) -> str:
        """截断对话内容
        
        Args:
            dialogue: 原始对话
            ratio: 保留比例
        
        Returns:
            截断后的对话
        """
        length = int(len(dialogue) * ratio)
        truncated = dialogue[:length]
        
        if length < len(dialogue):
            truncated += "..."
        
        return truncated
    
    def add_mechanism(self, mechanism: Mechanism):
        """添加机关"""
        self.mechanisms[mechanism.mechanism_id] = mechanism
    
    def get_nearby_mechanisms(self, player_id: str,
                              max_distance: float = 5.0) -> list[Mechanism]:
        """获取附近的机关
        
        Args:
            player_id: 玩家ID
            max_distance: 最大距离
        
        Returns:
            附近的机关列表
        """
        if player_id not in self.player_positions:
            return []
        
        player_pos = self.player_positions[player_id]
        nearby_mechanisms = []
        
        for mechanism in self.mechanisms.values():
            distance = player_pos.distance_to(mechanism.location)
            if distance <= max_distance:
                nearby_mechanisms.append(mechanism)
        
        return nearby_mechanisms
    
    def activate_mechanism(self, player_id: str,
                          mechanism_id: str) -> tuple[bool, str]:
        """激活机关
        
        Args:
            player_id: 玩家ID
            mechanism_id: 机关ID
        
        Returns:
            (是否成功, 消息)
        """
        if mechanism_id not in self.mechanisms:
            return False, "机关不存在"
        
        mechanism = self.mechanisms[mechanism_id]
        
        if mechanism.is_activated:
            return False, "机关已经被激活"
        
        success = mechanism.add_player(player_id)
        
        if not success:
            return False, f"机关需要{mechanism.required_players}人同时操作"
        
        if mechanism.is_activated:
            return True, f"机关已激活！需要{mechanism.required_players}人同时操作"
        else:
            remaining = mechanism.required_players - len(mechanism.current_players)
            return True, f"机关进度：{mechanism.get_progress()*100:.0f}%，还需要{remaining}人"
    
    def deactivate_mechanism(self, player_id: str,
                             mechanism_id: str) -> tuple[bool, str]:
        """停用机关
        
        Args:
            player_id: 玩家ID
            mechanism_id: 机关ID
        
        Returns:
            (是否成功, 消息)
        """
        if mechanism_id not in self.mechanisms:
            return False, "机关不存在"
        
        mechanism = self.mechanisms[mechanism_id]
        
        success = mechanism.remove_player(player_id)
        
        if not success:
            return False, "你不在该机关上"
        
        return True, "已离开机关"
    
    def can_sneak_attack(self, attacker_id: str, target_id: str) -> bool:
        """判断是否可以偷袭
        
        Args:
            attacker_id: 攻击者ID
            target_id: 目标ID
        
        Returns:
            是否可以偷袭
        """
        if attacker_id not in self.players or target_id not in self.players:
            return False
        
        attacker = self.players[attacker_id]
        target = self.players[target_id]
        
        if not target.can_see(attacker, self.max_view_distance, self.view_angle):
            return True
        
        return False
    
    def get_players_in_location(self, location: str) -> list[PlayerState]:
        """获取特定位置的所有玩家

        Args:
            location: 位置名称

        Returns:
            玩家状态列表
        """
        if location not in self.location_players:
            return []

        # 使用位置索引快速获取玩家
        player_ids = self.location_players[location]
        return [self.players[pid] for pid in player_ids
                if pid in self.players and self.players[pid].is_alive]

    def to_dict(self) -> PhysicsDict:
        """序列化为字典"""
        return {
            "players": {
                player_id: {
                    "player_id": player.player_id,
                    "name": player.name,
                    "position": {
                        "x": player.position.x,
                        "y": player.position.y,
                        "z": player.position.z
                    },
                    "facing_direction": player.facing_direction.value,
                    "is_alive": player.is_alive,
                    "is_visible": player.is_visible,
                    "is_speaking": player.is_speaking,
                    "current_action": player.current_action,
                    "inventory": player.inventory
                }
                for player_id, player in self.players.items()
            },
            "mechanisms": {
                mechanism_id: {
                    "mechanism_id": mechanism.mechanism_id,
                    "name": mechanism.name,
                    "location": {
                        "x": mechanism.location.x,
                        "y": mechanism.location.y,
                        "z": mechanism.location.z
                    },
                    "required_players": mechanism.required_players,
                    "current_players": list(mechanism.current_players),
                    "is_activated": mechanism.is_activated,
                    "activation_time": mechanism.activation_time,
                    "description": mechanism.description
                }
                for mechanism_id, mechanism in self.mechanisms.items()
            },
            "max_view_distance": self.max_view_distance,
            "max_hear_distance": self.max_hear_distance,
            "view_angle": self.view_angle
        }
    
    @classmethod
    def from_dict(cls, data: PhysicsDict) -> "MultiplayerPhysicsSystem":
        """从字典反序列化"""
        system = cls()
        
        players_data = data.get("players", {})
        for player_id, player_data in players_data.items():
            position = Position(
                x=player_data["position"]["x"],
                y=player_data["position"]["y"],
                z=player_data["position"]["z"]
            )
            
            player_state = PlayerState(
                player_id=player_data["player_id"],
                name=player_data["name"],
                position=position,
                facing_direction=Direction(player_data["facing_direction"]),
                is_alive=player_data["is_alive"],
                is_visible=player_data["is_visible"],
                is_speaking=player_data["is_speaking"],
                current_action=player_data["current_action"],
                inventory=player_data["inventory"]
            )
            
            system.players[player_id] = player_state
            system.player_positions[player_id] = position
        
        mechanisms_data = data.get("mechanisms", {})
        for mechanism_id, mechanism_data in mechanisms_data.items():
            position = Position(
                x=mechanism_data["location"]["x"],
                y=mechanism_data["location"]["y"],
                z=mechanism_data["location"]["z"]
            )
            
            mechanism = Mechanism(
                mechanism_id=mechanism_data["mechanism_id"],
                name=mechanism_data["name"],
                location=position,
                required_players=mechanism_data["required_players"],
                current_players=set(mechanism_data["current_players"]),
                is_activated=mechanism_data["is_activated"],
                activation_time=mechanism_data["activation_time"],
                description=mechanism_data["description"]
            )
            
            system.mechanisms[mechanism_id] = mechanism
        
        system.max_view_distance = data.get("max_view_distance", 10.0)
        system.max_hear_distance = data.get("max_hear_distance", 20.0)
        system.view_angle = data.get("view_angle", 120.0)
        
        return system
