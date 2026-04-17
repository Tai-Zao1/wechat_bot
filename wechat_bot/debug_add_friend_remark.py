#!/usr/bin/env python3
"""最小调试：按手机号打开添加好友弹窗，打印昵称提取与备注写入信息。"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def log(message: str) -> None:
    print(f"[DEBUG] {message}", flush=True)


def dump_texts(label: str, container) -> None:
    try:
        nodes = container.descendants(control_type="Text")
    except Exception as exc:
        log(f"{label} 文本读取失败: {exc}")
        return
    log(f"{label} 文本数量: {len(nodes)}")
    for idx, node in enumerate(nodes):
        try:
            text = str(node.window_text() or "").strip()
            aid = str(node.automation_id() or "").strip()
            cls = str(node.class_name() or "").strip()
        except Exception:
            text = ""
            aid = ""
            cls = ""
        if not text:
            continue
        log(f"{label} Text[{idx}] text={text!r} auto_id={aid!r} class={cls!r}")


def dump_edits(label: str, container) -> None:
    try:
        nodes = container.descendants(control_type="Edit")
    except Exception as exc:
        log(f"{label} 编辑框读取失败: {exc}")
        return
    log(f"{label} 编辑框数量: {len(nodes)}")
    for idx, node in enumerate(nodes):
        try:
            title = str(node.window_text() or "").strip()
            cls = str(node.class_name() or "").strip()
            value = ""
            try:
                value = str(node.get_value() or "").strip()
            except Exception:
                pass
            rect = node.rectangle()
            rect_text = f"({rect.left},{rect.top},{rect.right},{rect.bottom})"
        except Exception:
            title = ""
            cls = ""
            value = ""
            rect_text = "unknown"
        log(f"{label} Edit[{idx}] title={title!r} class={cls!r} value={value!r} rect={rect_text}")


def dump_buttons(label: str, container) -> None:
    try:
        nodes = container.descendants(control_type="Button")
    except Exception as exc:
        log(f"{label} 按钮读取失败: {exc}")
        return
    log(f"{label} 按钮数量: {len(nodes)}")
    for idx, node in enumerate(nodes):
        try:
            text = str(node.window_text() or "").strip()
            aid = str(node.automation_id() or "").strip()
            cls = str(node.class_name() or "").strip()
        except Exception:
            text = ""
            aid = ""
            cls = ""
        log(f"{label} Button[{idx}] text={text!r} auto_id={aid!r} class={cls!r}")


def wait_exists(control, timeout_s: float, step: float = 0.1) -> bool:
    deadline = time.time() + max(timeout_s, 0.1)
    while time.time() < deadline:
        try:
            if control.exists(timeout=0.05):
                return True
        except Exception:
            pass
        time.sleep(step)
    return False


def find_add_friend_window(timeout_s: float = 4.0):
    from pywinauto import Desktop

    ui = Desktop(backend="uia")
    deadline = time.time() + max(timeout_s, 0.1)
    while time.time() < deadline:
        candidates = []
        # 1) 先找真正的 Window 节点
        try:
            for win in ui.windows():
                try:
                    title = str(win.window_text() or "").strip()
                    cls = str(win.class_name() or "").strip()
                    if cls == "mmui::AddFriendWindow" or title == "添加朋友":
                        candidates.append(win)
                except Exception:
                    continue
        except Exception:
            pass
        # 2) 再按你提供的结构，从“添加朋友”标题文本往上取窗口祖先
        try:
            title_nodes = ui.descendants(control_type="Text", title="添加朋友")
        except Exception:
            title_nodes = []
        for node in title_nodes:
            current = node
            for _ in range(6):
                try:
                    current = current.parent()
                except Exception:
                    current = None
                if current is None:
                    break
                try:
                    current_type = str(current.friendly_class_name() or "").strip()
                except Exception:
                    current_type = ""
                try:
                    current_title = str(current.window_text() or "").strip()
                except Exception:
                    current_title = ""
                try:
                    current_cls = str(current.class_name() or "").strip()
                except Exception:
                    current_cls = ""
                if current_type == "Window" or current_cls == "mmui::AddFriendWindow" or current_title == "添加朋友":
                    candidates.append(current)
                    break
        if candidates:
            dedup = []
            seen = set()
            for item in candidates:
                try:
                    key = int(getattr(item, "handle", 0) or 0)
                except Exception:
                    key = id(item)
                if key in seen:
                    continue
                seen.add(key)
                dedup.append(item)
            dedup.sort(key=lambda item: item.rectangle().width() * item.rectangle().height(), reverse=True)
            target = dedup[0]
            try:
                handle = int(getattr(target, "handle", 0) or 0)
            except Exception:
                handle = 0
            if handle:
                return ui.window(handle=handle)
            return target
        time.sleep(0.1)
    return None


def dump_add_friend_candidates() -> None:
    from pywinauto import Desktop

    ui = Desktop(backend="uia")
    try:
        wins = ui.windows()
    except Exception as exc:
        log(f"桌面窗口读取失败: {exc}")
        return
    for idx, win in enumerate(wins):
        try:
            title = str(win.window_text() or "").strip()
            cls = str(win.class_name() or "").strip()
            if "添加" in title or cls == "mmui::AddFriendWindow":
                log(f"候选窗口[{idx}] title={title!r} class={cls!r}")
        except Exception:
            continue
    try:
        title_nodes = ui.descendants(control_type="Text", title="添加朋友")
    except Exception:
        title_nodes = []
    for idx, node in enumerate(title_nodes):
        try:
            parent = node.parent()
            parent_title = str(parent.window_text() or "").strip() if parent is not None else ""
            parent_cls = str(parent.class_name() or "").strip() if parent is not None else ""
        except Exception:
            parent_title = ""
            parent_cls = ""
        log(f"标题文本[{idx}] title='添加朋友' parent_title={parent_title!r} parent_class={parent_cls!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description="调试添加好友备注填充")
    parser.add_argument("--phone", required=True, help="手机号")
    parser.add_argument("--greetings", default=None, help="招呼语")
    parser.add_argument("--maximize", action="store_true", help="微信主窗口最大化")
    parser.add_argument("--auto-close", action="store_true", help="调试结束后自动关闭窗口")
    args = parser.parse_args()

    from pyweixin.WeChatTools import Navigator
    from pyweixin.Uielements import Buttons, Groups, Lists, SideBar, Windows

    from wechat_bot.add_friend_by_phone import (
        extract_contact_nickname,
        format_phone_for_remark,
        normalize_phone,
    )

    phone = normalize_phone(args.phone)
    buttons = Buttons()
    groups = Groups()
    lists = Lists()
    side_bar = SideBar()
    windows = Windows()
    main_window = None
    add_friend_pane = None
    verify_friend_window = None

    try:
        log(f"开始调试手机号: {phone}")
        main_window = Navigator.open_weixin(is_maximize=args.maximize)
        log("已打开微信主窗口")

        chat_button = main_window.child_window(**side_bar.Chats)
        if wait_exists(chat_button, timeout_s=1.0):
            chat_button.click_input()
            log("已点击左侧“微信”聊天按钮")
        else:
            log("未找到左侧“微信”聊天按钮")

        quick_actions_button = main_window.child_window(**buttons.QuickActionsButton)
        if not wait_exists(quick_actions_button, timeout_s=1.5):
            dump_buttons("微信主窗口", main_window)
            return 10
        quick_actions_button.click_input()
        log("已点击“快捷操作”按钮")

        quick_actions_list = main_window.child_window(**lists.QuickActionsList)
        if not wait_exists(quick_actions_list, timeout_s=1.5):
            dump_texts("微信主窗口", main_window)
            dump_buttons("微信主窗口", main_window)
            return 11
        log("已出现快捷操作列表")
        dump_buttons("快捷操作列表", quick_actions_list)
        dump_texts("快捷操作列表", quick_actions_list)

        add_friend_button = quick_actions_list.child_window(**buttons.AddNewFriendButon)
        if wait_exists(add_friend_button, timeout_s=0.5):
            add_friend_button.click_input()
            log("已点击快捷操作里的“添加朋友”按钮")
        else:
            log("快捷操作列表中未直接命中“添加朋友”，回退键盘导航")
            quick_actions_list.type_keys("{UP}" * 2)
            quick_actions_list.type_keys("{ENTER}")
            log("已通过键盘导航触发“添加朋友”")

        add_friend_pane = find_add_friend_window(timeout_s=4.0)
        if add_friend_pane is None:
            log("未捕获到“添加朋友”窗口")
            dump_add_friend_candidates()
            return 12
        log(f"已命中添加好友弹窗 title={add_friend_pane.window_text()!r} class={add_friend_pane.class_name()!r}")
        log("已打开添加好友弹窗")
        dump_texts("添加好友弹窗", add_friend_pane)
        dump_edits("添加好友弹窗", add_friend_pane)
        edit = add_friend_pane.child_window(control_type="Edit")
        edit.set_text("")
        edit.set_text(phone)
        edit.type_keys("{ENTER}")
        log(f"已输入手机号并回车: {phone}")
        time.sleep(1.5)

        contact_profile_view = add_friend_pane.child_window(**groups.ContactProfileViewGroup)
        if not wait_exists(contact_profile_view, timeout_s=3.0):
            log("未识别到联系人资料面板")
            dump_texts("添加好友弹窗", add_friend_pane)
            dump_edits("添加好友弹窗", add_friend_pane)
            return 1

        dump_texts("搜索结果资料面板", contact_profile_view)
        add_to_contact = contact_profile_view.child_window(**buttons.AddToContactsButton)
        if not wait_exists(add_to_contact, timeout_s=1.5):
            log("未找到“添加到通讯录”按钮")
            dump_texts("搜索结果资料面板", contact_profile_view)
            return 2

        log("准备点击“添加到通讯录”")
        add_to_contact.click_input()
        time.sleep(0.5)
        verify_friend_window = find_add_friend_window(timeout_s=0.1)
        if verify_friend_window is not None:
            log("警告: 当前仍命中添加好友弹窗，尚未切到申请窗口")
        from pywinauto import Desktop
        ui = Desktop(backend="uia")
        verify_friend_window = ui.window(**windows.VerifyFriendWindow)
        if not wait_exists(verify_friend_window, timeout_s=3.0):
            log("未出现“申请添加朋友”窗口")
            dump_texts("添加好友弹窗", add_friend_pane)
            dump_edits("添加好友弹窗", add_friend_pane)
            return 3

        log("已出现“申请添加朋友”窗口")
        dump_texts("申请添加朋友窗口", verify_friend_window)
        dump_edits("申请添加朋友窗口", verify_friend_window)

        nickname = extract_contact_nickname(contact_profile_view, verify_friend_window)
        phone_text = format_phone_for_remark(phone)
        remark_text = f"{nickname}{phone_text}" if nickname else phone_text
        log(f"提取昵称: {nickname!r}")
        log(f"准备写入备注: {remark_text!r}")

        request_content_edit = verify_friend_window.child_window(control_type="Edit", found_index=0)
        remark_edit = verify_friend_window.child_window(control_type="Edit", found_index=1)
        if args.greetings is not None:
            request_content_edit.set_text(args.greetings)
            log(f"已写入招呼语: {args.greetings!r}")
        remark_edit.set_text(remark_text)
        log("已写入备注框，但未点击确定")

        if args.auto_close:
            return 0

        log("窗口已保留，请人工核对；确认后手动关闭窗口，或按 Ctrl+C 结束脚本")
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        log("收到 Ctrl+C，结束调试")
        return 0
    except Exception as exc:
        log(f"异常: {exc}")
        return 9
    finally:
        if args.auto_close:
            try:
                if verify_friend_window is not None:
                    verify_friend_window.close()
            except Exception:
                pass
            try:
                if add_friend_pane is not None:
                    add_friend_pane.close()
            except Exception:
                pass
            try:
                if main_window is not None:
                    main_window.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
