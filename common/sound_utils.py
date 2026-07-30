"""声源强度推断工具 - 从行动文本推断 SoundIntensity 档位。

本模块为 action_processor 与 npc_simulator 共享的公共工具，避免重复实现。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..systems.room_topology import SoundIntensity


# 声源强度关键词：用于从行动文本推断 SoundIntensity 档位
_LOUD_KEYWORDS = ("喊", "大叫", "呼救", "咆哮", "尖叫", "怒吼", "嘶吼")
_QUIET_KEYWORDS = ("蹑手蹑脚", "悄声", "低语", "轻手轻脚", "屏息")


def infer_sound_intensity(action_text: str) -> SoundIntensity:
    """从行动文本推断声源强度。

    匹配逻辑：
    - 命中 LOUD 关键词（喊/大叫/呼救/咆哮/尖叫/怒吼/嘶吼）→ LOUD
    - 命中 QUIET 关键词（蹑手蹑脚/悄声/低语/轻手轻脚/屏息）→ QUIET
    - 其余 → NORMAL
    """
    # 延迟导入避免 common → systems 的循环依赖
    from ..systems.room_topology import SoundIntensity

    text = action_text or ""
    for kw in _LOUD_KEYWORDS:
        if kw in text:
            return SoundIntensity.LOUD
    for kw in _QUIET_KEYWORDS:
        if kw in text:
            return SoundIntensity.QUIET
    return SoundIntensity.NORMAL
