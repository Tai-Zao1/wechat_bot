"""旧本地 AI 导入路径兼容层。"""

from wechat_bot.services import (
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
