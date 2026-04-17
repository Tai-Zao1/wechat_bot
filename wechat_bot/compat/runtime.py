"""旧运行时调度导入路径兼容层。"""

from wechat_bot.runtime import (
    claim_task_runtime,
    hold_wechat_ui,
    refresh_task_runtime,
    release_task_runtime,
    should_stop_task_runtime,
)

__all__ = [
    "claim_task_runtime",
    "hold_wechat_ui",
    "refresh_task_runtime",
    "release_task_runtime",
    "should_stop_task_runtime",
]
