#!/usr/bin/env python3
"""兼容旧导入路径的批量加好友脚本入口。"""

from __future__ import annotations

try:
    from .legacy_support import load_legacy_main
except ImportError:
    from legacy_support import load_legacy_main

main = load_legacy_main("wechat_bot.scripts.add_friends")

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
