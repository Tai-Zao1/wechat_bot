"""脚本层启动辅助。"""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_repo_root_for_scripts() -> Path:
    """确保以脚本文件方式运行时，仓库根目录在 `sys.path` 中。"""
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    return repo_root


__all__ = [
    "ensure_repo_root_for_scripts",
]
