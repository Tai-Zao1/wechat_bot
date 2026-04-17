"""网络配置读取辅助。"""

from __future__ import annotations

import os

from client_api import DEFAULT_CHECK_ONLINE_BASE_URL


def get_check_online_base_url() -> str:
    return (
        os.getenv("PYWECHAT_CHECK_ONLINE_BASE_URL", DEFAULT_CHECK_ONLINE_BASE_URL).strip()
        or DEFAULT_CHECK_ONLINE_BASE_URL
    )


__all__ = [
    "get_check_online_base_url",
]
