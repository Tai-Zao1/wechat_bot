"""wechat_bot 业务层入口。

这个包主要解决三件事：

1. `pyqt_app.py`
   GUI 控制台入口，适合普通使用者直接运行。
2. `auto_reply_unread.py` / `add_friend_by_phone.py`
   可单独运行的业务脚本，适合排查问题或做二次开发。
3. `common/` + `core/`
   默认值、路径、类型、配置等公共基础模块。

如果第一次读仓库，建议先看：

- `wechat_bot/pyqt_app.py`
- `wechat_bot/friend_messaging_service.py`
- `wechat_bot/auto_reply_unread.py`
"""

from .core import FriendProfile, SelfProfile, get_bot_app_root, get_bot_data_dir, get_bot_logs_dir

__all__ = [
    "FriendProfile",
    "SelfProfile",
    "get_bot_app_root",
    "get_bot_data_dir",
    "get_bot_logs_dir",
]
