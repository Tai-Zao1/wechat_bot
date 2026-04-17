# core 目录说明

`core/` 放的是业务层最底部的基础能力，不直接承载业务流程。

当前拆分如下：

- `paths.py`
  本地目录、缓存目录、日志目录、账号隔离路径。
- `runtime_policy.py`
  自动回复节流、任务优先级、运行时锁超时等策略常量。
- `network.py`
  和环境变量相关的网络配置读取。
- `types.py`
  `FriendProfile`、`SelfProfile` 等共享类型。
- `settings.py`
  历史兼容入口，继续汇总导出上面这些内容。

如果你在写新代码：

- 路径相关，优先从 `paths.py` 导入
- 调度/节流策略，优先从 `runtime_policy.py` 导入
- 在线检查地址，优先从 `network.py` 导入
