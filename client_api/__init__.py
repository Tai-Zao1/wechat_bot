"""后端接口客户端入口。

这个包只负责与后端服务通信，不处理微信 UI 自动化。

新手阅读建议：

- 先看 `WeChatAIClient`
- 再看 `client.py` 里的登录、聊天、好友同步几个方法
"""

from .client import (
    DEFAULT_BASE_URL,
    DEFAULT_CHECK_ONLINE_BASE_URL,
    DEFAULT_FRIEND_SYNC_PATH,
    DEFAULT_TIMEOUT,
    WeChatAIAuthenticationError,
    WeChatAIClient,
    WeChatAIClientError,
    WeChatAIClientState,
    WeChatAIForbiddenError,
    WeChatAINetworkError,
    WeChatAIRateLimitError,
    WeChatAIServerError,
    WeChatAIValidationError,
    get_client_app_root,
    get_client_logger,
    get_client_state_path,
    resolve_device_fingerprint_id,
)

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_CHECK_ONLINE_BASE_URL",
    "DEFAULT_FRIEND_SYNC_PATH",
    "DEFAULT_TIMEOUT",
    "WeChatAIAuthenticationError",
    "WeChatAIClient",
    "WeChatAIClientError",
    "WeChatAIClientState",
    "WeChatAIForbiddenError",
    "WeChatAINetworkError",
    "WeChatAIRateLimitError",
    "WeChatAIServerError",
    "WeChatAIValidationError",
    "get_client_app_root",
    "get_client_logger",
    "get_client_state_path",
    "resolve_device_fingerprint_id",
]
