# compat 目录说明

`compat/` 是历史导入路径兼容层。

这里不放新的业务实现，只做一件事：

- 把旧模块名映射到现在的主实现层

当前主要分成几类：

- `friends.py`
  对接新的 `services.friend_directory` / `services`。
- `local_ai.py`
  对接新的 `services.bailian_client` / `services`。
- `runtime.py`
  对接新的 `runtime`。
- `self_profile.py`
  对接新的 `runtime.self_profile` / `runtime`。
- `auto_reply.py`
  对接新的 `scripts.auto_reply_support`。

如果你在写新代码：

- 不要从 `compat/` 开始写
- 优先去真正的实现层改代码
- `compat/` 只在需要兼容旧导入路径时保留
