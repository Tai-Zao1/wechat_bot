"""wechat_bot 共享默认配置。"""

from __future__ import annotations

from typing import Final


# GUI 本地配置在 QSettings 中使用的组织名与应用名。
BOT_GUI_SETTINGS_ORG: Final[str] = "pywechat"
BOT_GUI_SETTINGS_APP: Final[str] = "bot_gui"

# 当前项目要求的最低微信版本。
MIN_WECHAT_VERSION: Final[tuple[int, int, int, int]] = (4, 1, 8, 0)

# 机器人常用默认文案。
DEFAULT_AUTO_REPLY_TEXT: Final[str] = "我已收到你的消息，稍后给你详细回复。"
DEFAULT_TIMED_SEND_MESSAGE: Final[str] = "你好，收到请回复。"
DEFAULT_ADD_FRIEND_SOURCE_TEXT: Final[str] = "接口实时获取待加手机号"

# 批量加好友接口与轮询时间默认值。
DEFAULT_ADD_FRIEND_API_PATH: Final[str] = "/autoWx/getNeedAddPhoneList"
DEFAULT_ADD_FRIEND_INTERVAL_MIN_SECONDS: Final[float] = 1200.0
DEFAULT_ADD_FRIEND_INTERVAL_MAX_SECONDS: Final[float] = 2100.0
DEFAULT_ADD_FRIEND_INTERVAL_MIN_MINUTES: Final[float] = 20.0
DEFAULT_ADD_FRIEND_INTERVAL_MAX_MINUTES: Final[float] = 35.0
MIN_ADD_FRIEND_INTERVAL_MINUTES: Final[float] = 20.0

# `wechat_bot` 自有持久化文件名。
SHORTLINK_RULE_FILENAME: Final[str] = "mini_shortlink_rules.json"
SELF_PROFILE_CACHE_FILENAME: Final[str] = "self_profile.json"
FRIEND_LIST_CACHE_FILENAME: Final[str] = "friend_list_cache.json"
