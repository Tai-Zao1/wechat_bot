#!/usr/bin/env python3
"""打开微信并实时监听未读消息后自动回复。"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import platform
import random
import re
import sys
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from wechat_bot.common import (
    AUTO_REPLY_CACHE_FILENAME,
    AUTO_REPLY_RECENT_CACHE_FILENAME,
    AUTO_REPLY_SELF_SENT_CACHE_FILENAME,
    DEFAULT_AUTO_REPLY_TEXT,
    RepliedCache,
    RuleList,
    SelfSentCache,
    load_keyword_rule_pairs,
    load_replied_cache,
    load_self_sent_cache,
    save_replied_cache,
    save_self_sent_cache,
)
from wechat_bot.core import (
    AUTO_REPLY_ACTIVE_COOLDOWN_MAX_S,
    AUTO_REPLY_ACTIVE_COOLDOWN_MIN_S,
    AUTO_REPLY_POLL_INTERVAL_S,
    AUTO_REPLY_SKIP_FRIENDS,
    AUTO_REPLY_UNREAD_COOLDOWN_MAX_S,
    AUTO_REPLY_UNREAD_COOLDOWN_MIN_S,
    get_bot_cache_file,
    get_check_online_base_url,
)
from wechat_bot.self_profile_cache import get_self_profile
from client_api import (
    WeChatAIAuthenticationError,
    WeChatAIClient,
    WeChatAIClientError,
)
try:
    from wechat_bot.task_scheduler import (
        claim_task_runtime,
        hold_wechat_ui,
        refresh_task_runtime,
        release_task_runtime,
        should_stop_task_runtime,
    )
except ImportError:
    from wechat_bot.task_scheduler import hold_wechat_ui

    def claim_task_runtime(
        task_type: str,
        owner_id: str,
        label: str = "",
        takeover_timeout_s: float = 15.0,
        poll_interval: float = 0.2,
        logger=None,
    ) -> dict:
        if logger is not None:
            logger(f"{task_type} 运行时不可用，降级旧版调度")
        return {
            "task_type": task_type,
            "owner_id": owner_id,
            "label": label,
            "pid": os.getpid(),
        }

    def refresh_task_runtime(task_type: str, owner_id: str, label: str = "") -> None:
        return None

    def release_task_runtime(task_type: str, owner_id: str) -> None:
        return None

    def should_stop_task_runtime(task_type: str, owner_id: str) -> bool:
        return False

CHAT_TIMESTAMP_RE = re.compile(
    r"^(?:"
    r"\d{2}:\d{2}"
    r"|昨天\s\d{2}:\d{2}"
    r"|星期[一二三四五六日天]\s\d{2}:\d{2}"
    r"|\d{1,2}月\d{1,2}日\s\d{2}:\d{2}"
    r"|\d{4}年\d{1,2}月\d{1,2}日\s\d{2}:\d{2}"
    r")$"
)
QUOTE_PREFIX_RE = re.compile(r"^引用\s+.+?\s+的消息\s*[:：]")
INLINE_QUOTE_MESSAGE_RE = re.compile(
    r"^(?P<reply>.+?)\s*引用\s+(?P<quoted_from>.+?)\s+的消息\s*[:：]\s*(?P<quoted>.+)$"
)
VOICE_MESSAGE_RE = re.compile(r'^语音\s*(\d+)"?秒(.*)$')

AUTH_EXPIRED_MARKER = "[AUTH_EXPIRED]"
AUTO_REPLY_STOP_MARKER = "[AUTO_REPLY_STOP]"
NO_SUBSCRIBER_ERROR_MARKERS = ("事件无法调用任何订户", "无法调用任何订户")
CHECK_ONLINE_BASE_URL = get_check_online_base_url()


def log(key: str, value: object) -> None:
    if key == "UI操作":
        return
    msg = f"[AUTO] {key}: {value}"
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        # 打包后的子进程在部分 Windows 机器上 stdout 仍可能是 gbk，emoji 会触发编码异常。
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe = msg.encode(enc, errors="replace").decode(enc, errors="replace")
        print(safe, flush=True)


def resolve_cache_file() -> Path:
    """Resolve persistent cache path under app-local bot storage."""
    try:
        return get_bot_cache_file(AUTO_REPLY_CACHE_FILENAME)
    except Exception:
        return Path(AUTO_REPLY_CACHE_FILENAME)


def resolve_recent_reply_file() -> Path:
    try:
        return get_bot_cache_file(AUTO_REPLY_RECENT_CACHE_FILENAME)
    except Exception:
        return Path(AUTO_REPLY_RECENT_CACHE_FILENAME)


def resolve_self_sent_file() -> Path:
    try:
        return get_bot_cache_file(AUTO_REPLY_SELF_SENT_CACHE_FILENAME)
    except Exception:
        return Path(AUTO_REPLY_SELF_SENT_CACHE_FILENAME)


def _build_msg_text_key(friend: str, msg_text: str) -> str:
    normalized = " ".join(str(msg_text).split())
    digest = hashlib.sha1(f"{friend}|{normalized}".encode("utf-8")).hexdigest()  # noqa: S324
    return f"txt:{digest}"


def _build_runtime_key(friend: str, runtime_id: tuple[int, ...] | None) -> str | None:
    if not runtime_id:
        return None
    return f"rid:{friend}:{','.join(str(x) for x in runtime_id)}"


def _build_persistent_text_key(friend: str, msg_text: str) -> str | None:
    friend = str(friend or "").strip()
    normalized = " ".join(str(msg_text or "").split()).strip()
    if not friend or not normalized:
        return None
    digest = hashlib.sha1(f"{friend}|{normalized}".encode("utf-8")).hexdigest()  # noqa: S324
    return f"rtxt:{digest}"


def _build_shortlink_trigger_key(friend: str, keyword: str) -> str | None:
    friend = str(friend or "").strip()
    keyword = str(keyword or "").strip()
    if not friend or not keyword:
        return None
    digest = hashlib.sha1(f"{friend}|{keyword}".encode("utf-8")).hexdigest()  # noqa: S324
    return f"slink:{digest}"


def prune_replied_cache(cache: RepliedCache, now_ts: float, ttl_seconds: float) -> int:
    expired_keys = [k for k, ts in cache.items() if now_ts - ts > ttl_seconds]
    for k in expired_keys:
        cache.pop(k, None)
    return len(expired_keys)


def prune_self_sent_cache(
    cache: SelfSentCache,
    now_ts: float,
    ttl_seconds: float,
    max_items: int = 40,
) -> int:
    removed = 0
    empty_friends: list[str] = []
    for friend, items in cache.items():
        kept = [(ts, text) for ts, text in items if now_ts - ts <= ttl_seconds and text]
        if len(kept) > max_items:
            removed += len(kept) - max_items
            kept = kept[-max_items:]
        removed += max(0, len(items) - len(kept))
        if kept:
            cache[friend] = kept
        else:
            empty_friends.append(friend)
    for friend in empty_friends:
        cache.pop(friend, None)
    return removed


def load_rules(rule_file: str | None) -> RuleList:
    return load_keyword_rule_pairs(
        rule_file,
        value_key="reply",
        missing_message="规则文件不存在",
        invalid_message="规则文件格式不合法，支持 dict 或 list[{'keyword','reply'}]",
    )


def choose_reply(latest_text: str, default_reply: str, rules: RuleList) -> str:
    for keyword, reply in rules:
        if keyword in latest_text:
            return reply
    return default_reply


def load_miniprogram_forward_rules(rule_file: str | None) -> RuleList:
    """Load keyword -> source chat mapping for mini program forwarding."""
    return load_keyword_rule_pairs(
        rule_file,
        value_key="source_chat",
        missing_message="小程序转发规则文件不存在",
        invalid_message="小程序转发规则文件格式不合法，支持 dict 或 list[{'keyword','source_chat'}]",
    )


def choose_miniprogram_source(latest_text: str, rules: RuleList) -> str | None:
    for keyword, source_chat in rules:
        if keyword_hit(latest_text, keyword):
            return source_chat
    return None


def load_shortlink_rules(rule_file: str | None) -> RuleList:
    """Load keyword -> shortlink mapping."""
    return load_keyword_rule_pairs(
        rule_file,
        value_key="short_link",
        missing_message="短链规则文件不存在",
        invalid_message="短链规则文件格式不合法，支持 dict 或 list[{'keyword','short_link'}]",
    )


def choose_shortlink(latest_text: str, rules: RuleList) -> str | None:
    for keyword, short_link in rules:
        if keyword_hit(latest_text, keyword):
            return short_link
    return None


def choose_shortlink_rule(latest_text: str, rules: RuleList) -> tuple[str, str] | None:
    for keyword, short_link in rules:
        if keyword_hit(latest_text, keyword):
            return keyword, short_link
    return None


def normalize_for_match(text: str) -> str:
    clean = re.sub(r"\s+", "", str(text or ""))
    clean = re.sub(r"[，。！？、,.!?;；:：'\"“”‘’`~@#$%^&*()（）【】\[\]{}<>《》\-_=+|\\/]", "", clean)
    # 去掉常见口语前缀，提升关键词命中率（如“找工作平台”匹配“先工作平台”）
    prefixes = ("我想", "我要", "想要", "想", "要", "找", "先", "看", "问", "求")
    for p in prefixes:
        if clean.startswith(p) and len(clean) > len(p):
            clean = clean[len(p):]
            break
    return clean


def keyword_hit(latest_text: str, keyword: str) -> bool:
    raw_text = str(latest_text or "")
    raw_key = str(keyword or "").strip()
    if not raw_key:
        return False
    if raw_key in raw_text:
        return True
    t = normalize_for_match(raw_text)
    k = normalize_for_match(raw_key)
    if not t or not k:
        return False
    return (k in t) or (t in k and len(t) >= 3)

def load_chat_api_client() -> WeChatAIClient | None:
    try:
        client = WeChatAIClient.from_saved_state()
    except Exception as exc:
        log("聊天接口", f"读取登录态失败，使用本地规则回复: {exc}")
        return None
    if not client.is_authenticated:
        log("聊天接口", "当前未登录 API，使用本地规则回复")
        return None
    log("聊天接口", "已启用 /autoWx/chat")
    return client


def has_no_subscriber_error(exc: Exception) -> bool:
    texts: list[str] = [str(exc or "")]
    response_data = getattr(exc, "response_data", None)
    if response_data is not None:
        try:
            texts.append(json.dumps(response_data, ensure_ascii=False))
        except Exception:
            texts.append(str(response_data))
    merged = "\n".join(texts)
    return any(marker in merged for marker in NO_SUBSCRIBER_ERROR_MARKERS)


def call_check_online_with_wechat_id(
    wechat_id: str,
    timeout_s: float = 8.0,
    client: WeChatAIClient | None = None,
) -> tuple[bool, str]:
    wxid = str(wechat_id or "").strip()
    if not wxid:
        return False, "缺少当前登录微信号"
    target_client = client
    if target_client is None:
        try:
            target_client = WeChatAIClient.from_saved_state(auto_persist=False)
        except Exception as exc:
            return False, f"创建客户端失败: {exc}"
    try:
        result = target_client.check_online(
            wxid=wxid,
            base_url=CHECK_ONLINE_BASE_URL,
            timeout=max(timeout_s, 1.0),
            require_auth=True,
        )
        body = str(result.get("body", "")).replace("\n", " ")
        if len(body) > 240:
            body = body[:240] + "..."
        return True, f"path={result.get('path')} status={result.get('status')} body={body}"
    except Exception as exc:
        return False, str(exc)


def main() -> int:
    parser = argparse.ArgumentParser(description="打开微信并实时监听未读消息后自动回复")
    parser.add_argument(
        "--reply",
        default=DEFAULT_AUTO_REPLY_TEXT,
        help=f"自动回复内容（默认：{DEFAULT_AUTO_REPLY_TEXT}）",
    )
    parser.add_argument(
        "--search-pages",
        type=int,
        default=0,
        help="查找会话时滚动页数，0 表示直接用顶部搜索（默认 0）",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=AUTO_REPLY_POLL_INTERVAL_S,
        help="轮询间隔秒数（内部固定为 2.0）",
    )
    parser.add_argument(
        "--cooldown-active",
        type=float,
        default=None,
        help="兼容参数：当前会话固定冷却秒数（若传入，则等价于 min=max）",
    )
    parser.add_argument(
        "--cooldown-active-min",
        type=float,
        default=AUTO_REPLY_ACTIVE_COOLDOWN_MIN_S,
        help="当前打开会话冷却最小秒数（内部固定为 2.0）",
    )
    parser.add_argument(
        "--cooldown-active-max",
        type=float,
        default=AUTO_REPLY_ACTIVE_COOLDOWN_MAX_S,
        help="当前打开会话冷却最大秒数（内部固定为 3.0）",
    )
    parser.add_argument(
        "--cooldown-unread",
        type=float,
        default=None,
        help="兼容参数：未读会话固定冷却秒数（若传入，则等价于 min=max）",
    )
    parser.add_argument(
        "--cooldown-unread-min",
        type=float,
        default=AUTO_REPLY_UNREAD_COOLDOWN_MIN_S,
        help="未读会话冷却最小秒数（内部固定为 2.0）",
    )
    parser.add_argument(
        "--cooldown-unread-max",
        type=float,
        default=AUTO_REPLY_UNREAD_COOLDOWN_MAX_S,
        help="未读会话冷却最大秒数（内部固定为 3.0）",
    )
    parser.add_argument(
        "--max-burst-active",
        type=int,
        default=3,
        help="当前会话单轮最多连续回复多少条（默认 3）",
    )
    parser.add_argument(
        "--max-burst-unread",
        type=int,
        default=3,
        help="未读会话单轮最多连续回复多少条（默认 3）",
    )
    parser.add_argument(
        "--burst-gap",
        type=float,
        default=0.35,
        help="同一会话连续回复多条时，每条之间的间隔秒数（默认 0.35）",
    )
    parser.add_argument(
        "--history-probe-count",
        type=int,
        default=2,
        help="进入会话后补扫最近几条对方消息（默认 2）",
    )
    parser.add_argument(
        "--active-history-probe-count",
        type=int,
        default=5,
        help="当前会话补扫最近几条可见对方消息（默认 5）",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="脚本退出后不主动关闭微信窗口",
    )
    parser.add_argument(
        "--rules",
        default=None,
        help="关键词规则 JSON 文件路径（命中关键词时使用对应回复）",
    )
    parser.add_argument(
        "--mini-forward-rules",
        default=None,
        help="小程序转发规则 JSON 文件路径（命中关键词时转发小程序）",
    )
    parser.add_argument(
        "--mini-shortlink-rules",
        default=None,
        help="小程序短链规则 JSON 文件路径（命中关键词时先投递到素材会话，再转发小程序卡片）",
    )
    parser.add_argument(
        "--mini-shortlink-source-chat",
        default="文件传输助手",
        help="小程序短链素材会话名（默认：文件传输助手）",
    )
    parser.add_argument(
        "--mini-shortlink-prepare-delay",
        type=float,
        default=3.0,
        help="小程序短链投递后，等待微信生成可转发小程序记录的秒数（默认 3.0）",
    )
    parser.add_argument(
        "--bailian-app-id",
        default=None,
        help="兼容保留参数（已不使用，统一改走 /client/chat）",
    )
    parser.add_argument(
        "--bailian-api-key",
        default=None,
        help="兼容保留参数（已不使用，统一改走 /client/chat）",
    )
    parser.add_argument(
        "--bailian-timeout",
        type=float,
        default=20.0,
        help="兼容保留参数（已不使用，统一改走 /client/chat）",
    )
    parser.add_argument(
        "--bailian-system",
        default="你是微信自动回复助手。请用简短、礼貌、自然的中文直接回复用户消息，不要解释你是模型。",
        help="兼容保留参数（已不使用，统一改走 /client/chat）",
    )
    parser.add_argument(
        "--dedup-ttl-hours",
        type=float,
        default=24.0,
        help="防重缓存保留时长（小时，默认 24）",
    )
    parser.add_argument(
        "--dedup",
        action="store_true",
        help="启用防重复缓存（默认关闭）",
    )
    parser.add_argument(
        "--skip-friends",
        default=AUTO_REPLY_SKIP_FRIENDS,
        help="逗号分隔：需要跳过自动回复的会话名（默认包含系统会话）",
    )
    parser.add_argument(
        "--log-geometry",
        action="store_true",
        help="输出消息项坐标/方向判定诊断日志（默认关闭）",
    )
    args = parser.parse_args()
    # 自动回复核心轮询/冷却参数固定在代码内，不接受外部运行时改写。
    args.interval = AUTO_REPLY_POLL_INTERVAL_S
    args.cooldown_active_min = AUTO_REPLY_ACTIVE_COOLDOWN_MIN_S
    args.cooldown_active_max = AUTO_REPLY_ACTIVE_COOLDOWN_MAX_S
    args.cooldown_unread_min = AUTO_REPLY_UNREAD_COOLDOWN_MIN_S
    args.cooldown_unread_max = AUTO_REPLY_UNREAD_COOLDOWN_MAX_S
    args.skip_friends = AUTO_REPLY_SKIP_FRIENDS

    if (
        args.cooldown_active_min < 0
        or args.cooldown_active_max < args.cooldown_active_min
        or args.cooldown_active_max > 300
    ):
        raise ValueError("当前会话冷却区间不合法，请检查 --cooldown-active-min/--cooldown-active-max")
    if (
        args.cooldown_unread_min < 0
        or args.cooldown_unread_max < args.cooldown_unread_min
        or args.cooldown_unread_max > 300
    ):
        raise ValueError("未读会话冷却区间不合法，请检查 --cooldown-unread-min/--cooldown-unread-max")
    if args.history_probe_count <= 0:
        raise ValueError("进入会话补扫条数必须大于 0，请检查 --history-probe-count")
    if args.active_history_probe_count <= 0:
        raise ValueError("当前会话补扫条数必须大于 0，请检查 --active-history-probe-count")

    log("时间", datetime.now().isoformat(timespec="seconds"))
    log("系统平台", platform.platform())
    if args.log_geometry:
        log("坐标诊断", "已启用")

    if platform.system().lower() != "windows":
        log("状态", "非 Windows 环境，脚本退出")
        return 0

    import pyautogui
    from pywinauto import Desktop, mouse
    from pyweixin import Messages, Navigator, SystemSettings
    from pyweixin.Uielements import Buttons, Edits, Lists, Main_window as MainWindowUi, MenuItems, SideBar, Texts
    from pyweixin.WeChatTools import Tools
    from pyweixin.utils import scan_for_new_messages as raw_scan_for_new_messages

    rules = load_rules(args.rules)
    if rules:
        log("规则加载", f"{len(rules)} 条")
    else:
        log("规则加载", "0 条（仅使用默认回复）")
    mini_forward_rules = load_miniprogram_forward_rules(args.mini_forward_rules)
    if mini_forward_rules:
        log("小程序规则", f"{len(mini_forward_rules)} 条")
    else:
        log("小程序规则", "0 条（不转发小程序）")
    shortlink_rules = load_shortlink_rules(args.mini_shortlink_rules)
    if shortlink_rules:
        log("短链规则", f"{len(shortlink_rules)} 条")
    else:
        log("短链规则", "0 条（不使用短链转卡片）")
    log("聊天参数", "兼容保留 --bailian-* 参数，但当前统一改走 /client/chat")

    main_window = None
    active_next_reply_at: dict[str, float] = {}
    unread_next_reply_at: dict[str, float] = {}
    active_last_runtime: dict[str, tuple[int, ...]] = {}
    active_last_signature: dict[str, str] = {}
    active_last_text: dict[str, str] = {}
    active_seen_peer_rids: dict[str, set[tuple[int, ...]]] = {}
    active_seen_peer_keys: dict[str, set[str]] = {}
    active_seen_runtime_text_keys: dict[str, set[str]] = {}
    active_self_sent_rids: dict[str, set[tuple[int, ...]]] = {}
    active_self_sent_runtime_text_keys: dict[str, set[str]] = {}
    active_self_sent_signatures: dict[str, set[str]] = {}
    active_pending_msgs: dict[str, collections.deque[tuple[tuple[int, ...], str]]] = {}
    active_pending_seen: dict[str, set[tuple[int, ...]]] = {}
    quote_menu_probe_cache: dict[str, bool] = {}
    unread_pending_msgs: dict[str, collections.deque[tuple[tuple[int, ...] | None, str]]] = {}
    unread_pending_seen: dict[str, set[tuple[int, ...]]] = {}
    unread_pending_keys: dict[str, set[str]] = {}
    unread_session_seen_rids: dict[str, set[tuple[int, ...]]] = {}
    unread_session_seen_keys: dict[str, set[str]] = {}
    voice_text_cache: dict[tuple[int, ...], str] = {}
    voice_convert_attempted: set[tuple[int, ...]] = set()
    voice_self_like_rids: set[tuple[int, ...]] = set()
    replied_runtime_ids: dict[str, dict[tuple[int, ...], str]] = {}
    replied_recent_keys: dict[str, set[str]] = {}
    replied_recent_order: dict[str, collections.deque[tuple[float, str]]] = {}
    replied_recent_texts: dict[str, collections.deque[tuple[float, str]]] = {}
    recent_sent_texts: dict[str, collections.deque[tuple[float, str]]] = {}
    chat_api_client = load_chat_api_client()
    cache_file = resolve_cache_file()
    recent_reply_file = resolve_recent_reply_file()
    self_sent_file = resolve_self_sent_file()
    dedup_ttl_seconds = max(args.dedup_ttl_hours, 1.0) * 3600
    recent_reply_ttl_seconds = 7.0 * 24.0 * 3600.0
    recent_sent_ttl_seconds = 900.0
    replied_cache: dict[str, float] = {}
    recent_reply_cache = load_replied_cache(recent_reply_file)
    self_sent_cache = load_self_sent_cache(self_sent_file)
    legacy_self_sent_keys = [k for k in recent_reply_cache if k.startswith("stxt:")]
    for key in legacy_self_sent_keys:
        recent_reply_cache.pop(key, None)
    removed_recent = prune_replied_cache(recent_reply_cache, time.time(), recent_reply_ttl_seconds)
    if removed_recent or legacy_self_sent_keys:
        save_replied_cache(recent_reply_file, recent_reply_cache)
    removed_self_sent = prune_self_sent_cache(self_sent_cache, time.time(), recent_sent_ttl_seconds)
    if removed_self_sent:
        save_self_sent_cache(self_sent_file, self_sent_cache)
    log("最近回复缓存", f"已启用，加载 {len(recent_reply_cache)} 条，TTL=7天，文件 {recent_reply_file}")
    for friend, items in self_sent_cache.items():
        recent_sent_texts[friend] = collections.deque(items)
    loaded_self_sent = sum(len(items) for items in self_sent_cache.values())
    log("最近发送缓存", f"已启用，加载 {loaded_self_sent} 条，TTL=15分钟，文件 {self_sent_file}")
    if args.dedup:
        replied_cache = load_replied_cache(cache_file)
        removed = prune_replied_cache(replied_cache, time.time(), dedup_ttl_seconds)
        if removed:
            save_replied_cache(cache_file, replied_cache)
        log("防重缓存", f"已启用，加载 {len(replied_cache)} 条，文件 {cache_file}")
    else:
        log("防重缓存", "已关闭")
    edits = Edits()
    lists = Lists()
    menu_items = MenuItems()
    texts = Texts()
    buttons = Buttons()
    main_ui = MainWindowUi()
    side_bar = SideBar()
    last_keyboard_interrupt_at = 0.0
    skip_friends = {
        s.strip()
        for s in str(args.skip_friends or "").replace("，", ",").split(",")
        if s.strip()
    }
    # 素材会话仅用于短链中转，不应参与普通自动回复
    if args.mini_shortlink_source_chat:
        skip_friends.add(str(args.mini_shortlink_source_chat).strip())
    # 群聊暂不参与这条自动回复链路；后续若要支持，可基于这里的运行时识别结果单独扩展群聊策略。
    detected_group_chats: set[str] = set()
    read_only_sessions = {
        "微信团队",
        "微信游戏",
        "腾讯新闻",
        "订阅号消息",
        "服务通知",
    }
    read_only_suppress_until: dict[str, float] = {}
    read_only_suppress_seconds = 90.0
    if skip_friends:
        log("跳过会话", f"{len(skip_friends)} 个")

    self_wechat_id = ""
    self_nickname = ""
    try:
        current_wxid = ""
        try:
            current_wxid = str(Tools.get_current_wxid() or "").strip()
        except Exception:
            current_wxid = ""
        my_info = get_self_profile(
            expected_wxid=current_wxid,
            log=lambda message: log("聊天接口", message),
        )
        self_wechat_id = str(my_info.get("wechat_id") or my_info.get("wxid") or "").strip()
        self_nickname = str(my_info.get("nickname") or "").strip()
    except Exception as exc:
        log("聊天接口", f"读取本人微信信息失败，聊天接口将使用降级字段: {exc}")
    if not self_wechat_id:
        try:
            self_wechat_id = str(Tools.get_current_wxid() or "").strip()
        except Exception:
            self_wechat_id = ""

    def scan_for_new_messages(
        main_window=None,
        delay: float = 0.3,
        is_maximize: bool | None = None,
        close_weixin: bool | None = None,
    ) -> dict:
        """优先纯读会话列表，只有不在聊天页时才点击左侧“微信”按钮。"""
        target_main_window = main_window
        if target_main_window is None:
            target_main_window = open_wechat_main_window_with_retry()

        def traverse_message_list(list_items):
            filtered = [
                item
                for item in list_items
                if item.automation_id() not in {"session_item_服务号", "session_item_公众号"}
                and "消息免打扰" not in item.window_text()
            ]
            filtered = [item for item in filtered if new_message_pattern.search(item.window_text())]
            senders = [item.automation_id().replace("session_item_", "") for item in filtered]
            tips = [item.window_text() for item in filtered if item.window_text() not in senders]
            nums = [int(new_message_pattern.search(text).group(1)) for text in tips]
            return senders, nums

        chats_button = target_main_window.child_window(**side_bar.Chats)
        session_list = target_main_window.child_window(**main_ui.SessionList)
        new_message_pattern = re.compile(r"\n\[(\d+)条\]")
        clicked_chats_button = False

        try:
            session_list_ready = bool(session_list.exists(timeout=0.1) and session_list.is_visible())
        except Exception:
            session_list_ready = bool(session_list.exists(timeout=0.1))

        if not session_list_ready:
            log("会话扫描", "当前不在聊天页或会话列表不可见，点击左侧微信按钮后再扫描")
            try:
                if chats_button.exists(timeout=0.1):
                    chats_button.click_input()
                    clicked_chats_button = True
                    time.sleep(0.2)
            except Exception as exc:
                log("会话扫描", f"点击左侧微信按钮失败，回退旧扫描逻辑: {exc}")
                return raw_scan_for_new_messages(
                    main_window=target_main_window,
                    delay=delay,
                    is_maximize=is_maximize if is_maximize is not None else False,
                    close_weixin=close_weixin if close_weixin is not None else False,
                )
            try:
                session_list_ready = bool(session_list.exists(timeout=0.5) and session_list.is_visible())
            except Exception:
                session_list_ready = bool(session_list.exists(timeout=0.5))
            if not session_list_ready:
                log("会话扫描", "点击后仍未命中会话列表，回退旧扫描逻辑")
                return raw_scan_for_new_messages(
                    main_window=target_main_window,
                    delay=delay,
                    is_maximize=is_maximize if is_maximize is not None else False,
                    close_weixin=close_weixin if close_weixin is not None else False,
                )

        try:
            full_desc = chats_button.element_info.element.GetCurrentPropertyValue(30159)
        except Exception as exc:
            log("会话扫描", f"读取左侧微信按钮未读属性失败，回退旧扫描逻辑: {exc}")
            return raw_scan_for_new_messages(
                main_window=target_main_window,
                delay=delay,
                is_maximize=is_maximize if is_maximize is not None else False,
                close_weixin=close_weixin if close_weixin is not None else False,
            )

        try:
            session_list.type_keys("{HOME}")
        except Exception as exc:
            log("会话扫描", f"会话列表回顶失败，回退旧扫描逻辑: {exc}")
            return raw_scan_for_new_messages(
                main_window=target_main_window,
                delay=delay,
                is_maximize=is_maximize if is_maximize is not None else False,
                close_weixin=close_weixin if close_weixin is not None else False,
            )

        new_message_num_match = re.search(r"\d+", str(full_desc or ""))
        if not new_message_num_match:
            mode = "点击后" if clicked_chats_button else "直读"
            log("会话扫描", f"{mode}检测未读总数=0")
            return {}

        total_unread = int(new_message_num_match.group(0))
        new_message_senders: list[str] = []
        new_message_nums: list[int] = []
        new_messages_dict: dict[str, int] = {}
        mode = "点击后" if clicked_chats_button else "直读"
        log("会话扫描", f"{mode}检测未读总数={total_unread}")

        try:
            session_list.type_keys("{END}")
            time.sleep(1)
            last_item = session_list.children(control_type="ListItem")[-1].window_text()
            session_list.type_keys("{HOME}")
            time.sleep(1)
            while sum(new_messages_dict.values()) < total_unread:
                list_items = session_list.children(control_type="ListItem")
                time.sleep(delay)
                senders, nums = traverse_message_list(list_items)
                new_message_nums.extend(nums)
                new_message_senders.extend(senders)
                new_messages_dict = dict(zip(new_message_senders, new_message_nums))
                session_list.type_keys("{PGDN}")
                if list_items and list_items[-1].window_text() == last_item:
                    break
            session_list.type_keys("{HOME}")
        except Exception as exc:
            log("会话扫描", f"遍历会话列表失败，回退旧扫描逻辑: {exc}")
            return raw_scan_for_new_messages(
                main_window=target_main_window,
                delay=delay,
                is_maximize=is_maximize if is_maximize is not None else False,
                close_weixin=close_weixin if close_weixin is not None else False,
            )

        if new_messages_dict:
            log("会话扫描", f"命中未读会话 {len(new_messages_dict)} 个: {', '.join(new_messages_dict.keys())}")
        return new_messages_dict

    def should_skip_friend(friend: str) -> bool:
        name = str(friend or "").strip()
        if not name:
            return True
        return name in skip_friends

    def is_read_only_session(friend: str | None) -> bool:
        name = str(friend or "").strip()
        if not name:
            return False
        return name in detected_group_chats or name in read_only_sessions

    def get_session_policy(friend: str | None) -> str:
        name = str(friend or "").strip()
        if not name:
            return "ignore"
        if should_skip_friend(name):
            return "ignore"
        if is_read_only_session(name):
            return "read_only"
        return "reply"

    def is_read_only_suppressed(friend: str | None, now_ts: float | None = None) -> bool:
        name = str(friend or "").strip()
        if not name:
            return False
        ts = float(read_only_suppress_until.get(name, 0.0) or 0.0)
        current_ts = time.time() if now_ts is None else now_ts
        return ts > current_ts

    def suppress_read_only_session(friend: str | None, ttl_s: float = read_only_suppress_seconds, reason: str = "") -> None:
        name = str(friend or "").strip()
        if not name:
            return
        until = time.time() + max(ttl_s, 1.0)
        read_only_suppress_until[name] = until
        suffix = f"，原因={reason}" if reason else ""
        log("已读抑制", f"{name} {ttl_s:.0f}s{suffix}")

    def remember_group_chat(friend: str, source: str = "") -> None:
        name = str(friend or "").strip()
        if not name or name in detected_group_chats:
            return
        detected_group_chats.add(name)
        suffix = f"，来源={source}" if source else ""
        log("会话跳过", f"{name}（群聊，暂不自动回复{suffix}）")

    def is_auto_reply_group_chat(friend: str | None = None) -> bool:
        """当前聊天窗口若出现群聊专属标题控件，则视为群聊。"""
        try:
            if friend:
                current_name = get_current_chat_name()
                if current_name and current_name != friend:
                    return False
            return bool(main_window.child_window(**texts.GroupLabelText).exists(timeout=0.1))
        except Exception:
            return False

    def should_skip_auto_reply(friend: str | None) -> bool:
        name = str(friend or "").strip()
        if not name:
            return True
        return get_session_policy(name) != "reply"

    def looks_like_self_quote_text(friend: str, text: str) -> bool:
        """过滤明显由己方发送的引用文本，避免被误判为对方新消息。"""
        t = str(text or "").strip()
        if not t:
            return True
        if "对方最新消息：" in t:
            return False
        # 微信引用回复在不同版本里可能是“前缀引用”或“正文 + 引用尾巴”。
        if f"引用 {friend} 的消息" in t:
            return True
        # 不能把所有“引用 xxx 的消息”都当成自发消息；
        # 对方引用我方消息时，也会带引用前缀，但那仍然是对方的新消息。
        if not QUOTE_PREFIX_RE.search(t):
            return False
        norm = _normalize_msg_text(t)
        if not norm:
            return False
        return looks_like_recent_self_sent(friend, norm)

    def _normalize_msg_text(text: str) -> str:
        return " ".join(str(text or "").replace("\n", " ").split()).strip()

    def _split_nonempty_lines(text: str) -> list[str]:
        lines: list[str] = []
        seen: set[str] = set()
        for raw in str(text or "").splitlines():
            line = _normalize_msg_text(raw)
            if not line or line in seen:
                continue
            seen.add(line)
            lines.append(line)
        return lines

    def is_quote_context_text(text: str) -> bool:
        norm = _normalize_msg_text(text)
        if not norm:
            return False
        return bool(QUOTE_PREFIX_RE.search(norm) or ("引用 " in norm and " 的消息" in norm))

    def extract_quote_context_text(text: str) -> str:
        lines = _split_nonempty_lines(text)
        if not lines:
            return ""
        cleaned: list[str] = []
        for line in lines:
            if QUOTE_PREFIX_RE.match(line):
                continue
            cleaned.append(line)
        return _normalize_msg_text("\n".join(cleaned))

    def build_model_input_text(message_text: str, quote_text: str = "") -> str:
        msg = _normalize_msg_text(message_text)
        quoted = _normalize_msg_text(quote_text)
        if not quoted:
            return msg
        return f"对方引用内容：{quoted}\n对方最新消息：{msg}"

    def parse_inline_quote_message(text: str) -> tuple[str, str, str] | None:
        norm = _normalize_msg_text(text)
        if not norm:
            return None
        match = INLINE_QUOTE_MESSAGE_RE.match(norm)
        if not match:
            return None
        reply_text = _normalize_msg_text(match.group("reply"))
        quoted_from = _normalize_msg_text(match.group("quoted_from"))
        quoted_text = _normalize_msg_text(match.group("quoted"))
        if not reply_text:
            return None
        return reply_text, quoted_from, quoted_text

    def merge_quote_context_pairs(
        pairs: list[tuple[tuple[int, ...], str]],
    ) -> list[tuple[tuple[int, ...], str]]:
        return [(rid, _normalize_msg_text(text)) for rid, text in pairs if _normalize_msg_text(text)]

    def rect_to_text(rect) -> str:
        try:
            return f"({int(rect.left)},{int(rect.top)},{int(rect.right)},{int(rect.bottom)})"
        except Exception:
            return "(unknown)"

    def log_item_geometry(friend: str, item, *, source: str, stage: str, verdict: str, text: str = "") -> None:
        if not args.log_geometry:
            return
        try:
            chat_list = main_window.child_window(**lists.FriendChatList)
            list_rect = chat_list.rectangle() if chat_list.exists(timeout=0.05) else None
        except Exception:
            list_rect = None
        try:
            item_rect = item.rectangle()
        except Exception:
            item_rect = None
        try:
            rid = tuple(item.element_info.runtime_id or ())
        except Exception:
            rid = ()

        anchor_rects = _collect_item_anchor_rects(item)
        left_gap = right_gap = center_bias = None
        if item_rect is not None and list_rect is not None:
            left_gap = max(0, int(item_rect.left - list_rect.left))
            right_gap = max(0, int(list_rect.right - item_rect.right))
            center_bias = int(item_rect.mid_point().x - list_rect.mid_point().x)

        anchor_summary = "none"
        if anchor_rects:
            leftmost = min(left for left, _right, _top, _bottom in anchor_rects)
            rightmost = max(right for _left, right, _top, _bottom in anchor_rects)
            anchor_summary = f"{leftmost}-{rightmost}#{len(anchor_rects)}"

        preview = _normalize_msg_text(text or extract_item_text(item))[:80]
        log(
            "坐标诊断",
            f"{source}/{stage} {friend} rid={format_runtime_id(rid)} verdict={verdict} "
            f"list={rect_to_text(list_rect)} item={rect_to_text(item_rect)} "
            f"gapL={left_gap} gapR={right_gap} bias={center_bias} anchors={anchor_summary} text={preview}",
        )

    def remember_recent_sent_text(friend: str, text: str) -> None:
        """记录最近发送文本，兜底过滤UI误判导致的“自回复”。"""
        norm = _normalize_msg_text(text)
        if not norm:
            return
        now_ts = time.time()
        q = recent_sent_texts.setdefault(friend, collections.deque())
        q.append((now_ts, norm))
        # 保留更长窗口，避免进入会话补扫最近几条历史记录时把自己刚发的普通消息又识别成未读。
        while q and (now_ts - q[0][0] > recent_sent_ttl_seconds or len(q) > 40):
            q.popleft()
        self_sent_cache[friend] = list(q)
        prune_self_sent_cache(self_sent_cache, now_ts, recent_sent_ttl_seconds)
        try:
            save_self_sent_cache(self_sent_file, self_sent_cache)
        except Exception as exc:
            log("最近发送缓存保存失败", exc)

    def format_runtime_id(runtime_id: tuple[int, ...] | None) -> str:
        if not runtime_id:
            return "None"
        return "(" + ",".join(str(x) for x in runtime_id) + ")"

    def build_pending_text_key(text: str) -> str:
        norm = _normalize_msg_text(text)
        return hashlib.sha1(norm.encode("utf-8", errors="ignore")).hexdigest() if norm else ""

    def build_reply_identity_key(friend: str, runtime_id: tuple[int, ...] | None, text: str) -> str:
        norm = _normalize_msg_text(text)
        digest = hashlib.sha1(norm.encode("utf-8", errors="ignore")).hexdigest() if norm else "empty"  # noqa: S324
        rid_part = ",".join(str(x) for x in (runtime_id or ())) or "none"
        return f"{friend}|{rid_part}|{digest}"

    def has_replied_runtime_id(friend: str, runtime_id: tuple[int, ...] | None, text: str | None = None) -> bool:
        if not runtime_id:
            return False
        mapping = replied_runtime_ids.get(friend, {})
        if runtime_id not in mapping:
            return False
        if text is None:
            return True
        remembered_text = mapping.get(runtime_id, "")
        norm = _normalize_msg_text(text)
        return bool(remembered_text) and text_match_loose(remembered_text, norm)

    def remember_replied_runtime_id(friend: str, runtime_id: tuple[int, ...] | None, text: str | None = None) -> None:
        if not runtime_id:
            return
        replied_runtime_ids.setdefault(friend, {})[runtime_id] = _normalize_msg_text(text)

    def prune_replied_recent(friend: str, ttl: float = 1800.0, max_items: int = 200) -> None:
        q = replied_recent_order.setdefault(friend, collections.deque())
        s = replied_recent_keys.setdefault(friend, set())
        now_ts = time.time()
        while q and (now_ts - q[0][0] > ttl or len(q) > max_items):
            _, key = q.popleft()
            s.discard(key)
        text_q = replied_recent_texts.setdefault(friend, collections.deque())
        while text_q and (now_ts - text_q[0][0] > ttl or len(text_q) > max_items):
            text_q.popleft()

    def has_recently_replied_text(friend: str, text: str, ttl: float = 1800.0) -> bool:
        norm = _normalize_msg_text(text)
        if not norm:
            return False
        prune_replied_recent(friend, ttl=ttl)
        for _, replied_text in reversed(replied_recent_texts.get(friend, ())):
            if replied_text == norm:
                return True
        return False

    def has_persist_recently_replied_text(friend: str, text: str) -> bool:
        key = _build_persistent_text_key(friend, text)
        if not key:
            return False
        ts = recent_reply_cache.get(key)
        if ts is None:
            return False
        return time.time() - ts <= recent_reply_ttl_seconds

    def should_trigger_shortlink(friend: str, keyword: str, cooldown_s: float = 60.0) -> bool:
        key = _build_shortlink_trigger_key(friend, keyword)
        if not key:
            return True
        ts = recent_reply_cache.get(key)
        if ts is None:
            return True
        return time.time() - ts > cooldown_s

    def remember_shortlink_trigger(friend: str, keyword: str) -> None:
        key = _build_shortlink_trigger_key(friend, keyword)
        if not key:
            return
        now_ts = time.time()
        recent_reply_cache[key] = now_ts
        prune_replied_cache(recent_reply_cache, now_ts, recent_reply_ttl_seconds)
        try:
            save_replied_cache(recent_reply_file, recent_reply_cache)
        except Exception as exc:
            log("短链触发缓存保存失败", exc)

    def is_recently_replied(friend: str, runtime_id: tuple[int, ...] | None, text: str) -> bool:
        if has_replied_runtime_id(friend, runtime_id, text):
            return True
        key = build_reply_identity_key(friend, runtime_id, text)
        prune_replied_recent(friend)
        return key in replied_recent_keys.get(friend, set())

    def remember_recent_replied(friend: str, runtime_id: tuple[int, ...] | None, text: str) -> None:
        remember_replied_runtime_id(friend, runtime_id, text)
        key = build_reply_identity_key(friend, runtime_id, text)
        norm = _normalize_msg_text(text)
        q = replied_recent_order.setdefault(friend, collections.deque())
        s = replied_recent_keys.setdefault(friend, set())
        text_q = replied_recent_texts.setdefault(friend, collections.deque())
        prune_replied_recent(friend)
        if key in s:
            if norm:
                text_q.append((time.time(), norm))
            return
        s.add(key)
        now_ts = time.time()
        q.append((now_ts, key))
        if norm:
            text_q.append((now_ts, norm))
        persistent_text_key = _build_persistent_text_key(friend, text)
        if persistent_text_key:
            recent_reply_cache[persistent_text_key] = now_ts
            prune_replied_cache(recent_reply_cache, now_ts, recent_reply_ttl_seconds)
            try:
                save_replied_cache(recent_reply_file, recent_reply_cache)
            except Exception as exc:
                log("最近回复缓存保存失败", exc)

    def log_queue_event(queue_name: str, action: str, friend: str, runtime_id: tuple[int, ...] | None, text: str, size: int) -> None:
        preview = _normalize_msg_text(text)[:80]
        log(
            "队列",
            f"{queue_name} {action} {friend} rid={format_runtime_id(runtime_id)} 队列={size} 文本={preview}",
        )

    def looks_like_recent_self_sent(friend: str, text: str) -> bool:
        norm = _normalize_msg_text(text)
        if not norm:
            return True
        q = recent_sent_texts.get(friend)
        if not q:
            return False
        now_ts = time.time()
        while q and now_ts - q[0][0] > recent_sent_ttl_seconds:
            q.popleft()
        for _, sent in q:
            if norm == sent:
                return True
            # 引用回复/气泡抽取在不同版本里可能会附带或截断引用文本，
            # 这里允许“以已发送正文为前缀/主体”的轻度模糊命中。
            if len(sent) >= 12 and (norm.startswith(sent) or sent.startswith(norm)):
                return True
            if len(sent) >= 24 and (sent in norm or norm in sent):
                return True
        return False

    def trim_pairs_after_recent_self_anchor(
        friend: str,
        pairs: list[tuple[tuple[int, ...] | None, str]],
        *,
        source: str,
    ) -> list[tuple[tuple[int, ...] | None, str]]:
        """命中最近自发文本时，直接把该锚点之前的历史裁掉，减少重复判定。"""
        if len(pairs) <= 1:
            return pairs
        q = recent_sent_texts.get(friend)
        if not q:
            return pairs
        sent_texts = [text for _ts, text in q if text]
        if not sent_texts:
            return pairs
        anchor_idx = -1
        for idx in range(len(pairs) - 1, -1, -1):
            _rid, msg = pairs[idx]
            norm = _normalize_msg_text(msg)
            if not norm:
                continue
            for sent in reversed(sent_texts):
                if text_match_loose(norm, sent):
                    anchor_idx = idx
                    break
            if anchor_idx >= 0:
                break
        if anchor_idx < 0:
            return pairs
        if anchor_idx == len(pairs) - 1:
            log("未读修正", f"{friend} {source}命中自发锚点，当前候选全部丢弃")
            return []
        log("未读修正", f"{friend} {source}命中自发锚点，丢弃前{anchor_idx + 1}条历史，仅保留后续新消息")
        return pairs[anchor_idx + 1 :]

    def should_auto_reply_text(text: str) -> bool:
        norm = _normalize_msg_text(text)
        if not norm:
            return False
        voice_payload = extract_voice_payload(norm)
        if VOICE_MESSAGE_RE.match(norm):
            return bool(voice_payload)
        if norm in {"[长消息]", "文件", "动画表情"}:
            return False
        if looks_like_program_traceback(norm):
            return False
        system_text_markers = (
        )
        if any(marker in norm for marker in system_text_markers):
            return False
        if norm.startswith("文件 "):
            return False
        if "微信电脑版" in norm and ("文件" in norm or ".exe" in norm or ".zip" in norm or ".rar" in norm):
            return False
        return True

    def looks_like_program_traceback(text: str) -> bool:
        norm = _normalize_msg_text(text)
        if not norm:
            return False
        if norm.startswith("Traceback (most recent call last):"):
            return True
        if ' File "' in f" {norm}" and ", line " in norm and (
            "Traceback" in norm
            or "Error:" in norm
            or "Exception:" in norm
            or "RuntimeError:" in norm
        ):
            return True
        return False

    def extract_voice_payload(text: str) -> str:
        norm = _normalize_msg_text(text)
        if not norm:
            return ""
        matched = VOICE_MESSAGE_RE.match(norm)
        if not matched:
            return ""
        payload = (matched.group(2) or "").strip()
        if payload.startswith("未播放"):
            payload = payload[3:].strip()
        return payload

    def is_voice_runtime_id(runtime_id: tuple[int, ...] | None) -> bool:
        if not runtime_id:
            return False
        if runtime_id in voice_text_cache:
            return True
        item = find_chat_item_by_runtime_id(runtime_id)
        if item is None:
            return False
        try:
            return item.class_name() == "mmui::ChatVoiceItemView"
        except Exception:
            return False

    def is_self_like_voice_runtime_id(runtime_id: tuple[int, ...] | None) -> bool:
        if not runtime_id:
            return False
        return runtime_id in voice_self_like_rids

    def find_chat_item_by_runtime_id(runtime_id: tuple[int, ...] | None):
        if not runtime_id:
            return None
        try:
            chat_list = main_window.child_window(**lists.FriendChatList)
            if not chat_list.exists(timeout=0.1):
                return None
            for item in chat_list.children(control_type="ListItem"):
                try:
                    rid = tuple(item.element_info.runtime_id or ())
                except Exception:
                    rid = ()
                if rid == runtime_id:
                    return item
        except Exception:
            return None
        return None

    def find_voice_convert_menu_item():
        candidates = [
            {"title": "语音转文字", "control_type": "MenuItem"},
            {"title": "转为文字", "control_type": "MenuItem"},
            {"title_re": ".*(?:语音转文字|转为文字).*", "control_type": "MenuItem"},
        ]
        for kwargs in candidates:
            try:
                item = main_window.child_window(**kwargs)
                if item.exists(timeout=0.15):
                    return item
            except Exception:
                pass
        try:
            desktop = Desktop(backend="uia")
        except Exception:
            return None
        for kwargs in candidates:
            try:
                item = desktop.window(**kwargs)
                if item.exists(timeout=0.15):
                    return item
            except Exception:
                pass
        for kwargs in candidates:
            try:
                nodes = desktop.descendants(**kwargs)
            except Exception:
                nodes = []
            for node in nodes:
                try:
                    if node.exists(timeout=0.05):
                        return node
                except Exception:
                    continue
        return None

    # 已回退：此前这里新增过“消费阶段先探测语音转文字菜单”的试验逻辑。
    # 该逻辑会对当前聊天中的手机端自发语音产生额外右键干扰，暂不启用，保留注释便于后续继续排查。
    # def probe_voice_convert_action(item) -> tuple[bool, object | None]:
    #     try:
    #         rect = item.rectangle()
    #         click_x = rect.left + min(100, max(28, rect.width() // 3))
    #         click_y = rect.mid_point().y
    #         mouse.right_click(coords=(click_x, click_y))
    #         convert_item = find_voice_convert_menu_item()
    #         if convert_item is None:
    #             try:
    #                 pyautogui.press("esc", _pause=False)
    #             except Exception:
    #                 pass
    #             return False, None
    #         return True, convert_item
    #     except Exception:
    #         try:
    #             pyautogui.press("esc", _pause=False)
    #         except Exception:
    #             pass
    #         return False, None

    def convert_voice_item_to_text(item, timeout: float = 8.0, interval: float = 0.3, convert_item=None) -> str:
        try:
            rid = tuple(item.element_info.runtime_id or ())
        except Exception:
            rid = ()
        cached = voice_text_cache.get(rid)
        if cached:
            return cached
        if rid and rid in voice_self_like_rids:
            return ""
        if rid and rid in voice_convert_attempted:
            return ""
        raw_text = _normalize_msg_text(extract_item_text_fallback(item))
        payload = extract_voice_payload(raw_text)
        if payload:
            if rid:
                voice_text_cache[rid] = payload
            return payload
        try:
            if item.class_name() != "mmui::ChatVoiceItemView":
                return ""
        except Exception:
            return ""
        try:
            if rid:
                voice_convert_attempted.add(rid)
            rect = item.rectangle()
            click_x = rect.left + min(100, max(28, rect.width() // 3))
            click_y = rect.mid_point().y
            mouse.right_click(coords=(click_x, click_y))
            if convert_item is None:
                convert_item = find_voice_convert_menu_item()
            if convert_item is None:
                if rid:
                    voice_self_like_rids.add(rid)
                log("语音转写", f"rid={format_runtime_id(rid)} 未找到“语音转文字”菜单项，视为自己的语音并跳过")
                return ""
            log("语音转写", f"rid={format_runtime_id(rid)} 点击菜单项: {(convert_item.window_text() or '转为文字').strip()}")
            convert_item.click_input()
            deadline = time.time() + timeout
            while time.time() < deadline:
                current = find_chat_item_by_runtime_id(rid) or item
                current_text = _normalize_msg_text(extract_item_text_fallback(current))
                payload = extract_voice_payload(current_text)
                if payload:
                    if rid:
                        voice_text_cache[rid] = payload
                    log("语音转写", f"rid={format_runtime_id(rid)} 成功: {payload[:80]}")
                    return payload
                time.sleep(interval)
            log("语音转写", f"rid={format_runtime_id(rid)} 等待转写超时")
        except Exception:
            try:
                pyautogui.press("esc", _pause=False)
            except Exception:
                pass
            return ""
        return ""

    def resolve_message_for_consumption(
        friend: str,
        runtime_id: tuple[int, ...] | None,
        msg_text: str,
    ) -> str:
        norm = _normalize_msg_text(msg_text)
        if not runtime_id:
            return norm
        item = find_chat_item_by_runtime_id(runtime_id)
        if item is None:
            return norm
        try:
            class_name = item.class_name()
        except Exception:
            class_name = ""
        if class_name != "mmui::ChatVoiceItemView":
            return norm
        cached_voice_text = voice_text_cache.get(runtime_id or ())
        if cached_voice_text:
            return _normalize_msg_text(cached_voice_text)
        # 已回退：此前这里新增过“消费阶段先探测菜单/快速判定本人语音”的分支。
        # 该分支会额外插入一次右键动作，当前先恢复为直接进入原有转写流程。
        voice_text = convert_voice_item_to_text(item)
        if voice_text:
            return _normalize_msg_text(voice_text)
        log("语音转写", f"{friend} rid={format_runtime_id(runtime_id)} 消费阶段未取得转写文本，跳过")
        return ""

    def extract_item_text_fallback(item) -> str:
        try:
            text = item.window_text().strip()
        except Exception:
            text = ""
        if text:
            return text
        parts: list[str] = []
        for ctype in ("Text", "Document", "Edit"):
            try:
                nodes = item.descendants(control_type=ctype)
            except Exception:
                nodes = []
            for node in nodes:
                try:
                    t = node.window_text().strip()
                except Exception:
                    t = ""
                if t:
                    parts.append(t)
        if not parts:
            return ""
        merged: list[str] = []
        seen: set[str] = set()
        for p in parts:
            if p in seen:
                continue
            seen.add(p)
            merged.append(p)
        return "\n".join(merged).strip()

    def is_friend_accept_system_text(text: str) -> bool:
        norm = _normalize_msg_text(text)
        if not norm:
            return False
        return (
            "我通过了你的朋友验证请求" in norm
            and "现在我们可以开始聊天了" in norm
        )

    def is_greeting_divider_text(text: str) -> bool:
        norm = _normalize_msg_text(text)
        if not norm:
            return False
        return "以上是打招呼的消息" in norm

    def should_attempt_quote_text(text: str) -> bool:
        norm = _normalize_msg_text(text)
        if not norm:
            return False
        if not should_auto_reply_text(norm):
            return False
        suspicious_tokens = (
            "GUI日志文件",
            "PyWeChatBot\\logs",
            "AppData\\Local\\PyWeChatBot",
            "[AUTO]",
            "wxid_",
            "系统平台:",
            "监听状态:",
            "已启动:",
            "退出码=",
        )
        if any(token in norm for token in suspicious_tokens):
            return False
        if norm.startswith("[") and "] " in norm and ":" in norm[:12]:
            return False
        return True

    def enqueue_active_pending(friend: str, items: list[tuple[tuple[int, ...], str]], source: str = "当前会话") -> int:
        if not items:
            return 0
        q = active_pending_msgs.setdefault(friend, collections.deque())
        seen = active_pending_seen.setdefault(friend, set())
        added = 0
        for rid, text in items:
            if not rid or rid in seen:
                continue
            q.append((rid, text))
            seen.add(rid)
            added += 1
            log_queue_event(f"active[{source}]", "新进入", friend, rid, text, len(q))
        return added

    def pop_active_pending(friend: str) -> tuple[tuple[int, ...], str] | None:
        q = active_pending_msgs.get(friend)
        if not q:
            return None
        item = q.popleft()
        rid = item[0]
        seen = active_pending_seen.get(friend)
        if seen is not None:
            seen.discard(rid)
        log_queue_event("active", "移除", friend, rid, item[1], len(q))
        return item

    def remember_seen_peer_rid(friend: str, rid: tuple[int, ...]) -> None:
        if not rid:
            return
        s = active_seen_peer_rids.setdefault(friend, set())
        s.add(rid)
        # 防止集合无限增长，极端情况下做一次轻量清理
        if len(s) > 1200:
            active_seen_peer_rids[friend] = set(list(s)[-800:])

    def has_seen_peer_rid(friend: str, rid: tuple[int, ...]) -> bool:
        return rid in active_seen_peer_rids.get(friend, set())

    def remember_seen_peer_key(friend: str, key: str) -> None:
        if not key:
            return
        s = active_seen_peer_keys.setdefault(friend, set())
        s.add(key)
        if len(s) > 1200:
            active_seen_peer_keys[friend] = set(list(s)[-800:])

    def has_seen_peer_key(friend: str, key: str) -> bool:
        return key in active_seen_peer_keys.get(friend, set())

    def build_runtime_text_key(
        runtime_id: tuple[int, ...] | None,
        text: str,
        item=None,
    ) -> str:
        norm = _normalize_msg_text(text)
        if not norm:
            norm = "[empty]"
        extra = ""
        if item is not None:
            try:
                rect = item.rectangle()
                extra = f"|{int(rect.width())}x{int(rect.height())}"
            except Exception:
                extra = ""
        rid_part = ",".join(str(x) for x in (runtime_id or ())) or "none"
        base = f"{rid_part}|{norm}{extra}"
        return hashlib.sha1(base.encode("utf-8", errors="ignore")).hexdigest()  # noqa: S324

    def remember_seen_runtime_text_key(friend: str, key: str) -> None:
        if not key:
            return
        s = active_seen_runtime_text_keys.setdefault(friend, set())
        s.add(key)
        if len(s) > 1200:
            active_seen_runtime_text_keys[friend] = set(list(s)[-800:])

    def has_seen_runtime_text_key(friend: str, key: str) -> bool:
        return key in active_seen_runtime_text_keys.get(friend, set())

    def remember_self_sent_runtime_id(friend: str, rid: tuple[int, ...]) -> None:
        if not rid:
            return
        s = active_self_sent_rids.setdefault(friend, set())
        s.add(rid)
        if len(s) > 800:
            active_self_sent_rids[friend] = set(list(s)[-400:])

    def remember_self_sent_runtime_text_key(friend: str, key: str) -> None:
        if not key:
            return
        s = active_self_sent_runtime_text_keys.setdefault(friend, set())
        s.add(key)
        if len(s) > 800:
            active_self_sent_runtime_text_keys[friend] = set(list(s)[-400:])

    def remember_self_sent_signature(friend: str, signature: str) -> None:
        if not signature:
            return
        s = active_self_sent_signatures.setdefault(friend, set())
        s.add(signature)
        if len(s) > 800:
            active_self_sent_signatures[friend] = set(list(s)[-400:])

    def remember_self_sent_item(friend: str, item, text: str = "") -> None:
        try:
            rid = tuple(item.element_info.runtime_id or ())
        except Exception:
            rid = ()
        if rid:
            remember_self_sent_runtime_id(friend, rid)
        runtime_text_key = build_runtime_text_key(rid, text or extract_item_text(item), item)
        remember_self_sent_runtime_text_key(friend, runtime_text_key)
        try:
            signature = build_item_signature(item)
        except Exception:
            signature = ""
        remember_self_sent_signature(friend, signature)

    def is_remembered_self_sent_item(friend: str, item, text: str = "") -> bool:
        try:
            rid = tuple(item.element_info.runtime_id or ())
        except Exception:
            rid = ()
        if rid and rid in active_self_sent_rids.get(friend, set()):
            return True
        runtime_text_key = build_runtime_text_key(rid, text or extract_item_text(item), item)
        if runtime_text_key and runtime_text_key in active_self_sent_runtime_text_keys.get(friend, set()):
            return True
        try:
            signature = build_item_signature(item)
        except Exception:
            signature = ""
        if signature and signature in active_self_sent_signatures.get(friend, set()):
            return True
        return False

    def enqueue_unread_pending(friend: str, items: list[tuple[tuple[int, ...] | None, str]], source: str = "未读") -> int:
        if not items:
            return 0
        q = unread_pending_msgs.setdefault(friend, collections.deque())
        seen = unread_pending_seen.setdefault(friend, set())
        pending_keys = unread_pending_keys.setdefault(friend, set())
        added = 0
        for rid, text in items:
            text = str(text or "").strip()
            if not text:
                continue
            text_key = build_pending_text_key(text)
            if rid and has_replied_runtime_id(friend, rid, text):
                continue
            if rid and rid in seen:
                continue
            if text_key and text_key in pending_keys:
                continue
            q.append((rid, text))
            if rid:
                seen.add(rid)
                remember_seen_peer_rid(friend, rid)
            if text_key:
                pending_keys.add(text_key)
            added += 1
            log_queue_event(f"unread[{source}]", "新进入", friend, rid, text, len(q))
            if is_voice_runtime_id(rid):
                log("语音队列", f"{friend} 未读队列已记录 rid={format_runtime_id(rid)} 文本={text[:80]}")
        return added

    def reset_unread_capture_session(friend: str) -> None:
        unread_session_seen_rids[friend] = set()
        unread_session_seen_keys[friend] = set()

    def remember_unread_session_item(friend: str, rid: tuple[int, ...] | None, text: str) -> None:
        if rid:
            unread_session_seen_rids.setdefault(friend, set()).add(rid)
            return
        text_key = build_pending_text_key(text)
        if text_key:
            unread_session_seen_keys.setdefault(friend, set()).add(text_key)

    def has_unread_session_item(friend: str, rid: tuple[int, ...] | None, text: str) -> bool:
        if rid:
            return rid in unread_session_seen_rids.get(friend, set())
        text_key = build_pending_text_key(text)
        if not text_key:
            return False
        return text_key in unread_session_seen_keys.get(friend, set())

    def pop_unread_pending(friend: str) -> tuple[tuple[int, ...] | None, str] | None:
        q = unread_pending_msgs.get(friend)
        if not q:
            return None
        rid, text = q.popleft()
        if rid:
            seen = unread_pending_seen.get(friend)
            if seen is not None:
                seen.discard(rid)
        text_key = build_pending_text_key(text)
        if text_key:
            pending_keys = unread_pending_keys.get(friend)
            if pending_keys is not None:
                pending_keys.discard(text_key)
        log_queue_event("unread", "移除", friend, rid, text, len(q))
        return rid, text

    def push_front_unread_pending(friend: str, item: tuple[tuple[int, ...] | None, str], source: str = "抢占回退") -> None:
        rid, text = item
        q = unread_pending_msgs.setdefault(friend, collections.deque())
        q.appendleft((rid, text))
        if rid:
            unread_pending_seen.setdefault(friend, set()).add(rid)
        text_key = build_pending_text_key(text)
        if text_key:
            unread_pending_keys.setdefault(friend, set()).add(text_key)
        log_queue_event(f"unread[{source}]", "新进入", friend, rid, text, len(q))

    def capture_current_chat_to_unread_pending(
        friend: str,
        unread_num: int,
        source: str,
        *,
        after_entry_only: bool = False,
    ) -> int:
        """已进入目标会话后，先把最近未读消息落到本地队列，避免切回其他会话时丢失。"""
        probe_num = max(int(unread_num or 0), int(args.history_probe_count or 0), 1)
        if probe_num <= 0:
            return 0
        visible_pairs = collect_recent_peer_items(
            max(probe_num + 12, 20),
            friend=friend,
        )
        if not visible_pairs:
            return 0
        visible_pairs = trim_pairs_after_recent_self_anchor(
            friend,
            visible_pairs,
            source="进入会话补扫",
        )
        if not visible_pairs:
            return 0
        log("未读修正", f"{friend} 会话列表标记={unread_num}，历史补扫={probe_num}，实际候选={len(visible_pairs)}")
        if not after_entry_only and len(visible_pairs) > 1:
            divider_idx = -1
            accept_idx = -1
            for idx, (_rid, msg) in enumerate(visible_pairs):
                if is_greeting_divider_text(msg):
                    divider_idx = idx
                if is_friend_accept_system_text(msg):
                    accept_idx = idx
            if 0 <= divider_idx < len(visible_pairs) - 1:
                log(
                    "未读修正",
                    f"{friend} 补扫命中打招呼分界，丢弃前{divider_idx + 1}条历史，仅保留分界后的消息",
                )
                visible_pairs = visible_pairs[divider_idx + 1 :]
            elif accept_idx >= 0:
                log(
                    "未读修正",
                    f"{friend} 补扫命中好友通过验证提示，丢弃前{accept_idx}条验证前历史，仅从验证提示开始处理",
                )
                visible_pairs = visible_pairs[accept_idx:]
        candidate_pairs: list[tuple[tuple[int, ...] | None, str]] = []
        for rid, msg in visible_pairs:
            if looks_like_self_quote_text(friend, msg):
                log("自发过滤", f"{friend} -> 进入会话补扫命中引用文本特征")
                continue
            if looks_like_recent_self_sent(friend, msg):
                log("自发过滤", f"{friend} -> 进入会话补扫命中最近发送文本")
                continue
            # 旧逻辑：未读补扫阶段直接按文本可回复性过滤，语音会被当作“非文本”跳过。
            # if not should_auto_reply_text(msg):
            #     log("消息类型跳过", f"{friend} -> 进入会话补扫跳过非文本消息: {_normalize_msg_text(msg)[:80]}")
            #     continue
            # 新逻辑：语音先入候选队列，消费阶段再判断并转写；避免当前会话遗漏语音。
            is_voice_candidate = bool(VOICE_MESSAGE_RE.match(_normalize_msg_text(msg))) or is_voice_runtime_id(rid)
            if not is_voice_candidate and not should_auto_reply_text(msg):
                log("消息类型跳过", f"{friend} -> 进入会话补扫跳过非文本消息: {_normalize_msg_text(msg)[:80]}")
                continue
            candidate_pairs.append((rid, msg))

        pairs: list[tuple[tuple[int, ...] | None, str]] = []
        for rid, msg in candidate_pairs:
            if after_entry_only and has_unread_session_item(friend, rid, msg):
                continue
            if after_entry_only and (
                has_recently_replied_text(friend, msg, ttl=300.0)
                or has_persist_recently_replied_text(friend, msg)
            ):
                continue
            log("收到未读", f"{friend} rid={format_runtime_id(rid)} 文本={_normalize_msg_text(msg)[:80]}")
            pairs.append((rid, msg))
        if not after_entry_only and len(pairs) > 1:
            last_recent_reply_idx = -1
            for idx, (rid, msg) in enumerate(pairs):
                if (
                    has_replied_runtime_id(friend, rid, msg)
                    or has_recently_replied_text(friend, msg)
                    or has_persist_recently_replied_text(friend, msg)
                ):
                    last_recent_reply_idx = idx
            if 0 <= last_recent_reply_idx < len(pairs) - 1:
                log(
                    "未读修正",
                    f"{friend} 进入会话命中历史锚点，丢弃前{last_recent_reply_idx + 1}条已回历史，仅保留后续新消息",
                )
                pairs = pairs[last_recent_reply_idx + 1 :]
            elif last_recent_reply_idx == len(pairs) - 1:
                log(
                    "未读修正",
                    f"{friend} 进入会话候选均为近期已回复历史，整批丢弃",
                )
                pairs = []
        for rid, msg in candidate_pairs:
            remember_unread_session_item(friend, rid, msg)
        added = enqueue_unread_pending(friend, pairs, source=source)
        if added > 0:
            now_pending = len(unread_pending_msgs.get(friend, ()))
            log("未读会话缓存", f"{friend} 新增{added}条待回复（队列{now_pending}）")
        return added

    def get_post_switch_candidate_count(friend: str, unread_num: int) -> int:
        probe_num = max(int(unread_num or 0), int(args.history_probe_count or 0), 1)
        if probe_num <= 0:
            return 0
        visible_pairs = collect_recent_peer_items(
            max(probe_num + 12, 20),
            friend=friend,
        )
        if not visible_pairs:
            return 0
        visible_pairs = trim_pairs_after_recent_self_anchor(
            friend,
            visible_pairs,
            source="切入后估算",
        )
        if not visible_pairs:
            return 0
        count = 0
        for rid, msg in visible_pairs:
            if looks_like_self_quote_text(friend, msg):
                continue
            if looks_like_recent_self_sent(friend, msg):
                continue
            if not should_auto_reply_text(msg):
                continue
            if has_replied_runtime_id(friend, rid, msg):
                continue
            count += 1
        return count

    def make_reply(friend: str, latest_text: str) -> str:
        nonlocal chat_api_client
        if chat_api_client is None:
            chat_api_client = load_chat_api_client()
        if chat_api_client is not None:
            try:
                clean_friend = str(friend or "").strip()
                wechat_id = self_wechat_id or "wxid_unknown"
                log(
                    "聊天接口请求",
                    f"{friend} -> /autoWx/chat wechatId={wechat_id} nickname={self_nickname or '-'} toNickname={clean_friend or '-'}",
                )
                log(
                    "聊天接口参数",
                    {
                        "wechatId": wechat_id,
                        "content": latest_text,
                        "nickname": self_nickname,
                        "displayName": clean_friend,
                        "toNickname": clean_friend,
                    },
                )
                result = chat_api_client.chat(
                    wxid=wechat_id,
                    message=latest_text,
                    nickname=self_nickname,
                    display_name=clean_friend,
                    to_nickname=clean_friend,
                )
                reply = str(result.get("reply") or "").strip()
                if reply:
                    reply_source = str(result.get("reply_source") or "").strip() or "-"
                    record_id = result.get("record_id")
                    log("聊天接口回复", f"{friend} source={reply_source} record_id={record_id}")
                    return reply
                log("聊天接口回复为空", f"{friend} -> 使用本地规则回复")
            except WeChatAIAuthenticationError as exc:
                log("聊天接口鉴权失效", f"{friend} -> {exc}")
                print(f"{AUTH_EXPIRED_MARKER} 自动回复鉴权失效: {exc}", flush=True)
                raise SystemExit(401)
            except KeyboardInterrupt as exc:
                log("聊天接口调用中断", f"{friend} -> {exc}")
            except Exception as exc:
                if has_no_subscriber_error(exc):
                    current_wxid = str(self_wechat_id or "").strip()
                    ok, detail = call_check_online_with_wechat_id(current_wxid, client=chat_api_client)
                    if ok:
                        log("在线检查回调", f"{friend} -> checkOnline 成功: {detail}")
                    else:
                        log("在线检查回调失败", f"{friend} -> {detail}")
                    print(
                        f"{AUTO_REPLY_STOP_MARKER} 检测到“事件无法调用任何订户”，"
                        f"已调用 checkOnline，停止自动回复。wechatId={current_wxid or 'wxid_unknown'}",
                        flush=True,
                    )
                    raise SystemExit(460)
                log("聊天接口调用失败", f"{friend} -> {exc}")
                chat_api_client = None
        return choose_reply(latest_text, args.reply, rules)

    def random_cooldown(min_s: float, max_s: float) -> float:
        if max_s <= min_s:
            return float(min_s)
        return random.uniform(min_s, max_s)

    def is_replied_before(
        friend: str,
        msg_text: str,
        runtime_id: tuple[int, ...] | None,
        *,
        use_persistent_recent: bool = True,
    ) -> bool:
        if is_recently_replied(friend, runtime_id, msg_text):
            return True
        if not args.dedup:
            return False
        now_ts = time.time()
        runtime_key = _build_runtime_key(friend, runtime_id)
        runtime_ts = replied_cache.get(runtime_key) if runtime_key else None
        return runtime_ts is not None and now_ts - runtime_ts <= dedup_ttl_seconds

    def mark_replied(friend: str, msg_text: str, runtime_id: tuple[int, ...] | None) -> None:
        if is_voice_runtime_id(runtime_id):
            log("语音队列", f"{friend} 准备写入已回复 rid={format_runtime_id(runtime_id)} 文本={_normalize_msg_text(msg_text)[:80]}")
        remember_recent_replied(friend, runtime_id, msg_text)
        now_ts = time.time()
        runtime_key = _build_runtime_key(friend, runtime_id)
        if runtime_key:
            recent_reply_cache[runtime_key] = now_ts
        prune_replied_cache(recent_reply_cache, now_ts, recent_reply_ttl_seconds)
        try:
            save_replied_cache(recent_reply_file, recent_reply_cache)
        except Exception as exc:
            log("最近回复缓存保存失败", exc)
        if is_voice_runtime_id(runtime_id):
            log("语音队列", f"{friend} 已写入已回复 rid={format_runtime_id(runtime_id)}")
        if not args.dedup:
            return
        if runtime_key:
            replied_cache[runtime_key] = now_ts
        prune_replied_cache(replied_cache, now_ts, dedup_ttl_seconds)
        try:
            save_replied_cache(cache_file, replied_cache)
        except Exception as exc:
            log("缓存保存失败", exc)

    def collect_recent_peer_items(
        limit: int,
        *,
        friend: str | None = None,
    ) -> list[tuple[tuple[int, ...], str]]:
        """从当前会话底部向上收集最近的对方消息 runtime_id 与文本。"""
        if limit <= 0:
            return []
        chat_list = main_window.child_window(**lists.FriendChatList)
        edit_area = main_window.child_window(**edits.CurrentChatEdit)
        if not chat_list.exists(timeout=0.1) or not edit_area.exists(timeout=0.1):
            return []
        pairs: list[tuple[tuple[int, ...], str]] = []
        items = chat_list.children(control_type="ListItem")
        for item in reversed(items):
            raw_text = _normalize_msg_text(extract_item_text(item))
            if CHAT_TIMESTAMP_RE.fullmatch(raw_text):
                # 历史补扫只保留当天消息；一旦碰到“昨天/星期/月日/年月日”分隔，直接停止继续向上找。
                if not re.fullmatch(r"\d{2}:\d{2}", raw_text):
                    break
                continue
            rid = tuple(item.element_info.runtime_id or ())
            if not rid:
                continue
            ok, text = is_peer_message_item(
                friend,
                item,
                edit_area,
                require_quote_menu=False,
            )
            if not ok:
                continue
            text = text or "[长消息]"
            pairs.append((rid, text))
            if len(pairs) >= limit:
                break
        pairs.reverse()  # oldest -> newest
        return merge_quote_context_pairs(pairs)

    def collect_peer_runtime_ids(limit: int) -> list[tuple[int, ...]]:
        return [rid for rid, _ in collect_recent_peer_items(limit)]

    def text_match_loose(a: str, b: str) -> bool:
        na = _normalize_msg_text(a)
        nb = _normalize_msg_text(b)
        if not na or not nb:
            return False
        if na == nb:
            return True
        short, long = (na, nb) if len(na) <= len(nb) else (nb, na)
        if len(short) >= 8 and short in long:
            return True
        return False

    def align_reply_inputs_with_visible_items(
        reply_inputs: list[str],
        visible_pairs: list[tuple[tuple[int, ...], str]],
    ) -> list[tuple[tuple[int, ...] | None, str]]:
        """尽量按文本把 pull_messages 的结果与当前可见对方消息对齐，避免 runtime_id 错配。"""
        if not reply_inputs:
            return []
        aligned: list[tuple[tuple[int, ...] | None, str]] = []
        visible_idx = 0
        visible_len = len(visible_pairs)
        for msg in reply_inputs:
            rid: tuple[int, ...] | None = None
            match_idx: int | None = None
            for idx in range(visible_idx, visible_len):
                candidate_rid, candidate_text = visible_pairs[idx]
                if text_match_loose(msg, candidate_text):
                    rid = candidate_rid
                    match_idx = idx
                    break
            if match_idx is not None:
                visible_idx = match_idx + 1
            aligned.append((rid, msg))
        return aligned

    def _collect_item_anchor_rects(item) -> list[tuple[int, int, int, int]]:
        """收集消息项内可见子控件矩形，用于估算消息落点。"""
        rects: list[tuple[int, int, int, int]] = []
        for ctype in ("Button", "Image", "Text", "Document", "Edit"):
            try:
                nodes = item.descendants(control_type=ctype)
            except Exception:
                nodes = []
            for node in nodes[:12]:
                try:
                    rect = node.rectangle()
                except Exception:
                    continue
                if rect.width() <= 2 or rect.height() <= 2:
                    continue
                rects.append((int(rect.left), int(rect.right), int(rect.top), int(rect.bottom)))
        return rects

    def is_my_bubble_fast(item, edit_area) -> bool:
        """优先用气泡在消息区的左右落点判断是否为本人发送。"""
        try:
            item_rect = item.rectangle()
            chat_list = main_window.child_window(**lists.FriendChatList)
            if not chat_list.exists(timeout=0.05):
                return False
            list_rect = chat_list.rectangle()
            item_width = max(1, int(item_rect.width()))
            list_width = max(1, int(list_rect.width()))
            if item_width <= 0 or list_width <= 0:
                return False

            left_gap = max(0, int(item_rect.left - list_rect.left))
            right_gap = max(0, int(list_rect.right - item_rect.right))
            center_bias = int(item_rect.mid_point().x - list_rect.mid_point().x)

            if right_gap <= 80 and center_bias >= max(40, list_width // 12):
                return True
            if left_gap <= 80 and center_bias <= -max(40, list_width // 12):
                return False

            anchor_xs: list[int] = []
            for ctype in ("Button", "Image", "Text", "Document"):
                try:
                    nodes = item.descendants(control_type=ctype)
                except Exception:
                    nodes = []
                for node in nodes[:8]:
                    try:
                        rect = node.rectangle()
                    except Exception:
                        continue
                    if rect.width() <= 2 or rect.height() <= 2:
                        continue
                    anchor_xs.append(rect.mid_point().x)
            if anchor_xs:
                avg_x = sum(anchor_xs) / len(anchor_xs)
                if avg_x >= list_rect.mid_point().x + max(36, list_width * 0.08):
                    return True
                if avg_x <= list_rect.mid_point().x - max(36, list_width * 0.08):
                    return False

            gap_delta = left_gap - right_gap
            if gap_delta >= max(90, list_width // 10):
                return True
            if gap_delta <= -max(90, list_width // 10):
                return False
            return False
        except Exception:
            return False

    def extract_item_text(item) -> str:
        """尽量提取消息文本，兼容超长消息在 window_text 为空的情况。"""
        text = extract_item_text_fallback(item)
        parsed_inline_quote = parse_inline_quote_message(text)
        if parsed_inline_quote is not None:
            reply_text, _quoted_from, _quoted_text = parsed_inline_quote
            return reply_text
        if not text:
            return ""
        merged_text = text
        parsed_inline_quote = parse_inline_quote_message(merged_text)
        if parsed_inline_quote is not None:
            reply_text, _quoted_from, _quoted_text = parsed_inline_quote
            return reply_text
        return merged_text

    def build_item_signature(item) -> str:
        """构造消息项签名：用于 runtime_id 不变但内容变化的场景。"""
        text = extract_item_text(item)
        try:
            rect = item.rectangle()
            h = int(rect.height())
            w = int(rect.width())
        except Exception:
            h = -1
            w = -1
        # 不纳入 runtime_id，避免同一条消息因 UI 虚拟化换 runtime_id 造成误判。
        base = f"{w}x{h}|{text}"
        return hashlib.sha1(base.encode("utf-8", errors="ignore")).hexdigest()  # noqa: S324

    def build_peer_item_key(item) -> str:
        """对方消息稳定键：优先文本，规避长消息 runtime_id 抖动反复入队。"""
        text = extract_item_text(item)
        try:
            cname = item.class_name()
        except Exception:
            cname = ""
        if not text:
            try:
                rect = item.rectangle()
                dim = f"{int(rect.width())}x{int(rect.height())}"
            except Exception:
                dim = "unknown"
            text = f"[empty]|{dim}"
        # 旧逻辑：所有消息统一只用 class_name + text 做稳定键。
        # base = f"{cname}|{text}"
        #
        # 新逻辑：语音消息的原始文本经常重复，如“语音2"秒未播放”，
        # 仅用文本会把不同语音误判成同一条，导致当前会话 active 队列漏消息。
        # 对语音消息补充 runtime_id，降低重复误判；其他消息保持原逻辑。
        if cname == "mmui::ChatVoiceItemView":
            try:
                rid = tuple(item.element_info.runtime_id or ())
            except Exception:
                rid = ()
            rid_part = ",".join(str(x) for x in rid) or "none"
            base = f"{cname}|{rid_part}|{text}"
        else:
            base = f"{cname}|{text}"
        return hashlib.sha1(base.encode("utf-8", errors="ignore")).hexdigest()  # noqa: S324

    def build_quote_probe_cache_key(item, text: str = "") -> str:
        try:
            rid = tuple(item.element_info.runtime_id or ())
        except Exception:
            rid = ()
        rid_part = format_runtime_id(rid)
        try:
            cname = item.class_name()
        except Exception:
            cname = ""
        norm = _normalize_msg_text(text or extract_item_text(item))
        return f"{cname}|{rid_part}|{norm}"

    def is_replyable_peer_item(item, edit_area) -> bool:
        """过滤非消息项与明显不可回复的占位项。"""
        try:
            if item.class_name() == "mmui::ChatItemView":
                log_item_geometry("", item, source="replyable", stage="class", verdict="skip-chat-item-view")
                return False
        except Exception:
            pass
        text = extract_item_text(item)
        if not text:
            # 长消息在部分版本里会出现 window_text/descendants 文本为空，
            # 但控件本身是有效消息气泡；此处放行，避免“看起来没新消息”。
            try:
                cname = item.class_name()
            except Exception:
                cname = ""
            if cname not in {
                "mmui::ChatTextItemView",
                "mmui::ChatBubbleItemView",
                "mmui::ChatBubbleReferItemView",
                "mmui::ChatVoiceItemView",
            }:
                log_item_geometry("", item, source="replyable", stage="empty", verdict=f"skip-{cname or 'unknown'}")
                return False
        if CHAT_TIMESTAMP_RE.fullmatch(text):
            log_item_geometry("", item, source="replyable", stage="timestamp", verdict="skip-timestamp", text=text)
            return False
        log_item_geometry("", item, source="replyable", stage="pass", verdict="peer", text=text)
        return True

    def probe_item_has_quote_action(item, text: str = "") -> bool:
        """通过右键菜单是否存在“引用”来判定该消息是否可作为对方回复目标。"""
        cache_key = build_quote_probe_cache_key(item, text=text)
        cached = quote_menu_probe_cache.get(cache_key)
        if cached is not None:
            return bool(cached)
        try:
            rect = item.rectangle()
            click_x = rect.left + min(100, max(28, rect.width() // 3))
            click_y = rect.mid_point().y
            mouse.right_click(coords=(click_x, click_y))
            quote_item = main_window.child_window(**menu_items.QuoteMeunItem)
            ok = bool(quote_item.exists(timeout=0.15))
            quote_menu_probe_cache[cache_key] = ok
            try:
                pyautogui.press("esc", _pause=False)
            except Exception:
                pass
            return ok
        except Exception:
            quote_menu_probe_cache[cache_key] = False
            try:
                pyautogui.press("esc", _pause=False)
            except Exception:
                pass
            return False

    def is_peer_message_item(
        friend: str | None,
        item,
        edit_area,
        *,
        allow_empty_text: bool = False,
        require_quote_menu: bool = False,
    ) -> tuple[bool, str]:
        """统一判断一条消息是否应视为对方有效消息。"""
        if not is_replyable_peer_item(item, edit_area):
            return False, ""
        text = extract_item_text(item) or ""
        norm = _normalize_msg_text(text)
        if not norm and not allow_empty_text:
            return False, ""
        friend_name = str(friend or "").strip()
        if friend_name:
            if is_remembered_self_sent_item(friend_name, item, norm or text):
                return False, norm or text
            if looks_like_self_quote_text(friend_name, norm):
                return False, norm
            if looks_like_recent_self_sent(friend_name, norm):
                return False, norm
        try:
            item_class = item.class_name()
        except Exception:
            item_class = ""
        # 扫描阶段不要对所有消息做右键 probe，避免频繁 UIA 操作导致页面抖动/崩溃。
        # 仅在真正发送前（require_quote_menu=True）再用“引用”菜单做最终校验。
        if require_quote_menu and not probe_item_has_quote_action(item, text=norm or text):
            return False, norm or text
        # 语音消息在扫描阶段只作为候选入队，真正消费时再执行转写与过滤。
        if item_class == "mmui::ChatVoiceItemView":
            # 无几何前提下，使用稳定文本特征减少把“自己手机发送的语音”放入候选队列。
            # 常见对方语音文本为“语音X秒未播放”或已带转写内容；自己发送常见为“语音X秒”。
            voice_payload = extract_voice_payload(norm)
            if "未播放" in norm or voice_payload:
                return True, norm or text or "[语音消息]"
            return False, norm or text
        if norm and not should_auto_reply_text(norm):
            return False, norm
        return True, norm or text

    def remember_current_tail_as_self_sent(
        friend: str,
        expected_text: str = "",
        source: str = "发送后自发登记",
        attempts: int = 6,
        wait_s: float = 0.1,
    ) -> bool:
        """发送成功后登记当前会话末尾自发气泡，避免长时间后被误扫回 active 队列。"""
        if not friend:
            return False
        expected = _normalize_msg_text(expected_text)
        for idx in range(max(1, attempts)):
            try:
                chat_name_text = main_window.child_window(**texts.CurrentChatText)
                chat_list = main_window.child_window(**lists.FriendChatList)
                if not chat_name_text.exists(timeout=0.1) or not chat_list.exists(timeout=0.1):
                    return False
                current_friend = chat_name_text.window_text().strip()
                if current_friend != friend:
                    return False
                items = chat_list.children(control_type="ListItem")
                if not items:
                    return False
                last_item = items[-1]
                last_text = extract_item_text(last_item)
                last_norm = _normalize_msg_text(last_text)
                if expected and last_norm and not text_match_loose(expected, last_norm):
                    if idx < max(1, attempts) - 1:
                        time.sleep(max(wait_s, 0.0))
                        continue
                remember_self_sent_item(friend, last_item, last_norm or last_text or expected)
                log("自发登记", f"{friend} {source} 命中尾气泡 rid={format_runtime_id(tuple(last_item.element_info.runtime_id or ())) if last_item else 'None'}")
                return True
            except Exception:
                if idx < max(1, attempts) - 1:
                    time.sleep(max(wait_s, 0.0))
                    continue
                return False
        return False

    def snapshot_current_chat_runtime() -> tuple[str | None, tuple[int, ...] | None]:
        """返回当前聊天对象与最后一条消息 runtime_id。"""
        try:
            chat_name_text = main_window.child_window(**texts.CurrentChatText)
            chat_list = main_window.child_window(**lists.FriendChatList)
            if not chat_name_text.exists(timeout=0.1) or not chat_list.exists(timeout=0.1):
                return None, None
            friend_name = chat_name_text.window_text().strip()
            items = chat_list.children(control_type="ListItem")
            if not items:
                return friend_name, None
            rid = tuple(items[-1].element_info.runtime_id or ())
            return friend_name, rid if rid else None
        except Exception:
            return None, None

    def refresh_active_chat_baseline(friend: str | None, source: str = "发送后基线") -> None:
        """发送成功后刷新当前会话末尾签名，避免把自己刚发出的气泡变化误当成新消息。"""
        if not friend:
            return
        try:
            chat_name_text = main_window.child_window(**texts.CurrentChatText)
            chat_list = main_window.child_window(**lists.FriendChatList)
            if not chat_name_text.exists(timeout=0.1) or not chat_list.exists(timeout=0.1):
                return
            current_friend = chat_name_text.window_text().strip()
            if current_friend != friend:
                return
            items = chat_list.children(control_type="ListItem")
            if not items:
                return
            last_item = items[-1]
            rid = tuple(last_item.element_info.runtime_id or ())
            active_last_runtime[friend] = rid
            active_last_signature[friend] = build_item_signature(last_item)
            active_last_text[friend] = extract_item_text(last_item)
        except Exception as exc:
            log("会话基线异常", f"{friend} -> {exc}")

    def refresh_current_session_badge(friend: str | None) -> None:
        """轻点左侧当前会话项，尽量让会话列表未读角标与当前会话状态同步。"""
        if not friend:
            return
        try:
            chats_button = main_window.child_window(**side_bar.Chats)
            if chats_button.exists(timeout=0.1):
                chats_button.click_input()
            session_list = main_window.child_window(**main_ui.SessionList)
            if not session_list.exists(timeout=0.2):
                return
            items = session_list.children(control_type="ListItem")
            target = None
            target_auto_id = f"session_item_{friend}"
            for item in items:
                try:
                    if item.automation_id() == target_auto_id:
                        target = item
                        break
                except Exception:
                    continue
            if target is None:
                for item in items:
                    try:
                        if item.window_text().split("\n", 1)[0].strip() == friend:
                            target = item
                            break
                    except Exception:
                        continue
            if target is None:
                return
            try:
                target.click_input()
            except Exception:
                rect = target.rectangle()
                mouse.click(coords=(rect.left + min(28, max(12, rect.width() // 6)), rect.mid_point().y))
            log("会话刷新", f"{friend} 已点击左侧会话项")
        except Exception as exc:
            log("会话刷新异常", f"{friend} -> {exc}")

    def mark_current_chat_visible_as_seen(
        friend: str | None,
        source: str = "基线",
        upto_runtime_id: tuple[int, ...] | None = None,
    ) -> int:
        """把当前窗口里已可见的消息标成已见。

        当传入 upto_runtime_id 时，仅标记到该消息为止，避免把回复后新进来的消息提前吞掉。
        """
        if not friend:
            return 0
        try:
            chat_name_text = main_window.child_window(**texts.CurrentChatText)
            chat_list = main_window.child_window(**lists.FriendChatList)
            edit_area = main_window.child_window(**edits.CurrentChatEdit)
            if (
                not chat_name_text.exists(timeout=0.1)
                or not chat_list.exists(timeout=0.1)
                or not edit_area.exists(timeout=0.1)
            ):
                return 0
            current_friend = chat_name_text.window_text().strip()
            if current_friend != friend:
                return 0
            if not upto_runtime_id:
                return 0
            items = chat_list.children(control_type="ListItem")
            marked = 0
            for item in items:
                try:
                    rid = tuple(item.element_info.runtime_id or ())
                except Exception:
                    rid = ()
                if rid != upto_runtime_id:
                    continue
                ok, text = is_peer_message_item(friend, item, edit_area, require_quote_menu=False)
                if not ok:
                    return 0
                text = text or "[长消息]"
                remember_seen_peer_rid(friend, rid)
                remember_seen_runtime_text_key(friend, build_runtime_text_key(rid, text, item))
                try:
                    key = build_peer_item_key(item)
                except Exception:
                    key = ""
                if key:
                    remember_seen_peer_key(friend, key)
                marked = 1
                break
            if marked > 0:
                log("会话基线", f"{friend} {source} 标记已见{marked}条")
            return marked
        except Exception as exc:
            log("会话基线异常", f"{friend} -> {exc}")
            return 0

    def sweep_current_chat_pending(
        now_ts: float | None = None,
        expected_friend: str | None = None,
        source: str = "当前会话补扫",
        enqueue_to_active: bool = True,
        remember_seen_when_not_enqueued: bool = True,
    ) -> tuple[str | None, int]:
        """扫描当前打开会话里的新对方消息，并补入 active 队列。"""
        try:
            chat_name_text = main_window.child_window(**texts.CurrentChatText)
            if not chat_name_text.exists(timeout=0.1):
                return None, 0
            active_friend = chat_name_text.window_text().strip()
            if not active_friend:
                return None, 0
            if expected_friend and active_friend != expected_friend:
                return active_friend, 0
            if should_skip_friend(active_friend):
                return active_friend, 0
            if is_auto_reply_group_chat(active_friend):
                remember_group_chat(active_friend, source=source)
                return active_friend, 0

            chat_list = main_window.child_window(**lists.FriendChatList)
            edit_area = main_window.child_window(**edits.CurrentChatEdit)
            if not chat_list.exists(timeout=0.1):
                return active_friend, 0
            items = chat_list.children(control_type="ListItem")
            if not items:
                return active_friend, 0

            last_item = items[-1]
            runtime_id = tuple(last_item.element_info.runtime_id or ())
            signature = build_item_signature(last_item)
            last_text = extract_item_text(last_item)
            if active_friend not in active_last_runtime:
                active_last_runtime[active_friend] = runtime_id
                active_last_signature[active_friend] = signature
                active_last_text[active_friend] = last_text
                for item in items:
                    try:
                        rid0 = tuple(item.element_info.runtime_id or ())
                    except Exception:
                        rid0 = ()
                    if not rid0:
                        continue
                    ok0, text0 = is_peer_message_item(
                        active_friend,
                        item,
                        edit_area,
                        require_quote_menu=False,
                    )
                    if not ok0:
                        continue
                    text0 = text0 or "[长消息]"
                    remember_seen_peer_rid(active_friend, rid0)
                    try:
                        key0 = build_peer_item_key(item)
                    except Exception:
                        key0 = ""
                    if key0:
                        remember_seen_peer_key(active_friend, key0)
                    remember_seen_runtime_text_key(
                        active_friend,
                        build_runtime_text_key(rid0, text0, item),
                    )
                return active_friend, 0

            prev_runtime = active_last_runtime.get(active_friend)
            prev_signature = active_last_signature.get(active_friend, "")
            prev_text = active_last_text.get(active_friend, "")
            runtime_unchanged = prev_runtime == runtime_id
            signature_changed = signature != prev_signature
            text_changed = bool(last_text) and (last_text != prev_text)

            # 只关注最近几条对方消息，避免全量扫描历史记录导致抖动。
            active_probe_count = max(int(args.active_history_probe_count or 0), 1)
            recent_items = items[-active_probe_count:] if len(items) > active_probe_count else items
            new_items = []
            for item in recent_items:
                try:
                    ridi = tuple(item.element_info.runtime_id or ())
                except Exception:
                    ridi = ()
                if not ridi:
                    continue
                oki, texti = is_peer_message_item(
                    active_friend,
                    item,
                    edit_area,
                    require_quote_menu=False,
                )
                if not oki:
                    continue
                texti = texti or "[长消息]"
                runtime_text_key = build_runtime_text_key(ridi, texti, item)
                if has_seen_runtime_text_key(active_friend, runtime_text_key):
                    continue
                try:
                    keyi = build_peer_item_key(item)
                except Exception:
                    keyi = ""
                if keyi and has_seen_peer_key(active_friend, keyi):
                    remember_seen_peer_rid(active_friend, ridi)
                    remember_seen_runtime_text_key(active_friend, runtime_text_key)
                    continue
                new_items.append(item)

            if not new_items and runtime_unchanged and (signature_changed or text_changed):
                new_items = [last_item]

            peer_new_items: list[tuple[tuple[int, ...], str]] = []
            for item in new_items:
                rid = tuple(item.element_info.runtime_id or ())
                if not rid:
                    continue
                ok, text = is_peer_message_item(
                    active_friend,
                    item,
                    edit_area,
                    require_quote_menu=False,
                )
                if not ok:
                    continue
                peer_new_items.append((rid, text or "[长消息]"))
                if enqueue_to_active or remember_seen_when_not_enqueued:
                    remember_seen_peer_rid(active_friend, rid)
                    remember_seen_runtime_text_key(
                        active_friend,
                        build_runtime_text_key(rid, text, item),
                    )
                    try:
                        key = build_peer_item_key(item)
                    except Exception:
                        key = ""
                    if key:
                        remember_seen_peer_key(active_friend, key)

            active_last_runtime[active_friend] = runtime_id
            active_last_signature[active_friend] = signature
            active_last_text[active_friend] = last_text

            added = 0
            if peer_new_items and enqueue_to_active:
                added = enqueue_active_pending(active_friend, peer_new_items, source=source)
                if added > 0:
                    pending_now = len(active_pending_msgs.get(active_friend, ()))
                    prefix = f"{source}: " if source else ""
                    log("当前会话缓存", f"{prefix}{active_friend} 新增{added}条待回复（队列{pending_now}）")
            return active_friend, added
        except KeyboardInterrupt as exc:
            log("当前会话补扫中断", f"{expected_friend or '-'} -> {exc}")
            return expected_friend, 0
        except Exception as exc:
            log("当前会话补扫异常", exc)
            return None, 0

    def resolve_reply_target_in_current_chat(
        friend: str | None,
        target_runtime_id: tuple[int, ...] | None = None,
        *,
        require_quote_menu: bool = True,
    ):
        """复用引用模式的目标消息定位与校验，但不负责实际点击“引用”."""
        try:
            chat_list = main_window.child_window(**lists.FriendChatList)
            edit_area = main_window.child_window(**edits.CurrentChatEdit)
            if not chat_list.exists(timeout=0.1) or not edit_area.exists(timeout=0.1):
                return None, None, "未找到聊天列表或输入框"
            items = chat_list.children(control_type="ListItem")
            if not items:
                return None, None, "聊天列表为空"

            target = None
            if target_runtime_id:
                log("UI操作", f"回复校验: 尝试定位目标runtime_id={target_runtime_id}")
                for item in items:
                    rid = tuple(item.element_info.runtime_id or ())
                    if rid == target_runtime_id:
                        target = item
                        break
                if target is None:
                    return None, None, "未定位到目标消息runtime_id"
                ok_peer, peer_text = is_peer_message_item(
                    friend,
                    target,
                    edit_area,
                    allow_empty_text=True,
                    require_quote_menu=require_quote_menu,
                )
                if not ok_peer:
                    return None, None, f"目标消息未通过对方消息校验: {_normalize_msg_text(peer_text)[:80]}"
            if target is None:
                log("UI操作", "回复校验: 未指定目标runtime_id，回退定位最新对方消息")
                for item in reversed(items):
                    ok_peer, _peer_text = is_peer_message_item(
                        friend,
                        item,
                        edit_area,
                        allow_empty_text=True,
                        require_quote_menu=require_quote_menu,
                    )
                    if ok_peer:
                        target = item
                        break
            if target is None:
                return None, None, "未找到可回复的对方消息"

            target_text = extract_item_text(target) or ""
            if not should_attempt_quote_text(target_text):
                return None, None, "目标消息文本不适合回复"

            try:
                list_rect = chat_list.rectangle()
                rect = target.rectangle()
                visible_h = min(rect.bottom, list_rect.bottom) - max(rect.top, list_rect.top)
                visible_w = min(rect.right, list_rect.right) - max(rect.left, list_rect.left)
                if visible_h <= 8 or visible_w <= 8:
                    return None, None, "目标消息气泡不在当前窗口可视区域"
            except Exception:
                pass
            return target, edit_area, "ok"
        except Exception as exc:
            return None, None, f"{exc}"

    def quote_and_send_in_current_chat(
        friend: str | None,
        reply_text: str,
        target_runtime_id: tuple[int, ...] | None = None,
    ) -> tuple[bool, str]:
        """在当前会话里对最新消息执行“引用”后发送回复。"""
        try:
            target, edit_area, reason = resolve_reply_target_in_current_chat(
                friend,
                target_runtime_id=target_runtime_id,
                require_quote_menu=True,
            )
            if target is None or edit_area is None:
                return False, reason

            rect = target.rectangle()
            try:
                target_class = target.class_name()
            except Exception:
                target_class = ""
            if target_class == "mmui::ChatVoiceItemView":
                click_x = rect.left + min(100, max(28, rect.width() // 3))
                click_y = rect.top + max(18, min(rect.height() // 3, 32))
            else:
                click_x = rect.left + 100
                click_y = rect.mid_point().y
            log("UI操作", f"右键消息坐标=({click_x},{click_y}) class={target_class or 'unknown'}")
            mouse.right_click(coords=(click_x, click_y))
            quote_item = main_window.child_window(**menu_items.QuoteMeunItem)
            if not quote_item.exists(timeout=0.2):
                return False, "右键菜单无“引用”选项，视为非对方消息"
            log("UI操作", "点击菜单项: 引用")
            quote_item.click_input()

            log("UI操作", "点击输入框")
            edit_area.click_input()
            log("UI操作", "复制回复内容到剪贴板")
            SystemSettings.copy_text_to_clipboard(reply_text)
            log("UI操作", "按下 Ctrl+V")
            pyautogui.hotkey("ctrl", "v", _pause=False)
            time.sleep(0.05)
            ok, reason = commit_send_in_current_chat(source="引用发送")
            if not ok:
                return False, reason
            return True, "已发送引用回复"
        except Exception as exc:
            return False, f"{exc}"

    def click_send_button_in_current_chat() -> tuple[bool, str]:
        try:
            edit_area = main_window.child_window(**edits.CurrentChatEdit)
            if not edit_area.exists(timeout=0.1):
                return False, "未找到输入框"
            edit_rect = edit_area.rectangle()
            candidates = []
            for btn in main_window.descendants(control_type="Button"):
                try:
                    title = str(btn.window_text() or "").strip()
                except Exception:
                    title = ""
                if "发送" not in title:
                    continue
                try:
                    if not btn.is_visible():
                        continue
                    rect = btn.rectangle()
                except Exception:
                    continue
                if rect.width() <= 0 or rect.height() <= 0:
                    continue
                if rect.mid_point().y < edit_rect.top - 40:
                    continue
                distance = abs(rect.mid_point().y - edit_rect.mid_point().y) + abs(rect.left - edit_rect.right)
                candidates.append((distance, btn, title))
            if not candidates:
                return False, "未找到发送按钮"
            _, btn, title = min(candidates, key=lambda x: x[0])
            log("UI操作", f"点击发送按钮: {title or '发送'}")
            btn.click_input()
            return True, "点击发送按钮成功"
        except Exception as exc:
            return False, str(exc)

    def commit_send_in_current_chat(source: str) -> tuple[bool, str]:
        ok, reason = click_send_button_in_current_chat()
        if ok:
            return True, reason
        log("UI操作", f"{source}: 点击发送按钮失败，回退 Alt+S -> {reason}")
        try:
            pyautogui.hotkey("alt", "s", _pause=False)
            return True, "已回退 Alt+S 发送"
        except Exception as exc:
            return False, str(exc)

    def send_plain_in_current_chat(reply_text: str) -> tuple[bool, str]:
        """在当前聊天窗口直接普通发送，输出动作级日志。"""
        try:
            edit_area = main_window.child_window(**edits.CurrentChatEdit)
            if not edit_area.exists(timeout=0.1):
                return False, "未找到输入框"
            log("UI操作", "普通发送: 点击输入框")
            edit_area.click_input()
            log("UI操作", "普通发送: 复制回复内容到剪贴板")
            SystemSettings.copy_text_to_clipboard(reply_text)
            log("UI操作", "普通发送: 按下 Ctrl+V")
            pyautogui.hotkey("ctrl", "v", _pause=False)
            time.sleep(0.05)
            ok, reason = commit_send_in_current_chat(source="普通发送")
            if not ok:
                return False, reason
            return True, "已普通发送"
        except Exception as exc:
            return False, str(exc)

    def send_reply(
        friend: str,
        reply_text: str,
        target_runtime_id: tuple[int, ...] | None = None,
        prefer_quote: bool = True,
    ) -> bool:
        target, _edit_area, reason = resolve_reply_target_in_current_chat(
            friend,
            target_runtime_id=target_runtime_id,
            require_quote_menu=True,
        )
        if target is None:
            log("回复校验失败", f"{friend} -> {reason}，本条视为非对方消息并跳过")
            return False
        del prefer_quote
        ok, reason = send_plain_in_current_chat(reply_text)
        if ok:
            remember_recent_sent_text(friend, reply_text)
            if not remember_current_tail_as_self_sent(friend, expected_text=reply_text, source="普通发送后"):
                log("自发登记", f"{friend} 普通发送后未能登记尾气泡，继续使用文本缓存兜底")
            refresh_active_chat_baseline(friend, source="普通发送后")
            log("发送方式", f"{friend} -> 普通发送(已过引用前校验)")
            return True
        log("普通发送失败", f"{friend} -> {reason}，降级 Messages.send_messages_to_friend")
        Messages.send_messages_to_friend(
            friend=friend,
            messages=[reply_text],
            search_pages=args.search_pages,
            is_maximize=False,
            close_weixin=False,
        )
        remember_recent_sent_text(friend, reply_text)
        if not remember_current_tail_as_self_sent(friend, expected_text=reply_text, source="普通发送降级后"):
            log("自发登记", f"{friend} 普通发送降级后未能登记尾气泡，继续使用文本缓存兜底")
        refresh_active_chat_baseline(friend, source="普通发送降级后")
        log("发送方式", f"{friend} -> 普通发送(降级)")
        return True

    def validate_reply_target_before_action(
        friend: str,
        target_runtime_id: tuple[int, ...] | None,
        *,
        action: str = "回复",
    ) -> bool:
        """调用接口或执行转发前，先确认目标仍是当前会话里的对方消息。"""
        target, _edit_area, reason = resolve_reply_target_in_current_chat(
            friend,
            target_runtime_id=target_runtime_id,
            require_quote_menu=False,
        )
        if target is None and "未找到聊天列表或输入框" in str(reason or ""):
            ok, open_reason = _open_chat_strict(friend)
            if ok:
                target, _edit_area, reason = resolve_reply_target_in_current_chat(
                    friend,
                    target_runtime_id=target_runtime_id,
                    require_quote_menu=False,
                )
            else:
                reason = f"{reason}; 重新打开会话失败: {open_reason}"
        if target is None:
            log("回复校验失败", f"{friend} -> {reason}，{action}前跳过")
            return False
        return True

    def _forward_current_page_to_friend(target_friend: str) -> tuple[bool, str]:
        """在已打开的小程序页面内，点击右上角菜单并执行“转发给朋友”."""
        try:
            desktop = Desktop(backend="uia")
            panes = [
                pane
                for pane in desktop.windows(control_type="Pane", class_name="Chrome_WidgetWin_0")
                if pane.is_visible()
            ]
            if not panes:
                return False, "未找到小程序页面窗口"
            page_pane = max(
                panes,
                key=lambda pane: pane.rectangle().width() * pane.rectangle().height(),
            )

            def is_miniprogram_page_open() -> bool:
                try:
                    visible = [w for w in desktop.windows(class_name="Chrome_WidgetWin_0") if w.is_visible()]
                    return bool(visible)
                except Exception:
                    return True

            menu_anchor = None
            pane_rect = page_pane.rectangle()
            # 优先 capsule；若不存在再回退右上角 Image 图标候选
            capsule_nodes = []
            for node in page_pane.descendants():
                try:
                    if not node.is_visible():
                        continue
                    if node.automation_id() != "capsule":
                        continue
                    rect = node.rectangle()
                    if rect.top > pane_rect.top + 160:
                        continue
                    if rect.left < pane_rect.right - 300:
                        continue
                    capsule_nodes.append(node)
                except Exception:
                    continue
            if capsule_nodes:
                menu_anchor = min(capsule_nodes, key=lambda n: n.rectangle().left)

            log("UI操作", "短链转卡片: 点击页面右上角菜单")
            def _find_forward_entry() -> object | None:
                scopes = [page_pane]
                try:
                    scopes.extend(
                        [w for w in desktop.windows(class_name="Chrome_WidgetWin_0") if w.is_visible()]
                    )
                except Exception:
                    pass
                fallback = None
                for scope in scopes:
                    for ctype in ("Text", "Button", "MenuItem"):
                        try:
                            candidates = scope.descendants(control_type=ctype)
                        except Exception:
                            candidates = []
                        for node in candidates:
                            try:
                                if not node.is_visible():
                                    continue
                                title = node.window_text().strip()
                                if title == "转发给朋友":
                                    return node
                                if fallback is None and ("转发" in title):
                                    fallback = node
                            except Exception:
                                continue
                return fallback

            def _click_capsule_menu() -> None:
                probe_points: list[tuple[int, int]] = []
                if menu_anchor is not None:
                    mrect = menu_anchor.rectangle()
                    # 优先找胶囊内部最左可见子控件（通常是...）
                    try:
                        children = [
                            n for n in menu_anchor.descendants()
                            if n.is_visible() and n.rectangle().width() > 4 and n.rectangle().height() > 4
                        ]
                    except Exception:
                        children = []
                    children = sorted(children, key=lambda n: n.rectangle().mid_point().x)
                    if children:
                        c = children[0].rectangle()
                        probe_points.append((c.mid_point().x, c.mid_point().y))
                    probe_points.extend([
                        (mrect.left + 12, mrect.mid_point().y),
                        (mrect.left + max(16, int(mrect.width() * 0.2)), mrect.mid_point().y),
                        (mrect.left + max(22, int(mrect.width() * 0.3)), mrect.mid_point().y),
                    ])
                else:
                    # 无 capsule 时：按你提供的控件特征，在右上角找 Image 图标
                    image_nodes = []
                    for img in page_pane.descendants(control_type="Image"):
                        try:
                            if not img.is_visible():
                                continue
                            r = img.rectangle()
                            if r.width() < 24 or r.width() > 64 or r.height() < 24 or r.height() > 64:
                                continue
                            if r.top > pane_rect.top + 200:
                                continue
                            if r.left < pane_rect.right - 320:
                                continue
                            image_nodes.append(img)
                        except Exception:
                            continue
                    # 按“从右往左第3个”优先，再尝试邻近项
                    image_nodes = sorted(image_nodes, key=lambda n: n.rectangle().left)
                    if image_nodes:
                        n = len(image_nodes)
                        order: list[int] = []
                        # 第3个(从右) => 索引 n-3
                        if n - 3 >= 0:
                            order.append(n - 3)
                        # 再试第2个、第4个、第1个(从右)
                        if n - 2 >= 0:
                            order.append(n - 2)
                        if n - 4 >= 0:
                            order.append(n - 4)
                        if n - 1 >= 0:
                            order.append(n - 1)
                        # 兜底补齐剩余索引
                        for i in range(n - 1, -1, -1):
                            order.append(i)
                        seen_idx: set[int] = set()
                        for idx in order:
                            if idx in seen_idx:
                                continue
                            seen_idx.add(idx)
                            r = image_nodes[idx].rectangle()
                            probe_points.append((r.mid_point().x, r.mid_point().y))
                    if not probe_points:
                        raise RuntimeError("未找到页面右上角菜单图标")
                seen: set[tuple[int, int]] = set()
                for mx, my in probe_points:
                    if (mx, my) in seen:
                        continue
                    seen.add((mx, my))
                    log("UI操作", f"短链转卡片: 菜单点击坐标=({mx},{my})")
                    mouse.click(coords=(mx, my))
                    time.sleep(0.35)
                    if _find_forward_entry() is not None:
                        return
                    if not is_miniprogram_page_open():
                        raise RuntimeError("点击后小程序窗口消失，疑似误触缩小/关闭")
                raise RuntimeError("点击菜单图标后仍未弹出菜单")

            try:
                _click_capsule_menu()
            except Exception as exc:
                return False, f"点击胶囊菜单失败: {exc}"
            if not is_miniprogram_page_open():
                return False, "点击菜单后小程序窗口消失，疑似误触关闭"
            time.sleep(0.2)

            forward_text = None
            deadline = time.time() + 2.2
            while time.time() < deadline and forward_text is None:
                forward_text = _find_forward_entry()
                if forward_text is None:
                    time.sleep(0.1)
            if forward_text is None:
                return False, "未找到“转发给朋友”入口"

            log("UI操作", "短链转卡片: 点击 转发给朋友")
            try:
                forward_text.click_input()
            except Exception:
                frect = forward_text.rectangle()
                mouse.click(coords=(frect.mid_point().x, frect.mid_point().y))
            time.sleep(0.3)

            log("UI操作", f"转发选择: 搜索并勾选好友 {target_friend}")
            ok, reason = Tools.pick_and_send_in_session_picker(
                target_friend=target_friend,
                timeout=8.0,
                logger=lambda m: log("UI操作", f"转发选择: {m}"),
            )
            if not ok:
                return False, reason
            log("UI操作", "转发选择: 点击发送按钮")
            close_ok, close_reason = Tools.close_active_miniprogram_window(timeout=1.8)
            log("UI操作", f"短链转卡片: 发送后关闭小程序 -> {close_reason}")
            if not close_ok:
                log("短链转卡片", f"发送后关闭小程序失败: {close_reason}")
            return True, "页面内转发成功"
        except Exception as exc:
            return False, str(exc)

    def _open_chat_strict(friend: str) -> tuple[bool, str]:
        """严格打开指定会话，禁止落到网络/搜一搜结果。"""
        try:
            chats_button = main_window.child_window(**side_bar.Chats)
            if chats_button.exists(timeout=0.2):
                chats_button.click_input()
            current_chat_label = dict(texts.CurrentChatText)
            current_chat_label["title"] = friend
            current_chat = main_window.child_window(**current_chat_label)
            if current_chat.exists(timeout=0.3):
                return True, "already-in-chat"

            search_edit = main_window.child_window(**edits.SearchEdit)
            if not search_edit.exists(timeout=0.3):
                search_controls = main_window.descendants(**edits.SearchEdit)
                if not search_controls:
                    return False, "未找到顶部搜索框"
                search_edit = search_controls[0]
            search_edit.click_input()
            try:
                search_edit.type_keys("^a{BACKSPACE}", set_foreground=True)
            except Exception:
                pass
            search_edit.set_text(friend)
            time.sleep(0.6)

            search_results = main_window.child_window(**lists.SearchResult)
            if not search_results.exists(timeout=0.5):
                return False, "未出现搜索结果列表"
            items = search_results.children(control_type="ListItem")
            if not items:
                return False, "搜索结果为空"

            target_item = None
            target_auto_id = f"search_item_{friend}"
            # 0) 优先按 AutomationId 精确命中标准搜索结果项，避免落到“功能/网络搜索”等分组。
            for item in items:
                try:
                    if item.automation_id() == target_auto_id:
                        if item.class_name() == "mmui::SearchContentCellView":
                            target_item = item
                            break
                except Exception:
                    continue
            # 1) 其次命中标准联系人/群聊搜索结果项。
            for item in items:
                if target_item is not None:
                    break
                try:
                    if item.class_name() != "mmui::SearchContentCellView":
                        continue
                    if item.window_text() == friend:
                        target_item = item
                        break
                except Exception:
                    continue
            # 2) 最后才对少数功能型会话开放 XTableCell 兜底，例如“文件传输助手”。
            for idx, item in enumerate(items):
                if target_item is not None:
                    break
                try:
                    if friend not in {"文件传输助手"}:
                        continue
                    if item.class_name() != "mmui::XTableCell":
                        continue
                    if item.window_text() != friend:
                        continue
                    if idx > 0 and items[idx - 1].window_text() == "功能":
                        target_item = item
                        break
                except Exception:
                    continue
            if target_item is None:
                return False, f"搜索结果未命中精确会话[{friend}]"

            target_item.click_input()
            if current_chat.exists(timeout=0.8):
                return True, "opened"
            return False, f"点击后未进入会话[{friend}]"
        except Exception as exc:
            return False, str(exc)

    def _open_chat_history_window(source_chat: str):
        """打开 source_chat 的聊天记录窗口（复用 pyweixin 内置流程）。"""
        try:
            history_window = Navigator.open_chat_history(
                friend=source_chat,
                TabItem=None,
                search_pages=args.search_pages,
                is_maximize=False,
                close_weixin=False,
            )
            return history_window, "ok"
        except Exception as exc:
            return None, str(exc)

    def _open_miniprogram_history_window(source_chat: str):
        """打开 source_chat 的聊天记录窗口并切换到“小程序”页签。"""
        history_window, reason = _open_chat_history_window(source_chat)
        if history_window is None:
            return None, reason

        try:
            tab_button = history_window.child_window(control_type="Button", class_name="mmui::XMouseEventView")
            if tab_button.exists(timeout=0.2):
                tab_button.click_input()
        except Exception:
            pass
        mini_tab = history_window.child_window(title="小程序", control_type="TabItem")
        if not mini_tab.exists(timeout=0.3):
            mini_tab = history_window.child_window(title="小程序", control_type="TabItem", class_name="mmui::XButton")
        if mini_tab.exists(timeout=0.3):
            mini_tab.click_input()
        return history_window, "ok"

    def _open_miniprogram_from_history_first_item(source_chat: str, short_link: str | None = None) -> tuple[bool, str]:
        """按固定流程：打开聊天记录窗口，优先点击命中短链的记录，等待小程序页打开。"""
        def wait_miniprogram_open(timeout_s: float = 10.0) -> bool:
            deadline = time.time() + max(timeout_s, 0.8)
            while time.time() < deadline:
                try:
                    desktop = Desktop(backend="uia")
                    chrome_windows = [w for w in desktop.windows(class_name="Chrome_WidgetWin_0") if w.is_visible()]
                    if chrome_windows:
                        pane = max(chrome_windows, key=lambda w: w.rectangle().width() * w.rectangle().height())
                        rect = pane.rectangle()
                        if rect.width() >= 320 and rect.height() >= 480:
                            return True
                except Exception:
                    pass
                time.sleep(0.1)
            return False

        history_window = None
        def close_history_window() -> None:
            nonlocal history_window
            if history_window is None:
                return
            try:
                if hasattr(history_window, "exists") and (not history_window.exists(timeout=0.1)):
                    return
            except Exception:
                pass
            try:
                history_window.close()
                time.sleep(0.2)
            except Exception:
                pass
            try:
                if hasattr(history_window, "exists") and (not history_window.exists(timeout=0.1)):
                    return
            except Exception:
                pass
            try:
                history_window.set_focus()
                history_window.type_keys("%{F4}", set_foreground=True)
                time.sleep(0.2)
            except Exception:
                pass
        try:
            history_window, reason = _open_chat_history_window(source_chat)
            if history_window is None:
                return False, reason

            chat_history_list = history_window.child_window(**lists.ChatHistoryList)
            if not chat_history_list.exists(timeout=0.6):
                chat_history_list = history_window.child_window(title="聊天记录", control_type="List")
            if not chat_history_list.exists(timeout=0.6):
                return False, "聊天记录窗口未找到 ChatHistoryList"
            try:
                Tools.activate_chatHistoryList(chat_history_list)
            except Exception:
                pass

            items = chat_history_list.children(control_type="ListItem")
            if not items:
                return False, "聊天记录列表为空"

            target_item = None
            normalized_short = str(short_link or "").strip()
            if normalized_short:
                for it in items:
                    try:
                        txt = (it.window_text() or "").strip()
                    except Exception:
                        txt = ""
                    if normalized_short in txt:
                        target_item = it
                        log("UI操作", f"聊天记录命中短链项: {txt[:80]}")
                        break
            if target_item is None:
                target_item = items[0]
                log("UI操作", "聊天记录未命中短链项，回退第一条")

            first_item = target_item
            try:
                rect = first_item.rectangle()
                log("UI操作", f"聊天记录首条区域=({rect.left},{rect.top},{rect.right},{rect.bottom})")
            except Exception:
                pass
            log("UI操作", "短链转卡片: 点击聊天记录目标项")
            # 固定策略：直接双击目标记录项（由 UIA 定位，不再手算屏幕坐标）
            try:
                history_window.set_focus()
            except Exception:
                pass
            try:
                first_item.set_focus()
            except Exception:
                pass
            click_coords = None
            try:
                rect = first_item.rectangle()
                w = max(rect.width(), 1)
                h = max(rect.height(), 1)
                # 单行短链时，默认中点可能落在空白区域，向下偏移一点更容易命中文本
                click_coords = (int(w * 0.5), min(h - 4, int(h * 0.5) + 8))
                log("UI操作", f"聊天记录目标项相对点击坐标={click_coords}")
            except Exception:
                click_coords = None
            try:
                if click_coords is not None:
                    first_item.double_click_input(coords=click_coords)
                else:
                    first_item.double_click_input()
                if wait_miniprogram_open(10.0):
                    close_history_window()
                    return True, "已通过双击目标记录项打开小程序页"
            except Exception:
                pass

            # 回退 1：单击目标项
            try:
                if click_coords is not None:
                    first_item.click_input(coords=click_coords)
                else:
                    first_item.click_input()
                if wait_miniprogram_open(10.0):
                    close_history_window()
                    return True, "已通过单击目标记录项打开小程序页"
            except Exception:
                pass

            # 回退 2：Invoke + Enter（触发默认动作）
            try:
                first_item.invoke()
                log("UI操作", "聊天记录目标项: 调用 invoke()")
                if wait_miniprogram_open(10.0):
                    close_history_window()
                    return True, "已通过 invoke 打开小程序页"
            except Exception:
                pass
            try:
                first_item.type_keys("{ENTER}", set_foreground=True)
                if wait_miniprogram_open(10.0):
                    close_history_window()
                    return True, "已通过回车打开小程序页"
            except Exception:
                pass
            return False, "点击聊天记录第一条后未打开小程序页面"
        except Exception as exc:
            return False, str(exc)
        finally:
            close_history_window()

    def _click_latest_shortlink_in_current_chat(
        short_link: str,
        after_runtime_id: tuple[int, ...] | None = None,
        click_first_item: bool = False,
    ) -> tuple[bool, str]:
        """在当前会话中点击最新一条匹配短链的消息，并确认小程序页面已打开。"""
        def is_miniprogram_page_open() -> bool:
            desktop = Desktop(backend="uia")
            chrome_windows = [w for w in desktop.windows(class_name="Chrome_WidgetWin_0") if w.is_visible()]
            if not chrome_windows:
                return False
            try:
                pane = max(chrome_windows, key=lambda w: w.rectangle().width() * w.rectangle().height())
                rect = pane.rectangle()
                return rect.width() >= 320 and rect.height() >= 480
            except Exception:
                return True

        def wait_miniprogram_open(timeout_s: float = 2.0) -> bool:
            deadline = time.time() + max(timeout_s, 0.5)
            while time.time() < deadline:
                if is_miniprogram_page_open():
                    return True
                time.sleep(0.1)
            return False

        try:
            if click_first_item:
                chat_list = main_window.child_window(**lists.ChatHistoryList)
                if chat_list.exists(timeout=0.3):
                    log("UI操作", "短链点击: 使用 ChatHistoryList")
                else:
                    chat_list = main_window.child_window(**lists.FriendChatList)
                    log("UI操作", "短链点击: ChatHistoryList 未命中，回退 FriendChatList")
            else:
                chat_list = main_window.child_window(**lists.FriendChatList)
            edit_area = main_window.child_window(**edits.CurrentChatEdit)
            if not chat_list.exists(timeout=0.5):
                return False, "未找到聊天消息列表"
            if not edit_area.exists(timeout=0.3):
                return False, "未找到输入框"
            items = chat_list.children(control_type="ListItem")
            if not items:
                return False, "聊天列表为空"

            candidates = []
            normalized_short = str(short_link).strip()

            def _is_shortlink_text(text: str) -> bool:
                if not text:
                    return False
                return bool(
                    (normalized_short and normalized_short in text)
                    or ("#小程序://" in text)
                )

            def _is_timestamp_text(text: str) -> bool:
                return bool(text and CHAT_TIMESTAMP_RE.match(text))

            if click_first_item:
                visible_items = []
                try:
                    list_rect = chat_list.rectangle()
                except Exception:
                    list_rect = None
                for it in items:
                    try:
                        rect = it.rectangle()
                        if rect.width() <= 10 or rect.height() <= 10:
                            continue
                        if list_rect is not None:
                            if rect.bottom <= list_rect.top or rect.top >= list_rect.bottom:
                                continue
                        # 过滤明显离屏/虚拟项
                        if rect.bottom <= 0:
                            continue
                        visible_items.append(it)
                    except Exception:
                        continue
                if visible_items:
                    visible_items.sort(key=lambda it: it.rectangle().top)
                    candidates = [visible_items[0]]
                    log("UI操作", "短链点击: 按配置点击聊天消息列表可见第一条")
                else:
                    candidates = [items[-1]]
                    log("UI操作", "短链点击: 未找到可见项，回退点击列表末条")
            else:
            # 0) 优先定位“发送后新增”的那条消息（最稳，不依赖左右气泡判断）
                if after_runtime_id:
                    try:
                        start_idx = -1
                        for i, it in enumerate(items):
                            rid = tuple(it.element_info.runtime_id or ())
                            if rid == after_runtime_id:
                                start_idx = i
                                break
                        if start_idx >= 0 and start_idx + 1 < len(items):
                            # 发送短链后常出现“时间戳 + 消息气泡”两条，保留最近几条新增项供尝试
                            for it in reversed(items[start_idx + 1 :]):
                                candidates.append(it)
                        elif start_idx == -1:
                            # 记录尾项不在当前列表时，兜底尝试末尾几条
                            for it in reversed(items[-4:]):
                                candidates.append(it)
                    except Exception:
                        pass

                # 1) 文本命中短链
                for item in reversed(items):
                    try:
                        text = item.window_text().strip()
                    except Exception:
                        continue
                    if _is_shortlink_text(text):
                        candidates.append(item)
                if not candidates:
                    # 2) 文本尚不可见时，兜底尝试最后几条消息
                    candidates = list(reversed(items[-4:]))
                if not candidates:
                    return False, "未找到可点击短链消息"
                # 去重后保留多个最新候选，避免仅命中时间戳条目
                dedup_candidates: list = []
                seen_rids: set[tuple[int, ...]] = set()
                for it in candidates:
                    rid = tuple(it.element_info.runtime_id or ())
                    if rid and rid in seen_rids:
                        continue
                    if rid:
                        seen_rids.add(rid)
                    dedup_candidates.append(it)
                non_timestamp_candidates = []
                for it in dedup_candidates:
                    try:
                        txt = it.window_text().strip()
                    except Exception:
                        txt = ""
                    if _is_timestamp_text(txt):
                        continue
                    non_timestamp_candidates.append(it)
                candidates = non_timestamp_candidates or dedup_candidates
                candidates = candidates[:3]
            log("UI操作", f"短链点击: 候选消息 {len(candidates)} 条")

            for target_item in candidates:
                try:
                    try:
                        t = target_item.window_text().strip().replace("\n", " ")
                    except Exception:
                        t = ""
                    log("UI操作", f"短链点击: 尝试消息 [{t[:60]}]")
                    # 优先点击消息中的文本控件（尽量点到“字”上，而不是气泡空白）
                    text_nodes = []
                    try:
                        text_nodes.extend(target_item.descendants(control_type="Hyperlink"))
                    except Exception:
                        pass
                    try:
                        text_nodes.extend(target_item.descendants(control_type="Text"))
                    except Exception:
                        pass
                    clicked = False
                    for node in text_nodes:
                        try:
                            node_text = node.window_text().strip()
                            if _is_shortlink_text(node_text):
                                nrect = node.rectangle()
                                mouse.click(coords=(nrect.mid_point().x, nrect.mid_point().y))
                                clicked = True
                                if wait_miniprogram_open(1.0):
                                    return True, "已点击短链文本并打开小程序页"
                                mouse.double_click(coords=(nrect.mid_point().x, nrect.mid_point().y))
                                if wait_miniprogram_open(1.0):
                                    return True, "已双击短链文本并打开小程序页"
                                break
                        except Exception:
                            continue

                    # 文本节点不可用时，退回消息上方文本区域点击
                    if not clicked:
                        rect = target_item.rectangle()
                        log("UI操作", f"短链点击: 消息区域=({rect.left},{rect.top},{rect.right},{rect.bottom})")
                        y = rect.top + min(26, max(12, rect.height() // 2))
                        width = max(rect.width(), 1)
                        grid_y = [max(rect.top + 6, rect.top + int(rect.height() * 0.35)), y]
                        text_points = [
                            (rect.left + int(width * 0.35), grid_y[0]),
                            (rect.left + int(width * 0.55), grid_y[0]),
                            (rect.left + int(width * 0.75), grid_y[0]),
                            (rect.left + int(width * 0.55), grid_y[1]),
                            (rect.left + int(width * 0.75), grid_y[1]),
                        ]
                        for x, y in text_points:
                            mouse.click(coords=(x, y))
                            if wait_miniprogram_open(0.9):
                                return True, "已点击短链文本区域并打开小程序页"
                        for x, y in text_points[:2]:
                            mouse.double_click(coords=(x, y))
                            if wait_miniprogram_open(0.9):
                                return True, "已双击短链文本区域并打开小程序页"

                    # 最后兜底容器点击一次，避免完全无响应
                    target_item.click_input()
                    if wait_miniprogram_open(0.8):
                        return True, "已点击短链消息并打开小程序页"
                except Exception:
                    continue
            return False, "点击短链后未打开小程序页面"
        except Exception as exc:
            return False, str(exc)

    def prepare_and_forward_miniprogram_from_shortlink(
        target_friend: str,
        short_link: str,
        source_chat: str,
    ) -> tuple[bool, str]:
        """短链转卡片固定流程: 打开素材会话 -> 发短链 -> 点短链 -> 转发给目标会话。"""
        def wait_shortlink_record_ready(max_wait_s: float = 10.0) -> float:
            """等待短链消息在当前素材会话消息列表可见，返回实际等待秒数。"""
            start = time.time()
            deadline = start + max(0.5, max_wait_s)
            normalized_short = str(short_link).strip()
            while time.time() < deadline:
                try:
                    chat_list = main_window.child_window(**lists.FriendChatList)
                    if chat_list.exists(timeout=0.1):
                        items = chat_list.children(control_type="ListItem")
                        for item in reversed(items[-8:] if len(items) > 8 else items):
                            try:
                                text = (item.window_text() or "").strip()
                            except Exception:
                                text = ""
                            if normalized_short and normalized_short in text:
                                return time.time() - start
                            if "#小程序://" in text:
                                return time.time() - start
                except Exception:
                    pass
                time.sleep(0.2)
            return time.time() - start

        try:
            log("UI操作", f"短链转卡片: 打开素材会话 {source_chat}")
            ok, reason = _open_chat_strict(source_chat)
            if not ok:
                return False, f"打开素材会话失败: {reason}"

            log("UI操作", f"短链转卡片: 在素材会话发送短链 {short_link}")
            ok, reason = send_plain_in_current_chat(short_link)
            if not ok:
                return False, f"发送短链失败: {reason}"

            max_wait_s = max(float(args.mini_shortlink_prepare_delay or 0.0), 1.0)
            log("UI操作", f"短链转卡片: 等待小程序记录生成（最长{max_wait_s:.1f}s，提前就绪即继续）")
            waited = wait_shortlink_record_ready(max_wait_s)
            log("UI操作", f"短链转卡片: 记录等待完成 {waited:.1f}s")

            log("UI操作", f"短链转卡片: 打开{source_chat}聊天记录并点击第一条")
            ok, reason = _open_miniprogram_from_history_first_item(
                source_chat=source_chat,
                short_link=short_link,
            )
            if not ok:
                return False, f"打开短链失败: {reason}"

            ok, reason = _forward_current_page_to_friend(target_friend=target_friend)
            if (not ok) and ("未找到页面右上角菜单按钮" in str(reason)):
                # 首次未进入小程序页时，再次按“聊天记录第一条”重试
                log("短链转卡片", "首次未进入小程序页，重试点击聊天记录第一条")
                retry_ok, retry_reason = _open_miniprogram_from_history_first_item(
                    source_chat=source_chat,
                    short_link=short_link,
                )
                if retry_ok:
                    ok, reason = _forward_current_page_to_friend(target_friend=target_friend)
                else:
                    reason = f"{reason}；重试点击失败: {retry_reason}"
            if ok:
                return True, reason

            log("短链转卡片", f"页面内转发失败，回退聊天记录转发: {reason}")
            ok, reason = forward_latest_miniprogram(source_chat=source_chat, target_friend=target_friend)
            if ok:
                # 回退转发成功后，同样尝试关闭可能仍打开的小程序窗口
                try:
                    close_ok, close_reason = Tools.close_active_miniprogram_window(timeout=1.8)
                    log("UI操作", f"短链转卡片: 回退转发后关闭小程序 -> {close_reason}")
                    if not close_ok:
                        log("短链转卡片", f"回退转发后关闭小程序失败: {close_reason}")
                except Exception as exc:
                    log("短链转卡片", f"回退转发后关闭小程序异常: {exc}")
                return True, f"素材会话[{source_chat}] -> {target_friend}"
            return False, reason
        except Exception as exc:
            return False, str(exc)

    def forward_latest_miniprogram(source_chat: str, target_friend: str) -> tuple[bool, str]:
        """Forward latest mini program from source chat history to target friend."""
        chat_history_window = None
        try:
            log("UI操作", f"转发小程序: 打开聊天记录 source_chat={source_chat}")
            chat_history_window, reason = _open_miniprogram_history_window(source_chat)
            if chat_history_window is None:
                return False, reason
            mini_list = chat_history_window.child_window(**lists.MiniProgramList)
            if not mini_list.exists(timeout=0.3):
                return False, f"素材会话[{source_chat}]无小程序记录"
            items = mini_list.children(control_type="ListItem")
            if not items:
                return False, f"素材会话[{source_chat}]小程序列表为空"
            target_item = items[0]
            rec = target_item.rectangle()
            log("UI操作", f"转发小程序: 右键小程序坐标=({rec.right - 10},{rec.mid_point().y})")
            mouse.right_click(coords=(rec.right - 10, rec.mid_point().y))
            forward_item = chat_history_window.child_window(**menu_items.ForwardMenuItem)
            if not forward_item.exists(timeout=0.2):
                forward_item = main_window.child_window(**menu_items.ForwardMenuItem)
            if not forward_item.exists(timeout=0.2):
                return False, "未找到转发菜单项"
            log("UI操作", "转发小程序: 点击菜单项 转发")
            forward_item.click_input()

            log("UI操作", f"转发选择: 搜索并勾选好友 {target_friend}")
            ok, reason = Tools.pick_and_send_in_session_picker(
                target_friend=target_friend,
                timeout=8.0,
                logger=lambda m: log("UI操作", f"转发选择: {m}"),
            )
            if not ok:
                return False, reason
            log("UI操作", "转发选择: 点击发送按钮")
            return True, "转发成功"
        except Exception as exc:
            return False, str(exc)
        finally:
            try:
                if chat_history_window is not None:
                    chat_history_window.close()
            except Exception:
                pass

    def handle_active_chat(now_ts: float) -> str | None:
        """监听当前打开会话的新消息（即使不在未读列表中）。"""
        try:
            active_friend = get_current_chat_name()
            if not active_friend:
                return None
            active_policy = get_session_policy(active_friend)
            if active_policy == "ignore":
                return None
            if active_policy == "read_only":
                if not is_read_only_suppressed(active_friend, now_ts=now_ts):
                    consume_unread_without_reply(active_friend, 0, source="当前会话只读消费")
                    return active_friend
                return None
            active_friend, _ = sweep_current_chat_pending(now_ts=now_ts, source="当前会话监听")
            if not active_friend:
                return None

            pending_q = active_pending_msgs.get(active_friend)
            if not pending_q:
                return None

            if now_ts < active_next_reply_at.get(active_friend, 0.0):
                wait_left = max(0.0, active_next_reply_at.get(active_friend, 0.0) - now_ts)
                log("当前会话等待", f"{active_friend} 待回复{len(pending_q)}条，发送间隔剩余{wait_left:.1f}s")
                return None

            sent_count = 0
            skipped_count = 0
            last_sent_runtime_id: tuple[int, ...] | None = None
            while pending_q and sent_count < max(1, args.max_burst_active):
                current = pop_active_pending(active_friend)
                if current is None:
                    break
                rid, msg_text = current
                try:
                    msg_text = resolve_message_for_consumption(active_friend, rid, msg_text)
                    if not msg_text:
                        skipped_count += 1
                        continue
                    if looks_like_self_quote_text(active_friend, msg_text):
                        log("自发过滤", f"{active_friend} -> 命中引用文本特征")
                        skipped_count += 1
                        continue
                    if looks_like_recent_self_sent(active_friend, msg_text):
                        log("自发过滤", f"{active_friend} -> 命中最近发送文本")
                        skipped_count += 1
                        continue
                    if not should_auto_reply_text(msg_text):
                        log("消息类型跳过", f"{active_friend} -> {msg_text[:60]}")
                        skipped_count += 1
                        continue
                    if is_replied_before(
                        active_friend,
                        msg_text,
                        rid,
                        use_persistent_recent=False,
                    ):
                        log("防重跳过", f"{active_friend} -> {msg_text[:60]}")
                        skipped_count += 1
                        continue
                    if not validate_reply_target_before_action(active_friend, rid, action="当前会话处理"):
                        skipped_count += 1
                        continue

                    shortlink_match = choose_shortlink_rule(msg_text, shortlink_rules)
                    if shortlink_match:
                        keyword, short_link = shortlink_match
                        if not should_trigger_shortlink(active_friend, keyword):
                            log("短链抑制", f"{active_friend} -> {keyword} 冷却中，跳过重复触发")
                            mark_replied(active_friend, msg_text, rid)
                            skipped_count += 1
                            continue
                        log("短链命中", msg_text.replace("\n", " ")[:80])
                        ok, reason = prepare_and_forward_miniprogram_from_shortlink(
                            target_friend=active_friend,
                            short_link=short_link,
                            source_chat=args.mini_shortlink_source_chat,
                        )
                        if ok:
                            log("短链转卡片", f"{active_friend} <- {reason}")
                        else:
                            log("短链转卡片失败", f"{active_friend} <- {reason}，降级发送短链文本")
                            sent_ok = send_reply(active_friend, short_link, target_runtime_id=rid, prefer_quote=True)
                            if not sent_ok:
                                skipped_count += 1
                                continue
                            log("短链发送", f"{active_friend} <- {short_link}")
                        remember_shortlink_trigger(active_friend, keyword)
                        mark_replied(active_friend, msg_text, rid)
                        sent_count += 1
                        if pending_q and sent_count < max(1, args.max_burst_active):
                            time.sleep(max(args.burst_gap, 0.0))
                        continue

                    source_chat = choose_miniprogram_source(msg_text, mini_forward_rules)
                    if source_chat:
                        ok, reason = forward_latest_miniprogram(source_chat=source_chat, target_friend=active_friend)
                        if ok:
                            log("小程序转发", f"{active_friend} <- {source_chat}")
                            mark_replied(active_friend, msg_text, rid)
                            sent_count += 1
                            if pending_q and sent_count < max(1, args.max_burst_active):
                                time.sleep(max(args.burst_gap, 0.0))
                            continue
                        log("小程序转发失败", f"{active_friend} <- {source_chat}: {reason}")

                    sweep_current_chat_pending(
                        expected_friend=active_friend,
                        source="百炼前补扫",
                        enqueue_to_active=False,
                        remember_seen_when_not_enqueued=False,
                    )
                    reply_text = make_reply(active_friend, msg_text)
                    sweep_current_chat_pending(
                        expected_friend=active_friend,
                        source="百炼后补扫",
                        enqueue_to_active=False,
                        remember_seen_when_not_enqueued=False,
                    )
                    sent_ok = send_reply(active_friend, reply_text, target_runtime_id=rid, prefer_quote=True)
                    if not sent_ok:
                        skipped_count += 1
                        continue
                    time.sleep(0.5)
                    sweep_current_chat_pending(
                        expected_friend=active_friend,
                        source="发送后待回复复检",
                        enqueue_to_active=True,
                    )
                    mark_replied(active_friend, msg_text, rid)
                    last_sent_runtime_id = rid
                    sent_count += 1
                    log("当前会话命中", msg_text.replace("\n", " ")[:80])
                    if pending_q and sent_count < max(1, args.max_burst_active):
                        time.sleep(max(args.burst_gap, 0.0))
                except Exception as item_exc:
                    skipped_count += 1
                    log("当前会话处理异常", f"{active_friend} rid={rid} -> {item_exc}")

            if sent_count > 0:
                empty_confirm_rounds = 0
                while len(active_pending_msgs.get(active_friend, ())) == 0 and empty_confirm_rounds < 2:
                    empty_confirm_rounds += 1
                    wait_s = 0.5
                    log(
                        "当前会话收尾",
                        f"{active_friend} 待回复为空，{wait_s:.1f}s后做第{empty_confirm_rounds}次离开前复检",
                    )
                    time.sleep(wait_s)
                    sweep_current_chat_pending(
                        expected_friend=active_friend,
                        source="当前会话收尾复检",
                        enqueue_to_active=True,
                    )

            pending_left = len(active_pending_msgs.get(active_friend, ()))
            if sent_count > 0:
                if pending_left > 0:
                    cd = min(max(args.burst_gap, 0.2), 1.0)
                    active_next_reply_at[active_friend] = time.time() + cd
                    log("当前会话续回", f"{active_friend} 仍有{pending_left}条待回复，{cd:.1f}s后继续")
                else:
                    cd = random_cooldown(args.cooldown_active_min, args.cooldown_active_max)
                    active_next_reply_at[active_friend] = time.time() + cd
                    log("当前会话冷却", f"{active_friend} -> {cd:.1f}s")
            elif skipped_count > 0 and pending_left == 0:
                cd = min(max(args.burst_gap, 0.2), 1.0)
                active_next_reply_at[active_friend] = time.time() + cd
                log("当前会话冷却", f"{active_friend} -> 跳过后缓冲{cd:.1f}s")
            if sent_count > 0:
                sweep_current_chat_pending(
                    expected_friend=active_friend,
                    source="回复后校准",
                    enqueue_to_active=True,
                )
            pending_left = len(active_pending_msgs.get(active_friend, ()))
            current_chat_name = get_current_chat_name()
            if sent_count > 0 and pending_left == 0:
                mark_current_chat_visible_as_seen(
                    active_friend,
                    source="当前会话回复后",
                    upto_runtime_id=last_sent_runtime_id,
                )
            if pending_left == 0 and should_click_session_badge(
                active_friend,
                current_chat_name=current_chat_name,
                enqueue_to_active=True,
            ):
                refresh_current_session_badge(active_friend)
                mark_current_chat_visible_as_seen(active_friend, source="点击会话项后")
            log(
                "当前会话回复",
                f"{active_friend}（发送{sent_count}，跳过{skipped_count}，剩余{pending_left}）",
            )
            return active_friend if (sent_count > 0 or skipped_count > 0) else None
        except Exception as exc:
            log("当前会话监听异常", exc)
            return None

    def get_current_chat_name() -> str | None:
        try:
            chat_name_text = main_window.child_window(**texts.CurrentChatText)
            if not chat_name_text.exists(timeout=0.1):
                return None
            name = chat_name_text.window_text().strip()
            return name or None
        except KeyboardInterrupt as exc:
            log("读取当前会话中断", exc)
            return None
        except Exception:
            return None

    def has_active_priority(friend: str | None) -> bool:
        if not friend or should_skip_auto_reply(friend):
            return False
        return len(active_pending_msgs.get(friend, ())) > 0

    def should_defer_session_list_scan(current_friend: str | None, purpose: str) -> bool:
        """当前会话刚回复完时，避免立刻翻动左侧会话列表导致焦点切走。"""
        if not current_friend or should_skip_auto_reply(current_friend):
            return False
        defer_until = float(active_next_reply_at.get(current_friend, 0.0) or 0.0)
        now_ts = time.time()
        if now_ts < defer_until:
            wait_left = max(0.0, defer_until - now_ts)
            log("会话优先", f"{current_friend} 当前会话冷却中，{purpose}延后{wait_left:.1f}s")
            return True
        return False

    def describe_main_window_unavailable(window) -> str:
        if window is None:
            return "窗口对象为空"
        try:
            if hasattr(window, "is_minimized") and window.is_minimized():
                return "窗口已最小化"
        except Exception:
            pass
        try:
            if hasattr(window, "is_visible") and not window.is_visible():
                return "窗口不可见"
        except Exception:
            pass
        return ""

    def is_main_window_unavailable(window) -> bool:
        return bool(describe_main_window_unavailable(window))

    def should_recover_main_window(exc: Exception) -> bool:
        text = str(exc or "")
        recovery_markers = (
            "mmui::XTabBarItem",
            "mmui::MainWindow",
            "'title': '微信', 'control_type': 'Button'",
            '"title": "微信", "control_type": "Button"',
            "ElementNotFoundError",
            "InvalidWindowHandle",
            "窗口已最小化",
        )
        if any(marker in text for marker in recovery_markers):
            return True
        return is_main_window_unavailable(main_window)

    def try_recover_main_window(exc: Exception, context: str = "") -> bool:
        nonlocal main_window
        if not should_recover_main_window(exc):
            return False
        try:
            unavailable_reason = describe_main_window_unavailable(main_window)
            exc_text = str(exc or "").strip()
            reason = unavailable_reason or (exc_text[:120] if exc_text else "控件不可用/窗口句柄失效")
            log("窗口恢复", f"{context or '-'} -> 检测到微信窗口不可用: {reason}，尝试重新打开")
            main_window = open_wechat_main_window_with_retry(max_attempts=3, retry_delay=0.8)
            log("窗口恢复", f"{context or '-'} -> 已重新打开微信主窗口")
            return True
        except Exception as reopen_exc:
            log("窗口恢复失败", f"{context or '-'} -> {reopen_exc}")
            return False

    def handle_scan_exception(exc: Exception, context: str = "") -> None:
        if has_no_subscriber_error(exc):
            current_wxid = str(self_wechat_id or "").strip()
            ok, detail = call_check_online_with_wechat_id(current_wxid, client=chat_api_client)
            if ok:
                log("在线检查回调", f"{context or '-'} -> checkOnline 成功: {detail}")
            else:
                log("在线检查回调失败", f"{context or '-'} -> {detail}")
            print(
                f"{AUTO_REPLY_STOP_MARKER} 扫描阶段检测到“事件无法调用任何订户”，"
                f"已调用 checkOnline，停止自动回复。wechatId={current_wxid or 'wxid_unknown'}",
                flush=True,
            )
            raise SystemExit(461)
        if try_recover_main_window(exc, context=context or "扫描异常"):
            return
        log("扫描异常", exc)

    def should_resume_current_chat(current_friend: str | None) -> bool:
        """扫未读前后做一次轻量抢占：当前会话若又来了消息，先继续处理当前会话。"""
        if not current_friend or should_skip_auto_reply(current_friend):
            return False
        visible_friend, added = sweep_current_chat_pending(
            expected_friend=current_friend,
            source="扫描后校准",
        )
        if visible_friend == current_friend and (added > 0 or has_active_priority(current_friend)):
            log("会话优先", f"{current_friend} 当前会话有新消息，继续优先处理")
            return True
        if should_defer_session_list_scan(current_friend, "当前会话复检"):
            return False
        try:
            latest_unread_map = scan_for_new_messages(
                main_window=main_window,
                is_maximize=False,
                close_weixin=False,
            )
        except KeyboardInterrupt as exc:
            log("扫描中断", f"{current_friend} -> {exc}")
            return False
        except Exception as exc:
            handle_scan_exception(exc, context=f"当前会话复检:{current_friend or '-'}")
            return False
        if int(latest_unread_map.get(current_friend, 0) or 0) > 0:
            log("会话优先", f"{current_friend} 在会话列表出现新未读，继续优先处理")
            return True
        return False

    def should_stay_on_current_chat(current_friend: str | None) -> bool:
        """正式扫描未读前，再校准一次当前会话，避免同时来消息时切到其他会话。"""
        if not current_friend or should_skip_auto_reply(current_friend):
            return False
        visible_friend, added = sweep_current_chat_pending(
            expected_friend=current_friend,
            source="扫描前校准",
        )
        if visible_friend == current_friend and (added > 0 or has_active_priority(current_friend)):
            log("会话优先", f"{current_friend} 扫描未读前发现新消息，继续优先处理")
            return True
        return False

    def should_stay_on_current_chat_before_switch(current_friend: str | None, target_friend: str) -> bool:
        """真正切换到其他未读会话前，再复检一次当前会话。"""
        if not current_friend or current_friend == target_friend or should_skip_auto_reply(current_friend):
            return False
        visible_friend, added = sweep_current_chat_pending(
            expected_friend=current_friend,
            source="切换前校准",
        )
        if visible_friend == current_friend and (added > 0 or has_active_priority(current_friend)):
            log("会话优先", f"{current_friend} 在切换到 {target_friend} 前发现新消息，取消切换")
            return True
        return False

    def get_latest_unread_count(friend: str) -> int:
        if should_defer_session_list_scan(friend, "未读角标复检"):
            return 0
        try:
            latest_unread_map = scan_for_new_messages(
                main_window=main_window,
                is_maximize=False,
                close_weixin=False,
            )
        except KeyboardInterrupt as exc:
            log("扫描中断", f"{friend} -> {exc}")
            return -1
        except Exception as exc:
            handle_scan_exception(exc, context=f"未读角标复检:{friend}")
            return -1
        return int(latest_unread_map.get(friend, 0) or 0)

    def should_click_session_badge(
        friend: str,
        current_chat_name: str | None,
        enqueue_to_active: bool,
    ) -> bool:
        time.sleep(0.35)
        if current_chat_name == friend:
            if should_defer_session_list_scan(friend, "点击会话项"):
                return False
            sweep_current_chat_pending(
                expected_friend=friend,
                source="点击前校准",
                enqueue_to_active=enqueue_to_active,
            )
            if enqueue_to_active and has_active_priority(friend):
                log("会话刷新", f"{friend} 点击前发现当前会话又有新消息，暂不点头像")
                return False
        unread_count = get_latest_unread_count(friend)
        if unread_count > 0:
            log("会话刷新", f"{friend} 点击前发现未读标记={unread_count}，暂不点头像")
            return False
        return True

    def should_resume_original_chat_after_switch(
        original_current_friend: str | None,
        target_friend: str,
    ) -> bool:
        """已点进目标会话但还没开始处理时，再检查原当前会话是否来了新消息。"""
        if (
            not original_current_friend
            or original_current_friend == target_friend
            or should_skip_auto_reply(original_current_friend)
        ):
            return False
        latest_count = get_latest_unread_count(original_current_friend)
        if latest_count > 0:
            log(
                "会话优先",
                f"{original_current_friend} 在进入 {target_friend} 会话后出现未读({latest_count})，先回原会话",
            )
            return True
        return False

    def consume_unread_without_reply(friend: str, count: int, *, source: str) -> None:
        """进入会话消费未读，但不进入自动回复队列。"""
        try:
            current_friend = get_current_chat_name()
            if current_friend != friend:
                ok, reason = _open_chat_strict(friend)
                if not ok:
                    log("已读处理失败", f"{friend} -> 打开会话失败: {reason}")
                    suppress_read_only_session(friend, reason="打开会话失败")
                    return
            if is_auto_reply_group_chat(friend):
                remember_group_chat(friend, source=source)
            mark_current_chat_visible_as_seen(friend, source=source)
            refresh_current_session_badge(friend)
            after = get_latest_unread_count(friend)
            if after > 0:
                log("已读处理", f"{friend} 复检仍有未读={after}，暂不回复")
                suppress_read_only_session(friend, reason=f"复检未读={after}")
            else:
                read_only_suppress_until.pop(friend, None)
                log("已读处理", f"{friend} 已消费未读")
        except Exception as exc:
            log("已读处理失败", f"{friend} -> {exc}")
            suppress_read_only_session(friend, reason="处理异常")

    def open_wechat_main_window_with_retry(max_attempts: int = 4, retry_delay: float = 1.0):
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return Navigator.open_weixin(is_maximize=False)
            except Exception as exc:
                last_exc = exc
                log("启动重试", f"打开微信主窗口失败，第{attempt}/{max_attempts}次 -> {exc}")
                if attempt >= max_attempts:
                    break
                time.sleep(retry_delay)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("打开微信主窗口失败")

    auto_reply_owner_id = f"auto_reply:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    try:
        claim_task_runtime(
            task_type="auto_reply",
            owner_id=auto_reply_owner_id,
            label="poll",
            takeover_timeout_s=20.0,
            logger=lambda m: log("运行时", m),
        )
        main_window = open_wechat_main_window_with_retry()
        log(
            "监听状态",
            (
                f"已启动，轮询 {args.interval}s，"
                f"当前会话冷却随机[{args.cooldown_active_min},{args.cooldown_active_max}]s，"
                f"未读冷却随机[{args.cooldown_unread_min},{args.cooldown_unread_max}]s，"
                f"当前会话单轮最多{args.max_burst_active}条，未读会话切入后清空队列，"
                f"当前会话补扫最近{args.active_history_probe_count}条，"
                f"进入会话后补扫最近{args.history_probe_count}条，"
                f"按 Ctrl+C 停止"
            ),
        )
        preferred_current_friend: str | None = None
        while True:
            refresh_task_runtime("auto_reply", auto_reply_owner_id, label="poll")
            if should_stop_task_runtime("auto_reply", auto_reply_owner_id):
                log("业务退出", "新实例接管，当前实例退出")
                return 0
            now = time.time()
            try:
                with hold_wechat_ui(
                    task_type="auto_reply",
                    owner_id=auto_reply_owner_id,
                    label="poll",
                    timeout_s=120.0,
                    logger=lambda m: log("调度", m),
                ):
                        current_friend = get_current_chat_name()
                        if current_friend and get_session_policy(current_friend) == "reply":
                            preferred_current_friend = current_friend
                        active_replied = handle_active_chat(now)
                        if active_replied:
                            continue
                        current_friend = get_current_chat_name()
                        if should_stay_on_current_chat(current_friend):
                            continue
                        if current_friend and has_active_priority(current_friend):
                            log("会话优先", f"{current_friend} 当前会话仍有待回复，先不扫描未读")
                            continue
                        if should_defer_session_list_scan(current_friend, "主循环未读扫描"):
                            continue

                        try:
                            unread_map = scan_for_new_messages(
                                main_window=main_window,
                                is_maximize=False,
                                close_weixin=False,
                            )
                        except KeyboardInterrupt as exc:
                            log("扫描中断", exc)
                            continue
                        except Exception as exc:
                            handle_scan_exception(exc, context="主循环未读扫描")
                            continue

                        current_friend = get_current_chat_name()
                        if should_resume_current_chat(current_friend):
                            continue
                        if current_friend and current_friend in unread_map and get_session_policy(current_friend) == "reply":
                            sweep_current_chat_pending(
                                expected_friend=current_friend,
                                source="当前会话未读兜底",
                                enqueue_to_active=True,
                            )
                            if has_active_priority(current_friend):
                                log("会话优先", f"{current_friend} 当前已打开，未读改走当前会话队列处理")
                                continue
                        if (
                            preferred_current_friend
                            and preferred_current_friend != current_friend
                            and get_session_policy(preferred_current_friend) == "reply"
                            and int(unread_map.get(preferred_current_friend, 0) or 0) > 0
                        ):
                            log("会话优先", f"{preferred_current_friend} 与其他会话同时来消息，优先处理原当前会话")
                            unread_items = [(preferred_current_friend, unread_map[preferred_current_friend])]
                        elif current_friend and current_friend in unread_map and get_session_policy(current_friend) == "reply":
                            log("当前会话优先", f"{current_friend} 在未读列表中，本轮仅处理该会话")
                            unread_items = [(current_friend, unread_map[current_friend])]
                        else:
                            unread_items = list(unread_map.items())
                        pending_only_items = [
                            (friend, 0)
                            for friend, q in unread_pending_msgs.items()
                            if q and friend not in dict(unread_items) and get_session_policy(friend) == "reply"
                        ]
                        unread_items.extend(pending_only_items)

                        for friend, count in unread_items:
                            policy = get_session_policy(friend)
                            if policy == "ignore":
                                log("会话跳过", f"{friend}（系统/排除名单）")
                                continue
                            if policy == "read_only":
                                if is_read_only_suppressed(friend, now_ts=now):
                                    continue
                                consume_unread_without_reply(friend, int(count or 0), source="主循环只读消费")
                                continue
                            if now < unread_next_reply_at.get(friend, 0.0):
                                continue
                            log("处理会话", f"{friend}（会话列表标记={count}）")
                            current_friend = get_current_chat_name()
                            if current_friend == friend and get_session_policy(friend) == "reply":
                                sweep_current_chat_pending(
                                    expected_friend=friend,
                                    source="当前会话未读改派",
                                    enqueue_to_active=True,
                                )
                                log("会话优先", f"{friend} 当前已打开，改走当前会话队列处理")
                                handle_active_chat(time.time())
                                break
                            if should_stay_on_current_chat_before_switch(current_friend, friend):
                                break
                            if should_resume_current_chat(current_friend):
                                break
                            log("收到未读", f"{friend} 会话列表标记={count}")
                            pending_q = unread_pending_msgs.get(friend)
                            pending_count = len(pending_q) if pending_q else 0
                            if pending_count == 0:
                                try:
                                    unread_num = max(1, int(count))
                                except Exception:
                                    unread_num = 1
                                if unread_num <= 0:
                                    continue
                                current_friend = get_current_chat_name()
                                if should_stay_on_current_chat_before_switch(current_friend, friend):
                                    break
                                if should_resume_current_chat(current_friend):
                                    break
                                switch_from_friend = current_friend
                                latest_count = get_latest_unread_count(friend)
                                if latest_count == 0:
                                    log("未读修正", f"{friend} 进入会话前复检=0，跳过本轮切换")
                                    continue
                                if latest_count > 0 and latest_count != count:
                                    log("未读修正", f"{friend} 会话列表标记={count}，切换前复检={latest_count}")
                                    unread_num = max(1, latest_count)
                                ok, reason = _open_chat_strict(friend)
                                if not ok:
                                    log("发送失败", f"{friend} -> 打开会话失败: {reason}")
                                    continue
                                if is_auto_reply_group_chat(friend):
                                    remember_group_chat(friend, source="切入未读会话后")
                                    consume_unread_without_reply(friend, unread_num, source="切入未读会话后")
                                    continue
                                post_switch_count = get_post_switch_candidate_count(friend, unread_num)
                                if post_switch_count > unread_num:
                                    log(
                                        "未读修正",
                                        f"{friend} 切换后复检={post_switch_count}，高于切换前{unread_num}，按切换后抓取",
                                    )
                                    unread_num = post_switch_count
                                elif 0 < post_switch_count < unread_num:
                                    log(
                                        "未读修正",
                                        f"{friend} 切换后复检={post_switch_count}，低于切换前{unread_num}，仍按切换前抓取",
                                    )
                                else:
                                    log("未读修正", f"{friend} 切换后复检={post_switch_count}")
                                reset_unread_capture_session(friend)
                                capture_current_chat_to_unread_pending(friend, unread_num, source="进入会话后缓存")
                                if should_resume_original_chat_after_switch(switch_from_friend, friend):
                                    continue
                                pending_q = unread_pending_msgs.get(friend)

                            pending_q = unread_pending_msgs.get(friend)
                            if not pending_q:
                                continue

                            latest_text = ""
                            sent_count = 0
                            skipped_count = 0
                            tail_idle_rounds = 0
                            tail_empty_confirm_rounds = 0
                            while True:
                                pending_q = unread_pending_msgs.get(friend)
                                if not pending_q:
                                    added_tail = capture_current_chat_to_unread_pending(
                                        friend,
                                        unread_num=0,
                                        source="未读收尾补扫",
                                        after_entry_only=True,
                                    )
                                    if added_tail > 0:
                                        tail_idle_rounds += 1
                                        tail_empty_confirm_rounds = 0
                                        if tail_idle_rounds >= 2 and sent_count == 0:
                                            log("未读收尾", f"{friend} 连续补扫仍无有效发送，结束本轮避免死循环")
                                            break
                                        log("未读收尾", f"{friend} 回复期间新增{added_tail}条，继续处理")
                                        continue
                                    if sent_count > 0 and tail_empty_confirm_rounds < 2:
                                        tail_empty_confirm_rounds += 1
                                        wait_s = 0.5
                                        log(
                                            "未读收尾",
                                            f"{friend} 待回复为空，{wait_s:.1f}s后做第{tail_empty_confirm_rounds}次离开前复检",
                                        )
                                        time.sleep(wait_s)
                                        continue
                                    break

                                item = pop_unread_pending(friend)
                                if item is None:
                                    continue
                                tail_idle_rounds = 0
                                tail_empty_confirm_rounds = 0
                                target_id, msg = item
                                msg = resolve_message_for_consumption(friend, target_id, msg)
                                if not msg:
                                    skipped_count += 1
                                    continue
                                latest_text = msg

                                if looks_like_self_quote_text(friend, msg):
                                    log("自发过滤", f"{friend} -> 命中引用文本特征")
                                    skipped_count += 1
                                    continue
                                if looks_like_recent_self_sent(friend, msg):
                                    log("自发过滤", f"{friend} -> 命中最近发送文本")
                                    skipped_count += 1
                                    continue
                                if not should_auto_reply_text(msg):
                                    log("消息类型跳过", f"{friend} -> {msg[:60]}")
                                    skipped_count += 1
                                    continue
                                if not validate_reply_target_before_action(friend, target_id, action="未读会话处理"):
                                    skipped_count += 1
                                    continue
                                shortlink_match = choose_shortlink_rule(msg, shortlink_rules)
                                if shortlink_match:
                                    keyword, short_link = shortlink_match
                                    if not should_trigger_shortlink(friend, keyword):
                                        log("短链抑制", f"{friend} -> {keyword} 冷却中，跳过重复触发")
                                        mark_replied(friend, msg, target_id)
                                        skipped_count += 1
                                        continue
                                    log("短链命中", msg.replace("\n", " ")[:80])
                                    ok, reason = prepare_and_forward_miniprogram_from_shortlink(
                                        target_friend=friend,
                                        short_link=short_link,
                                        source_chat=args.mini_shortlink_source_chat,
                                    )
                                    if ok:
                                        log("短链转卡片", f"{friend} <- {reason}")
                                    else:
                                        log("短链转卡片失败", f"{friend} <- {reason}，降级发送短链文本")
                                        sent_ok = send_reply(friend, short_link, target_runtime_id=target_id)
                                        if not sent_ok:
                                            skipped_count += 1
                                            continue
                                        log("短链发送", f"{friend} <- {short_link}")
                                    remember_shortlink_trigger(friend, keyword)
                                    mark_replied(friend, msg, target_id)
                                    sent_count += 1
                                    if unread_pending_msgs.get(friend):
                                        time.sleep(max(args.burst_gap, 0.0))
                                    continue

                                source_chat = choose_miniprogram_source(msg, mini_forward_rules)
                                if source_chat:
                                    ok, reason = forward_latest_miniprogram(source_chat=source_chat, target_friend=friend)
                                    if ok:
                                        log("小程序转发", f"{friend} <- {source_chat}")
                                        mark_replied(friend, msg, target_id)
                                        sent_count += 1
                                        if unread_pending_msgs.get(friend):
                                            time.sleep(max(args.burst_gap, 0.0))
                                        continue
                                    log("小程序转发失败", f"{friend} <- {source_chat}: {reason}")

                                sweep_current_chat_pending(
                                    expected_friend=friend,
                                    source="百炼前补扫",
                                    enqueue_to_active=False,
                                )
                                reply_text = make_reply(friend, msg)
                                sweep_current_chat_pending(
                                    expected_friend=friend,
                                    source="百炼后补扫",
                                    enqueue_to_active=False,
                                )
                                sent_ok = send_reply(friend, reply_text, target_runtime_id=target_id)
                                if not sent_ok:
                                    skipped_count += 1
                                    continue
                                time.sleep(0.5)
                                sweep_current_chat_pending(
                                    expected_friend=friend,
                                    source="发送后待回复复检",
                                    enqueue_to_active=False,
                                )
                                mark_replied(friend, msg, target_id)
                                sent_count += 1
                                if unread_pending_msgs.get(friend):
                                    time.sleep(max(args.burst_gap, 0.0))

                            if sent_count > 0:
                                cd = random_cooldown(args.cooldown_unread_min, args.cooldown_unread_max)
                                unread_next_reply_at[friend] = now + cd
                                log("未读会话冷却", f"{friend} -> {cd:.1f}s")
                                current_friend, current_rid = snapshot_current_chat_runtime()
                                if current_friend == friend and current_rid:
                                    active_last_runtime[friend] = current_rid
                                if latest_text:
                                    log("匹配消息", latest_text.replace("\n", " ")[:80])
                                left = len(unread_pending_msgs.get(friend, ()))
                                if left == 0:
                                    sweep_current_chat_pending(
                                        expected_friend=friend,
                                        source="回复后校准",
                                        enqueue_to_active=False,
                                    )
                                    mark_current_chat_visible_as_seen(friend, source="未读会话回复后")
                                    left = len(unread_pending_msgs.get(friend, ()))
                                    if current_friend == friend:
                                        left += len(active_pending_msgs.get(friend, ()))
                                if left == 0 and should_click_session_badge(
                                    friend,
                                    current_chat_name=current_friend,
                                    enqueue_to_active=False,
                                ):
                                    refresh_current_session_badge(friend)
                                    mark_current_chat_visible_as_seen(friend, source="点击会话项后")
                                log("发送成功", f"{friend}（本轮发送{sent_count}，跳过{skipped_count}，剩余{left}）")
                                break
            except KeyboardInterrupt:
                interrupt_ts = time.time()
                if interrupt_ts - last_keyboard_interrupt_at < 3.0:
                    log("监听状态", "连续收到 KeyboardInterrupt，停止监听")
                    return 0
                last_keyboard_interrupt_at = interrupt_ts
                log("监听状态", "捕获到异常 KeyboardInterrupt，已忽略并继续运行；3秒内再次出现才会退出")
                log("KeyboardInterrupt", traceback.format_exc().strip())
                continue
            except Exception as exc:
                if try_recover_main_window(exc, context="主循环调度异常"):
                    continue
                log("调度异常", exc)
            time.sleep(max(args.interval, 0.2))
    except KeyboardInterrupt:
        log("监听状态", "收到 Ctrl+C，停止监听")
        return 0
    finally:
        release_task_runtime("auto_reply", auto_reply_owner_id)
        if main_window is not None and not args.keep_open:
            try:
                main_window.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
