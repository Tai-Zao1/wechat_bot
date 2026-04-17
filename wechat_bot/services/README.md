# services 目录说明

`services/` 放的是业务实现，不放 GUI，不放微信 UI 控件定位细节。

如果你第一次读这里，按这个顺序看：

1. `friend_directory.py`
   好友列表、好友详情、头像同步。
2. `bailian_client.py`
   本地阿里百炼调用封装。
3. `reply_service.py`
   自动回复里 API 模式 / 本地模式 / 规则兜底的统一调度。
4. `timed_send.py`
   定时群发流程。

兼容说明：

- `friends.py`
  历史别名入口，主实现已经在 `friend_directory.py`。
- `local_ai.py`
  历史别名入口，主实现已经在 `bailian_client.py`。

如果你在写新代码：

- 好友资料相关，优先从 `friend_directory.py` 或 `wechat_bot.services` 导入
- 本地百炼相关，优先从 `bailian_client.py` 或 `wechat_bot.services` 导入
- 自动回复的回复源选择，优先看 `reply_service.py`
