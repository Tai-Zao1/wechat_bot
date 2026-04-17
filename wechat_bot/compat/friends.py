"""旧好友业务导入路径兼容层。"""

from wechat_bot.services import (
    fetch_friend_names,
    fetch_friend_profiles,
    get_cached_friend_names,
    run_timed_send_loop,
)

__all__ = [
    "fetch_friend_names",
    "fetch_friend_profiles",
    "get_cached_friend_names",
    "run_timed_send_loop",
]
