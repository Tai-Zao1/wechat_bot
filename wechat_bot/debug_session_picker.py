#!/usr/bin/env python3
"""最小调试：在“微信发送给”窗口中搜索并勾选好友后发送。"""

from __future__ import annotations

import argparse
import time


def log(msg: str) -> None:
    print(f"[DEBUG] {msg}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="调试微信发送给(SessionPickerWindow)选人发送")
    parser.add_argument("--target", required=True, help="要勾选并发送的好友昵称/备注")
    parser.add_argument("--timeout", type=float, default=12.0, help="等待发送窗口出现超时秒数")
    args = parser.parse_args()

    from pywinauto import Desktop
    import pyautogui

    target = str(args.target or "").strip()
    if not target:
        raise ValueError("--target 不能为空")

    ui = Desktop(backend="uia")
    deadline = time.time() + max(1.0, args.timeout)

    log(f"目标好友: {target}")
    log("等待“微信发送给”窗口...")

    picker = None
    while time.time() < deadline:
        wins = []
        # 1) 顶层窗口
        try:
            wins.extend(
                [
                    w
                    for w in ui.windows()
                    if w.is_visible()
                    and (
                        w.class_name() in {"mmui::SessionPickerWindow", "SelectContactWnd"}
                        or (w.window_text() or "").strip() in {"微信发送给", "选择联系人"}
                    )
                ]
            )
        except Exception:
            pass
        # 1.5) top_level_only=False 直接查
        for kw in (
            {"control_type": "Window", "class_name": "mmui::SessionPickerWindow", "title": "微信发送给", "top_level_only": False},
            {"control_type": "Window", "class_name": "mmui::SessionPickerWindow", "top_level_only": False},
            {"control_type": "Window", "title": "微信发送给", "top_level_only": False},
        ):
            try:
                w = ui.window(**kw)
                if w.exists(timeout=0.05) and w.is_visible():
                    wins.append(w)
            except Exception:
                pass
        # 2) 非顶层 descendants（关键兜底）
        try:
            wins.extend(
                [
                    w
                    for w in ui.descendants(control_type="Window")
                    if w.is_visible()
                    and (
                        w.class_name() in {"mmui::SessionPickerWindow", "SelectContactWnd"}
                        or (w.window_text() or "").strip() in {"微信发送给", "选择联系人"}
                    )
                ]
            )
        except Exception:
            pass
        try:
            wins.extend(
                [
                    p
                    for p in ui.descendants(control_type="Pane")
                    if p.is_visible() and (p.window_text() or "").strip() in {"微信发送给", "选择联系人"}
                ]
            )
        except Exception:
            pass
        # 去重
        dedup = []
        seen = set()
        for w in wins:
            try:
                h = int(getattr(w, "handle", 0) or 0)
            except Exception:
                h = id(w)
            if h in seen:
                continue
            seen.add(h)
            dedup.append(w)
        wins = dedup
        log(f"候选发送窗口数量: {len(wins)}")
        if wins:
            picker = max(wins, key=lambda w: w.rectangle().width() * w.rectangle().height())
            break
        time.sleep(0.1)

    if picker is None:
        log("未找到发送窗口，尝试仅用全局搜索框继续")
        try:
            edits = [e for e in ui.descendants(control_type="Edit", class_name="mmui::XValidatorTextEdit") if e.is_visible()]
        except Exception:
            edits = []
        if not edits:
            log("全局搜索框也未找到")
            return 1
        search = edits[0]
        picker = search.top_level_parent()
        log(f"全局命中搜索框，fallback parent: title={picker.window_text()} class={picker.class_name()}")
    else:
        log(f"命中窗口: title={picker.window_text()} class={picker.class_name()}")

    # 1) 搜索框
    search = picker.child_window(control_type="Edit", class_name="mmui::XValidatorTextEdit")
    if not search.exists(timeout=0.2):
        search = picker.child_window(title="搜索", control_type="Edit")
    if not search.exists(timeout=0.2):
        edits = picker.descendants(control_type="Edit")
        search = edits[0] if edits else None
    if search is None or (hasattr(search, "exists") and not search.exists(timeout=0.2)):
        log("未找到搜索框")
        return 2

    log("命中搜索框，清空并输入目标")
    search.click_input()
    try:
        search.type_keys("^a{BACKSPACE}", set_foreground=True)
    except Exception:
        pyautogui.hotkey("ctrl", "a", _pause=False)
        pyautogui.press("backspace", _pause=False)
    try:
        search.set_text(target)
    except Exception:
        pyautogui.typewrite(target, interval=0.03)
    time.sleep(0.35)

    # 2) 勾选目标
    try:
        boxes = picker.descendants(control_type="CheckBox")
    except Exception:
        boxes = []
    boxes = sorted(
        boxes,
        key=lambda b: 0
        if b.class_name() in {"mmui::SearchContactCellView", "mmui::SPSelectionContactRow"}
        else 1,
    )

    chosen = None
    for b in boxes:
        try:
            name = (b.window_text() or "").strip()
        except Exception:
            continue
        if name == target:
            chosen = b
            break
    if chosen is None:
        for b in boxes:
            try:
                name = (b.window_text() or "").strip()
            except Exception:
                continue
            if target in name:
                chosen = b
                break
    if chosen is None:
        log("未找到匹配复选框")
        return 3

    log(f"命中复选框: {chosen.window_text()} class={chosen.class_name()}")
    chosen.click_input()
    time.sleep(0.15)

    # 3) 发送按钮
    send_btn = picker.child_window(auto_id="confirm_btn", control_type="Button")
    if not send_btn.exists(timeout=0.2):
        send_btn = picker.child_window(control_type="Button", title="发送")
    if not send_btn.exists(timeout=0.2):
        send_btn = picker.child_window(control_type="Button", title_re="发送.*")
    if not send_btn.exists(timeout=0.2):
        log("未找到发送按钮，回退 Alt+S")
        pyautogui.hotkey("alt", "s", _pause=False)
        return 0

    log("点击发送按钮")
    send_btn.click_input()
    log("完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
