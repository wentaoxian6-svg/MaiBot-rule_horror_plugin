"""
工具函数 - 提供通用的验证、转换和辅助功能
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import TypeVar, cast



from .constants import ActionKeywords

T = TypeVar("T")



def is_dir_writable(path: str) -> bool:
    """检查目录是否可写
    
    Args:
        path: 目录路径
        
    Returns:
        是否可写
    """
    try:
        os.makedirs(path, exist_ok=True)
        test_file = os.path.join(path, ".write_test")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(test_file)
        return True
    except Exception:
        return False


def resolve_data_dir(plugin_dir: str, data_subdir: str = "data") -> str:
    """解析数据目录
    
    优先使用插件目录下的子目录；如果不可写（常见于 Linux/Docker 只读挂载），
    则回退到用户数据目录（XDG_DATA_HOME 或 ~/.local/share）。
    
    Args:
        plugin_dir: 插件目录
        data_subdir: 数据子目录名称
        
    Returns:
        可写的数据目录路径
    """
    preferred = os.path.join(plugin_dir, data_subdir)
    if is_dir_writable(preferred):
        return preferred

    xdg_home = os.getenv("XDG_DATA_HOME")
    if xdg_home:
        base = os.path.join(xdg_home, "maibot")
    else:
        base = os.path.join(os.path.expanduser("~"), ".local", "share", "maibot")

    fallback = os.path.join(base, "rule_horror")
    os.makedirs(fallback, exist_ok=True)
    return fallback


def safe_get_dict_value(
    data: Mapping[str, object] | None,
    key: str,
    default: T,
    expected_type: type[T] | None = None,
) -> T:
    """安全地从字典获取值，带类型检查

    Args:
        data: 字典数据
        key: 键名
        default: 默认值
        expected_type: 期望的类型

    Returns:
        值或默认值
    """
    if data is None:
        return default

    value = data.get(key, default)

    if expected_type is None:
        return cast(T, value)

    if isinstance(value, expected_type):
        return value

    return default



def normalize_text_for_comparison(text: str) -> str:
    """标准化文本用于比较
    
    移除空白字符和标点符号，便于模糊匹配
    
    Args:
        text: 原始文本
        
    Returns:
        标准化后的文本
    """
    # 移除所有空白字符
    normalized = re.sub(r"\s+", "", text)
    # 移除常见标点符号
    normalized = re.sub(
        r"[，,。.!！？?；;:""\"'''《》【()（）\\-—…·]",
        "",
        normalized
    )
    return normalized


def contains_action_keyword(text: str) -> bool:
    """检查文本是否包含行动关键词
    
    Args:
        text: 待检查的文本
        
    Returns:
        是否包含行动关键词
    """
    return any(keyword in text for keyword in ActionKeywords.KEYWORDS)


def validate_sanity_value(value: int) -> int:
    """验证并修正理智值
    
    Args:
        value: 理智值
        
    Returns:
        修正后的理智值（0-100）
    """
    from .constants import SanityThresholds
    return max(SanityThresholds.LOW, min(SanityThresholds.MAX, value))


def validate_health_value(value: int) -> int:
    """验证并修正生命值
    
    Args:
        value: 生命值
        
    Returns:
        修正后的生命值（0-100）
    """
    from .constants import HealthThresholds
    return max(HealthThresholds.MIN, min(HealthThresholds.MAX, value))


def safe_isinstance_check(obj: object, expected_type: type[object]) -> bool:
    """安全的类型检查，避免异常

    Args:
        obj: 待检查的对象
        expected_type: 期望的类型

    Returns:
        是否为期望类型
    """
    try:
        return isinstance(obj, expected_type)
    except Exception:
        return False



def extract_player_order(players: Mapping[str, object]) -> list[str]:
    """从玩家字典中提取玩家ID顺序

    Args:
        players: 玩家字典

    Returns:
        玩家ID列表
    """
    return [str(pid) for pid in players.keys() if str(pid)]



def build_error_message(base_message: str, error: Exception) -> str:
    """构建错误消息
    
    Args:
        base_message: 基础消息
        error: 异常对象
        
    Returns:
        完整的错误消息
    """
    return f"{base_message}: {str(error)}"


def clamp(value: float, min_value: float, max_value: float) -> float:
    """限制值在指定范围内
    
    Args:
        value: 原始值
        min_value: 最小值
        max_value: 最大值
        
    Returns:
        限制后的值
    """
    return max(min_value, min(max_value, value))
