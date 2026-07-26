"""PVP 战斗服务 - 处理玩家间攻击的伤害计算与伤情判定。

本模块从 ``action_processor.py`` 抽离，职责单一：
- ``PvPCombatService.handle_pvp`` PVP 攻击主逻辑（伤害公式 + 反伤 + 描述）
- ``PvPCombatService.compute_distance_decay`` 房间级距离衰减系数
- ``PvPCombatService.has_weapon`` / ``has_armor`` 玩家装备识别
- ``PvPCombatService.injury_level`` 伤害值映射伤情分段

伤害公式（与原 ``ActionProcessor._handle_pvp`` 完全一致，避免业务逻辑变更）：
    最终伤害 = max(1, (基础伤害 + 武器加成 + 力量修正 - 防御修正) * (1 - 距离衰减))

房间级模型下不再支持坐标级"背后偷袭"：可见性已由 ``is_same_room`` 判定，
同房间即正面相遇，``can_sneak`` 恒为 False。

注：``ActionResult`` 定义在 ``action_processor.py``，为避免循环导入，
本模块在 ``handle_pvp`` 内部按需 lazy import。
"""
from __future__ import annotations

from ...systems.room_topology import (
    build_room_graph,
    is_adjacent_room,
    is_same_room,
)
from ..game.models import GameSession, Player

# 武器类物品关键词：用于在 inventory 中识别武器（type 字段缺失时回退到名称匹配）
_WEAPON_KEYWORDS: tuple[str, ...] = ("刀", "枪", "棍", "棒", "剑", "斧", "锤", "匕首", "凶器")
# 防具类物品关键词：用于在 inventory 中识别防具（type 字段缺失时回退到名称匹配）
_ARMOR_KEYWORDS: tuple[str, ...] = ("甲", "盾", "护", "盔", "防弹", "护具", "护甲")


class PvPCombatService:
    """PVP 战斗计算服务。

    封装玩家间攻击的伤害修正公式与伤情判定。服务本身无状态，通过组合
    方式注入到 ``ActionProcessor`` 中，原 ``_handle_pvp`` 等方法保留为
    薄壳委托，避免破坏既有调用点。
    """

    def has_weapon(self, player: Player) -> bool:
        """检查玩家背包中是否持有武器类物品。"""
        for item in player.inventory:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type", "") or "").strip()
            if item_type in ("武器", "weapon", "Weapon"):
                return True
            item_name = str(item.get("name", "") or "").strip()
            if item_name and any(kw in item_name for kw in _WEAPON_KEYWORDS):
                return True
        return False

    def has_armor(self, player: Player) -> bool:
        """检查玩家背包中是否持有防具类物品。"""
        for item in player.inventory:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type", "") or "").strip()
            if item_type in ("防具", "armor", "Armor"):
                return True
            item_name = str(item.get("name", "") or "").strip()
            if item_name and any(kw in item_name for kw in _ARMOR_KEYWORDS):
                return True
        return False

    def compute_distance_decay(self, session: GameSession, attacker_loc: str, target_loc: str) -> float:
        """计算攻击距离衰减系数。

        返回衰减比例：
        - 0.0：同房间，无衰减
        - 0.5：相邻房间，伤害减半
        - 1.0：更远距离，无法攻击
        """
        if is_same_room(attacker_loc, target_loc):
            return 0.0
        env_state = session.environment_state if isinstance(session.environment_state, dict) else {}
        room_graph = env_state.get("room_graph", {})
        if not isinstance(room_graph, dict) or not room_graph:
            room_graph = build_room_graph(session.scene_structure or {})
        if is_adjacent_room(room_graph, attacker_loc, target_loc):
            return 0.5
        # 更远距离无法攻击
        return 1.0

    def injury_level(self, damage: int) -> str:
        """根据伤害值映射伤情分段。"""
        if damage < 10:
            return "轻伤"
        if damage < 20:
            return "中等伤"
        if damage < 35:
            return "重伤"
        return "致命伤"

    def handle_pvp(
        self,
        attacker: Player,
        target: Player,
        action: str,
        session: GameSession,
    ):
        """处理 PVP 攻击。

        伤害公式：基础伤害 + 武器加成 + 力量修正 - 防御修正，再乘以 (1 - 距离衰减)。
        伤情根据最终伤害值分段判定，不再硬编码"重伤"。

        房间级模型下不再支持坐标级"背后偷袭"：可见性已由 is_same_room 判定，
        同房间即正面相遇，can_sneak 恒为 False。

        Returns:
            ``ActionResult`` 实例（描述中包含伤情与体力变化）
        """
        # lazy import 避免与 action_processor.py 形成模块级循环导入
        from .action_processor import ActionResult

        # 1) 判定攻击类型：房间级模型无"背后"概念，统一为正面攻击
        can_sneak = False
        base_damage = 20 if can_sneak else 10

        # 2) 距离衰减：同房间无衰减，相邻房间衰减 50%，更远无法攻击
        distance_decay = self.compute_distance_decay(session, attacker.location, target.location)
        if distance_decay >= 1.0:
            return ActionResult(
                description=f"你与{target.name}距离太远，无法发起有效攻击。",
            )

        # 3) 武器加成：攻击者持有武器类物品时额外加成
        weapon_bonus = 5 if self.has_weapon(attacker) else 0

        # 4) 力量修正：健康度反映力量，25 分一段，100 健康=+2，0 健康=-2
        strength_modifier = (attacker.health // 25) - 2

        # 5) 防御修正：目标持有防具类物品时削减伤害
        defense_modifier = 5 if self.has_armor(target) else 0

        # 6) 计算最终伤害（保底 1 点，避免出现 0 伤害的"攻击未生效"假象）
        raw_damage = base_damage + weapon_bonus + strength_modifier - defense_modifier
        final_damage = max(1, int(raw_damage * (1 - distance_decay)))

        # 7) 应用伤害到目标
        target.health = max(0, target.health - final_damage)
        target.injury = self.injury_level(final_damage)

        # 8) 反伤：正面攻击时攻击者也会受到反震伤害（可被自身护甲削减）
        counter_damage = 0
        if not can_sneak:
            counter_base = 5
            counter_defense = 5 if self.has_armor(attacker) else 0
            counter_damage = max(0, counter_base - counter_defense)
            if counter_damage > 0:
                attacker.health = max(0, attacker.health - counter_damage)
                attacker.injury = self.injury_level(counter_damage)

        # 9) 生成描述（包含伤情与体力变化，便于玩家感知公式结果）
        if can_sneak:
            desc = f"你从背后袭击了{target.name}，他毫无防备，受到了{target.injury}（体力 -{final_damage}）。"
        elif counter_damage > 0:
            desc = (
                f"你冲向{target.name}发起攻击，他有所防备，"
                f"对方受到了{target.injury}（体力 -{final_damage}），"
                f"你也被反震受到{attacker.injury}（体力 -{counter_damage}）。"
            )
        else:
            desc = f"你冲向{target.name}发起攻击，他有所防备，对方受到了{target.injury}（体力 -{final_damage}）。"

        return ActionResult(description=desc)
