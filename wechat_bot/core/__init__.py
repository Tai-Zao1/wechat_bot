"""wechat_bot 公共基础层。

这里聚合四类能力：

1. 路径
   本地缓存、日志、调度目录。
2. 运行策略
   自动回复节流、任务优先级、锁超时。
3. 网络配置
   读取和环境变量相关的基础地址。
4. 类型
   `FriendProfile`、`SelfProfile`。
"""

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
from .network import (
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
