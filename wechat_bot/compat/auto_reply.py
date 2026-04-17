"""旧自动回复辅助导入路径兼容层。"""

from wechat_bot.scripts.auto_reply_support import (
    AUTO_REPLY_CACHE_FILENAME,
    AUTO_REPLY_RECENT_CACHE_FILENAME,
    AUTO_REPLY_SELF_SENT_CACHE_FILENAME,
    RepliedCache,
    RuleList,
    SelfSentCache,
    load_keyword_rule_pairs,
    load_replied_cache,
    load_self_sent_cache,
    save_replied_cache,
    save_self_sent_cache,
)

__all__ = [
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
