"""wechat_bot 路径与账号隔离辅助函数。"""

from __future__ import annotations

import os
import re
from pathlib import Path


BOT_APP_DIRNAME = "PyWeChatBot"


def sanitize_file_piece(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z_-]+", "_", str(text)).strip("_") or "unknown_wxid"


def get_current_wxid_key() -> str:
    """解析当前登录账号的 wxid，用于隔离缓存与日志目录。"""
    try:
        from pyweixin.WeChatTools import Tools

        wxid = Tools.get_current_wxid()
        if wxid:
            return sanitize_file_piece(str(wxid))
    except Exception:
        pass
    return "unknown_wxid"


def get_bot_app_root() -> Path:
    """返回机器人统一的数据根目录。"""
    base = (
        os.getenv("LOCALAPPDATA")
        or os.getenv("APPDATA")
        or str(Path.home() / "AppData" / "Local")
    )
    root = Path(base) / BOT_APP_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_bot_data_dir(wxid: str | None = None) -> Path:
    wxid_key = sanitize_file_piece(wxid) if wxid else get_current_wxid_key()
    data_dir = get_bot_app_root() / "data" / wxid_key
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_bot_logs_dir(wxid: str | None = None) -> Path:
    wxid_key = sanitize_file_piece(wxid) if wxid else get_current_wxid_key()
    log_dir = get_bot_app_root() / "logs" / wxid_key
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def get_bot_cache_dir() -> Path:
    cache_dir = get_bot_app_root() / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_bot_cache_file(filename: str) -> Path:
    return get_bot_cache_dir() / filename


def get_bot_scheduler_dir() -> Path:
    scheduler_dir = get_bot_app_root() / "scheduler"
    scheduler_dir.mkdir(parents=True, exist_ok=True)
    return scheduler_dir
