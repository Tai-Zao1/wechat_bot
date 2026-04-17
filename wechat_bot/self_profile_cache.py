#!/usr/bin/env python3
"""本人微信资料缓存与读取。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from wechat_bot.common import SELF_PROFILE_CACHE_FILENAME, load_json_dict, write_json_file
from wechat_bot.core import SelfProfile, get_bot_cache_file


EMPTY_SELF_PROFILE: SelfProfile = {"wxid": "", "wechat_id": "", "nickname": ""}


def _cache_file() -> Path:
    """返回本人资料缓存文件路径。"""
    return get_bot_cache_file(SELF_PROFILE_CACHE_FILENAME)


def load_self_profile_cache() -> SelfProfile:
    """读取本人资料缓存；不存在或损坏时返回空结构。"""
    path = _cache_file()
    data = load_json_dict(path)
    if not data:
        return EMPTY_SELF_PROFILE.copy()
    return {
        "wxid": str(data.get("wxid") or "").strip(),
        "wechat_id": str(data.get("wechat_id") or "").strip(),
        "nickname": str(data.get("nickname") or "").strip(),
    }


def save_self_profile_cache(profile: SelfProfile) -> None:
    """持久化当前登录账号的本人资料。"""
    path = _cache_file()
    payload = {
        "wxid": str(profile.get("wxid") or "").strip(),
        "wechat_id": str(profile.get("wechat_id") or "").strip(),
        "nickname": str(profile.get("nickname") or "").strip(),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    write_json_file(path, payload)


def _close_window_safely(window_obj: Any) -> None:
    """尽量关闭 UI 窗口，避免残留弹窗影响后续自动化。"""
    if window_obj is None:
        return
    try:
        window_obj.close()
        return
    except Exception:
        pass
    try:
        window_obj.type_keys("%{F4}")
    except Exception:
        pass


def fetch_self_profile(log: Callable[[str], None] | None = None) -> SelfProfile:
    """直接从微信 UI 读取本人昵称/微信号，并在失败时收尾弹窗。"""
    from pywinauto import Desktop, mouse
    from pyweixin import Navigator
    from pyweixin.WeChatTools import Tools

    def emit(message: str) -> None:
        if log is not None:
            log(message)

    def _find_text_by_auto_id(auto_id: str, class_name: str = "", timeout_s: float = 2.5) -> str:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            for parent in (moments_window, desktop):
                try:
                    nodes = parent.descendants(control_type="Text")
                except Exception:
                    nodes = []
                for node in nodes:
                    try:
                        node_auto_id = str(node.automation_id() or "").strip()
                        node_class_name = str(node.class_name() or "").strip()
                        text = str(node.window_text() or "").strip()
                    except Exception:
                        continue
                    if not text or node_auto_id != auto_id:
                        continue
                    if class_name and node_class_name != class_name:
                        continue
                    return text
            time.sleep(0.1)
        return ""

    profile_pane = None
    moments_window = None
    wxid = ""
    try:
        try:
            wxid = str(Tools.get_current_wxid() or "").strip()
        except Exception:
            wxid = ""
        moments_window = Navigator.open_moments(is_maximize=False, close_weixin=False)
        moments_list = moments_window.child_window(control_type="List", auto_id="sns_list")
        children = moments_list.children()
        if not children:
            raise RuntimeError("未找到朋友圈列表项")
        rec = children[0].rectangle()
        mouse.click(coords=(rec.right - 60, rec.bottom - 35))
        time.sleep(0.4)
        desktop = Desktop(backend="uia")

        nickname = _find_text_by_auto_id(
            "right_v_view.nickname_button_view.display_name_text",
            timeout_s=2.5,
        )
        if not nickname:
            emit("未命中昵称控件 auto_id=right_v_view.nickname_button_view.display_name_text")

        wechat_id = _find_text_by_auto_id(
            "right_v_view.user_info_center_view.basic_line_view.ContactProfileTextView",
            class_name="mmui::ContactProfileTextView",
            timeout_s=2.0,
        )
        if not wechat_id:
            texts: list[str] = []
            for parent in (moments_window, desktop):
                try:
                    nodes = parent.descendants(control_type="Text")
                except Exception:
                    nodes = []
                for node in nodes:
                    try:
                        text = str(node.window_text() or "").strip()
                    except Exception:
                        text = ""
                    if text:
                        texts.append(text)
                if texts:
                    break
            for idx, text in enumerate(texts):
                if text == "微信号：" and idx + 1 < len(texts):
                    wechat_id = str(texts[idx + 1] or "").strip()
                    break

        profile: SelfProfile = {
            "wxid": wxid,
            "wechat_id": wechat_id or wxid,
            "nickname": nickname,
        }
        save_self_profile_cache(profile)
        emit(f"本人资料缓存已刷新 wxid={profile['wxid'] or '-'} nickname={profile['nickname'] or '-'}")
        return profile
    finally:
        try:
            from pyautogui import press

            press("esc", _pause=False)
        except Exception:
            pass
        _close_window_safely(profile_pane)
        _close_window_safely(moments_window)


def get_self_profile(expected_wxid: str | None = None, log: Callable[[str], None] | None = None) -> SelfProfile:
    """优先读取缓存；缓存账号不匹配时再实时抓取。"""
    cached = load_self_profile_cache()
    current_wxid = str(expected_wxid or "").strip()
    cached_wxid = str(cached.get("wxid") or "").strip()
    if cached and cached_wxid and (not current_wxid or cached_wxid == current_wxid):
        return cached
    return fetch_self_profile(log=log)
