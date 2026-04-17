"""wechat_bot 业务层入口。

这个包主要解决三件事：

1. `gui/`
   给读仓库的人一个明确的 GUI 入口层。
2. `scripts/`
   给可执行脚本提供更容易理解的名字。
3. `services/` + `runtime/` + `common/` + `core/`
   业务逻辑、运行时状态、默认值、路径、类型、配置等公共模块。

兼容考虑下，旧入口仍然保留：

- `pyqt_app.py`
  GUI 控制台入口，适合普通使用者直接运行。
- `auto_reply_unread.py` / `add_friend_by_phone.py`
  可单独运行的兼容脚本入口，适合排查旧调用链或兼容历史运行方式。

根目录现在只建议关注三类文件：

1. 真正的入口
   `pyqt_app.py`
2. 兼容旧运行方式的薄封装
   `auto_reply_unread.py`、`add_friend_by_phone.py`、`check_wechat_status.py`、`open_wechat_window.py`
3. 兼容旧导入路径的薄封装
   `friend_messaging_service.py`、`task_scheduler.py`、`self_profile_cache.py`、`local_bailian.py`

这些旧模块现在优先转发到 `compat/`，再由 `compat/` 映射到新的实现层。

如果第一次读仓库，建议先看：

- `wechat_bot/README.md`
- `wechat_bot/gui/__init__.py`
- `wechat_bot/scripts/registry.py`
- `wechat_bot/scripts/README.md`
- `wechat_bot/services/`
- `wechat_bot/services/README.md`
- `wechat_bot/runtime/`
- `wechat_bot/runtime/README.md`
- `wechat_bot/compat/`
- `wechat_bot/compat/README.md`
- `wechat_bot/common/README.md`
- `wechat_bot/core/README.md`
- `wechat_bot/examples/README.md`
- `wechat_bot/templates/README.md`
- `wechat_bot/pyqt_app.py`
- `wechat_bot/scripts/auto_reply.py`
- `wechat_bot/services/friend_directory.py`
"""

from .core import FriendProfile, SelfProfile, get_bot_app_root, get_bot_data_dir, get_bot_logs_dir

__all__ = [
    "FriendProfile",
    "SelfProfile",
    "get_bot_app_root",
    "get_bot_data_dir",
    "get_bot_logs_dir",
]
