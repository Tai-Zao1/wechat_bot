# wechat_bot 目录说明

`wechat_bot/` 是这个仓库的业务层。

如果你想快速读懂这个目录，按这个顺序看：

1. `pyqt_app.py`
   GUI 主入口。
2. `scripts/registry.py`
   先看有哪些脚本入口、旧名字和新名字怎么对应。
3. `scripts/`
   真正的脚本实现层，自动回复、批量加好友、打开微信、状态检测都在这里。
   自动回复专用的规则/缓存辅助也已经放进这个层。
   如果只看这一层，建议先读 `scripts/README.md`。
4. `services/`
   业务逻辑层，负责好友列表、头像同步、定时群发、本地百炼等。
   自动回复里的 API / 本地百炼回复调度，放在 `services/reply_service.py`。
   如果只看这一层，建议先读 `services/README.md`。
5. `runtime/`
   运行时状态层，负责跨进程 UI 锁、任务接管、本人资料缓存。
   如果只看这一层，建议先读 `runtime/README.md`。
6. `compat/`
   旧导入路径兼容层。根目录保留的历史模块，大多先转到这里，再转到新实现。
   如果只看这一层，建议先读 `compat/README.md`。
7. `common/` 和 `core/`
   公共常量、JSON 存储、路径、类型、全局配置。
   这两个目录里分别补了 `README.md`，适合第一次读代码时先看。
8. `examples/` 和 `templates/`
   示例规则与 Excel 模板目录，也分别补了 `README.md`。

补充说明：

- `common/` 现在尽量只保留真正通用的内容。
- 自动回复专用辅助已经迁到 `scripts/auto_reply_support.py`。
- `services/friends.py`、`services/local_ai.py` 只是兼容别名，不是主实现。
- `services/README.md` 明确区分了主实现文件和历史别名文件。
- `scripts/README.md` 明确区分了真实脚本实现与旧脚本入口。
- `runtime/README.md` 说明了运行时协调和业务服务的边界。
- `compat/README.md` 说明了哪些模块只是历史兼容，不是新的实现入口。
- `examples/README.md`、`templates/README.md` 用来说明示例文件和模板文件的用途。
- `core/settings.py` 现在是兼容汇总入口，新的细分实现已经拆到 `core/runtime_policy.py` 和 `core/network.py`。

兼容说明：

根目录仍保留一批旧文件名，分成两类：

1. 旧脚本入口：
   `auto_reply_unread.py`、`add_friend_by_phone.py`、`check_wechat_status.py`、`open_wechat_window.py`
2. 旧模块入口：
   `friend_messaging_service.py`、`task_scheduler.py`、`self_profile_cache.py`、`local_bailian.py`

这些文件目前都保留，目的是兼容旧导入路径和旧运行方式。新代码优先放到 `scripts/`、`services/`、`runtime/`。
旧脚本入口的公共转发逻辑集中在 `legacy_support.py`。
旧模块入口的公共转发逻辑集中在 `compat/`。

可以把现在的目录理解成下面这五层：

- `gui/`
  给人看的 GUI 入口。
- `scripts/`
  给任务流程看的脚本层。
- `services/`
  给业务逻辑看的服务层。
- `runtime/`
  给运行状态和跨进程协作看的运行时层。
- `compat/`
  给历史导入路径看的兼容层。
