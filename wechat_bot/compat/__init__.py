"""旧导入路径兼容层。

这个目录不放业务实现，只做历史导入路径到新实现层的映射。

注意：

- 这里不要预加载各个兼容子模块
- 兼容子模块可能再依赖 `common/`、`services/`、`runtime/`
- 预加载会放大循环导入风险

推荐直接按子模块使用，例如：

- `wechat_bot.compat.friends`
- `wechat_bot.compat.local_ai`
- `wechat_bot.compat.runtime`
- `wechat_bot.compat.self_profile`
- `wechat_bot.compat.auto_reply`

第一次阅读建议先看 `compat/README.md`。
"""

__all__ = [
    "auto_reply",
    "friends",
    "local_ai",
    "runtime",
    "self_profile",
]
