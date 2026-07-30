"""玩家交互服务 - 处理玩家之间的直接交互（给物品、攻击）。

本模块从 ``action_processor.py`` 抽离，职责单一：
- ``PlayerInteractionService.maybe_handle_player_interaction`` 检测并处理玩家间交互（多人模式）
- ``PlayerInteractionService.find_target_player_in_action`` 从行动文本匹配目标玩家
- ``PlayerInteractionService.is_give_action`` / ``is_attack_action`` 行动类型判定
- ``PlayerInteractionService.handle_give_item`` 物品转移（Task 9：双方背包名词匹配）
- ``PlayerInteractionService.handle_pvp`` PVP 攻击（委托给 ``PvPCombatService``）

调用方（``ActionProcessor``）通过组合持有 ``PlayerInteractionService`` 实例，
原 ``_maybe_handle_player_interaction`` 等方法保留为薄壳委托，避免破坏既有调用点。

Task 9：交互目标选择排除死亡玩家；``_is_attack_action`` 剔除"打听/打开/打电话"等非攻击短语；
物品转移用双方背包名词匹配替代正则截取。
"""
from __future__ import annotations

import logging

from ...common import GameModes
from ...systems.room_topology import is_same_room
from ..game.models import GameSession, Player, PlayerStatus
from .pvp_combat import PvPCombatService

logger = logging.getLogger(__name__)


class PlayerInteractionService:
    """玩家交互服务。

    封装玩家之间的直接交互逻辑：给物品、PVP 攻击。
    服务持有 ``pvp_service``（``PvPCombatService``）实例用于 PVP 伤害计算，
    通过组合方式注入到 ``ActionProcessor`` 中。

    房间级模型下，可见性 = 同房间；声音可听性由 ``room_topology.can_hear_between_rooms`` 判定。
    """

    def __init__(self, pvp_service: PvPCombatService) -> None:
        self._pvp_service: PvPCombatService = pvp_service

    def maybe_handle_player_interaction(
        self,
        action: str,
        player: Player,
        session: GameSession,
    ):
        """检测玩家之间的直接交互（给物品、喊话、攻击）。

        仅多人模式生效。返回 None 表示不是玩家交互，交给常规 LLM 判定。
        房间级模型下，可见性 = 同房间；声音可听性由 room_topology.can_hear_between_rooms 判定。
        """
        # lazy import 避免与 action_processor.py 形成模块级循环导入
        from .action_processor import ActionResult

        if session.game_mode != GameModes.MULTI.value:
            return None

        # 1) 检测是否针对某玩家
        target_player = self.find_target_player_in_action(action, session, player)
        if target_player is None:
            return None

        # 2) 检查可见性：房间级模型下同房间即可见
        can_see_target = is_same_room(player.location, target_player.location)

        if not can_see_target:
            return ActionResult(
                description=f"你看不见{target_player.name}，无法对他执行该行动。"
            )

        # 3) 分类处理
        if self.is_give_action(action):
            return self.handle_give_item(player, target_player, action)
        if self.is_attack_action(action):
            return self.handle_pvp(player, target_player, action, session)

        return None  # 交给常规 LLM 判定

    def find_target_player_in_action(
        self,
        action: str,
        session: GameSession,
        player: Player,
    ) -> Player | None:
        """从行动文本里匹配目标玩家名字。"""
        for other in session.players.values():
            if other.player_id == player.player_id:
                continue
            # Task 9：排除死亡玩家，不能对尸体执行给物品/攻击等交互
            if other.status != PlayerStatus.ALIVE:
                continue
            if other.name and other.name in action:
                return other
        return None

    def is_give_action(self, action: str) -> bool:
        """检测是否是给物品的行动。"""
        return any(k in action for k in ["给", "递给", "交给", "塞给", "扔给"])

    def is_attack_action(self, action: str) -> bool:
        """检测是否是攻击行动。"""
        # Task 9：显式排除含"打"的常见非攻击短语，避免"打听小明""打开门"等误触发 PvP
        non_attack_with_da = [
            "打听", "打开", "打电话", "打哈欠", "打字", "打量", "打算",
            "打扫", "打水", "打招呼", "打车", "打针", "打包", "打雷",
        ]
        # 明确攻击词（不含单字"打"），命中即视为攻击
        explicit_attack_keywords = ["攻击", "推", "掐", "刺", "砸", "殴打", "揍", "打人"]
        if any(k in action for k in explicit_attack_keywords):
            return True
        # "打"字单独出现时，排除非攻击短语后再判定为攻击
        if "打" in action and not any(p in action for p in non_attack_with_da):
            return True
        return False

    def handle_give_item(
        self,
        giver: Player,
        receiver: Player,
        action: str,
    ):
        """处理物品转移。

        P5 阶段不实现异步通知接收方（receiver 是 session 里的对象，不是真实聊天会话）。
        通知由 P2 的事件广播系统处理。本方法只修改 inventory 并返回描述。

        Task 9：用"双方背包名词匹配"提取物品，不再用正则截取。
        "把钥匙给小明"会匹配到"钥匙"而非"小明"；"给小明钥匙"也能正确匹配。
        """
        # lazy import 避免与 action_processor.py 形成模块级循环导入
        from .action_processor import ActionResult

        # 从双方背包物品名中匹配行动文本，提取被转让的物品
        # 优先匹配 giver 背包（giver 必须持有才能给），receiver 背包名仅作辅助识别
        giver_item_names = [
            str(item.get("name", "")).strip()
            for item in giver.inventory
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        ]

        # 按长度降序匹配，避免短名误匹配为长名子串（如"钥匙"不应先于"旧钥匙"匹配）
        matched_item_name = ""
        for name in sorted(set(giver_item_names), key=len, reverse=True):
            if name and name in action:
                matched_item_name = name
                break

        if not matched_item_name:
            return ActionResult(description=f"你想给{receiver.name}什么？请说明你持有的物品。")

        # 在 giver.inventory 里查找物品（按精确名称匹配）
        item = None
        for inv_item in giver.inventory:
            if not isinstance(inv_item, dict):
                continue
            inv_name = str(inv_item.get("name", "")).strip()
            if inv_name == matched_item_name:
                item = inv_item
                break

        if not item:
            return ActionResult(description=f"你没有 {matched_item_name}。")

        giver.inventory.remove(item)
        receiver.inventory.append(item)

        return ActionResult(description=f"你把 {matched_item_name} 递给了 {receiver.name}。")

    def handle_pvp(
        self,
        attacker: Player,
        target: Player,
        action: str,
        session: GameSession,
    ):
        """处理 PVP 攻击（委托给 ``PvPCombatService``，保留签名以兼容调用点）。

        详见 ``core/services/pvp_combat.py`` 中 ``PvPCombatService.handle_pvp`` 的实现：
        - 伤害公式：基础伤害 + 武器加成 + 力量修正 - 防御修正，再乘以 (1 - 距离衰减)
        - 伤情根据最终伤害值分段判定
        - 房间级模型下 can_sneak 恒为 False
        """
        return self._pvp_service.handle_pvp(attacker, target, action, session)
