#!/usr/bin/env python3
"""微信状态检测脚本。"""

from __future__ import annotations

try:
    from .bootstrap import ensure_repo_root_for_scripts
    from .debug_tools import debug_log, exit_if_not_windows, log_debug_banner, log_import_failure
except ImportError:
    from bootstrap import ensure_repo_root_for_scripts
    from debug_tools import debug_log, exit_if_not_windows, log_debug_banner, log_import_failure

ensure_repo_root_for_scripts()

log = debug_log


def main() -> int:
    log_debug_banner()
    skip_code = exit_if_not_windows("检测到非 Windows 系统，跳过微信检测")
    if skip_code is not None:
        return skip_code

    try:
        from pyweixin.WeChatTools import Tools, WxWindowManage
    except Exception as exc:
        log_import_failure(exc)
        return 1

    try:
        is_running = Tools.is_weixin_running()
        log("微信运行中", is_running)
    except Exception as exc:
        log("微信运行中", f"异常: {exc}")
        return 2

    try:
        wechat_path = Tools.where_weixin(copy_to_clipboard=False)
        log("微信路径", wechat_path or "<空>")
    except Exception as exc:
        log("微信路径", f"异常: {exc}")

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
