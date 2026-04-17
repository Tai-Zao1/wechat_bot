"""旧本人资料缓存导入路径兼容层。"""

from wechat_bot.runtime import (
    EMPTY_SELF_PROFILE,
    fetch_self_profile,
    get_self_profile,
    load_self_profile_cache,
    save_self_profile_cache,
)

__all__ = [
    "EMPTY_SELF_PROFILE",
    "fetch_self_profile",
    "get_self_profile",
    "load_self_profile_cache",
    "save_self_profile_cache",
]
