"""好友目录与头像同步服务。"""

from __future__ import annotations

import time
import traceback
from pathlib import Path
from typing import Any, Callable

from wechat_bot.common import FRIEND_LIST_CACHE_FILENAME, load_json_dict, write_json_file
from wechat_bot.core import FriendProfile, get_bot_data_dir, get_current_wxid_key


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


def _load_friend_profile_cache(wxid: str | None = None) -> list[FriendProfile]:
    """从本地缓存读取完整好友资料。"""
    cache_path = _friend_cache_path(wxid=wxid)
    data = load_json_dict(cache_path)
    if not data:
        return []
    profiles = data.get("profiles", [])
    if not isinstance(profiles, list):
        return []
    return _normalize_friend_profiles(profiles)


def get_cached_friend_names(wxid: str | None = None) -> list[str]:
    """返回本地缓存中的好友名称列表。"""
    return _load_friend_name_cache(wxid=wxid)


def get_cached_friend_profiles(wxid: str | None = None) -> list[FriendProfile]:
    """返回本地缓存中的完整好友资料。"""
    return _load_friend_profile_cache(wxid=wxid)


def _save_friend_cache(
    names: list[str],
    *,
    profiles: list[FriendProfile] | None = None,
    wxid: str | None = None,
) -> None:
    """保存好友缓存，兼容名称列表与完整资料。"""
    cache_path = _friend_cache_path(wxid=wxid)
    payload = {
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "names": _normalize_names(names),
    }
    if profiles:
        payload["profiles"] = _normalize_friend_profiles(profiles)
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
    """把 pyweixin 原始字典或本地缓存字典整理为统一好友资料结构。"""
    profiles: list[FriendProfile] = []
    seen: set[tuple[str, str]] = set()
    skip_names = {"服务号", "公众号"}
    for item in raw_profiles:
        if not isinstance(item, dict):
            continue

        # 兼容两种来源：
        # 1. pyweixin 原始字段：备注/昵称/微信号/头像路径
        # 2. 本地缓存字段：display_name/remark/nickname/wechat_id/avatar_path
        remark = str(item.get("备注") or item.get("remark") or "").strip()
        nickname = str(item.get("昵称") or item.get("nickname") or "").strip()
        wechat_number = str(item.get("微信号") or item.get("wechat_id") or "").strip()
        avatar_path = str(item.get("头像路径") or item.get("avatar_path") or "").strip()
        display_name = str(item.get("display_name") or "").strip()
        if not display_name:
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
                "avatar_path": avatar_path,
            }
        )
    profiles.sort(key=lambda item: item.get("display_name", ""))
    return profiles


def _profiles_from_names(names: list[str]) -> list[FriendProfile]:
    """把只有展示名的好友列表转换为统一资料结构。"""
    return [
        {"display_name": name, "remark": name, "nickname": "", "wechat_id": "", "avatar_path": ""}
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
            _save_friend_cache(names, wxid=wxid_key)
            emit(f"好友与头像已同步到: {avatar_dir} (wxid={wxid_key})")
            return names
        emit("pyweixin通讯录解析为空，回退会话列表")
    except Exception as exc:
        emit(f"pyweixin通讯录获取失败，已自动回退会话列表: {exc}")

    try:
        names = _fetch_names_from_session_items(log=emit)
        if names:
            _save_friend_cache(names, wxid=wxid_key)
            emit(f"已从会话ListItem加载好友 {len(names)} 人")
            return names
        emit("会话ListItem解析为空，尝试dump_sessions")
    except Exception as exc:
        emit(f"会话ListItem获取失败，尝试dump_sessions: {exc}")
        emit(traceback.format_exc())

    names = _fetch_names_from_dump_sessions(log=emit)
    if not names:
        cached_names = _load_friend_name_cache(wxid=wxid_key)
        if cached_names:
            emit(f"实时加载失败，回退到本地缓存好友 {len(cached_names)} 人")
            return cached_names
        raise RuntimeError("通讯录与会话回退均未获取到好友名称")
    _save_friend_cache(names, wxid=wxid_key)
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

    if not force_refresh:
        cached_profiles = _load_friend_profile_cache(wxid=wxid_key)
        if cached_profiles:
            emit(f"已从本地缓存加载好友资料 {len(cached_profiles)} 人 (wxid={wxid_key})")
            return cached_profiles

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
            avatar_count = sum(1 for item in profiles if str(item.get("avatar_path", "")).strip())
            _save_friend_cache(
                [item["display_name"] for item in profiles],
                profiles=profiles,
                wxid=wxid_key,
            )
            emit(f"好友详情与头像已同步到: {avatar_dir} (wxid={wxid_key})")
            emit(f"好友详情解析: 共 {len(profiles)} 人，带头像路径 {avatar_count} 人")
            return profiles
        emit("pyweixin通讯录详情为空，回退会话列表")
    except Exception as exc:
        emit(f"pyweixin通讯录详情获取失败，已自动回退会话列表: {exc}")

    try:
        names = _fetch_names_from_session_items(log=emit)
        if names:
            profiles = _profiles_from_names(names)
            _save_friend_cache(names, profiles=profiles, wxid=wxid_key)
            emit(f"已从会话ListItem加载好友 {len(names)} 人（无微信号）")
            return profiles
        emit("会话ListItem解析为空，尝试dump_sessions")
    except Exception as exc:
        emit(f"会话ListItem获取失败，尝试dump_sessions: {exc}")
        emit(traceback.format_exc())

    try:
        names = _fetch_names_from_dump_sessions(log=emit)
        if names:
            profiles = _profiles_from_names(names)
            _save_friend_cache(names, profiles=profiles, wxid=wxid_key)
            emit(f"已从dump_sessions加载好友 {len(names)} 人（无微信号）")
            return profiles
        emit("dump_sessions解析为空，尝试本地缓存")
    except Exception as exc:
        emit(f"dump_sessions获取失败，尝试本地缓存: {exc}")
        emit(traceback.format_exc())

    cached_names = _load_friend_name_cache(wxid=wxid_key)
    if cached_names:
        emit(f"实时详情加载失败，回退到本地缓存好友 {len(cached_names)} 人（无微信号）")
        return _profiles_from_names(cached_names)
    raise RuntimeError("通讯录详情与会话回退均未获取到好友信息")


__all__ = [
    "fetch_friend_names",
    "fetch_friend_profiles",
    "get_cached_friend_names",
    "get_cached_friend_profiles",
]
