#!/usr/bin/env python3
"""打开微信主窗口脚本（最小调试）。"""

from __future__ import annotations

import platform
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from wechat_bot.self_profile_cache import fetch_self_profile


def log(key: str, value: object) -> None:
    print(f"[DEBUG] {key}: {value}")


def _is_ui_not_found_error(exc: Exception) -> bool:
    text = str(exc)
    keywords = (
        "无法识别定位到微信主界面",
        "无法识别",
        "NotFoundError",
    )
    return any(k in text for k in keywords)


def _force_close_wechat_processes() -> None:
    for proc_name in ("Weixin.exe", "WeChat.exe", "WeChatAppEx.exe"):
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", proc_name],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            continue


def _start_windows_narrator() -> bool:
    for cmd in (["Narrator.exe"], ["cmd", "/c", "start", "", "narrator"]):
        try:
            subprocess.Popen(cmd)
            return True
        except Exception:
            continue
    return False


def main() -> int:
    log("时间", datetime.now().isoformat(timespec="seconds"))
    log("Python版本", sys.version.split()[0])
    log("系统平台", platform.platform())

    if platform.system().lower() != "windows":
        log("状态", "检测到非 Windows 系统，无法执行微信窗口打开操作")
        return 0

    try:
        from pyweixin.WeChatTools import Navigator
    except Exception as exc:
        log("导入pyweixin", f"失败: {exc}")
        traceback.print_exc(limit=1)
        return 1

    try:
        window = Navigator.open_weixin(is_maximize=False)
        log("打开微信主窗口", "成功")
        log("窗口类名", window.class_name())
        log("窗口标题", window.window_text())
        try:
            fetch_self_profile(log=lambda message: log("本人资料", message))
        except Exception as exc:
            log("本人资料", f"刷新失败: {exc}")
        return 0
    except Exception as exc:
        log("打开微信主窗口", f"失败: {exc}")
        if _is_ui_not_found_error(exc):
            log("异常处理", "检测到UI识别失败，准备退出微信并开启讲述人")
            _force_close_wechat_processes()
            if _start_windows_narrator():
                log("讲述人模式", "已尝试启动 Narrator.exe，请在登录微信前保持开启后重试")
            else:
                log("讲述人模式", "启动失败，请手动按 Win + Ctrl + Enter 开启讲述人后重试")
            return 3
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
