"""
环境状态快照系统
维护环境状态的持久化，确保世界的一致性
"""

from typing import Dict, List, Optional, Set
from enum import Enum


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


class EnvironmentState:
    """环境状态快照
    
    记录环境中关键物体的状态，确保：
    - 门的开/关/锁状态持久化
    - 物品位置和状态持久化
    - 灯光状态持久化
    - 墙壁、地面等环境变化持久化
    - LLM只描述"变化"而非"全貌"
    """
    
    def __init__(self):
        self.doors: Dict[str, DoorState] = {}
        self.items: Dict[str, Dict[str, any]] = {}
        self.lights: Dict[str, LightState] = {}
        self.walls: Dict[str, Dict[str, any]] = {}
        self.floors: Dict[str, Dict[str, any]] = {}
        self.objects: Dict[str, Dict[str, any]] = {}
        self.atmosphere: Dict[str, any] = {}
        self.sounds: List[str] = []
        self.smells: List[str] = {}
        self.temperature: float = 20.0
        self.humidity: float = 50.0
        self.entropy_level: float = 0.0
        self.changed_objects: Set[str] = set()
    
    def set_door_state(self, door_id: str, state: DoorState):
        """设置门的状态"""
        self.doors[door_id] = state
        self.changed_objects.add(door_id)
    
    def get_door_state(self, door_id: str) -> Optional[DoorState]:
        """获取门的状态"""
        return self.doors.get(door_id)
    
    def set_item_position(self, item_id: str, location: str, state: str = "正常"):
        """设置物品位置和状态"""
        if item_id not in self.items:
            self.items[item_id] = {}
        self.items[item_id]["location"] = location
        self.items[item_id]["state"] = state
        self.changed_objects.add(item_id)
    
    def get_item_info(self, item_id: str) -> Optional[Dict[str, any]]:
        """获取物品信息"""
        return self.items.get(item_id)
    
    def set_light_state(self, light_id: str, state: LightState):
        """设置灯光状态"""
        self.lights[light_id] = state
        self.changed_objects.add(light_id)
    
    def get_light_state(self, light_id: str) -> Optional[LightState]:
        """获取灯光状态"""
        return self.lights.get(light_id)
    
    def add_wall_damage(self, wall_id: str, damage_type: str, description: str):
        """添加墙壁损坏"""
        if wall_id not in self.walls:
            self.walls[wall_id] = {
                "damage": [],
                "writings": [],
                "blood_stains": []
            }
        self.walls[wall_id]["damage"].append({
            "type": damage_type,
            "description": description
        })
        self.changed_objects.add(wall_id)
    
    def add_wall_writing(self, wall_id: str, text: str, style: str = "潦草"):
        """添加墙壁文字"""
        if wall_id not in self.walls:
            self.walls[wall_id] = {
                "damage": [],
                "writings": [],
                "blood_stains": []
            }
        self.walls[wall_id]["writings"].append({
            "text": text,
            "style": style
        })
        self.changed_objects.add(wall_id)
    
    def add_blood_stain(self, location_id: str, description: str, amount: str = "少量"):
        """添加血迹"""
        if location_id not in self.walls and location_id not in self.floors:
            self.floors[location_id] = {
                "blood_stains": [],
                "stains": []
            }
        
        if location_id in self.walls:
            self.walls[location_id]["blood_stains"].append({
                "description": description,
                "amount": amount
            })
        else:
            self.floors[location_id]["blood_stains"].append({
                "description": description,
                "amount": amount
            })
        
        self.changed_objects.add(location_id)
    
    def set_object_state(self, object_id: str, state: Dict[str, any]):
        """设置物体状态"""
        self.objects[object_id] = state
        self.changed_objects.add(object_id)
    
    def get_object_state(self, object_id: str) -> Optional[Dict[str, any]]:
        """获取物体状态"""
        return self.objects.get(object_id)
    
    def update_atmosphere(self, **kwargs):
        """更新氛围"""
        for key, value in kwargs.items():
            self.atmosphere[key] = value
        self.changed_objects.add("atmosphere")
    
    def add_sound(self, sound: str):
        """添加声音"""
        if sound not in self.sounds:
            self.sounds.append(sound)
            self.changed_objects.add("sounds")
    
    def add_smell(self, smell: str):
        """添加气味"""
        if smell not in self.smells:
            self.smells.append(smell)
            self.changed_objects.add("smells")
    
    def set_temperature(self, temperature: float):
        """设置温度"""
        self.temperature = temperature
        self.changed_objects.add("temperature")
    
    def set_humidity(self, humidity: float):
        """设置湿度"""
        self.humidity = humidity
        self.changed_objects.add("humidity")
    
    def increase_entropy(self, amount: float = 1.0):
        """增加熵值（环境恶化程度）"""
        self.entropy_level = min(100.0, self.entropy_level + amount)
        self.changed_objects.add("entropy")
    
    def get_entropy_level(self) -> float:
        """获取熵值"""
        return self.entropy_level
    
    def get_entropy_description(self) -> str:
        """获取熵值描述"""
        if self.entropy_level < 20:
            return "环境相对稳定"
        elif self.entropy_level < 40:
            return "环境开始出现异常"
        elif self.entropy_level < 60:
            return "环境明显恶化"
        elif self.entropy_level < 80:
            return "环境极度危险"
        else:
            return "环境即将崩溃"
    
    def get_changes(self) -> Dict[str, any]:
        """获取所有变化的对象"""
        changes = {}
        for obj_id in self.changed_objects:
            if obj_id in self.doors:
                changes[f"door_{obj_id}"] = {
                    "type": "door",
                    "id": obj_id,
                    "state": self.doors[obj_id].value
                }
            elif obj_id in self.items:
                changes[f"item_{obj_id}"] = {
                    "type": "item",
                    "id": obj_id,
                    "info": self.items[obj_id]
                }
            elif obj_id in self.lights:
                changes[f"light_{obj_id}"] = {
                    "type": "light",
                    "id": obj_id,
                    "state": self.lights[obj_id].value
                }
            elif obj_id in self.walls:
                changes[f"wall_{obj_id}"] = {
                    "type": "wall",
                    "id": obj_id,
                    "info": self.walls[obj_id]
                }
            elif obj_id in self.objects:
                changes[f"object_{obj_id}"] = {
                    "type": "object",
                    "id": obj_id,
                    "state": self.objects[obj_id]
                }
            elif obj_id == "atmosphere":
                changes["atmosphere"] = {
                    "type": "atmosphere",
                    "state": self.atmosphere
                }
            elif obj_id == "sounds":
                changes["sounds"] = {
                    "type": "sounds",
                    "list": self.sounds
                }
            elif obj_id == "smells":
                changes["smells"] = {
                    "type": "smells",
                    "list": self.smells
                }
            elif obj_id == "temperature":
                changes["temperature"] = {
                    "type": "temperature",
                    "value": self.temperature
                }
            elif obj_id == "entropy":
                changes["entropy"] = {
                    "type": "entropy",
                    "level": self.entropy_level,
                    "description": self.get_entropy_description()
                }
        
        return changes
    
    def clear_changes(self):
        """清除变化记录"""
        self.changed_objects.clear()
    
    def to_dict(self) -> Dict[str, any]:
        """序列化为字典"""
        return {
            "doors": {k: v.value for k, v in self.doors.items()},
            "items": self.items,
            "lights": {k: v.value for k, v in self.lights.items()},
            "walls": self.walls,
            "floors": self.floors,
            "objects": self.objects,
            "atmosphere": self.atmosphere,
            "sounds": self.sounds,
            "smells": self.smells,
            "temperature": self.temperature,
            "humidity": self.humidity,
            "entropy_level": self.entropy_level
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, any]) -> 'EnvironmentState':
        """从字典反序列化"""
        env = cls()
        
        for door_id, state_str in data.get("doors", {}).items():
            env.doors[door_id] = DoorState(state_str)
        
        env.items = data.get("items", {})
        
        for light_id, state_str in data.get("lights", {}).items():
            env.lights[light_id] = LightState(state_str)
        
        env.walls = data.get("walls", {})
        env.floors = data.get("floors", {})
        env.objects = data.get("objects", {})
        env.atmosphere = data.get("atmosphere", {})
        env.sounds = data.get("sounds", [])
        env.smells = data.get("smells", [])
        env.temperature = data.get("temperature", 20.0)
        env.humidity = data.get("humidity", 50.0)
        env.entropy_level = data.get("entropy_level", 0.0)
        
        return env
    
    def generate_scene_description(self, location: str, include_changes_only: bool = True) -> str:
        """生成场景描述
        
        Args:
            location: 当前位置
            include_changes_only: 是否只包含变化的部分
        
        Returns:
            场景描述文本
        """
        description_parts = []
        
        if include_changes_only:
            changes = self.get_changes()
            if not changes:
                description_parts.append("环境没有明显变化。")
            
            for change_id, change_info in changes.items():
                if change_info["type"] == "door":
                    description_parts.append(f"门的状态：{change_info['state']}")
                elif change_info["type"] == "light":
                    description_parts.append(f"灯光状态：{change_info['state']}")
                elif change_info["type"] == "atmosphere":
                    if change_info["state"]:
                        description_parts.append(f"氛围变化：{change_info['state']}")
                elif change_info["type"] == "entropy":
                    description_parts.append(f"环境状态：{change_info['description']}")
        else:
            description_parts.append(f"当前温度：{self.temperature}°C")
            description_parts.append(f"当前湿度：{self.humidity}%")
            description_parts.append(f"环境状态：{self.get_entropy_description()}")
            
            if self.sounds:
                description_parts.append(f"环境声音：{', '.join(self.sounds)}")
            
            if self.smells:
                description_parts.append(f"环境气味：{', '.join(self.smells)}")
        
        return "\n".join(description_parts)
