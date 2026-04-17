# scripts 目录说明

`scripts/` 放的是可执行脚本的真实实现。

这里的定位是：

- 面向“任务流程”
- 不直接做 GUI 页面组织
- 按需调用 `services/`、`runtime/`、`pyweixin/`

如果你第一次读这里，按这个顺序看：

1. `registry.py`
   先看新脚本名、旧脚本名、实现文件之间的映射。
2. `__main__.py`
   看 `python -m wechat_bot.scripts ...` 是怎么分发到各脚本的。
3. `auto_reply.py`
   自动回复主流程。
4. `add_friends.py`
   API / Excel 批量加好友流程。
5. `check_wechat.py`
   微信状态检测。
6. `open_wechat.py`
   打开或恢复微信主窗口。

补充说明：

- `auto_reply_support.py`
  不是独立脚本，而是自动回复脚本专用的缓存/规则辅助。
- `bootstrap.py`
  不是独立脚本，而是脚本层统一的启动辅助，用来处理以文件方式运行时的导入路径。
- `debug_tools.py`
  不是独立脚本，而是调试型脚本共用的日志与平台检测辅助。

兼容说明：

- 根目录旧脚本文件名仍然保留，例如：
  `auto_reply_unread.py`、`add_friend_by_phone.py`
- 它们通过 `legacy_support.py` 转发到这里的真实实现

如果你在写新代码：

- 优先改这里的真实脚本实现
- 不要继续往根目录旧脚本入口里增加逻辑
