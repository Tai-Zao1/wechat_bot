"""wechat_bot 共享数据类型。"""

from __future__ import annotations

from typing import TypedDict


class FriendProfile(TypedDict):
    display_name: str
    remark: str
    nickname: str
    wechat_id: str
    avatar_path: str


class SelfProfile(TypedDict):
    wxid: str
    wechat_id: str
    nickname: str
