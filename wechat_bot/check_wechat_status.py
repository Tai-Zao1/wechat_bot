#!/usr/bin/env python3
"""微信状态检测脚本（最小调试）。"""

from __future__ import annotations

import platform
import sys
import traceback
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def log(key: str, value: object) -> None:
    print(f"[DEBUG] {key}: {value}")


def main() -> int:
    log("时间", datetime.now().isoformat(timespec="seconds"))
    log("Python版本", sys.version.split()[0])
    log("系统平台", platform.platform())

    if platform.system().lower() != "windows":
        log("状态", "检测到非 Windows 系统，跳过微信检测")
        return 0

    try:
        from pyweixin.WeChatTools import Tools, WxWindowManage
    except Exception as exc:
        log("导入pyweixin", f"失败: {exc}")
        traceback.print_exc(limit=1)
        return 1

    # 1) 基础运行状态
    try:
        is_running = Tools.is_weixin_running()
        log("微信运行中", is_running)
    except Exception as exc:
        log("微信运行中", f"异常: {exc}")
        return 2

    # 2) 安装路径
    try:
        wechat_path = Tools.where_weixin(copy_to_clipboard=False)
        log("微信路径", wechat_path or "<空>")
    except Exception as exc:
        log("微信路径", f"异常: {exc}")

    # 3) 登录态检测（优先窗口类型，再用 wxid 目录辅助）
    if is_running:
        try:
            wx = WxWindowManage()
            handle = wx.find_wx_window()
            if handle == 0:
                log("窗口识别", "未识别到主窗口/登录窗口（可能未暴露 UI）")
            elif wx.window_type == 0:
                log("微信已登录", False)
            else:
                log("微信已登录", True)
        except Exception as exc:
            log("窗口识别", f"异常: {exc}")
    else:
        log("微信已登录", False)

    try:
        wxid_folder = Tools.where_wxid_folder(open_folder=False)
        log("wxid目录", wxid_folder or "<空>")
    except Exception as exc:
        log("wxid目录", f"异常: {exc}")

    try:
        chatfile_folder = Tools.where_chatfile_folder(open_folder=False)
        log("聊天文件目录", chatfile_folder or "<空>")
    except Exception as exc:
        log("聊天文件目录", f"异常: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
