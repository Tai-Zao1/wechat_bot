"""好友定时群发服务。"""

from __future__ import annotations

import os
import random
import threading
import time
import uuid
from typing import Callable

from wechat_bot.core import get_bot_cache_file
from wechat_bot.runtime import (
    claim_task_runtime,
    hold_wechat_ui,
    refresh_task_runtime,
    release_task_runtime,
    should_stop_task_runtime,
)


def _remember_recent_self_sent_text(friend: str, message: str) -> int:
    """把定时群发发出的文本登记到自动回复自发缓存，避免被误判为对方消息。"""
    clean_friend = str(friend or "").strip()
    clean_message = " ".join(str(message or "").split()).strip()
    if not clean_friend or not clean_message:
        return 0
    try:
        from wechat_bot.scripts.auto_reply_support import (
            AUTO_REPLY_SELF_SENT_CACHE_FILENAME,
            load_self_sent_cache,
            save_self_sent_cache,
        )

        cache_path = get_bot_cache_file(AUTO_REPLY_SELF_SENT_CACHE_FILENAME)
        cache = load_self_sent_cache(cache_path)
        now_ts = time.time()
        rows = list(cache.get(clean_friend, []))
        rows.append((now_ts, clean_message))
        rows = [(ts, text) for ts, text in rows if text][-40:]
        cache[clean_friend] = rows
        save_self_sent_cache(cache_path, cache)
        return len(rows)
    except Exception:
        return 0


def _reopen_wechat_window(is_maximize: bool = False) -> tuple[bool, str]:
    """发送前尝试恢复微信主窗口，减少窗口焦点丢失导致的异常。"""
    try:
        from pyweixin.WeChatTools import Navigator

        Navigator.open_weixin(is_maximize=is_maximize)
        return True, "已恢复微信主窗口"
    except Exception as exc:
        return False, f"恢复微信主窗口失败: {exc}"


def _send_message_once(friend: str, message: str) -> None:
    """给单个好友发送一条文本消息。"""
    from pyweixin import Messages

    Messages.send_messages_to_friend(
        friend=friend,
        messages=[message],
        search_pages=0,
        is_maximize=False,
        close_weixin=False,
    )


def run_timed_send_loop(
    friends: list[str],
    message: str,
    interval_min: float = 5.0,
    interval_max: float = 10.0,
    stop_event: threading.Event | None = None,
    log: Callable[[str], None] | None = None,
) -> int:
    """按随机间隔给选中的好友逐个发送消息，可被手动中断。"""
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
                    reopen_ok, reopen_reason = _reopen_wechat_window(is_maximize=False)
                    if not reopen_ok:
                        emit(f"发送准备 -> {friend}: {reopen_reason}")
                    try:
                        _send_message_once(friend=friend, message=message)
                    except Exception as exc:
                        text = str(exc)
                        if "NoneType" in text and "child_window" in text:
                            emit(f"发送重试 -> {friend}: 检测到窗口对象丢失，重新恢复后再试一次")
                            reopen_ok, reopen_reason = _reopen_wechat_window(is_maximize=False)
                            if not reopen_ok:
                                raise RuntimeError(reopen_reason) from exc
                            time.sleep(0.2)
                            _send_message_once(friend=friend, message=message)
                        else:
                            raise
                    cached = _remember_recent_self_sent_text(friend=friend, message=message)
                    if cached > 0:
                        emit(f"自发缓存 -> {friend}: 已登记最近发送文本")
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


__all__ = ["run_timed_send_loop"]
