"""wechat_bot 共享配置常量。"""

from __future__ import annotations

import os

from client_api import DEFAULT_CHECK_ONLINE_BASE_URL


PRIORITY_ORDER: dict[str, int] = {
    "auto_reply": 1,
    "add_friend": 2,
    "timed_send": 3,
}

PENDING_STALE_SECONDS = 30.0
UI_LOCK_STALE_SECONDS = 300.0

AUTO_REPLY_POLL_INTERVAL_S = 2.0
AUTO_REPLY_ACTIVE_COOLDOWN_MIN_S = 2.0
AUTO_REPLY_ACTIVE_COOLDOWN_MAX_S = 3.0
AUTO_REPLY_UNREAD_COOLDOWN_MIN_S = 2.0
AUTO_REPLY_UNREAD_COOLDOWN_MAX_S = 3.0
AUTO_REPLY_SKIP_FRIENDS = "微信游戏,微信团队,腾讯新闻,订阅号消息,服务通知"


def get_check_online_base_url() -> str:
    return (
        os.getenv("PYWECHAT_CHECK_ONLINE_BASE_URL", DEFAULT_CHECK_ONLINE_BASE_URL).strip()
        or DEFAULT_CHECK_ONLINE_BASE_URL
    )
