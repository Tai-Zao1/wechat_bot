"""wechat_bot 公共配置、路径与类型定义。"""

from .paths import (
    get_bot_app_root,
    get_bot_cache_dir,
    get_bot_cache_file,
    get_bot_data_dir,
    get_bot_logs_dir,
    get_bot_scheduler_dir,
    get_current_wxid_key,
    sanitize_file_piece,
)
from .settings import (
    AUTO_REPLY_ACTIVE_COOLDOWN_MAX_S,
    AUTO_REPLY_ACTIVE_COOLDOWN_MIN_S,
    AUTO_REPLY_POLL_INTERVAL_S,
    AUTO_REPLY_SKIP_FRIENDS,
    AUTO_REPLY_UNREAD_COOLDOWN_MAX_S,
    AUTO_REPLY_UNREAD_COOLDOWN_MIN_S,
    PENDING_STALE_SECONDS,
    PRIORITY_ORDER,
    UI_LOCK_STALE_SECONDS,
    get_check_online_base_url,
)
from .types import FriendProfile, SelfProfile

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
    "FriendProfile",
    "SelfProfile",
    "get_bot_app_root",
    "get_bot_cache_dir",
    "get_bot_cache_file",
    "get_bot_data_dir",
    "get_bot_logs_dir",
    "get_bot_scheduler_dir",
    "get_check_online_base_url",
    "get_current_wxid_key",
    "sanitize_file_piece",
]
