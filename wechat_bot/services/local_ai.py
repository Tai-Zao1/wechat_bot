"""本地 AI 服务兼容入口。

主实现已经迁到 `wechat_bot.services.bailian_client`。
这里保留是为了兼容旧导入路径。

新代码不要再导入这个文件，优先改用：

- `wechat_bot.services`
- `wechat_bot.services.bailian_client`
"""

from .bailian_client import (
    DEFAULT_BAILIAN_ENDPOINT,
    DEFAULT_BAILIAN_SYSTEM_PROMPT,
    LocalBailianClient,
    LocalBailianConfig,
    LocalBailianError,
    mask_secret,
)

__all__ = [
    "DEFAULT_BAILIAN_ENDPOINT",
    "DEFAULT_BAILIAN_SYSTEM_PROMPT",
    "LocalBailianClient",
    "LocalBailianConfig",
    "LocalBailianError",
    "mask_secret",
]
