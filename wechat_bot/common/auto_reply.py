"""自动回复相关的共享辅助函数。"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from .json_store import load_json_dict, load_json_list, write_json_file


AUTO_REPLY_CACHE_FILENAME: Final[str] = "auto_reply_cache.json"
AUTO_REPLY_RECENT_CACHE_FILENAME: Final[str] = "auto_reply_recent.json"
AUTO_REPLY_SELF_SENT_CACHE_FILENAME: Final[str] = "auto_reply_self_sent.json"

RuleList = list[tuple[str, str]]
RepliedCache = dict[str, float]
SelfSentCache = dict[str, list[tuple[float, str]]]


def load_replied_cache(path: Path) -> RepliedCache:
    """读取去重缓存，统一把值归一为时间戳浮点数。"""
    data = load_json_dict(path)
    if not data:
        return {}
    cache: RepliedCache = {}
    for key, value in data.items():
        if not isinstance(key, str):
            continue
        try:
            cache[key] = float(value)
        except Exception:
            continue
    return cache


def save_replied_cache(path: Path, cache: RepliedCache) -> None:
    """保存去重缓存。"""
    write_json_file(path, cache)


def load_self_sent_cache(path: Path) -> SelfSentCache:
    """读取机器人自己发出的消息缓存，用于后续去重判断。"""
    data = load_json_dict(path)
    if not data:
        return {}
    cache: SelfSentCache = {}
    for friend, items in data.items():
        if not isinstance(friend, str) or not isinstance(items, list):
            continue
        rows: list[tuple[float, str]] = []
        for item in items:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            try:
                ts = float(item[0])
            except Exception:
                continue
            text = " ".join(str(item[1] or "").split()).strip()
            if text:
                rows.append((ts, text))
        if rows:
            cache[friend] = rows
    return cache


def save_self_sent_cache(path: Path, cache: SelfSentCache) -> None:
    """保存机器人自己发出的消息缓存。"""
    write_json_file(path, cache)


def resolve_rule_file(rule_file: str | None, missing_message: str) -> Path | None:
    """将规则文件路径标准化；未传入时返回 `None`。"""
    if not rule_file:
        return None
    path = Path(rule_file).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"{missing_message}: {path}")
    return path


def load_keyword_rule_pairs(
    rule_file: str | None,
    *,
    value_key: str,
    missing_message: str,
    invalid_message: str,
) -> RuleList:
    """读取关键词规则，统一兼容 dict 与 list[dict] 两种 JSON 格式。"""
    path = resolve_rule_file(rule_file, missing_message)
    if path is None:
        return []

    data_dict = load_json_dict(path)
    if data_dict:
        rules: RuleList = []
        for key, value in data_dict.items():
            keyword = str(key).strip()
            mapped_value = str(value).strip()
            if keyword and mapped_value:
                rules.append((keyword, mapped_value))
        return rules

    data_list = load_json_list(path)
    if data_list:
        rules = []
        for item in data_list:
            if not isinstance(item, dict):
                continue
            keyword = str(item.get("keyword", "")).strip()
            mapped_value = str(item.get(value_key, "")).strip()
            if keyword and mapped_value:
                rules.append((keyword, mapped_value))
        return rules

    raise ValueError(invalid_message)
