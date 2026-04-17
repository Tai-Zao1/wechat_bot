"""旧脚本入口的兼容辅助。

保留根目录旧文件名，避免外部脚本、打包配置或用户习惯失效；
实际实现统一转发到 `wechat_bot.scripts.*`。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Callable


def ensure_repo_root() -> Path:
    """确保以旧脚本方式运行时，仓库根目录仍在 `sys.path` 中。"""
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    return repo_root


def load_legacy_main(module_name: str) -> Callable[[], int]:
    """按模块路径加载新的脚本入口函数。"""
    ensure_repo_root()
    module = importlib.import_module(module_name)
    main = getattr(module, "main", None)
    if not callable(main):
        raise AttributeError(f"{module_name} 未导出可调用的 main()")
    return main


__all__ = [
    "ensure_repo_root",
    "load_legacy_main",
]
