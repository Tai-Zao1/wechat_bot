"""统一管理可执行脚本入口。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScriptSpec:
    script_name: str
    display_name: str
    relative_path: str
    requires_win32com_cache: bool
    module_names: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    summary: str = ""

    @property
    def legacy_names(self) -> tuple[str, ...]:
        """返回兼容旧运行方式的名字集合。"""
        return (self.script_name, *self.aliases)


SCRIPT_REGISTRY: tuple[ScriptSpec, ...] = (
    ScriptSpec(
        script_name="check_wechat_status.py",
        display_name="check_wechat",
        relative_path="scripts/check_wechat.py",
        requires_win32com_cache=True,
        module_names=("wechat_bot.scripts.check_wechat", "wechat_bot.check_wechat_status", "check_wechat_status"),
        aliases=("check_wechat.py",),
        summary="检查微信窗口与登录状态",
    ),
    ScriptSpec(
        script_name="open_wechat_window.py",
        display_name="open_wechat",
        relative_path="scripts/open_wechat.py",
        requires_win32com_cache=True,
        module_names=("wechat_bot.scripts.open_wechat", "wechat_bot.open_wechat_window", "open_wechat_window"),
        aliases=("open_wechat.py",),
        summary="打开或恢复微信主窗口",
    ),
    ScriptSpec(
        script_name="auto_reply_unread.py",
        display_name="auto_reply",
        relative_path="scripts/auto_reply.py",
        requires_win32com_cache=True,
        module_names=("wechat_bot.scripts.auto_reply", "wechat_bot.auto_reply_unread", "auto_reply_unread"),
        aliases=("auto_reply.py",),
        summary="自动回复未读消息",
    ),
    ScriptSpec(
        script_name="add_friend_by_phone.py",
        display_name="add_friends",
        relative_path="scripts/add_friends.py",
        requires_win32com_cache=True,
        module_names=("wechat_bot.scripts.add_friends", "wechat_bot.add_friend_by_phone", "add_friend_by_phone"),
        aliases=("add_friends.py",),
        summary="通过 API 或 Excel 批量加好友",
    ),
)


def resolve_script_spec(script_name: str) -> ScriptSpec | None:
    """按旧文件名、新别名或展示名解析脚本定义。"""
    target = str(script_name or "").strip()
    if not target:
        return None
    for spec in SCRIPT_REGISTRY:
        if target in {spec.script_name, spec.display_name, *spec.aliases}:
            return spec
    return None


def format_script_help_lines() -> tuple[str, ...]:
    """输出给 CLI 的脚本索引说明。"""
    lines: list[str] = []
    for spec in SCRIPT_REGISTRY:
        lines.append(f"- {spec.display_name:<12} {spec.summary}")
        lines.append(f"  实现: wechat_bot/{spec.relative_path}")
        lines.append(f"  兼容: {', '.join(spec.legacy_names)}")
    return tuple(lines)
