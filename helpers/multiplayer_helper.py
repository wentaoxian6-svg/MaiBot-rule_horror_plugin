"""
多人模式辅助函数 - 简化多人模式相关的复杂逻辑
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING

from ..common import JsonObject
from ..common.constants import GameModes
from ..common.utils import safe_get_dict_value

if TYPE_CHECKING:
    from ..core import GameSession, Player

logger = logging.getLogger(__name__)


def should_assign_identities(session: GameSession) -> bool:
    """检查是否应该分配身份
    
    Args:
        session: 游戏会话
        
    Returns:
        是否应该分配身份
    """
    return session.game_mode == GameModes.MULTI.value


def get_rule_network(session: GameSession) -> JsonObject:
    """安全地获取规则网络

    Args:
        session: 游戏会话

    Returns:
        规则网络字典
    """
    rule_network = getattr(session, "rule_network", None)
    if isinstance(rule_network, dict):
        return rule_network
    return {}


def get_multi_identity_info(session: GameSession) -> JsonObject:
    """获取多人身份信息

    Args:
        session: 游戏会话

    Returns:
        多人身份信息字典
    """
    rule_network = get_rule_network(session)
    multi_identity = rule_network.get("multi_identity")
    if isinstance(multi_identity, dict):
        return multi_identity
    return {}


def get_identity_assignments(session: GameSession) -> dict[str, JsonObject]:
    """获取身份分配信息（按玩家ID索引）

    Args:
        session: 游戏会话

    Returns:
        身份分配字典 {player_id: assignment_info}
    """
    multi_identity = get_multi_identity_info(session)
    assignments = multi_identity.get("assignments")

    by_player_id: dict[str, JsonObject] = {}

    if not isinstance(assignments, list):
        return by_player_id

    for assignment in assignments:
        if not isinstance(assignment, dict):
            continue

        player_id = str(assignment.get("player_id", "") or "").strip()
        if player_id:
            by_player_id[player_id] = assignment

    return by_player_id


def get_identity_list(session: GameSession) -> list[JsonObject]:
    """获取身份列表（用于循环分配）

    Args:
        session: 游戏会话

    Returns:
        身份列表
    """
    multi_identity = get_multi_identity_info(session)
    identities = multi_identity.get("identities")

    if not isinstance(identities, list):
        return []

    return [i for i in identities if isinstance(i, dict)]


def build_player_order(session: GameSession, explicit_order: list[str] | None = None) -> list[str]:
    """构建玩家顺序列表
    
    Args:
        session: 游戏会话
        explicit_order: 显式指定的顺序
        
    Returns:
        玩家ID列表
    """
    order = [str(pid) for pid in (explicit_order or []) if str(pid)]
    
    # 补充未在显式顺序中的玩家
    for player_id in session.players.keys():
        pid = str(player_id)
        if pid not in order:
            order.append(pid)
    
    return order


def assign_identity_to_player(
    player: Player,
    identity_info: Mapping[str, object],
) -> None:
    """将身份信息分配给玩家

    Args:
        player: 玩家对象
        identity_info: 身份信息
    """
    player.identity = safe_get_dict_value(identity_info, "identity_name", None, str)
    player.identity_description = safe_get_dict_value(
        identity_info, "identity_description", None, str
    )

    unique_rules_raw = identity_info.get("unique_rules")
    player.unique_rules = unique_rules_raw if isinstance(unique_rules_raw, list) else []

    # 日志记录，便于调试
    logger.debug(f"分配身份给玩家 {player.name}: identity={player.identity}, unique_rules_count={len(player.unique_rules)}")

    player.exclusive_info = safe_get_dict_value(
        identity_info, "exclusive_info", None, str
    )


def assign_multiplayer_identities(
    session: GameSession,
    player_order: list[str] | None = None
) -> dict[str, str]:
    """分配多人模式身份

    Args:
        session: 游戏会话
        player_order: 玩家顺序（可选）

    Returns:
        分配结果 {player_id: identity_name}
    """
    if not should_assign_identities(session):
        return {}

    # 获取身份分配信息
    assignments_by_id = get_identity_assignments(session)
    identity_list = get_identity_list(session)

    # 如果既没有按ID分配，也没有身份列表，则无法分配
    if not assignments_by_id and not identity_list:
        logger.warning("多人模式身份信息缺失")
        return {}

    # 构建玩家顺序
    order = build_player_order(session, player_order)

    # 分配身份
    assigned: dict[str, str] = {}

    for index, player_id in enumerate(order):
        player = session.players.get(player_id)
        if not player:
            continue

        # 优先使用按ID分配的身份
        if player_id in assignments_by_id:
            identity_info = assignments_by_id[player_id]
        # 否则从身份列表中循环分配
        elif identity_list:
            identity_info = identity_list[index % len(identity_list)]
        else:
            continue

        # 分配身份
        assign_identity_to_player(player, identity_info)

        if player.identity:
            assigned[player_id] = player.identity

    # 记录分配结果到环境状态
    _record_assignments_to_environment(session, assigned)

    return assigned


def _record_assignments_to_environment(
    session: GameSession,
    assignments: dict[str, str],
) -> None:
    """将分配结果记录到环境状态

    Args:
        session: 游戏会话
        assignments: 分配结果
    """
    env_state_raw = getattr(session, "environment_state", None)
    if not isinstance(env_state_raw, dict):
        return

    multiplayer_info = env_state_raw.get("multiplayer")
    if not isinstance(multiplayer_info, dict):
        multiplayer_info = {}
        env_state_raw["multiplayer"] = multiplayer_info

    multiplayer_info["assigned_identities"] = assignments.copy()


def get_common_rules(session: GameSession) -> list[JsonObject]:
    """获取共同规则

    Args:
        session: 游戏会话

    Returns:
        共同规则列表
    """
    multi_identity = get_multi_identity_info(session)
    common_rules = multi_identity.get("common_rules")

    if not isinstance(common_rules, list):
        return []

    return [r for r in common_rules if isinstance(r, dict)]


def extract_rule_text(rule: object) -> str:
    """从规则对象中提取文本

    Args:
        rule: 规则对象（可能是字典或字符串）

    Returns:
        规则文本
    """
    if isinstance(rule, dict):
        return str(rule.get("text", "") or "").strip()

    return str(rule).strip()

