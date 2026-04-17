#!/usr/bin/env python3
"""兼容旧导入路径的好友业务入口。"""

from __future__ import annotations

from wechat_bot.compat.friends import (
    fetch_friend_names,
    fetch_friend_profiles,
    get_cached_friend_names,
    run_timed_send_loop,
)
from wechat_bot.core import (
    get_bot_app_root,
    get_bot_data_dir,
    get_bot_logs_dir,
    sanitize_file_piece,
)

__all__ = [
    "fetch_friend_names",
    "fetch_friend_profiles",
    "get_cached_friend_names",
    "get_bot_app_root",
    "get_bot_data_dir",
    "get_bot_logs_dir",
    "run_timed_send_loop",
    "sanitize_file_piece",
]
