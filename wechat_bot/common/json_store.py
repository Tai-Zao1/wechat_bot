"""轻量 JSON 读写辅助，统一容错与编码处理。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json_dict(path: Path) -> dict[str, Any]:
    """读取 JSON 对象；失败或类型不匹配时返回空字典。"""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def load_json_list(path: Path) -> list[Any]:
    """读取 JSON 数组；失败或类型不匹配时返回空列表。"""
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return data


def write_json_file(path: Path, payload: Any) -> None:
    """写入 UTF-8 JSON，并确保父目录存在。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
