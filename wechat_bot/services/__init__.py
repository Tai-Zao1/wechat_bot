"""业务服务层入口。

这里放的是“业务逻辑”，而不是 GUI 或底层微信控件操作。
当前主要包含：

- 好友列表、头像同步、定时群发
- 本地百炼调用

其中：

- `friend_directory.py` 是主实现
- `friends.py` 只是兼容/别名入口
- `bailian_client.py` 是主实现
- `local_ai.py` 只是兼容/别名入口
- `reply_service.py` 负责自动回复里的 API/本地百炼回复调度

新代码优先直接从 `wechat_bot.services` 或主实现文件导入，
不要继续往 `friends.py`、`local_ai.py` 里增加逻辑。
"""

from .friend_directory import (
    fetch_friend_names,
    fetch_friend_profiles,
    get_cached_friend_names,
    get_cached_friend_profiles,
)
from .bailian_client import (
    DEFAULT_BAILIAN_ENDPOINT,
    DEFAULT_BAILIAN_SYSTEM_PROMPT,
    LocalBailianClient,
    LocalBailianConfig,
    LocalBailianError,
    mask_secret,
)
from .timed_send import run_timed_send_loop
from .reply_service import (
    AUTH_EXPIRED_MARKER,
    AUTO_REPLY_STOP_MARKER,
    AutoReplyService,
    ReplyRequest,
    ReplyServiceConfig,
    ReplyServiceStop,
)

__all__ = [
    "AUTH_EXPIRED_MARKER",
    "AUTO_REPLY_STOP_MARKER",
    "AutoReplyService",
    "DEFAULT_BAILIAN_ENDPOINT",
    "DEFAULT_BAILIAN_SYSTEM_PROMPT",
    "LocalBailianClient",
    "LocalBailianConfig",
    "LocalBailianError",
    "ReplyRequest",
    "ReplyServiceConfig",
    "ReplyServiceStop",
    "fetch_friend_names",
    "fetch_friend_profiles",
    "get_cached_friend_names",
    "get_cached_friend_profiles",
    "mask_secret",
    "run_timed_send_loop",
]
