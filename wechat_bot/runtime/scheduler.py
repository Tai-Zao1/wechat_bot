#!/usr/bin/env python3
"""Cross-process WeChat UI execution coordinator."""

from __future__ import annotations

import ctypes
import json
import os
import random
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from wechat_bot.core.paths import (
    get_bot_scheduler_dir,
)
from wechat_bot.core.runtime_policy import (
    PENDING_STALE_SECONDS,
    PRIORITY_ORDER,
    UI_LOCK_STALE_SECONDS,
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
        "updated_at": _now_ts(),
    }
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))
        os.close(fd)
        return True
    except FileExistsError:
        lock_meta = _read_ui_lock_meta()
        if not lock_meta:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            return False
        pid = lock_meta.get("pid")
        updated_at = float(lock_meta.get("updated_at", 0.0) or 0.0)
        if pid is not None and not _is_pid_alive(int(pid)):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            return False
        if _now_ts() - updated_at > UI_LOCK_STALE_SECONDS:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            return False
        return False


def _refresh_ui_lock(task_type: str, owner_id: str, label: str) -> None:
    path = _ui_lock_file()
    meta = {
        "task_type": task_type,
        "owner_id": owner_id,
        "label": label,
        "pid": os.getpid(),
        "updated_at": _now_ts(),
    }
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _release_ui_lock(task_type: str, owner_id: str) -> None:
    path = _ui_lock_file()
    meta = _read_ui_lock_meta()
    if not meta:
        return
    if meta.get("task_type") == task_type and meta.get("owner_id") == owner_id:
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
    _upsert_pending(task_type, owner_id, label)
    deadline = _now_ts() + max(takeover_timeout_s, poll_interval)
    while True:
        if not _has_higher_priority_pending(task_type, owner_id):
            if logger is not None:
                logger(f"{task_type} 已获取运行资格")
            runtime = {
                "task_type": task_type,
                "owner_id": owner_id,
                "label": label,
                "pid": os.getpid(),
                "heartbeat": _now_ts(),
            }
            _runtime_file(task_type).write_text(json.dumps(runtime, ensure_ascii=False, indent=2), encoding="utf-8")
            return runtime
        if _now_ts() >= deadline:
            break
        time.sleep(max(poll_interval, 0.05))
    if logger is not None:
        logger(f"{task_type} 等待超时，继续尝试旧模式运行")
    return {
        "task_type": task_type,
        "owner_id": owner_id,
        "label": label,
        "pid": os.getpid(),
    }


def refresh_task_runtime(task_type: str, owner_id: str, label: str = "") -> None:
    _upsert_pending(task_type, owner_id, label)
    path = _runtime_file(task_type)
    data = {
        "task_type": task_type,
        "owner_id": owner_id,
        "label": label,
        "pid": os.getpid(),
        "heartbeat": _now_ts(),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def release_task_runtime(task_type: str, owner_id: str) -> None:
    _clear_pending(task_type, owner_id)
    path = _runtime_file(task_type)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if isinstance(data, dict) and data.get("owner_id") == owner_id:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    _release_ui_lock(task_type, owner_id)


def should_stop_task_runtime(task_type: str, owner_id: str) -> bool:
    path = _runtime_file(task_type)
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    current_owner = str(data.get("owner_id") or "").strip()
    if not current_owner:
        return False
    return current_owner != owner_id


@contextmanager
def hold_wechat_ui(
    task_type: str,
    owner_id: str,
    label: str = "",
    timeout_s: float = 120.0,
    poll_interval: float = 0.2,
    logger: Callable[[str], None] | None = None,
) -> Iterator[None]:
    deadline = _now_ts() + max(timeout_s, poll_interval)
    while True:
        if _try_acquire_ui_lock(task_type, owner_id, label):
            break
        if should_stop_task_runtime(task_type, owner_id):
            raise RuntimeError("运行资格已被新实例接管")
        if _now_ts() >= deadline:
            raise TimeoutError("等待微信 UI 锁超时")
        if logger is not None and random.random() < 0.1:
            logger("等待微信 UI 资源释放")
        time.sleep(max(poll_interval, 0.05))

    try:
        _refresh_ui_lock(task_type, owner_id, label)
        yield
    finally:
        _release_ui_lock(task_type, owner_id)


__all__ = [
    "claim_task_runtime",
    "hold_wechat_ui",
    "refresh_task_runtime",
    "release_task_runtime",
    "should_stop_task_runtime",
]
