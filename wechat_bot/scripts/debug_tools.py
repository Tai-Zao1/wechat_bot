"""脚本层调试日志辅助。"""

from __future__ import annotations

import platform
import sys
import traceback
from datetime import datetime


def debug_log(key: str, value: object) -> None:
    print(f"[DEBUG] {key}: {value}")


def log_debug_banner() -> None:
    """输出调试脚本统一的启动信息。"""
    debug_log("时间", datetime.now().isoformat(timespec="seconds"))
    debug_log("Python版本", sys.version.split()[0])
    debug_log("系统平台", platform.platform())


def exit_if_not_windows(skip_message: str) -> int | None:
    """非 Windows 环境下输出提示并返回退出码。"""
    if platform.system().lower() != "windows":
        debug_log("状态", skip_message)
        return 0
    return None


def log_import_failure(exc: Exception) -> None:
    """统一输出导入失败日志。"""
    debug_log("导入pyweixin", f"失败: {exc}")
    traceback.print_exc(limit=1)


__all__ = [
    "debug_log",
    "exit_if_not_windows",
    "log_debug_banner",
    "log_import_failure",
]
