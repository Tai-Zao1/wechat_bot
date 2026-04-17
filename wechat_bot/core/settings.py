"""兼容旧导入路径的配置汇总入口。

主实现已经拆到：

- `runtime_policy.py`
- `network.py`
"""

from __future__ import annotations

from .network import get_check_online_base_url
from .runtime_policy import (
    AUTO_REPLY_ACTIVE_COOLDOWN_MAX_S,
    AUTO_REPLY_ACTIVE_COOLDOWN_MIN_S,
    AUTO_REPLY_POLL_INTERVAL_S,
    AUTO_REPLY_SKIP_FRIENDS,
    AUTO_REPLY_UNREAD_COOLDOWN_MAX_S,
    AUTO_REPLY_UNREAD_COOLDOWN_MIN_S,
    PENDING_STALE_SECONDS,
    PRIORITY_ORDER,
    UI_LOCK_STALE_SECONDS,
)

__all__ = [
    "AUTO_REPLY_ACTIVE_COOLDOWN_MAX_S",
    "AUTO_REPLY_ACTIVE_COOLDOWN_MIN_S",
    "AUTO_REPLY_POLL_INTERVAL_S",
    "AUTO_REPLY_SKIP_FRIENDS",
    "AUTO_REPLY_UNREAD_COOLDOWN_MAX_S",
    "AUTO_REPLY_UNREAD_COOLDOWN_MIN_S",
    "PENDING_STALE_SECONDS",
    "PRIORITY_ORDER",
    "UI_LOCK_STALE_SECONDS",
    "get_check_online_base_url",
]
