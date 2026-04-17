"""好友相关业务服务兼容入口。

主实现已经迁到 `wechat_bot.services.friend_directory`。
这里保留是为了兼容旧导入路径。

新代码不要再导入这个文件，优先改用：

- `wechat_bot.services`
- `wechat_bot.services.friend_directory`
"""

from .friend_directory import (
    fetch_friend_names,
    fetch_friend_profiles,
    get_cached_friend_names,
)
from .timed_send import run_timed_send_loop

__all__ = [
    "fetch_friend_names",
    "fetch_friend_profiles",
    "get_cached_friend_names",
    "run_timed_send_loop",
]
