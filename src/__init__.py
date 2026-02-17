"""Plugin-local `src` namespace package shim.

MaiBot 的核心代码位于 `modules/MaiBot/src`，但在本仓库根目录下并不存在同名顶层包。

本插件会通过 `from src...` 引用 MaiBot 的公共 API（如 `src.plugin_system`、`src.chat`）。
为避免在不同加载器/类型检查环境下出现解析差异，这里把 `src` 扩展为一个
namespace package，并把 MaiBot 的真实 `src` 目录加入搜索路径。

注意：该文件不引入任何运行时依赖，只做路径拼接与包路径扩展。
"""

from __future__ import annotations

import pkgutil
from pathlib import Path


# 允许把多个目录合并为同一个 `src` 命名空间
__path__ = pkgutil.extend_path(__path__, __name__)  # type: ignore[name-defined]

_here = Path(__file__).resolve()
# .../modules/MaiBot/plugins/rule_horror_plugin-main/src/__init__.py
# parents[3] -> .../modules/MaiBot
_maibot_root = _here.parents[3]
_maibot_src = _maibot_root / "src"

if _maibot_src.exists():
    __path__.append(str(_maibot_src))  # type: ignore[attr-defined]
