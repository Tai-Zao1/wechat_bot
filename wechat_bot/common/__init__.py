"""wechat_bot 通用默认值与存储辅助。

新代码优先从这里拿真正通用的常量、JSON 存储能力。

自动回复脚本的专用缓存工具仍然保留在这里做兼容导出，
但推荐改为直接从 `wechat_bot.scripts.auto_reply_support` 导入。
"""

from __future__ import annotations

from .defaults import (
    BOT_GUI_SETTINGS_APP,
    BOT_GUI_SETTINGS_ORG,
    DEFAULT_ADD_FRIEND_API_PATH,
    DEFAULT_ADD_FRIEND_INTERVAL_MAX_MINUTES,
    DEFAULT_ADD_FRIEND_INTERVAL_MAX_SECONDS,
    DEFAULT_ADD_FRIEND_INTERVAL_MIN_MINUTES,
    DEFAULT_ADD_FRIEND_INTERVAL_MIN_SECONDS,
    DEFAULT_ADD_FRIEND_SOURCE_TEXT,
    DEFAULT_AUTO_REPLY_TEXT,
    DEFAULT_TIMED_SEND_MESSAGE,
    FRIEND_LIST_CACHE_FILENAME,
    MIN_ADD_FRIEND_INTERVAL_MINUTES,
    MIN_WECHAT_VERSION,
    SELF_PROFILE_CACHE_FILENAME,
    SHORTLINK_RULE_FILENAME,
)
from .json_store import load_json_dict, load_json_list, write_json_file


_AUTO_REPLY_EXPORTS = {
    "AUTO_REPLY_CACHE_FILENAME",
    "AUTO_REPLY_RECENT_CACHE_FILENAME",
    "AUTO_REPLY_SELF_SENT_CACHE_FILENAME",
    "RepliedCache",
    "RuleList",
    "SelfSentCache",
    "load_keyword_rule_pairs",
    "load_replied_cache",
    "load_self_sent_cache",
    "save_replied_cache",
    "save_self_sent_cache",
}


def __getattr__(name: str) -> object:
    # 兼容旧导入路径：自动回复专用辅助已迁到 scripts/auto_reply_support.py。
    if name in _AUTO_REPLY_EXPORTS:
        from . import auto_reply as _auto_reply

        return getattr(_auto_reply, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "BOT_GUI_SETTINGS_APP",
    "BOT_GUI_SETTINGS_ORG",
    "DEFAULT_ADD_FRIEND_API_PATH",
    "DEFAULT_ADD_FRIEND_INTERVAL_MAX_MINUTES",
    "DEFAULT_ADD_FRIEND_INTERVAL_MAX_SECONDS",
    "DEFAULT_ADD_FRIEND_INTERVAL_MIN_MINUTES",
    "DEFAULT_ADD_FRIEND_INTERVAL_MIN_SECONDS",
    "DEFAULT_ADD_FRIEND_SOURCE_TEXT",
    "DEFAULT_AUTO_REPLY_TEXT",
    "DEFAULT_TIMED_SEND_MESSAGE",
    "FRIEND_LIST_CACHE_FILENAME",
    "MIN_ADD_FRIEND_INTERVAL_MINUTES",
    "MIN_WECHAT_VERSION",
    "SELF_PROFILE_CACHE_FILENAME",
    "SHORTLINK_RULE_FILENAME",
    "load_json_dict",
    "load_json_list",
    "write_json_file",
    # 兼容导出
    "AUTO_REPLY_CACHE_FILENAME",
    "AUTO_REPLY_RECENT_CACHE_FILENAME",
    "AUTO_REPLY_SELF_SENT_CACHE_FILENAME",
    "RepliedCache",
    "RuleList",
    "SelfSentCache",
    "load_keyword_rule_pairs",
    "load_replied_cache",
    "load_self_sent_cache",
    "save_replied_cache",
    "save_self_sent_cache",
]
