#!/usr/bin/env python3
"""好友列表与定时群发业务逻辑（与 GUI 解耦）。"""

from __future__ import annotations

import os
import random
import re
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Callable

from wechat_bot.common import FRIEND_LIST_CACHE_FILENAME, load_json_dict, write_json_file
from wechat_bot.core import (
    FriendProfile,
    get_bot_app_root,
    get_bot_data_dir,
    get_bot_logs_dir,
    get_current_wxid_key,
    sanitize_file_piece,
)
from wechat_bot.task_scheduler import (
    claim_task_runtime,
    hold_wechat_ui,
    refresh_task_runtime,
    release_task_runtime,
    should_stop_task_runtime,
)


def _friend_cache_path(wxid: str | None = None) -> Path:
    """返回当前账号隔离后的好友缓存路径。"""
    return get_bot_data_dir(wxid=wxid) / FRIEND_LIST_CACHE_FILENAME


def _load_friend_name_cache(wxid: str | None = None) -> list[str]:
    """从本地缓存读取好友昵称列表。"""
    cache_path = _friend_cache_path(wxid=wxid)
    data = load_json_dict(cache_path)
    if not data:
        return []
    names = data.get("names", [])
    if not isinstance(names, list):
        return []
    return _normalize_names([str(name) for name in names])


def get_cached_friend_names(wxid: str | None = None) -> list[str]:
    """返回本地缓存中的好友名称列表。"""
    return _load_friend_name_cache(wxid=wxid)


def _save_friend_name_cache(names: list[str], wxid: str | None = None) -> None:
    """保存好友名称缓存，供 GUI 和回退逻辑复用。"""
    cache_path = _friend_cache_path(wxid=wxid)
    payload = {
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "names": _normalize_names(names),
    }
    write_json_file(cache_path, payload)


def _normalize_names(raw_names: list[str]) -> list[str]:
    """清洗名称列表，去空值、去重并过滤公众号类条目。"""
    names: list[str] = []
    seen: set[str] = set()
    skip_names = {"服务号", "公众号"}
    for name in raw_names:
        clean = str(name).strip()
        if not clean:
            continue
        if clean == "无" or clean in skip_names:
            continue
        if clean in seen:
            continue
        seen.add(clean)
        names.append(clean)
    names.sort()
    return names


def _normalize_friend_profiles(raw_profiles: list[dict[str, Any]]) -> list[FriendProfile]:
    """把 pyweixin 返回的原始字典整理为统一好友资料结构。"""
    profiles: list[FriendProfile] = []
    seen: set[tuple[str, str]] = set()
    skip_names = {"服务号", "公众号"}
    for item in raw_profiles:
        if not isinstance(item, dict):
            continue
        remark = str(item.get("备注", "")).strip()
        nickname = str(item.get("昵称", "")).strip()
        wechat_number = str(item.get("微信号", "")).strip()
        display_name = remark if remark and remark != "无" else nickname
        if not display_name:
            display_name = wechat_number
        if not display_name or display_name == "无" or display_name in skip_names:
            continue
        wechat_number = "" if wechat_number == "无" else wechat_number
        key = (display_name, wechat_number)
        if key in seen:
            continue
        seen.add(key)
        profiles.append(
            {
                "display_name": display_name,
                "remark": "" if remark == "无" else remark,
                "nickname": "" if nickname == "无" else nickname,
                "wechat_id": wechat_number,
            }
        )
    profiles.sort(key=lambda item: item.get("display_name", ""))
    return profiles


def _profiles_from_names(names: list[str]) -> list[FriendProfile]:
    """把只有展示名的好友列表转换为统一资料结构。"""
    return [
        {"display_name": name, "remark": name, "nickname": "", "wechat_id": ""}
        for name in names
    ]


def _extract_name_from_list_item(item: Any) -> str:
    """从 WeChat 4.1.8 会话 ListItem 中提取展示名。"""
    try:
        aid = str(item.automation_id() or "").strip()
    except Exception:
        aid = ""
    if aid.startswith("session_item_"):
        name = aid.replace("session_item_", "", 1).strip()
        if name:
            return name

    try:
        text = str(item.window_text() or "").strip()
    except Exception:
        text = ""
    if text and text not in {"会话", "聊天"}:
        return text
    return ""


def _fetch_names_from_session_items(log: Callable[[str], None] | None = None) -> list[str]:
    """从主窗口会话列表项的 `automation_id` 回退提取好友名称。"""
    from pyweixin.WeChatTools import ListItems, Main_window, Navigator, SideBar

    main_window = Navigator.open_weixin(is_maximize=False)
    raw_names: list[str] = []

    try:
        chat_button = main_window.child_window(**SideBar.Chats)
        if chat_button.exists(timeout=0.8):
            chat_button.click_input()
            time.sleep(0.2)
    except Exception as exc:
        if log is not None:
            log(f"会话按钮点击失败，继续直接扫描: {exc}")

    candidates: list[Any] = []
    try:
        session_list = main_window.child_window(**Main_window.SessionList)
        if session_list.exists(timeout=1.0):
            try:
                session_list.type_keys("{HOME}")
                time.sleep(0.1)
            except Exception:
                pass
            candidates.extend(session_list.children(**ListItems.SessionListItem))
            candidates.extend(session_list.descendants(control_type="ListItem"))
    except Exception as exc:
        if log is not None:
            log(f"会话列表控件定位失败，继续扫描全窗口ListItem: {exc}")

    try:
        candidates.extend(main_window.descendants(control_type="ListItem"))
    except Exception as exc:
        if log is not None:
            log(f"全窗口ListItem扫描失败: {exc}")

    seen_handles: set[int] = set()
    for item in candidates:
        try:
            handle = int(getattr(item, "handle", 0) or 0)
        except Exception:
            handle = 0
        if handle and handle in seen_handles:
            continue
        if handle:
            seen_handles.add(handle)
        name = _extract_name_from_list_item(item)
        if name:
            raw_names.append(name)
    return _normalize_names(raw_names)


def _fetch_names_from_dump_sessions(log: Callable[[str], None] | None = None) -> list[str]:
    """通过 pyweixin Messages.dump_sessions 再兜底读取会话名称。"""
    from pyweixin.WeChatAuto import Messages

    sessions = Messages.dump_sessions(chat_only=False, is_maximize=False, close_weixin=False)
    raw_session_names = [str(item[0]).strip() for item in sessions if isinstance(item, tuple) and len(item) >= 1]
    names = _normalize_names(raw_session_names)
    if log is not None:
        log(f"dump_sessions 返回 {len(names)} 个有效名称")
    return names


def fetch_friend_names(
    log: Callable[[str], None] | None = None,
    force_refresh: bool = False,
) -> list[str]:
    """优先从通讯录读取好友名称，失败时再回退到会话列表。"""
    def emit(text: str) -> None:
        if log is not None:
            log(text)

    wxid_key = get_current_wxid_key()
    if not force_refresh:
        cached_names = _load_friend_name_cache(wxid=wxid_key)
        if cached_names:
            emit(f"已从本地缓存加载好友 {len(cached_names)} 人 (wxid={wxid_key})")
            return cached_names

    try:
        from pyweixin.WeChatAuto import Contacts
        avatar_dir = get_bot_data_dir(wxid=wxid_key) / "friend_avatars"
        avatar_dir.mkdir(parents=True, exist_ok=True)
        details = Contacts.get_friends_detail(
            is_maximize=False,
            close_weixin=False,
            is_json=False,
            save_avatar=True,
            avatar_folder=str(avatar_dir),
            overwrite_avatar=True,
            mark_stale_avatar=True,
        )
        raw_names: list[str] = []
        for item in details:
            if not isinstance(item, dict):
                continue
            remark = str(item.get("备注", "")).strip()
            nickname = str(item.get("昵称", "")).strip()
            name = remark if remark and remark != "无" else nickname
            raw_names.append(name)
        names = _normalize_names(raw_names)
        if names:
            _save_friend_name_cache(names, wxid=wxid_key)
            emit(f"好友与头像已同步到: {avatar_dir} (wxid={wxid_key})")
            return names
        emit("pyweixin通讯录解析为空，回退会话列表")
    except Exception as exc:
        emit(f"pyweixin通讯录获取失败，回退会话列表: {exc}")
        emit(traceback.format_exc())

    try:
        names = _fetch_names_from_session_items(log=emit)
        if names:
            _save_friend_name_cache(names, wxid=wxid_key)
            emit(f"已从会话ListItem加载好友 {len(names)} 人")
            return names
        emit("会话ListItem解析为空，尝试dump_sessions")
    except Exception as exc:
        emit(f"会话ListItem获取失败，尝试dump_sessions: {exc}")
        emit(traceback.format_exc())

    names = _fetch_names_from_dump_sessions(log=emit)
    if not names:
        # 刷新失败时尽量回退旧缓存，避免界面无列表可用。
        cached_names = _load_friend_name_cache(wxid=wxid_key)
        if cached_names:
            emit(f"实时加载失败，回退到本地缓存好友 {len(cached_names)} 人")
            return cached_names
        raise RuntimeError("通讯录与会话回退均未获取到好友名称")
    _save_friend_name_cache(names, wxid=wxid_key)
    emit(f"已从dump_sessions加载好友 {len(names)} 人")
    return names


def fetch_friend_profiles(
    log: Callable[[str], None] | None = None,
    force_refresh: bool = False,
) -> list[FriendProfile]:
    """读取好友详情资料，尽量补齐展示名、备注、昵称和微信号。"""

    def emit(text: str) -> None:
        if log is not None:
            log(text)

    wxid_key = get_current_wxid_key()

    try:
        from pyweixin.WeChatAuto import Contacts

        avatar_dir = get_bot_data_dir(wxid=wxid_key) / "friend_avatars"
        avatar_dir.mkdir(parents=True, exist_ok=True)
        details = Contacts.get_friends_detail(
            is_maximize=False,
            close_weixin=False,
            is_json=False,
            save_avatar=True,
            avatar_folder=str(avatar_dir),
            overwrite_avatar=True,
            mark_stale_avatar=True,
        )
        profiles = _normalize_friend_profiles(details if isinstance(details, list) else [])
        if profiles:
            _save_friend_name_cache([item["display_name"] for item in profiles], wxid=wxid_key)
            emit(f"好友详情与头像已同步到: {avatar_dir} (wxid={wxid_key})")
            return profiles
        emit("pyweixin通讯录详情为空，回退会话列表")
    except Exception as exc:
        emit(f"pyweixin通讯录详情获取失败，回退会话列表: {exc}")
        emit(traceback.format_exc())

    try:
        names = _fetch_names_from_session_items(log=emit)
        if names:
            profiles = _profiles_from_names(names)
            _save_friend_name_cache(names, wxid=wxid_key)
            emit(f"已从会话ListItem加载好友 {len(names)} 人（无微信号）")
            return profiles
        emit("会话ListItem解析为空，尝试dump_sessions")
    except Exception as exc:
        emit(f"会话ListItem获取失败，尝试dump_sessions: {exc}")
        emit(traceback.format_exc())

    try:
        names = _fetch_names_from_dump_sessions(log=emit)
        if names:
            _save_friend_name_cache(names, wxid=wxid_key)
            emit(f"已从dump_sessions加载好友 {len(names)} 人（无微信号）")
            return _profiles_from_names(names)
        emit("dump_sessions解析为空，尝试本地缓存")
    except Exception as exc:
        emit(f"dump_sessions获取失败，尝试本地缓存: {exc}")
        emit(traceback.format_exc())

    cached_names = _load_friend_name_cache(wxid=wxid_key)
    if cached_names:
        emit(f"实时详情加载失败，回退到本地缓存好友 {len(cached_names)} 人（无微信号）")
        return _profiles_from_names(cached_names)
    raise RuntimeError("通讯录详情与会话回退均未获取到好友信息")


def run_timed_send_loop(
    friends: list[str],
    message: str,
    interval_min: float = 5.0,
    interval_max: float = 10.0,
    stop_event: threading.Event | None = None,
    log: Callable[[str], None] | None = None,
) -> int:
    """按随机间隔给选中的好友逐个发送消息，可被手动中断。"""
    from pyweixin import Messages

    if stop_event is None:
        stop_event = threading.Event()
    owner_id = f"timed_send:{os.getpid()}:{threading.get_ident()}:{uuid.uuid4().hex[:8]}"

    def emit(text: str) -> None:
        if log is not None:
            log(text)

    if not friends:
        emit("定时群发结束：未选择好友")
        return 1

    try:
        claim_task_runtime(
            task_type="timed_send",
            owner_id=owner_id,
            label="loop",
            takeover_timeout_s=20.0,
            logger=lambda m: emit(f"运行时: {m}"),
        )
        emit(f"定时群发已启动：好友 {len(friends)} 人，间隔随机 {interval_min}-{interval_max}s（单轮发送）")
        for idx, friend in enumerate(friends):
            refresh_task_runtime("timed_send", owner_id, label=friend)
            if should_stop_task_runtime("timed_send", owner_id):
                emit("业务退出: 新实例接管，当前实例退出")
                return 0
            if stop_event.is_set():
                emit("定时群发已手动停止")
                return 0
            try:
                with hold_wechat_ui(
                    task_type="timed_send",
                    owner_id=owner_id,
                    label=friend,
                    timeout_s=120.0,
                    logger=lambda m: emit(f"调度: {m}"),
                ):
                    Messages.send_messages_to_friend(
                        friend=friend,
                        messages=[message],
                        search_pages=0,
                        is_maximize=False,
                        close_weixin=False,
                    )
                emit(f"已发送 -> {friend}")
            except Exception as exc:
                emit(f"发送失败 -> {friend}: {exc}")

            if idx < len(friends) - 1:
                wait_s = random.uniform(interval_min, interval_max)
                emit(f"下次发送等待 {wait_s:.1f}s")
                end_ts = time.time() + max(wait_s, 0.0)
                while time.time() < end_ts:
                    if should_stop_task_runtime("timed_send", owner_id):
                        emit("业务退出: 新实例接管，当前实例退出")
                        return 0
                    if stop_event.is_set():
                        emit("定时群发已手动停止")
                        return 0
                    time.sleep(0.2)
        emit("定时群发全部发送完成，已自动停止")
        return 0
    finally:
        release_task_runtime("timed_send", owner_id)
