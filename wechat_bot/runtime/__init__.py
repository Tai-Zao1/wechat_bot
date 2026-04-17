"""运行时状态与缓存服务。

这里只放运行过程中共享的状态能力，例如：

- UI 占用锁与任务接管
- 当前账号本地资料缓存

第一次阅读建议先看 `runtime/README.md`。
"""

from .scheduler import (
    claim_task_runtime,
    hold_wechat_ui,
    refresh_task_runtime,
    release_task_runtime,
    should_stop_task_runtime,
)
from .self_profile import (
    EMPTY_SELF_PROFILE,
    fetch_self_profile,
    get_self_profile,
    load_self_profile_cache,
    save_self_profile_cache,
)

__all__ = [
    "EMPTY_SELF_PROFILE",
    "claim_task_runtime",
    "fetch_self_profile",
    "get_self_profile",
    "hold_wechat_ui",
    "load_self_profile_cache",
    "refresh_task_runtime",
    "release_task_runtime",
    "save_self_profile_cache",
    "should_stop_task_runtime",
]
