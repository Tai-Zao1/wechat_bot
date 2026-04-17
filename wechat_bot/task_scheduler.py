#!/usr/bin/env python3
"""Cross-process WeChat UI execution coordinator."""

from __future__ import annotations

import json
import os
import random
import time
import ctypes
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from wechat_bot.core import (
    PENDING_STALE_SECONDS,
    PRIORITY_ORDER,
    UI_LOCK_STALE_SECONDS,
    get_bot_scheduler_dir,
)

def _scheduler_dir() -> Path:
    return get_bot_scheduler_dir()


def _state_file() -> Path:
    return _scheduler_dir() / "ui_state.json"


def _state_lock_file() -> Path:
    return _scheduler_dir() / "ui_state.lock"


def _ui_lock_file() -> Path:
    return _scheduler_dir() / "wechat_ui.lock"


def _runtime_file(task_type: str) -> Path:
    safe_name = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(task_type))
    return _scheduler_dir() / f"runtime_{safe_name}.json"


def _now_ts() -> float:
    return time.time()


def _is_pid_alive(pid: int | None) -> bool:
    try:
        target = int(pid or 0)
    except Exception:
        return False
    if target <= 0:
        return False
    if os.name == "nt":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, target)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return int(exit_code.value) == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(target, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@contextmanager
def _exclusive_file(path: Path, stale_s: float = 10.0) -> Iterator[None]:
    while True:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("utf-8"))
            os.close(fd)
            break
        except FileExistsError:
            try:
                age = _now_ts() - path.stat().st_mtime
            except FileNotFoundError:
                continue
            if age > stale_s:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                continue
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _load_state() -> dict:
    path = _state_file()
    if not path.exists():
        return {"pending": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"pending": {}}
    if not isinstance(data, dict):
        return {"pending": {}}
    pending = data.get("pending")
    if not isinstance(pending, dict):
        data["pending"] = {}
    return data


def _save_state(data: dict) -> None:
    path = _state_file()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _cleanup_pending_locked(data: dict, stale_s: float = PENDING_STALE_SECONDS) -> None:
    pending = data.setdefault("pending", {})
    now = _now_ts()
    empty_types: list[str] = []
    for task_type, owners in pending.items():
        if not isinstance(owners, dict):
            empty_types.append(task_type)
            continue
        dead_owners = []
        for owner_id, info in owners.items():
            if not isinstance(info, dict):
                dead_owners.append(owner_id)
                continue
            pid = info.get("pid")
            if pid is not None and not _is_pid_alive(int(pid)):
                dead_owners.append(owner_id)
                continue
            heartbeat = float(info.get("heartbeat", 0.0) or 0.0)
            if now - heartbeat > stale_s:
                dead_owners.append(owner_id)
        for owner_id in dead_owners:
            owners.pop(owner_id, None)
        if not owners:
            empty_types.append(task_type)
    for task_type in empty_types:
        pending.pop(task_type, None)


def _upsert_pending(task_type: str, owner_id: str, label: str = "") -> None:
    with _exclusive_file(_state_lock_file()):
        data = _load_state()
        _cleanup_pending_locked(data)
        pending = data.setdefault("pending", {})
        task_map = pending.setdefault(task_type, {})
        task_map[owner_id] = {
            "label": label,
            "pid": os.getpid(),
            "heartbeat": _now_ts(),
        }
        _save_state(data)


def _clear_pending(task_type: str, owner_id: str) -> None:
    with _exclusive_file(_state_lock_file()):
        data = _load_state()
        pending = data.setdefault("pending", {})
        task_map = pending.get(task_type)
        if isinstance(task_map, dict):
            task_map.pop(owner_id, None)
            if not task_map:
                pending.pop(task_type, None)
        _cleanup_pending_locked(data)
        _save_state(data)


def _has_higher_priority_pending(task_type: str, owner_id: str) -> bool:
    with _exclusive_file(_state_lock_file()):
        data = _load_state()
        _cleanup_pending_locked(data)
        _save_state(data)
        pending = data.get("pending", {})
        my_priority = PRIORITY_ORDER.get(task_type, 0)
        for other_type, owners in pending.items():
            if PRIORITY_ORDER.get(other_type, 0) <= my_priority:
                continue
            if not isinstance(owners, dict):
                continue
            for other_owner in owners:
                if other_type == task_type and other_owner == owner_id:
                    continue
                return True
    return False


def _read_ui_lock_meta() -> dict | None:
    path = _ui_lock_file()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _try_acquire_ui_lock(task_type: str, owner_id: str, label: str) -> bool:
    path = _ui_lock_file()
    meta = {
        "task_type": task_type,
        "owner_id": owner_id,
        "label": label,
        "pid": os.getpid(),
        "acquired_at": _now_ts(),
    }
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        lock_meta = _read_ui_lock_meta()
        if lock_meta is not None:
            pid = lock_meta.get("pid")
            if pid is not None and not _is_pid_alive(int(pid)):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                return False
            acquired_at = float(lock_meta.get("acquired_at", 0.0) or 0.0)
            if _now_ts() - acquired_at > UI_LOCK_STALE_SECONDS:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
        else:
            try:
                age = _now_ts() - path.stat().st_mtime
            except FileNotFoundError:
                age = 0.0
            if age > UI_LOCK_STALE_SECONDS:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
        return False
    try:
        os.write(fd, json.dumps(meta, ensure_ascii=False).encode("utf-8"))
    finally:
        os.close(fd)
    return True


def _release_ui_lock(owner_id: str) -> None:
    path = _ui_lock_file()
    if not path.exists():
        return
    meta = _read_ui_lock_meta() or {}
    if meta.get("owner_id") not in {None, owner_id}:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _load_runtime_locked(task_type: str) -> dict | None:
    path = _runtime_file(task_type)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _save_runtime_locked(task_type: str, data: dict) -> None:
    path = _runtime_file(task_type)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _clear_runtime_locked(task_type: str) -> None:
    path = _runtime_file(task_type)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def claim_task_runtime(
    task_type: str,
    owner_id: str,
    label: str = "",
    takeover_timeout_s: float = 15.0,
    poll_interval: float = 0.2,
    logger: Callable[[str], None] | None = None,
) -> dict:
    started_at = _now_ts()
    stop_logged = False
    while True:
        with _exclusive_file(_state_lock_file()):
            current = _load_runtime_locked(task_type)
            if not current:
                payload = {
                    "task_type": task_type,
                    "owner_id": owner_id,
                    "label": label,
                    "pid": os.getpid(),
                    "claimed_at": _now_ts(),
                }
                _save_runtime_locked(task_type, payload)
                return payload

            current_owner = str(current.get("owner_id", "") or "")
            current_pid = current.get("pid")
            if current_owner == owner_id:
                current["pid"] = os.getpid()
                current["label"] = label
                current["claimed_at"] = current.get("claimed_at") or _now_ts()
                _save_runtime_locked(task_type, current)
                return current

            if not _is_pid_alive(int(current_pid or 0)):
                _clear_runtime_locked(task_type)
                continue

            if not current.get("stop_requested_at"):
                current["stop_requested_at"] = _now_ts()
                current["stop_requested_by_pid"] = os.getpid()
                current["stop_requested_by_owner"] = owner_id
                current["stop_reason"] = "takeover"
                _save_runtime_locked(task_type, current)
                if logger is not None:
                    logger(f"接管旧实例 pid={current_pid}")
        waited = _now_ts() - started_at
        if takeover_timeout_s is not None and waited >= takeover_timeout_s:
            raise TimeoutError(f"{task_type} 等待旧实例退出超时")
        if logger is not None and waited >= 1.0 and not stop_logged:
            logger("等待旧实例")
            stop_logged = True
        time.sleep(max(poll_interval + random.uniform(0.0, 0.05), 0.05))


def refresh_task_runtime(task_type: str, owner_id: str, label: str = "") -> None:
    with _exclusive_file(_state_lock_file()):
        current = _load_runtime_locked(task_type)
        if not current:
            return
        if current.get("owner_id") != owner_id:
            return
        current["pid"] = os.getpid()
        current["label"] = label or current.get("label", "")
        current["heartbeat"] = _now_ts()
        _save_runtime_locked(task_type, current)


def release_task_runtime(task_type: str, owner_id: str) -> None:
    with _exclusive_file(_state_lock_file()):
        current = _load_runtime_locked(task_type)
        if not current:
            return
        if current.get("owner_id") not in {None, owner_id}:
            return
        _clear_runtime_locked(task_type)


def should_stop_task_runtime(task_type: str, owner_id: str) -> bool:
    with _exclusive_file(_state_lock_file()):
        current = _load_runtime_locked(task_type)
        if not current:
            return False
        if current.get("owner_id") != owner_id:
            return True
        return bool(current.get("stop_requested_at"))


class WeChatUILease:
    def __init__(self, task_type: str, owner_id: str, label: str = "") -> None:
        self.task_type = task_type
        self.owner_id = owner_id
        self.label = label
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        _release_ui_lock(self.owner_id)
        _clear_pending(self.task_type, self.owner_id)
        self._released = True

    def __enter__(self) -> "WeChatUILease":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def acquire_wechat_ui_lease(
    task_type: str,
    owner_id: str,
    label: str = "",
    timeout_s: float | None = None,
    poll_interval: float = 0.2,
    logger: Callable[[str], None] | None = None,
) -> WeChatUILease:
    started_at = _now_ts()
    wait_logged = False
    while True:
        _upsert_pending(task_type, owner_id, label=label)
        if not _has_higher_priority_pending(task_type, owner_id):
            if _try_acquire_ui_lock(task_type, owner_id, label=label):
                if logger is not None:
                    waited = _now_ts() - started_at
                    if waited >= 0.5:
                        logger(f"获得 UI 执行权，等待 {waited:.1f}s")
                return WeChatUILease(task_type=task_type, owner_id=owner_id, label=label)
        waited = _now_ts() - started_at
        if timeout_s is not None and waited >= timeout_s:
            _clear_pending(task_type, owner_id)
            raise TimeoutError(f"{task_type} 等待微信UI执行权超时")
        if logger is not None and waited >= 1.0 and not wait_logged:
            logger("等待 UI 执行权")
            wait_logged = True
        time.sleep(max(poll_interval + random.uniform(0.0, 0.05), 0.05))


@contextmanager
def hold_wechat_ui(
    task_type: str,
    owner_id: str,
    label: str = "",
    timeout_s: float | None = None,
    poll_interval: float = 0.2,
    logger: Callable[[str], None] | None = None,
) -> Iterator[WeChatUILease]:
    lease = acquire_wechat_ui_lease(
        task_type=task_type,
        owner_id=owner_id,
        label=label,
        timeout_s=timeout_s,
        poll_interval=poll_interval,
        logger=logger,
    )
    try:
        yield lease
    finally:
        lease.release()
