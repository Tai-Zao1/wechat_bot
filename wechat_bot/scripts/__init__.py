"""脚本入口层。

这个目录提供更容易理解的脚本名称，同时兼容旧文件名。
脚本注册表在 `registry.py`，实际实现放在同目录下各脚本模块。
第一次阅读建议先看 `scripts/README.md`。
"""

from .registry import SCRIPT_REGISTRY, format_script_help_lines, resolve_script_spec

__all__ = [
    "SCRIPT_REGISTRY",
    "format_script_help_lines",
    "resolve_script_spec",
]
