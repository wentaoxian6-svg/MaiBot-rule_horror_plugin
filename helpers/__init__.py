"""
辅助模块 - 多人模式辅助和图片生成
"""
from .multiplayer_helper import (
    should_assign_identities,
    get_rule_network,
    get_multi_identity_info,
    get_identity_assignments,
    get_identity_list,
    build_player_order,
    assign_identity_to_player,
    assign_multiplayer_identities,
    get_common_rules,
    extract_rule_text,
)

__all__ = [
    "should_assign_identities",
    "get_rule_network",
    "get_multi_identity_info",
    "get_identity_assignments",
    "get_identity_list",
    "build_player_order",
    "assign_identity_to_player",
    "assign_multiplayer_identities",
    "get_common_rules",
    "extract_rule_text",
]
