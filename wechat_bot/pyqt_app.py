#!/usr/bin/env python3
"""PyQt 控制台：将 wechat_bot 脚本作为按钮功能。"""

from __future__ import annotations

import io
import importlib
import os
import re
import subprocess
import sys
import tempfile
import threading
import traceback
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from PyQt5.QtCore import QProcess, QProcessEnvironment, QSettings, QSize, QThread, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpacerItem,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from wechat_bot.common import (
    BOT_GUI_SETTINGS_APP,
    BOT_GUI_SETTINGS_ORG,
    DEFAULT_ADD_FRIEND_INTERVAL_MAX_MINUTES,
    DEFAULT_ADD_FRIEND_INTERVAL_MIN_MINUTES,
    DEFAULT_ADD_FRIEND_SOURCE_TEXT,
    DEFAULT_AUTO_REPLY_TEXT,
    DEFAULT_TIMED_SEND_MESSAGE,
    MIN_ADD_FRIEND_INTERVAL_MINUTES,
    MIN_WECHAT_VERSION as DEFAULT_MIN_WECHAT_VERSION,
    SHORTLINK_RULE_FILENAME,
    load_json_dict,
    write_json_file,
)
from wechat_bot.core import get_bot_app_root, get_bot_data_dir, get_bot_logs_dir
from wechat_bot.services import (
    DEFAULT_BAILIAN_ENDPOINT,
    DEFAULT_BAILIAN_SYSTEM_PROMPT,
    fetch_friend_names,
    fetch_friend_profiles,
    get_cached_friend_names,
    get_cached_friend_profiles,
    run_timed_send_loop,
)
from wechat_bot.scripts import resolve_script_spec

try:
    import win32gui  # type: ignore
except Exception:  # pragma: no cover - non-Windows env
    win32gui = None

try:
    import win32api  # type: ignore
except Exception:  # pragma: no cover - non-Windows env
    win32api = None


BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from client_api import WeChatAIClient


def decode_process_output(raw: bytes) -> str:
    """Decode child process output with common Windows encodings."""
    if not raw:
        return ""
    for encoding in ("utf-8", "gbk", "cp936", "utf-16-le"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def ensure_win32com_cache_writable() -> None:
    """修复打包环境下 win32com gencache 指向 C:\\Windows 的问题。"""
    try:
        base = get_bot_app_root()
        gen_root = base / "gen_py"
        pyver = f"{sys.version_info.major}.{sys.version_info.minor}"
        target = gen_root / pyver
        target.mkdir(parents=True, exist_ok=True)
        init_file = target / "__init__.py"
        if not init_file.exists():
            init_file.write_text("# auto-created for win32com gencache\n", encoding="utf-8")

        import win32com  # type: ignore
        # 关键：必须先改 __gen_path__，再导入 win32com.client.gencache
        win32com.__gen_path__ = str(gen_root)
        os.environ["PYWIN32_GEN_PATH"] = str(gen_root)

        import importlib
        from win32com.client import gencache  # type: ignore
        importlib.reload(gencache)
        gencache.is_readonly = False
    except Exception:
        # 这里不抛出，后续由真实导入错误给出详细信息
        pass


class _SignalWriter(io.TextIOBase):
    """把脚本标准输出转成 Qt 信号，便于在 GUI 日志面板展示。"""
    def __init__(self, emit_func: Callable[[str], None]) -> None:
        super().__init__()
        self._emit = emit_func

    def write(self, s: str) -> int:
        text = (s or "").rstrip()
        if text:
            self._emit(text)
        return len(s or "")

    def flush(self) -> None:
        return None


class EmbeddedWorker(QThread):
    """在子线程里以内嵌方式执行脚本，避免阻塞主界面。"""
    log = pyqtSignal(str)
    done = pyqtSignal(str, int)

    def __init__(self, script_name: str, script_args: list[str]) -> None:
        super().__init__()
        self.script_name = script_name
        self.script_args = script_args

    def run(self) -> None:
        writer = _SignalWriter(self.log.emit)
        code = 1
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                code = run_embedded_script(self.script_name, self.script_args)
        except Exception as exc:
            self.log.emit(f"[EMBED] 运行异常: {exc}")
            code = 1
        self.done.emit(self.script_name, int(code))


class FriendListWorker(QThread):
    """异步加载好友列表，避免 UI 卡顿。"""
    log = pyqtSignal(str)
    loaded = pyqtSignal(list)
    done = pyqtSignal(int)

    def __init__(self, force_refresh: bool = False) -> None:
        super().__init__()
        self.force_refresh = force_refresh

    def run(self) -> None:
        try:
            profiles = fetch_friend_profiles(log=self.log.emit, force_refresh=self.force_refresh)
            self.loaded.emit(profiles)
            self.done.emit(0)
        except Exception as exc:
            self.log.emit(f"加载好友列表失败: {exc}")
            self.done.emit(1)


class TimedSendWorker(QThread):
    """异步执行定时群发任务。"""
    log = pyqtSignal(str)
    done = pyqtSignal(int)

    def __init__(self, friends: list[str], message: str, interval_min: float = 5.0, interval_max: float = 10.0) -> None:
        super().__init__()
        self.friends = friends
        self.message = message
        self.interval_min = interval_min
        self.interval_max = interval_max
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        try:
            code = run_timed_send_loop(
                friends=self.friends,
                message=self.message,
                interval_min=self.interval_min,
                interval_max=self.interval_max,
                stop_event=self._stop_event,
                log=self.log.emit,
            )
            self.done.emit(int(code))
        except Exception as exc:
            self.log.emit(f"定时群发异常: {exc}")
            self.done.emit(1)


class ApiRequestWorker(QThread):
    """把耗时接口调用包装到线程中执行。"""
    success = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, func: Callable[[], object]) -> None:
        super().__init__()
        self._func = func

    def run(self) -> None:
        try:
            result = self._func()
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.success.emit(result)


class LoginWindow(QDialog):
    """登录成功前不允许进入主功能页。"""

    def __init__(self, parent: QWidget | None, client: WeChatAIClient, settings: QSettings) -> None:
        super().__init__(parent)
        self.client = client
        self.settings = settings
        self.api_request_worker: ApiRequestWorker | None = None
        self.staff_info: dict | None = None
        self.user_info: dict | None = None

        self.setWindowTitle("运行模式")
        self.setModal(True)
        self.resize(540, 660)
        self.setObjectName("loginDialog")

        saved_mode = str(self.settings.value("run/mode", "api", type=str) or "api").strip().lower()
        if saved_mode not in {"api", "local"}:
            saved_mode = "api"
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("API 模式（后端登录/回复/同步）", "api")
        self.mode_combo.addItem("本地模式（本地百炼回复/本地好友缓存）", "local")
        self.mode_combo.setCurrentIndex(1 if saved_mode == "local" else 0)

        saved_username = self.settings.value("api/username", "", type=str) or self.settings.value("api/phone", "", type=str)
        self.username_input = QLineEdit(saved_username)
        self.username_input.setPlaceholderText("请输入用户名")
        self.settings.remove("api/password")
        self.password_input = QLineEdit("")
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setPlaceholderText("请输入登录密码")
        self.remember_checkbox = QCheckBox("记住用户名")
        self.remember_checkbox.setChecked(True)
        self.bailian_app_id_input = QLineEdit(str(self.settings.value("local/bailian_app_id", "", type=str) or ""))
        self.bailian_app_id_input.setPlaceholderText("请输入阿里百炼应用 appId")
        self.bailian_api_key_input = QLineEdit(str(self.settings.value("local/bailian_api_key", "", type=str) or ""))
        self.bailian_api_key_input.setEchoMode(QLineEdit.Password)
        self.bailian_api_key_input.setPlaceholderText("请输入阿里百炼 apiKey")
        self.bailian_system_input = QTextEdit(str(self.settings.value("local/bailian_system", DEFAULT_BAILIAN_SYSTEM_PROMPT, type=str) or DEFAULT_BAILIAN_SYSTEM_PROMPT))
        self.bailian_system_input.setMinimumHeight(80)
        self.bailian_endpoint_input = QLineEdit(str(self.settings.value("local/bailian_endpoint", DEFAULT_BAILIAN_ENDPOINT, type=str) or DEFAULT_BAILIAN_ENDPOINT))
        self.bailian_endpoint_input.setPlaceholderText("百炼 endpoint，使用 {app_id} 占位")
        self.message_label = QLabel("API 模式需要后端登录；本地模式不登录后端，聊天回复走本地阿里百炼配置，好友列表只保存在本机。")
        self.message_label.setWordWrap(True)
        self.message_label.setProperty("role", "pageDesc")
        self.login_btn = QPushButton("进入控制台")
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setObjectName("ghostButton")
        self.brand_title = QLabel("Wechat 机器人平台")
        self.brand_subtitle = QLabel("欢迎登录")
        self.footer_label = QLabel("PyWechat Bot Console")
        self.account_icon = QLabel("账号")
        self.password_icon = QLabel("密码")

        self._build_ui()
        self._apply_styles()

        self.login_btn.clicked.connect(self.login)
        self.cancel_btn.clicked.connect(self.reject)
        self.password_input.returnPressed.connect(self.login)
        self.mode_combo.currentIndexChanged.connect(self._update_mode_ui)
        self._update_mode_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 28, 34, 28)
        root.setSpacing(0)
        root.addSpacerItem(QSpacerItem(20, 18))

        card = QWidget()
        card.setObjectName("loginCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(34, 34, 34, 28)
        card_layout.setSpacing(16)

        self.brand_subtitle.setObjectName("brandSubtitle")
        self.brand_title.setObjectName("brandTitle")
        card_layout.addWidget(self.brand_subtitle)
        card_layout.addWidget(self.brand_title)
        card_layout.addWidget(self.message_label)
        card_layout.addSpacing(6)

        self.mode_row = self._build_combo_row(QLabel("模式"), self.mode_combo)
        self.username_row = self._build_input_row(self.account_icon, self.username_input)
        self.password_row = self._build_input_row(self.password_icon, self.password_input)
        self.bailian_app_id_row = self._build_input_row(QLabel("AppID"), self.bailian_app_id_input)
        self.bailian_api_key_row = self._build_input_row(QLabel("Key"), self.bailian_api_key_input)
        self.bailian_endpoint_row = self._build_input_row(QLabel("Endpoint"), self.bailian_endpoint_input)
        card_layout.addWidget(self.mode_row)
        card_layout.addWidget(self.username_row)
        card_layout.addWidget(self.password_row)
        card_layout.addWidget(self.bailian_app_id_row)
        card_layout.addWidget(self.bailian_api_key_row)
        card_layout.addWidget(self.bailian_system_input)
        card_layout.addWidget(self.bailian_endpoint_row)

        remember_row = QHBoxLayout()
        remember_row.setContentsMargins(0, 0, 0, 0)
        remember_row.addWidget(self.remember_checkbox)
        remember_row.addStretch(1)
        card_layout.addLayout(remember_row)

        self.login_btn.setDefault(True)
        card_layout.addWidget(self.login_btn)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.addStretch(1)
        action_row.addWidget(self.cancel_btn)
        card_layout.addLayout(action_row)

        self.footer_label.setObjectName("footerLabel")
        self.footer_label.setAlignment(Qt.AlignCenter)
        card_layout.addSpacing(4)
        card_layout.addWidget(self.footer_label)

        root.addStretch(1)
        root.addWidget(card)
        root.addStretch(1)

    def _build_input_row(self, icon_label: QLabel, input_widget: QLineEdit) -> QWidget:
        row = QWidget()
        row.setObjectName("inputRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(10)
        icon_label.setObjectName("inputIcon")
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setFixedWidth(28)
        layout.addWidget(icon_label)
        layout.addWidget(input_widget)
        return row

    def _build_combo_row(self, icon_label: QLabel, input_widget: QComboBox) -> QWidget:
        row = QWidget()
        row.setObjectName("inputRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(10)
        icon_label.setObjectName("inputIcon")
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setFixedWidth(28)
        layout.addWidget(icon_label)
        layout.addWidget(input_widget)
        return row

    def _current_mode(self) -> str:
        return str(self.mode_combo.currentData() or "api")

    def _update_mode_ui(self) -> None:
        is_api = self._current_mode() == "api"
        for widget in (self.username_row, self.password_row, self.remember_checkbox):
            widget.setVisible(is_api)
        for widget in (
            self.bailian_app_id_row,
            self.bailian_api_key_row,
            self.bailian_system_input,
            self.bailian_endpoint_row,
        ):
            widget.setVisible(not is_api)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QDialog#loginDialog {
                background: transparent;
            }
            QWidget#loginCard {
                background: rgba(255, 255, 255, 0.94);
                border: 1px solid rgba(255, 255, 255, 0.85);
                border-radius: 22px;
            }
            QLabel#brandSubtitle {
                color: #1f2937;
                font-size: 18px;
                font-weight: 700;
            }
            QLabel#brandTitle {
                color: #1677ff;
                font-size: 24px;
                font-weight: 800;
            }
            QLabel[role="pageDesc"] {
                color: #6b7280;
                font-size: 13px;
                line-height: 1.4;
            }
            QWidget#inputRow {
                background: #ffffff;
                border: 1px solid #d7dfeb;
                border-radius: 10px;
                min-height: 48px;
            }
            QLabel#inputIcon {
                color: #8b95a7;
                font-size: 13px;
                font-weight: 700;
            }
            QLineEdit {
                border: none;
                background: transparent;
                color: #111827;
                font-size: 14px;
                padding: 0;
                min-height: 46px;
            }
            QLineEdit::placeholder {
                color: #9ca3af;
            }
            QCheckBox {
                color: #4b5563;
                font-size: 13px;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
            }
            QCheckBox::indicator:unchecked {
                border: 1px solid #c9d3e3;
                border-radius: 4px;
                background: #ffffff;
            }
            QCheckBox::indicator:checked {
                border: 1px solid #1677ff;
                border-radius: 4px;
                background: #1677ff;
            }
            QPushButton {
                min-height: 46px;
                border-radius: 10px;
                font-size: 15px;
                font-weight: 700;
            }
            QPushButton:disabled {
                background: #9ec5fe;
                color: #eff6ff;
                border: none;
            }
            QPushButton#ghostButton {
                min-height: 36px;
                padding: 0 18px;
                background: transparent;
                border: 1px solid #d7dfeb;
                color: #6b7280;
            }
            QPushButton#ghostButton:hover {
                background: #f8fbff;
                border-color: #b7c6da;
            }
            QPushButton:not(#ghostButton) {
                background: #1677ff;
                border: none;
                color: #ffffff;
            }
            QPushButton:not(#ghostButton):hover {
                background: #0f6ae6;
            }
            QLabel#footerLabel {
                color: #8a94a6;
                font-size: 12px;
            }
            """
        )

    def _set_busy(self, busy: bool) -> None:
        self.login_btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(not busy)
        self.remember_checkbox.setEnabled(not busy)
        self.username_input.setEnabled(not busy)
        self.password_input.setEnabled(not busy)
        self.mode_combo.setEnabled(not busy)
        self.bailian_app_id_input.setEnabled(not busy)
        self.bailian_api_key_input.setEnabled(not busy)
        self.bailian_system_input.setEnabled(not busy)
        self.bailian_endpoint_input.setEnabled(not busy)

    def _finish_request(self) -> None:
        self._set_busy(False)

    def _handle_error(self, action_name: str, message: str) -> None:
        self._set_busy(False)
        self.staff_info = None
        self.user_info = None
        QMessageBox.warning(self, f"{action_name}失败", message)

    def _run_request(self, func: Callable[[], object], success_handler: Callable[[object], None], action_name: str) -> None:
        if self.api_request_worker is not None and self.api_request_worker.isRunning():
            QMessageBox.information(self, "请稍候", "当前已有请求在执行。")
            return
        self._set_busy(True)
        worker = ApiRequestWorker(func)
        worker.success.connect(success_handler)
        worker.success.connect(lambda _result: self._finish_request())
        worker.error.connect(lambda message: self._handle_error(action_name, message))
        worker.finished.connect(lambda: setattr(self, "api_request_worker", None))
        self.api_request_worker = worker
        worker.start()

    def login(self) -> None:
        mode = self._current_mode()
        self.settings.setValue("run/mode", mode)
        if mode == "local":
            app_id = self.bailian_app_id_input.text().strip()
            api_key = self.bailian_api_key_input.text().strip()
            system_prompt = self.bailian_system_input.toPlainText().strip() or DEFAULT_BAILIAN_SYSTEM_PROMPT
            endpoint = self.bailian_endpoint_input.text().strip() or DEFAULT_BAILIAN_ENDPOINT
            if not app_id:
                QMessageBox.warning(self, "参数错误", "请输入阿里百炼应用 appId。")
                return
            if not api_key:
                QMessageBox.warning(self, "参数错误", "请输入阿里百炼 apiKey。")
                return
            self.settings.setValue("local/bailian_app_id", app_id)
            self.settings.setValue("local/bailian_api_key", api_key)
            self.settings.setValue("local/bailian_system", system_prompt)
            self.settings.setValue("local/bailian_endpoint", endpoint)
            self.settings.remove("api/password")
            self.accept()
            return

        username = self.username_input.text().strip()
        password = self.password_input.text()
        if not username:
            QMessageBox.warning(self, "参数错误", "请输入登录用户名。")
            return
        if not password:
            QMessageBox.warning(self, "参数错误", "请输入登录密码。")
            return
        self.client.base_url = self.client.base_url.rstrip("/")
        self.client.save_state()
        if self.remember_checkbox.isChecked():
            self.settings.setValue("api/username", username)
        else:
            self.settings.remove("api/username")
        self.settings.remove("api/phone")
        self.settings.remove("api/remember_password")
        self.settings.remove("api/password")
        self._run_request(
            lambda: self.client.login(username, password, device_id=self.client.device_id),
            self._on_login_success,
            "登录",
        )

    def _on_login_success(self, result: object) -> None:
        payload = result if isinstance(result, dict) else {}
        data = payload.get("data") or {}
        self.staff_info = data.get("staff") if isinstance(data.get("staff"), dict) else None
        self.user_info = data.get("user_info") if isinstance(data.get("user_info"), dict) else None
        if self.user_info is None and isinstance(data.get("user"), dict):
            self.user_info = data.get("user")
        self.password_input.clear()
        self.accept()

    def paintEvent(self, event: Any) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0.0, QColor("#e8f3ff"))
        gradient.setColorAt(0.55, QColor("#f5f9ff"))
        gradient.setColorAt(1.0, QColor("#ddeefe"))
        painter.fillRect(self.rect(), gradient)

        glow_pen = QPen(QColor(255, 255, 255, 90))
        glow_pen.setWidth(1)
        painter.setPen(glow_pen)
        painter.setBrush(QColor(255, 255, 255, 36))
        for rect in (
            (28, 22, 120, 120),
            (self.width() - 170, 54, 136, 136),
            (self.width() // 2 - 90, self.height() - 130, 180, 92),
        ):
            x, y, w, h = rect
            path = QPainterPath()
            path.addEllipse(x, y, w, h)
            painter.drawPath(path)

        super().paintEvent(event)


def run_embedded_script(script_name: str, script_args: list[str]) -> int:
    """在当前进程中执行脚本 main()，用于打包后子命令模式。"""
    spec = resolve_script_spec(script_name)
    if spec is None:
        print(f"[EMBED] 未知脚本: {script_name}")
        return 2

    # 这些脚本会导入 pyweixin -> win32com，先确保 gencache 可写
    if spec.requires_win32com_cache:
        ensure_win32com_cache_writable()

    module = None
    for candidate in spec.module_names:
        try:
            module = importlib.import_module(candidate)
            break
        except ModuleNotFoundError:
            continue
    if module is None:
        print(f"[EMBED] 无法导入模块: {spec.display_name}")
        return 2

    old_argv = sys.argv[:]
    try:
        sys.argv = [spec.script_name] + script_args
        return int(module.main())
    finally:
        sys.argv = old_argv


class MainWindow(QMainWindow):
    NAV_PAGE_COUNT = 3
    DOCK_SIDE = "right"
    DOCK_GAP = 8
    DOCK_FOLLOW_HEIGHT = True
    MIN_WECHAT_VERSION = DEFAULT_MIN_WECHAT_VERSION

    def __init__(
        self,
        api_client: WeChatAIClient | None = None,
        on_auth_expired: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("PyWechat Bot 控制台")
        self.resize(420, 800)
        self.settings = QSettings(BOT_GUI_SETTINGS_ORG, BOT_GUI_SETTINGS_APP)
        self.api_client = api_client
        self.on_auth_expired = on_auth_expired
        self.auth_expired_handling = False
        self.run_mode = str(self.settings.value("run/mode", "api", type=str) or "api").strip().lower()
        if self.run_mode not in {"api", "local"}:
            self.run_mode = "api"

        self.auto_process: QProcess | None = None
        self.add_friend_process: QProcess | None = None
        self.child_processes: list[QProcess] = []
        self.embedded_workers: list[EmbeddedWorker] = []
        self.friend_list_worker: FriendListWorker | None = None
        self.timed_send_worker: TimedSendWorker | None = None
        self.timed_send_schedule_timer: QTimer | None = None
        self.timed_send_pending_friends: list[str] = []
        self.timed_send_pending_message = ""
        self.narrator_countdown_box: QMessageBox | None = None
        self.narrator_countdown_timer: QTimer | None = None
        self.narrator_countdown_remaining = 0
        self.wechat_ui_poll_timer: QTimer | None = None
        self.wechat_dock_timer: QTimer | None = None
        self.wechat_dock_hwnd: int | None = None
        self.wechat_dock_minimized_by_wechat = False
        self.auto_start_pending = False

        self.reply_input = QLineEdit(DEFAULT_AUTO_REPLY_TEXT)
        self.shortlink_rules_edit = QTextEdit()
        self.shortlink_rules_edit.setPlaceholderText("每行一条：关键词|小程序短链（命中后先发到文件传输助手，再转发卡片）")
        self.shortlink_rules_edit.setMinimumHeight(110)
        self.save_shortlink_rules_btn = QPushButton("保存短链配置")

        self.desktop_dir = Path.home() / "Desktop"
        self.app_root_dir = get_bot_app_root()
        source_text = DEFAULT_ADD_FRIEND_SOURCE_TEXT if self.run_mode == "api" else "本地模式可上传Excel手机号添加；API获取添加需切换API模式"
        self.add_friend_source_input = QLineEdit(source_text)
        self.add_friend_source_input.setReadOnly(True)
        self.greetings_input = QLineEdit("")
        self.interval_min_input = QLineEdit(str(int(DEFAULT_ADD_FRIEND_INTERVAL_MIN_MINUTES)))
        self.interval_max_input = QLineEdit(str(int(DEFAULT_ADD_FRIEND_INTERVAL_MAX_MINUTES)))
        self.timed_message_input = QLineEdit(DEFAULT_TIMED_SEND_MESSAGE)
        self.timed_start_time_input = QLineEdit(datetime.now().strftime("%H:%M"))
        self.friend_list_widget = QListWidget()
        self.friend_list_widget.setMinimumHeight(180)
        self.friend_list_widget.setSelectionMode(QListWidget.NoSelection)
        self.friend_list_widget.setIconSize(QSize(28, 28))
        self.friend_profiles: list[dict[str, str]] = []
        self.friend_sync_worker: ApiRequestWorker | None = None
        self.load_friends_btn = QPushButton("加载好友列表")
        self.check_all_friends_btn = QPushButton("全选")
        self.uncheck_all_friends_btn = QPushButton("全不选")
        self.start_timed_send_btn = QPushButton("启动定时群发")
        self.stop_timed_send_btn = QPushButton("停止定时群发")
        self.stop_timed_send_btn.setEnabled(False)
        self.start_dock_btn = QPushButton("启动停靠")
        self.stop_dock_btn = QPushButton("停止停靠")
        self.stop_dock_btn.setEnabled(False)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setContextMenuPolicy(Qt.CustomContextMenu)
        self.log_output.customContextMenuRequested.connect(self._show_log_context_menu)

        self.current_wxid = self._resolve_current_wxid()
        self.log_dir = get_bot_logs_dir(self.current_wxid)
        self.current_log_date = datetime.now().strftime("%Y-%m-%d")
        self.log_file_path = self._build_log_file_path(self.current_wxid)

        self.start_auto_btn = QPushButton("启动自动回复")
        self.stop_auto_btn = QPushButton("停止实时自动回复")
        self.stop_auto_btn.setEnabled(False)

        self._build_ui()
        if self.run_mode == "local":
            self.start_add_api_btn.setEnabled(False)
            self.stop_add_btn.setEnabled(False)
        self._apply_style()
        self._init_side_menu_icons()
        self._restore_nav_index()
        self._load_shortlink_rules_to_editor()
        self.append_log(f"GUI日志文件: {self.log_file_path}")
        self.append_log(f"当前运行模式: {'API模式' if self.run_mode == 'api' else '本地模式'}")
        self._load_friend_list_from_cache_on_startup()
        self.append_log("如需最新好友数据，请点击“加载好友列表”实时刷新。")

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                font-family: "Segoe UI", "Microsoft YaHei";
                font-size: 13px;
            }
            QMainWindow {
                background: #f3f6fb;
            }
            QGroupBox {
                border: 1px solid #d9e2ef;
                border-radius: 10px;
                margin-top: 10px;
                padding: 10px 10px 12px 10px;
                background: #ffffff;
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                top: 2px;
                color: #1f2d3d;
            }
            QLineEdit, QTextEdit {
                border: 1px solid #cbd6e2;
                border-radius: 8px;
                padding: 6px 8px;
                background: #ffffff;
            }
            QPushButton {
                border: 1px solid #2f6feb;
                border-radius: 8px;
                background: #2f6feb;
                color: #ffffff;
                padding: 4px 10px;
                min-height: 28px;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton:hover {
                background: #255fcc;
            }
            QPushButton:disabled {
                background: #9db5df;
                border-color: #9db5df;
            }
            QPushButton[variant="secondary"] {
                background: #ffffff;
                color: #255fcc;
            }
            QPushButton[variant="secondary"]:hover {
                background: #eef4ff;
            }
            QPushButton[variant="danger"] {
                border-color: #d14a4a;
                background: #d14a4a;
            }
            QPushButton[variant="danger"]:hover {
                background: #b93f3f;
            }
            QListWidget#sideMenu {
                border: 1px solid #d9e2ef;
                border-radius: 10px;
                background: #ffffff;
                padding: 4px;
            }
            QListWidget#sideMenu::item {
                border-radius: 7px;
                padding: 8px;
                margin: 2px 2px;
            }
            QListWidget#sideMenu::item:selected {
                background: #2f6feb;
                color: #ffffff;
            }
            """
        )

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(10)

        menu_widget = QWidget()
        menu_layout = QVBoxLayout(menu_widget)
        menu_layout.setContentsMargins(0, 0, 0, 0)
        menu_layout.setSpacing(8)
        menu_title = QLabel("功能菜单")
        menu_title.setStyleSheet("font-size:14px;font-weight:700;color:#1f2d3d;")
        self.nav_list = QListWidget()
        self.nav_list.setObjectName("sideMenu")
        self.nav_list.setMinimumWidth(110)
        self.nav_list.setMaximumWidth(150)
        self.nav_list.addItems(["微信控制与自动回复", "好友定时群发", "批量加好友"])
        menu_layout.addWidget(menu_title)
        menu_layout.addWidget(self.nav_list, 1)
        root_layout.addWidget(menu_widget, 0)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)
        self.content_stack = QStackedWidget()

        basic_group = QGroupBox("基础功能")
        basic_layout = QHBoxLayout(basic_group)
        basic_layout.setSpacing(8)
        btn_open = QPushButton("打开微信窗口")
        btn_open.setProperty("variant", "secondary")
        btn_open.clicked.connect(self.run_open_window)
        basic_layout.addWidget(btn_open)
        page_basic = QWidget()
        page_basic_layout = QVBoxLayout(page_basic)
        page_basic_layout.setContentsMargins(0, 0, 0, 0)
        page_basic_layout.setSpacing(8)
        page_basic_layout.addWidget(basic_group)
        auto_group = QGroupBox("实时自动回复")
        auto_layout = QFormLayout(auto_group)
        auto_layout.addRow("默认回复", self.reply_input)
        auto_layout.addRow("运行参数", QLabel("轮询固定 2s；当前会话冷却 2-3s；未读冷却 2-3s"))
        self.save_shortlink_rules_btn.setProperty("variant", "secondary")
        self.save_shortlink_rules_btn.clicked.connect(self.save_shortlink_rules)
        auto_layout.addRow("关键词短链", self.shortlink_rules_edit)
        auto_layout.addRow(self.save_shortlink_rules_btn)

        auto_btn_layout = QHBoxLayout()
        auto_btn_layout.setSpacing(8)
        self.stop_auto_btn.setProperty("variant", "danger")
        self.stop_dock_btn.setProperty("variant", "danger")
        self.start_dock_btn.clicked.connect(self.start_wechat_dock)
        self.stop_dock_btn.clicked.connect(self.stop_wechat_dock)
        self.start_auto_btn.clicked.connect(self.start_auto_reply)
        self.stop_auto_btn.clicked.connect(self.stop_auto_reply)
        auto_btn_layout.addWidget(self.start_auto_btn)
        auto_btn_layout.addWidget(self.stop_auto_btn)
        auto_layout.addRow(auto_btn_layout)
        page_basic_layout.addWidget(auto_group)
        page_basic_layout.addStretch(1)
        self.content_stack.addWidget(page_basic)

        timed_group = QGroupBox("好友定时群发")
        timed_layout = QGridLayout(timed_group)
        timed_layout.setHorizontalSpacing(8)
        timed_layout.setVerticalSpacing(6)
        self.load_friends_btn.setProperty("variant", "secondary")
        self.check_all_friends_btn.setProperty("variant", "secondary")
        self.uncheck_all_friends_btn.setProperty("variant", "secondary")
        self.stop_timed_send_btn.setProperty("variant", "danger")
        self.load_friends_btn.clicked.connect(self.load_friend_list)
        self.check_all_friends_btn.clicked.connect(self.check_all_friends)
        self.uncheck_all_friends_btn.clicked.connect(self.uncheck_all_friends)
        self.start_timed_send_btn.clicked.connect(self.start_timed_send)
        self.stop_timed_send_btn.clicked.connect(self.stop_timed_send)
        timed_layout.addWidget(self.load_friends_btn, 0, 0)
        timed_layout.addWidget(self.check_all_friends_btn, 0, 1)
        timed_layout.addWidget(self.uncheck_all_friends_btn, 0, 2)
        timed_layout.addWidget(QLabel("发送内容"), 1, 0)
        timed_layout.addWidget(self.timed_message_input, 1, 1, 1, 2)
        timed_layout.addWidget(QLabel("开始时间(HH:MM)"), 2, 0)
        timed_layout.addWidget(self.timed_start_time_input, 2, 1, 1, 2)
        timed_layout.addWidget(QLabel("轮询间隔: 随机 5-10 秒"), 3, 0, 1, 3)
        timed_layout.addWidget(self.friend_list_widget, 4, 0, 1, 3)
        timed_btn_row = QHBoxLayout()
        timed_btn_row.setSpacing(8)
        timed_btn_row.addWidget(self.start_timed_send_btn)
        timed_btn_row.addWidget(self.stop_timed_send_btn)
        timed_layout.addLayout(timed_btn_row, 5, 0, 1, 3)
        page_timed = QWidget()
        page_timed_layout = QVBoxLayout(page_timed)
        page_timed_layout.setContentsMargins(0, 0, 0, 0)
        page_timed_layout.setSpacing(8)
        page_timed_layout.addWidget(timed_group)
        page_timed_layout.addStretch(1)
        self.content_stack.addWidget(page_timed)

        add_group = QGroupBox("批量添加好友")
        add_layout = QGridLayout(add_group)
        add_layout.setHorizontalSpacing(8)
        add_layout.setVerticalSpacing(6)
        add_layout.addWidget(QLabel("数据来源"), 0, 0)
        add_layout.addWidget(self.add_friend_source_input, 0, 1, 1, 3)

        add_layout.addWidget(QLabel("打招呼语"), 1, 0)
        add_layout.addWidget(self.greetings_input, 1, 1, 1, 2)

        add_layout.addWidget(QLabel("最小间隔(分钟)"), 2, 0)
        add_layout.addWidget(self.interval_min_input, 2, 1)
        add_layout.addWidget(QLabel("最大间隔(分钟)"), 2, 2)
        add_layout.addWidget(self.interval_max_input, 2, 3)

        self.start_add_excel_btn = QPushButton("上传Excel添加")
        self.start_add_excel_btn.clicked.connect(self.run_add_friends_from_excel)
        self.start_add_api_btn = QPushButton("API获取添加")
        self.start_add_api_btn.clicked.connect(self.run_add_friends_from_api)
        self.stop_add_btn = QPushButton("停止批量添加")
        self.stop_add_btn.clicked.connect(self.stop_add_friends)
        self.stop_add_btn.setEnabled(False)
        add_btn_row = QHBoxLayout()
        add_btn_row.setSpacing(8)
        add_btn_row.addWidget(self.start_add_excel_btn)
        add_btn_row.addWidget(self.start_add_api_btn)
        add_btn_row.addWidget(self.stop_add_btn)
        add_layout.addLayout(add_btn_row, 3, 0, 1, 4)
        page_add = QWidget()
        page_add_layout = QVBoxLayout(page_add)
        page_add_layout.setContentsMargins(0, 0, 0, 0)
        page_add_layout.setSpacing(8)
        page_add_layout.addWidget(add_group)
        page_add_layout.addStretch(1)
        self.content_stack.addWidget(page_add)

        log_group = QGroupBox("运行日志")
        log_layout = QVBoxLayout(log_group)
        log_layout.setContentsMargins(8, 8, 8, 8)
        log_layout.addWidget(self.log_output)
        right_layout.addWidget(self.content_stack, 1)
        right_layout.addWidget(log_group, 1)
        root_layout.addWidget(right_widget, 1)
        self.nav_list.currentRowChanged.connect(self._on_nav_row_changed)

        for btn in (
            btn_open,
            self.start_auto_btn,
            self.stop_auto_btn,
            self.save_shortlink_rules_btn,
            self.load_friends_btn,
            self.check_all_friends_btn,
            self.uncheck_all_friends_btn,
            self.start_timed_send_btn,
            self.stop_timed_send_btn,
            self.start_add_excel_btn,
            self.start_add_api_btn,
            self.stop_add_btn,
        ):
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.setCentralWidget(root)

    def _shortlink_rule_file(self) -> Path:
        return get_bot_data_dir(wxid=self.current_wxid) / SHORTLINK_RULE_FILENAME

    def _local_bailian_args(self) -> list[str]:
        app_id = str(self.settings.value("local/bailian_app_id", "", type=str) or "").strip()
        system_prompt = str(self.settings.value("local/bailian_system", DEFAULT_BAILIAN_SYSTEM_PROMPT, type=str) or DEFAULT_BAILIAN_SYSTEM_PROMPT)
        endpoint = str(self.settings.value("local/bailian_endpoint", DEFAULT_BAILIAN_ENDPOINT, type=str) or DEFAULT_BAILIAN_ENDPOINT)
        args = ["--chat-mode", "local"]
        if app_id:
            args.extend(["--bailian-app-id", app_id])
        if system_prompt:
            args.extend(["--bailian-system", system_prompt])
        if endpoint:
            args.extend(["--bailian-endpoint", endpoint])
        return args

    def _local_bailian_env(self) -> dict[str, str]:
        api_key = str(self.settings.value("local/bailian_api_key", "", type=str) or "").strip()
        return {"PYWECHAT_BAILIAN_API_KEY": api_key} if api_key else {}

    def _load_shortlink_rules_to_editor(self) -> None:
        rule_file = self._shortlink_rule_file()
        data = load_json_dict(rule_file)
        if not data:
            self.shortlink_rules_edit.setPlainText("")
            return
        lines: list[str] = []
        if isinstance(data, dict):
            for k, v in data.items():
                keyword = str(k).strip()
                short_link = str(v).strip()
                if keyword and short_link:
                    lines.append(f"{keyword}|{short_link}")
        elif isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                keyword = str(item.get("keyword", "")).strip()
                short_link = str(item.get("short_link", "")).strip()
                if keyword and short_link:
                    lines.append(f"{keyword}|{short_link}")
        self.shortlink_rules_edit.setPlainText("\n".join(lines))

    def save_shortlink_rules(self) -> None:
        lines = [line.strip() for line in self.shortlink_rules_edit.toPlainText().splitlines() if line.strip()]
        rules: dict[str, str] = {}
        for line in lines:
            if "|" not in line:
                QMessageBox.warning(self, "格式错误", f"规则格式错误：{line}\n请使用：关键词|短链")
                return
            keyword, short_link = line.split("|", 1)
            keyword = keyword.strip()
            short_link = short_link.strip()
            if not keyword or not short_link:
                QMessageBox.warning(self, "格式错误", f"规则不能为空：{line}")
                return
            rules[keyword] = short_link
        rule_file = self._shortlink_rule_file()
        write_json_file(rule_file, rules)
        self.append_log(f"短链规则已保存: {rule_file}（{len(rules)}条）")

    def _init_side_menu_icons(self) -> None:
        style = self.style()
        icons: list[QIcon] = [
            style.standardIcon(QStyle.SP_ComputerIcon),
            style.standardIcon(QStyle.SP_BrowserReload),
            style.standardIcon(QStyle.SP_DriveHDIcon),
        ]
        for i, icon in enumerate(icons):
            item = self.nav_list.item(i)
            if item is not None:
                item.setIcon(icon)

    def _restore_nav_index(self) -> None:
        saved = self.settings.value("ui/nav_index", 0, type=int)
        if saved < 0 or saved >= self.NAV_PAGE_COUNT:
            saved = 0
        self.nav_list.setCurrentRow(saved)
        self.content_stack.setCurrentIndex(saved)

    def _on_nav_row_changed(self, index: int) -> None:
        if index < 0 or index >= self.NAV_PAGE_COUNT:
            return
        self.content_stack.setCurrentIndex(index)
        self.settings.setValue("ui/nav_index", index)

    def _ts(self) -> str:
        return datetime.now().strftime("%H:%M:%S")

    def _sanitize_file_piece(self, text: str) -> str:
        return re.sub(r"[^0-9A-Za-z_-]+", "_", text).strip("_") or "unknown_wxid"

    def _extract_wxid_from_text(self, text: str) -> str | None:
        match = re.search(r"(wxid_[0-9A-Za-z_-]+)", str(text))
        if not match:
            return None
        return self._sanitize_file_piece(match.group(1))

    def _resolve_current_wxid(self) -> str:
        try:
            from pyweixin.WeChatTools import Tools

            wxid = Tools.get_current_wxid()
            if wxid:
                return self._sanitize_file_piece(str(wxid))
        except Exception:
            pass
        return "unknown_wxid"

    def _resolve_current_api_wechat_id(self) -> str:
        try:
            from wechat_bot.runtime.self_profile import get_self_profile

            profile = get_self_profile()
            wechat_id = str(profile.get("wechat_id") or "").strip()
            if wechat_id:
                return wechat_id
            wxid = str(profile.get("wxid") or "").strip()
            if wxid:
                return wxid
        except Exception:
            pass
        if self.current_wxid and self.current_wxid != "unknown_wxid":
            return self.current_wxid
        return ""

    def _build_log_file_path(self, wxid: str) -> Path:
        self.log_dir = get_bot_logs_dir(wxid)
        return self.log_dir / f"{self.current_log_date}_{wxid}.log"

    def _refresh_log_file_by_wxid(self) -> None:
        latest_wxid = self._resolve_current_wxid()
        latest_date = datetime.now().strftime("%Y-%m-%d")
        if latest_date != self.current_log_date:
            self.current_log_date = latest_date
            self.log_file_path = self._build_log_file_path(self.current_wxid)
            self._write_log_file_line(f"[{self._ts()}] 日志切换: {self.log_file_path}")
        if latest_wxid != self.current_wxid:
            self.current_wxid = latest_wxid
            self.log_file_path = self._build_log_file_path(self.current_wxid)
            self._write_log_file_line(f"[{self._ts()}] 日志切换: {self.log_file_path}")
            self._load_shortlink_rules_to_editor()

    def _write_log_file_line(self, line: str) -> None:
        try:
            self.log_file_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_file_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def append_log(self, text: str) -> None:
        self._refresh_log_file_by_wxid()
        content = str(text).strip()
        if not content:
            return
        for raw_line in content.splitlines():
            clean_line = raw_line.strip()
            if not clean_line:
                continue
            # 优先从脚本输出中提取 wxid，及时修正 unknown_wxid 文件名
            maybe_wxid = self._extract_wxid_from_text(clean_line)
            if maybe_wxid and maybe_wxid != self.current_wxid:
                self.current_wxid = maybe_wxid
                self.log_file_path = self._build_log_file_path(self.current_wxid)
                self._write_log_file_line(f"[{self._ts()}] 日志切换: {self.log_file_path}")

            line = f"[{self._ts()}] {clean_line}"
            self.log_output.append(line)
            self._write_log_file_line(line)
        self.log_output.verticalScrollBar().setValue(self.log_output.verticalScrollBar().maximum())

    def _show_log_context_menu(self, pos: Any) -> None:
        menu = QMenu(self)
        clear_action = menu.addAction("清空日志")
        chosen = menu.exec_(self.log_output.mapToGlobal(pos))
        if chosen == clear_action:
            self.log_output.clear()
            self.append_log("日志已清空")

    def _start_embedded_worker(
        self,
        script_name: str,
        args: list[str],
        on_done: Callable[[int], None] | None = None,
    ) -> None:
        worker = EmbeddedWorker(script_name, args)
        worker.log.connect(self.append_log)
        worker.done.connect(lambda name, code: self.append_log(f"{name} 结束，退出码={code}"))
        if on_done is not None:
            worker.done.connect(lambda _name, code: on_done(int(code)))
        worker.finished.connect(lambda: self.embedded_workers.remove(worker) if worker in self.embedded_workers else None)
        self.embedded_workers.append(worker)
        self.append_log(f"已启动(内嵌): {script_name} {' '.join(self._sanitize_process_args(args))}")
        worker.start()

    def _sanitize_process_args(self, args: list[str]) -> list[str]:
        sanitized: list[str] = []
        hide_next = False
        secret_options = {"--bailian-api-key", "--bailian-system", "--password", "--token"}
        for arg in args:
            if hide_next:
                sanitized.append("***")
                hide_next = False
                continue
            sanitized.append(arg)
            if arg in secret_options:
                hide_next = True
        return sanitized

    def _start_process(
        self,
        script_name: str,
        args: list[str],
        keep_ref: bool = True,
        extra_env: dict[str, str] | None = None,
    ) -> QProcess | None:
        spec = resolve_script_spec(script_name)
        if spec is None:
            QMessageBox.critical(self, "启动失败", f"未知脚本: {script_name}")
            return None

        # 打包环境中，短任务改为进程内线程执行，避免 onefile 子进程反复创建临时目录失败。
        if getattr(sys, "frozen", False) and spec.script_name != "auto_reply_unread.py":
            self._start_embedded_worker(spec.script_name, args)
            return None

        process = QProcess(self)
        process.setWorkingDirectory(str(BASE_DIR))
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUTF8", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONUNBUFFERED", "1")
        for key, value in (extra_env or {}).items():
            env.insert(str(key), str(value))
        process.setProcessEnvironment(env)

        process.readyReadStandardOutput.connect(
            lambda: self._handle_process_output(process, script_name, is_stderr=False)
        )
        process.readyReadStandardError.connect(
            lambda: self._handle_process_output(process, script_name, is_stderr=True)
        )
        process.finished.connect(
            lambda code, status: self.append_log(f"{spec.script_name} 结束，退出码={code} 状态={int(status)}")
        )

        if getattr(sys, "frozen", False):
            # 打包后，直接复用当前 exe 作为子进程，避免依赖外部 Python。
            process.start(sys.executable, ["--run-script", spec.script_name] + args)
        else:
            python_exe = sys.executable
            script_path = BASE_DIR / spec.relative_path
            if not script_path.exists():
                QMessageBox.critical(self, "文件缺失", f"脚本不存在: {script_path}")
                return None
            process.start(python_exe, [str(script_path)] + args)
        if not process.waitForStarted(5000):
            QMessageBox.critical(self, "启动失败", f"无法启动脚本: {spec.script_name}")
            return None

        self.append_log(f"已启动: {spec.script_name} {' '.join(self._sanitize_process_args(args))}")
        if keep_ref:
            self.child_processes.append(process)
        return process

    def _handle_process_output(self, process: QProcess, script_name: str, *, is_stderr: bool) -> None:
        raw = bytes(process.readAllStandardError() if is_stderr else process.readAllStandardOutput())
        text = decode_process_output(raw).rstrip()
        if text:
            self.append_log(text)
            if script_name == "auto_reply_unread.py":
                self._check_auto_reply_auth_expired(text)

    def _check_auto_reply_auth_expired(self, text: str) -> None:
        content = str(text or "")
        if not content or self.auth_expired_handling:
            return
        markers = (
            "[AUTH_EXPIRED]",
            "自动回复鉴权失效",
        )
        if not any(marker in content for marker in markers):
            return
        self.auth_expired_handling = True
        self.append_log("检测到 AI 自动回复接口 401/鉴权失效，准备停止自动回复并返回登录页")
        QTimer.singleShot(0, self._handle_auto_reply_auth_expired)

    def _handle_auto_reply_auth_expired(self) -> None:
        try:
            if self.api_client is not None:
                self.api_client.clear_state()
        except Exception as exc:
            self.append_log(f"清理登录态失败: {exc}")

        if self.auto_process is not None and self.auto_process.state() != QProcess.NotRunning:
            self.stop_auto_reply()
        else:
            self.stop_wechat_dock()

        QMessageBox.warning(self, "登录失效", "AI 自动回复接口返回 401，请重新登录。")
        if callable(self.on_auth_expired):
            self.on_auth_expired()

    def run_check_status(self) -> None:
        self._refresh_log_file_by_wxid()
        self.move(20, 20)
        self._start_process("check_wechat_status.py", [])

    def _parse_version_tuple(self, version_text: str) -> tuple[int, int, int, int] | None:
        parts = [int(p) for p in re.findall(r"\d+", str(version_text or ""))]
        if not parts:
            return None
        while len(parts) < 4:
            parts.append(0)
        return tuple(parts[:4])

    def _format_version_tuple(self, version: tuple[int, int, int, int]) -> str:
        major, minor, patch, build = version
        if build:
            return f"{major}.{minor}.{patch}.{build}"
        return f"{major}.{minor}.{patch}"

    def _get_wechat_version(self, wechat_path: str) -> tuple[int, int, int, int] | None:
        if not wechat_path:
            return None
        if win32api is not None:
            try:
                info = win32api.GetFileVersionInfo(wechat_path, "\\")
                ms = info["FileVersionMS"]
                ls = info["FileVersionLS"]
                return (
                    win32api.HIWORD(ms),
                    win32api.LOWORD(ms),
                    win32api.HIWORD(ls),
                    win32api.LOWORD(ls),
                )
            except Exception:
                pass
        try:
            import ctypes

            size = ctypes.windll.version.GetFileVersionInfoSizeW(wechat_path, None)
            if size <= 0:
                return None
            buf = ctypes.create_string_buffer(size)
            ctypes.windll.version.GetFileVersionInfoW(wechat_path, 0, size, buf)
            lptr = ctypes.c_void_p()
            ulen = ctypes.c_uint()
            if not ctypes.windll.version.VerQueryValueW(buf, "\\", ctypes.byref(lptr), ctypes.byref(ulen)):
                return None
            class VS_FIXEDFILEINFO(ctypes.Structure):
                _fields_ = [
                    ("dwSignature", ctypes.c_uint32),
                    ("dwStrucVersion", ctypes.c_uint32),
                    ("dwFileVersionMS", ctypes.c_uint32),
                    ("dwFileVersionLS", ctypes.c_uint32),
                    ("dwProductVersionMS", ctypes.c_uint32),
                    ("dwProductVersionLS", ctypes.c_uint32),
                    ("dwFileFlagsMask", ctypes.c_uint32),
                    ("dwFileFlags", ctypes.c_uint32),
                    ("dwFileOS", ctypes.c_uint32),
                    ("dwFileType", ctypes.c_uint32),
                    ("dwFileSubtype", ctypes.c_uint32),
                    ("dwFileDateMS", ctypes.c_uint32),
                    ("dwFileDateLS", ctypes.c_uint32),
                ]
            ffi = ctypes.cast(lptr.value, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
            return (
                ffi.dwFileVersionMS >> 16,
                ffi.dwFileVersionMS & 0xFFFF,
                ffi.dwFileVersionLS >> 16,
                ffi.dwFileVersionLS & 0xFFFF,
            )
        except Exception:
            return None

    def _ensure_wechat_ready_for_auto_reply(self) -> bool:
        if sys.platform != "win32":
            QMessageBox.warning(self, "环境不支持", "自动回复仅支持 Windows 环境。")
            return False
        try:
            from pyweixin.WeChatTools import Tools, WxWindowManage
        except Exception as exc:
            QMessageBox.warning(self, "初始化失败", f"无法导入微信自动化依赖：{exc}")
            self.append_log(f"微信依赖导入失败: {exc}")
            self.append_log(traceback.format_exc(limit=1).strip())
            return False

        try:
            is_running = bool(Tools.is_weixin_running())
        except Exception as exc:
            QMessageBox.warning(self, "检测失败", f"无法检测微信运行状态：{exc}")
            self.append_log(f"检测微信运行状态失败: {exc}")
            return False
        if not is_running:
            QMessageBox.warning(self, "未找到微信", "请先打开并登录微信，再启动自动回复。")
            return False

        try:
            wx = WxWindowManage()
            handle = wx.find_wx_window()
            is_logged_in = bool(handle) and wx.window_type != 0
        except Exception as exc:
            QMessageBox.warning(self, "检测失败", f"无法识别微信登录状态：{exc}")
            self.append_log(f"识别微信登录状态失败: {exc}")
            return False
        if not is_logged_in:
            QMessageBox.warning(self, "微信未登录", "请先登录微信，再启动自动回复。")
            return False

        try:
            wechat_path = Tools.where_weixin(copy_to_clipboard=False)
        except Exception as exc:
            QMessageBox.warning(self, "检测失败", f"无法获取微信安装路径：{exc}")
            self.append_log(f"获取微信路径失败: {exc}")
            return False
        version = self._get_wechat_version(str(wechat_path or ""))
        if version is None:
            QMessageBox.warning(self, "版本检测失败", "无法读取微信版本，请确认微信已正确安装后重试。")
            self.append_log(f"微信版本检测失败: {wechat_path}")
            return False
        if version < self.MIN_WECHAT_VERSION:
            current = self._format_version_tuple(version)
            required = self._format_version_tuple(self.MIN_WECHAT_VERSION)
            QMessageBox.warning(
                self,
                "微信版本过低",
                f"当前微信版本为 {current}，自动回复要求微信版本不低于 {required}。\n请升级微信后再启动。",
            )
            self.append_log(f"微信版本过低: 当前={current}，要求>={required}")
            return False
        self.append_log(f"微信状态检测通过，版本={self._format_version_tuple(version)}")
        return True

    def run_open_window(self) -> None:
        # 打开微信窗口时，将控制台移到左上角，避免与微信主窗口重叠
        self._refresh_log_file_by_wxid()
        self.move(20, 20)
        if getattr(sys, "frozen", False):
            self._start_embedded_worker("open_wechat_window.py", [], on_done=self._on_open_window_done)
            return
        p = self._start_process("open_wechat_window.py", [])
        if p is not None:
            p.finished.connect(lambda code, _status: self._on_open_window_done(int(code)))

    def load_friend_list(self) -> None:
        if self.friend_list_worker is not None and self.friend_list_worker.isRunning():
            QMessageBox.information(self, "提示", "正在加载好友列表，请稍候。")
            return
        self.friend_list_widget.clear()
        self.friend_profiles = []
        self.append_log("开始加载好友列表（强制刷新，优先从联系人实时获取）...")
        worker = FriendListWorker(force_refresh=True)
        worker.log.connect(self.append_log)
        worker.loaded.connect(self._on_friend_list_loaded)
        worker.done.connect(self._on_friend_list_done)
        self.friend_list_worker = worker
        self.load_friends_btn.setEnabled(False)
        worker.start()

    def _load_friend_list_from_cache_on_startup(self) -> None:
        try:
            profiles = get_cached_friend_profiles(wxid=self.current_wxid)
        except Exception:
            profiles = []
        if profiles:
            self._on_friend_list_loaded(profiles)
            self.append_log(f"已从本地缓存恢复好友资料 {len(profiles)} 人")
            return
        try:
            names = get_cached_friend_names(wxid=self.current_wxid)
        except Exception:
            names = []
        if names:
            self._on_friend_list_loaded(names)
            self.append_log(f"已从本地缓存恢复好友列表 {len(names)} 人")
            return
        self.append_log("本地好友缓存为空，点击“加载好友列表”进行获取")

    def _on_friend_list_loaded(self, profiles: list) -> None:
        self.friend_list_widget.clear()
        self.friend_profiles = []
        avatar_map = self._load_saved_friend_avatar_map()
        self.append_log(f"头像索引加载: 命中 {len(avatar_map)} 条可用映射")
        avatar_hit_count = 0
        avatar_miss_count = 0
        for record in profiles:
            if isinstance(record, dict):
                display_name = str(record.get("display_name") or "").strip()
                remark = str(record.get("remark") or "").strip()
                nickname = str(record.get("nickname") or "").strip()
                wechat_id = str(record.get("wechat_id") or "").strip()
                avatar_path = str(record.get("avatar_path") or "").strip()
                normalized = {
                    "display_name": display_name,
                    "remark": remark,
                    "nickname": nickname,
                    "wechat_id": wechat_id,
                    "avatar_path": avatar_path,
                }
            else:
                display_name = str(record).strip()
                normalized = {
                    "display_name": display_name,
                    "remark": display_name,
                    "nickname": "",
                    "wechat_id": "",
                    "avatar_path": "",
                }
            if not display_name:
                continue
            self.friend_profiles.append(normalized)
            item = QListWidgetItem(display_name)
            avatar_path = self._resolve_friend_avatar_path(normalized, avatar_map)
            if avatar_path:
                avatar_hit_count += 1
            else:
                avatar_miss_count += 1
            item.setIcon(self._build_friend_avatar_icon(display_name, avatar_path=avatar_path))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            item.setData(Qt.UserRole, normalized)
            self.friend_list_widget.addItem(item)
        self.append_log(f"好友列表加载完成，共 {len(self.friend_profiles)} 人")
        self.append_log(f"好友头像加载: 成功 {avatar_hit_count} 人，未匹配 {avatar_miss_count} 人")
        self._sync_friend_profiles_to_backend()

    def _sync_friend_profiles_to_backend(self) -> None:
        if not self.friend_profiles:
            self.append_log("好友列表为空，跳过后端同步")
            return
        if self.run_mode == "local":
            self.append_log("本地模式：好友列表已保存到本机缓存，跳过后端同步")
            return
        if self.api_client is None or not self.api_client.is_authenticated:
            self.append_log("未登录后端，跳过好友列表同步")
            return
        if self.friend_sync_worker is not None and self.friend_sync_worker.isRunning():
            self.append_log("好友列表同步仍在进行，跳过重复提交")
            return
        payload = [
            {
                "display_name": item.get("display_name", ""),
                "remark": item.get("remark", ""),
                "nickname": item.get("nickname", ""),
                "wechat_id": item.get("wechat_id", ""),
            }
            for item in self.friend_profiles
        ]
        self.append_log(f"开始同步好友列表到后端，共 {len(payload)} 人")
        worker = ApiRequestWorker(
            lambda: self.api_client.sync_friend_profiles(
                wxid=self.current_wxid,
                friends=payload,
            )
        )
        worker.success.connect(self._on_friend_sync_success)
        worker.error.connect(self._on_friend_sync_error)
        worker.finished.connect(lambda: setattr(self, "friend_sync_worker", None))
        self.friend_sync_worker = worker
        worker.start()

    def _on_friend_sync_success(self, result: object) -> None:
        if isinstance(result, dict):
            message = str(result.get("message") or "成功")
        else:
            message = "成功"
        self.append_log(f"好友列表已同步到后端: {message}")

    def _on_friend_sync_error(self, message: str) -> None:
        self.append_log(f"好友列表同步失败: {message}")

    def _load_saved_friend_avatar_map(self) -> dict[str, str]:
        avatar_map: dict[str, str] = {}
        index_path = get_bot_data_dir(wxid=self.current_wxid) / "friend_avatars" / "index.json"
        if not index_path.is_file():
            return avatar_map
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            return avatar_map
        records = data.get("avatars", []) if isinstance(data, dict) else []
        if not isinstance(records, list):
            return avatar_map
        for record in records:
            if not isinstance(record, dict):
                continue
            if record.get("stale") is True:
                continue
            path_text = str(record.get("saved_path", "")).strip()
            if not path_text:
                continue
            avatar_path = Path(path_text)
            if not avatar_path.is_file():
                continue
            for key in ("remark", "nickname", "friend_input", "wechat_id", "unique_key"):
                name = str(record.get(key, "")).strip()
                if name and name != "无" and name not in avatar_map:
                    avatar_map[name] = str(avatar_path)
        return avatar_map

    def _resolve_friend_avatar_path(self, profile: dict[str, str], avatar_map: dict[str, str]) -> str:
        """从详情路径或头像索引中解析可用头像文件。"""
        candidates = [
            str(profile.get("avatar_path", "")).strip(),
            avatar_map.get(str(profile.get("display_name", "")).strip(), ""),
            avatar_map.get(str(profile.get("remark", "")).strip(), ""),
            avatar_map.get(str(profile.get("nickname", "")).strip(), ""),
            avatar_map.get(str(profile.get("wechat_id", "")).strip(), ""),
        ]
        for candidate in candidates:
            if not candidate:
                continue
            path = Path(candidate)
            if path.is_file():
                return str(path)
        return ""

    def _build_friend_avatar_icon(self, name: str, size: int = 28, avatar_path: str | None = None) -> QIcon:
        if avatar_path:
            pix = QPixmap(str(Path(avatar_path)))
            if not pix.isNull():
                return QIcon(pix.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
        clean_name = (name or "").strip()
        label = clean_name[0].upper() if clean_name else "?"
        palette = [
            "#2f6feb",
            "#1d9a6c",
            "#d97706",
            "#be185d",
            "#0e7490",
            "#7c3aed",
            "#b45309",
            "#334155",
        ]
        bg_color = QColor(palette[sum(ord(ch) for ch in clean_name) % len(palette)])

        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg_color)
        painter.drawEllipse(0, 0, size, size)

        text_size = max(10, int(size * 0.46))
        font = QFont("Segoe UI", text_size)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(pixmap.rect(), Qt.AlignCenter, label)
        painter.end()
        return QIcon(pixmap)

    def _on_friend_list_done(self, code: int) -> None:
        self.load_friends_btn.setEnabled(True)
        if code != 0:
            QMessageBox.warning(self, "加载失败", "好友列表加载失败，请查看日志。")
        self.friend_list_worker = None

    def check_all_friends(self) -> None:
        for i in range(self.friend_list_widget.count()):
            self.friend_list_widget.item(i).setCheckState(Qt.Checked)

    def uncheck_all_friends(self) -> None:
        for i in range(self.friend_list_widget.count()):
            self.friend_list_widget.item(i).setCheckState(Qt.Unchecked)

    def _get_checked_friends(self) -> list[str]:
        selected: list[str] = []
        for i in range(self.friend_list_widget.count()):
            item = self.friend_list_widget.item(i)
            if item.checkState() == Qt.Checked:
                selected.append(item.text().strip())
        return selected

    def start_timed_send(self) -> None:
        if self.timed_send_worker is not None and self.timed_send_worker.isRunning():
            QMessageBox.information(self, "提示", "定时群发已在运行。")
            return
        if self.timed_send_schedule_timer is not None:
            QMessageBox.information(self, "提示", "已存在待触发的定时任务。")
            return
        friends = self._get_checked_friends()
        if not friends:
            QMessageBox.warning(self, "参数错误", "请先勾选至少一个好友。")
            return
        message = self.timed_message_input.text().strip()
        if not message:
            QMessageBox.warning(self, "参数错误", "请输入发送内容。")
            return
        start_text = self.timed_start_time_input.text().strip()
        start_dt = self._parse_next_start_datetime(start_text)
        if start_dt is None:
            QMessageBox.warning(self, "参数错误", "开始时间格式错误，请使用 HH:MM 或 HH:MM:SS。")
            return

        delay_s = max((start_dt - datetime.now()).total_seconds(), 0.0)
        self.timed_send_pending_friends = friends
        self.timed_send_pending_message = message
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._trigger_timed_send_worker)
        timer.start(int(delay_s * 1000))
        self.timed_send_schedule_timer = timer
        self.start_timed_send_btn.setEnabled(False)
        self.stop_timed_send_btn.setEnabled(True)
        self.append_log(
            f"定时群发已设置：{start_dt.strftime('%Y-%m-%d %H:%M:%S')} 开始，随后按 5-10s 随机间隔轮询发送"
        )

    def _parse_next_start_datetime(self, text: str) -> datetime | None:
        now = datetime.now()
        parsed = None
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            return None
        target = now.replace(
            hour=parsed.hour,
            minute=parsed.minute,
            second=parsed.second,
            microsecond=0,
        )
        if target <= now:
            target = target + timedelta(days=1)
        return target

    def _trigger_timed_send_worker(self) -> None:
        self.timed_send_schedule_timer = None
        friends = list(self.timed_send_pending_friends)
        message = self.timed_send_pending_message
        self.timed_send_pending_friends = []
        self.timed_send_pending_message = ""
        if not friends or not message:
            self.start_timed_send_btn.setEnabled(True)
            self.stop_timed_send_btn.setEnabled(False)
            self.append_log("定时群发触发失败：任务参数为空")
            return
        self.append_log("已到达开始时间，启动定时群发")
        worker = TimedSendWorker(friends=friends, message=message, interval_min=5.0, interval_max=10.0)
        worker.log.connect(self.append_log)
        worker.done.connect(self._on_timed_send_done)
        self.timed_send_worker = worker
        worker.start()

    def stop_timed_send(self) -> None:
        canceled = False
        if self.timed_send_schedule_timer is not None:
            self.timed_send_schedule_timer.stop()
            self.timed_send_schedule_timer.deleteLater()
            self.timed_send_schedule_timer = None
            self.timed_send_pending_friends = []
            self.timed_send_pending_message = ""
            canceled = True
            self.append_log("已取消待触发的定时群发任务")
        if self.timed_send_worker is not None:
            self.append_log("正在停止定时群发...")
            self.timed_send_worker.stop()
            canceled = True
        if canceled and self.timed_send_worker is None:
            self.start_timed_send_btn.setEnabled(True)
            self.stop_timed_send_btn.setEnabled(False)

    def _on_timed_send_done(self, _code: int) -> None:
        self.start_timed_send_btn.setEnabled(True)
        self.stop_timed_send_btn.setEnabled(False)
        self.timed_send_worker = None

    def _on_open_window_done(self, code: int) -> None:
        if code != 3:
            return
        self.append_log("检测到UI主界面无法识别，已进入讲述人等待流程")
        self._start_narrator_countdown(300)

    def _find_wechat_window_handle(self) -> int | None:
        if win32gui is None:
            return None
        try:
            hwnd = int(win32gui.FindWindow("WeChatMainWndForPC", None))
            if hwnd:
                return hwnd
        except Exception:
            pass

        candidates: list[int] = []

        def _enum_cb(hwnd: int, _lparam: object) -> None:
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return
                class_name = win32gui.GetClassName(hwnd) or ""
                title = win32gui.GetWindowText(hwnd) or ""
                if "WeChatMainWndForPC" in class_name or title in {"微信", "WeChat"}:
                    candidates.append(int(hwnd))
            except Exception:
                return

        try:
            win32gui.EnumWindows(_enum_cb, None)
        except Exception:
            return None
        return candidates[0] if candidates else None

    def start_wechat_dock(self) -> None:
        if win32gui is None:
            QMessageBox.warning(self, "环境不支持", "当前环境缺少 pywin32，无法使用窗口停靠。")
            return
        if self.wechat_dock_timer is not None:
            QMessageBox.information(self, "提示", "微信窗口停靠已在运行。")
            return
        gap = self.DOCK_GAP

        hwnd = self._find_wechat_window_handle()
        if not hwnd:
            QMessageBox.warning(self, "未找到微信", "请先打开并登录微信，再启动停靠。")
            return

        self.wechat_dock_hwnd = int(hwnd)
        self._shrink_window_to_fit_dock_space(hwnd, gap)
        self.wechat_dock_minimized_by_wechat = False
        timer = QTimer(self)
        timer.setInterval(120)
        timer.timeout.connect(self._sync_wechat_dock)
        timer.start()
        self.wechat_dock_timer = timer
        self.start_dock_btn.setEnabled(False)
        self.stop_dock_btn.setEnabled(True)
        self.append_log("微信窗口停靠已启动")
        self._sync_wechat_dock()

    def _shrink_window_to_fit_dock_space(self, hwnd: int, gap: int) -> None:
        if win32gui is None:
            return
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        try:
            left, _top, right, _bottom = win32gui.GetWindowRect(hwnd)
        except Exception:
            return
        available = screen.availableGeometry()
        left_space = max(left - available.left() - gap, 0)
        right_space = max(available.right() - right - gap + 1, 0)
        fit_width = max(left_space, right_space)
        if fit_width <= 0:
            return
        if self.width() <= fit_width:
            return
        min_usable = 220
        target_width = max(min_usable, fit_width)
        self.resize(target_width, self.height())
        self.append_log(f"停靠前已自动调整窗口宽度为 {target_width}px")

    def stop_wechat_dock(self) -> None:
        if self.wechat_dock_timer is not None:
            self.wechat_dock_timer.stop()
            self.wechat_dock_timer.deleteLater()
            self.wechat_dock_timer = None
        self.wechat_dock_hwnd = None
        self.wechat_dock_minimized_by_wechat = False
        self.start_dock_btn.setEnabled(True)
        self.stop_dock_btn.setEnabled(False)
        self.append_log("微信窗口停靠已停止")

    def _sync_wechat_dock(self) -> None:
        if win32gui is None:
            self.stop_wechat_dock()
            return
        hwnd = self.wechat_dock_hwnd or self._find_wechat_window_handle()
        if not hwnd:
            self.append_log("微信窗口不存在，已停止停靠")
            self.stop_wechat_dock()
            return
        self.wechat_dock_hwnd = int(hwnd)
        if not win32gui.IsWindow(hwnd):
            self.stop_wechat_dock()
            return

        gap = self.DOCK_GAP

        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            wechat_w = max(right - left, 0)
            wechat_h = max(bottom - top, 0)
            if wechat_w <= 0 or wechat_h <= 0:
                return
            if win32gui.IsIconic(hwnd):
                if not self.isMinimized():
                    self.wechat_dock_minimized_by_wechat = True
                    self.showMinimized()
                return
            if self.wechat_dock_minimized_by_wechat and self.isMinimized():
                self.showNormal()
                self.wechat_dock_minimized_by_wechat = False
        except Exception:
            return

        if self.isMaximized() or self.isFullScreen():
            return

        screen = QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        target_w = self.width()
        target_h = wechat_h if self.DOCK_FOLLOW_HEIGHT else self.height()
        if self.DOCK_SIDE == "left":
            target_x = left - target_w - gap
        else:
            target_x = right + gap

        target_y = top
        if target_x < available.left():
            target_x = min(right + gap, available.right() - target_w + 1)
        if target_x + target_w > available.right():
            target_x = max(left - target_w - gap, available.left())
        if target_y < available.top():
            target_y = available.top()
        if target_y + target_h > available.bottom():
            target_y = max(available.bottom() - target_h + 1, available.top())

        if (
            self.x() != target_x
            or self.y() != target_y
            or self.width() != target_w
            or self.height() != target_h
        ):
            self.setGeometry(target_x, target_y, target_w, target_h)

    def _start_narrator_countdown(self, seconds: int) -> None:
        if self.narrator_countdown_timer is not None:
            self.append_log("讲述人倒计时已在进行中")
            return
        self._close_narrator_countdown_box()
        self.narrator_countdown_remaining = max(int(seconds), 1)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("讲述人模式等待")
        box.setStandardButtons(QMessageBox.NoButton)
        box.setText(self._build_narrator_countdown_text())
        box.setModal(False)
        box.show()
        self.narrator_countdown_box = box

        timer = QTimer(self)
        timer.setInterval(1000)
        timer.timeout.connect(self._tick_narrator_countdown)
        timer.start()
        self.narrator_countdown_timer = timer

    def _build_narrator_countdown_text(self) -> str:
        mm = self.narrator_countdown_remaining // 60
        ss = self.narrator_countdown_remaining % 60
        return f"请保持讲述人模式开启。\n倒计时结束后将自动启动微信并轮询识别UI主界面。\n剩余时间：{mm:02d}:{ss:02d}"

    def _tick_narrator_countdown(self) -> None:
        self.narrator_countdown_remaining -= 1
        if self.narrator_countdown_box is not None:
            self.narrator_countdown_box.setText(self._build_narrator_countdown_text())
        if self.narrator_countdown_remaining > 0:
            return
        if self.narrator_countdown_timer is not None:
            self.narrator_countdown_timer.stop()
            self.narrator_countdown_timer.deleteLater()
            self.narrator_countdown_timer = None
        self._close_narrator_countdown_box()
        self.append_log("倒计时结束，准备启动微信并轮询识别UI主界面")
        self._launch_wechat_and_poll_ui()

    def _close_narrator_countdown_box(self) -> None:
        if self.narrator_countdown_box is None:
            return
        try:
            self.narrator_countdown_box.hide()
            self.narrator_countdown_box.done(0)
            self.narrator_countdown_box.close()
            self.narrator_countdown_box.deleteLater()
        finally:
            self.narrator_countdown_box = None

    def _launch_wechat_and_poll_ui(self) -> None:
        self._close_narrator_countdown_box()
        try:
            from pyweixin.WeChatTools import Tools

            weixin_path = Tools.where_weixin(copy_to_clipboard=False)
            if not weixin_path:
                self.append_log("启动微信失败：未找到微信安装路径")
                QMessageBox.warning(self, "启动失败", "未找到微信安装路径，请确认已安装微信。")
                return
            subprocess.Popen([weixin_path], shell=False)
            self.append_log(f"已启动微信程序: {weixin_path}")
        except Exception as exc:
            self.append_log(f"启动微信失败: {exc}")
            QMessageBox.warning(self, "启动失败", f"启动微信失败：{exc}")
            return

        if self.wechat_ui_poll_timer is not None:
            self.wechat_ui_poll_timer.stop()
            self.wechat_ui_poll_timer.deleteLater()
        timer = QTimer(self)
        timer.setInterval(3000)
        timer.timeout.connect(self._poll_wechat_main_ui_ready)
        timer.start()
        self.wechat_ui_poll_timer = timer
        self.append_log("开始轮询微信UI主界面识别状态（每3秒）")

    def _poll_wechat_main_ui_ready(self) -> None:
        try:
            from pyweixin.WeChatTools import WxWindowManage

            wx = WxWindowManage()
            handle = wx.find_wx_window()
            is_ready = bool(handle) and wx.window_type != 0
        except Exception as exc:
            self.append_log(f"轮询微信UI异常: {exc}")
            return

        if not is_ready:
            self.append_log("轮询结果：暂未识别到微信UI主界面")
            return

        if self.wechat_ui_poll_timer is not None:
            self.wechat_ui_poll_timer.stop()
            self.wechat_ui_poll_timer.deleteLater()
            self.wechat_ui_poll_timer = None
        self.append_log("轮询结果：已识别到微信UI主界面")
        QMessageBox.information(self, "识别成功", "已识别到微信UI主界面，可以继续操作。")

    def start_auto_reply(self) -> None:
        self._refresh_log_file_by_wxid()
        if self.auto_process is not None and self.auto_process.state() != QProcess.NotRunning:
            QMessageBox.information(self, "提示", "自动回复已经在运行")
            return
        if self.auto_start_pending:
            self.append_log("自动回复启动中，请稍候...")
            return
        self.auto_start_pending = True
        self.append_log("启动自动回复前，先执行“打开微信窗口”")
        self.run_open_window()
        QTimer.singleShot(1500, self._start_auto_reply_after_open_window)

    def _start_auto_reply_after_open_window(self) -> None:
        self.auto_start_pending = False
        if self.auto_process is not None and self.auto_process.state() != QProcess.NotRunning:
            return
        if not self._ensure_wechat_ready_for_auto_reply():
            return

        args = [
            "--reply",
            self.reply_input.text().strip() or DEFAULT_AUTO_REPLY_TEXT,
            "--keep-open",
        ]
        extra_env: dict[str, str] = {}
        if self.run_mode == "local":
            args.extend(self._local_bailian_args())
            extra_env.update(self._local_bailian_env())
        else:
            args.extend(["--chat-mode", "api"])
        shortlink_rule_file = self._shortlink_rule_file()
        if shortlink_rule_file.is_file():
            args.extend(["--mini-shortlink-rules", str(shortlink_rule_file)])
            self.append_log(f"已加载短链规则: {shortlink_rule_file}")
        if self.wechat_dock_timer is None:
            self.start_wechat_dock()
            if self.wechat_dock_timer is None:
                self.append_log("微信窗口停靠未启动，已取消自动回复启动")
                return
        self.auto_process = self._start_process("auto_reply_unread.py", args, keep_ref=False, extra_env=extra_env)
        if self.auto_process is not None:
            self.start_auto_btn.setEnabled(False)
            self.stop_auto_btn.setEnabled(True)
            self.auto_process.finished.connect(self._on_auto_finished)

    def _on_auto_finished(self, _code: int, _status: QProcess.ExitStatus) -> None:
        self.start_auto_btn.setEnabled(True)
        self.stop_auto_btn.setEnabled(False)
        self.auto_process = None
        self.auth_expired_handling = False
        self.stop_wechat_dock()

    def stop_auto_reply(self) -> None:
        if self.auto_process is None:
            self.stop_wechat_dock()
            return
        self.append_log("正在停止自动回复...")
        self.auto_process.terminate()
        if not self.auto_process.waitForFinished(4000):
            self.auto_process.kill()
            self.auto_process.waitForFinished(2000)

    def _parse_add_friend_interval_seconds(self) -> tuple[int, int] | None:
        """读取批量加好友间隔参数，返回秒。"""
        try:
            interval_min_minutes = float(
                self.interval_min_input.text().strip() or str(DEFAULT_ADD_FRIEND_INTERVAL_MIN_MINUTES)
            )
            interval_max_minutes = float(
                self.interval_max_input.text().strip() or str(DEFAULT_ADD_FRIEND_INTERVAL_MAX_MINUTES)
            )
        except ValueError:
            QMessageBox.warning(self, "参数错误", "间隔时间请输入数字（单位：分钟）。")
            return None

        if interval_min_minutes < MIN_ADD_FRIEND_INTERVAL_MINUTES:
            QMessageBox.warning(
                self,
                "参数错误",
                f"最小间隔不能小于 {int(MIN_ADD_FRIEND_INTERVAL_MINUTES)} 分钟。",
            )
            return None
        if interval_max_minutes < interval_min_minutes:
            QMessageBox.warning(self, "参数错误", "最大间隔不能小于最小间隔。")
            return None

        return int(interval_min_minutes * 60), int(interval_max_minutes * 60)

    def _start_add_friend_process(self, args: list[str], log_text: str) -> None:
        """启动批量加好友子进程并维护按钮状态。"""
        self.append_log(log_text)
        process = self._start_process("add_friend_by_phone.py", args, keep_ref=False)
        if process is not None:
            self.add_friend_process = process
            self.start_add_excel_btn.setEnabled(False)
            self.start_add_api_btn.setEnabled(False)
            self.stop_add_btn.setEnabled(True)
            process.finished.connect(self._on_add_friends_finished)

    def run_add_friends_from_excel(self) -> None:
        self._refresh_log_file_by_wxid()
        if self.add_friend_process is not None and self.add_friend_process.state() != QProcess.NotRunning:
            QMessageBox.information(self, "提示", "批量添加好友已在运行。")
            return

        selected, _filter = QFileDialog.getOpenFileName(
            self,
            "选择手机号Excel",
            str(self.desktop_dir),
            "Excel 文件 (*.xlsx *.xlsm);;所有文件 (*)",
        )
        if not selected:
            return
        interval = self._parse_add_friend_interval_seconds()
        if interval is None:
            return
        interval_min_seconds, interval_max_seconds = interval

        args = [
            "--source",
            "excel",
            "--excel-path",
            selected,
            "--interval-min",
            str(interval_min_seconds),
            "--interval-max",
            str(interval_max_seconds),
        ]
        greetings = self.greetings_input.text().strip()
        if greetings:
            args.extend(["--greetings", greetings])
        self._start_add_friend_process(args, f"批量添加将从Excel读取手机号: {selected}")

    def run_add_friends_from_api(self) -> None:
        self._refresh_log_file_by_wxid()
        if self.run_mode == "local":
            QMessageBox.information(self, "本地模式", "本地模式不调用后端批量加好友接口，请使用“上传Excel添加”。")
            return
        if self.add_friend_process is not None and self.add_friend_process.state() != QProcess.NotRunning:
            QMessageBox.information(self, "提示", "批量添加好友已在运行。")
            return
        interval = self._parse_add_friend_interval_seconds()
        if interval is None:
            return
        interval_min_seconds, interval_max_seconds = interval

        args = [
            "--source",
            "api",
            "--loop",
            "--interval-min",
            str(interval_min_seconds),
            "--interval-max",
            str(interval_max_seconds),
        ]
        current_wechat_id = self._resolve_current_api_wechat_id()
        if current_wechat_id:
            args.extend(["--wechat-id", current_wechat_id])
        greetings = self.greetings_input.text().strip()
        if greetings:
            args.extend(["--greetings", greetings])
        self._start_add_friend_process(args, "批量添加将通过接口实时获取待加手机号")

    def _on_add_friends_finished(self, _code: int, _status: QProcess.ExitStatus) -> None:
        self.start_add_excel_btn.setEnabled(True)
        self.start_add_api_btn.setEnabled(self.run_mode == "api")
        self.stop_add_btn.setEnabled(False)
        self.add_friend_process = None

    def stop_add_friends(self) -> None:
        if self.add_friend_process is None:
            return
        self.append_log("正在停止批量添加好友...")
        self.add_friend_process.terminate()
        if not self.add_friend_process.waitForFinished(4000):
            self.add_friend_process.kill()
            self.add_friend_process.waitForFinished(2000)

    def closeEvent(self, event: Any) -> None:  # noqa: N802
        if self.narrator_countdown_timer is not None:
            self.narrator_countdown_timer.stop()
            self.narrator_countdown_timer.deleteLater()
            self.narrator_countdown_timer = None
        if self.wechat_ui_poll_timer is not None:
            self.wechat_ui_poll_timer.stop()
            self.wechat_ui_poll_timer.deleteLater()
            self.wechat_ui_poll_timer = None
        if self.wechat_dock_timer is not None:
            self.wechat_dock_timer.stop()
            self.wechat_dock_timer.deleteLater()
            self.wechat_dock_timer = None
        if self.timed_send_schedule_timer is not None:
            self.timed_send_schedule_timer.stop()
            self.timed_send_schedule_timer.deleteLater()
            self.timed_send_schedule_timer = None
        self._close_narrator_countdown_box()
        if self.friend_list_worker is not None and self.friend_list_worker.isRunning():
            self.friend_list_worker.quit()
            self.friend_list_worker.wait(2000)
        if self.friend_sync_worker is not None and self.friend_sync_worker.isRunning():
            self.friend_sync_worker.quit()
            self.friend_sync_worker.wait(2000)
        if self.timed_send_worker is not None and self.timed_send_worker.isRunning():
            self.timed_send_worker.stop()
            self.timed_send_worker.wait(3000)
        if self.auto_process is not None and self.auto_process.state() != QProcess.NotRunning:
            self.stop_auto_reply()
        if self.add_friend_process is not None and self.add_friend_process.state() != QProcess.NotRunning:
            self.stop_add_friends()
        for p in self.child_processes:
            if p.state() != QProcess.NotRunning:
                p.terminate()
        super().closeEvent(event)


class AppController:
    """协调登录窗口和主窗口生命周期。"""

    def __init__(self, app: QApplication) -> None:
        self.app = app
        self.settings = QSettings(BOT_GUI_SETTINGS_ORG, BOT_GUI_SETTINGS_APP)
        self.api_client = WeChatAIClient.from_saved_state(auto_persist=True)
        self.main_window: MainWindow | None = None

    def run(self) -> int:
        if not self._show_login_window():
            return 0
        return self.app.exec_()

    def _show_login_window(self) -> bool:
        login = LoginWindow(None, self.api_client, self.settings)
        accepted = login.exec_() == QDialog.Accepted
        if not accepted:
            if self.main_window is not None:
                self.main_window.close()
                self.main_window = None
            return False
        self._open_main_window()
        return True

    def _open_main_window(self) -> None:
        if self.main_window is not None:
            self.main_window.close()
        self.main_window = MainWindow(api_client=self.api_client, on_auth_expired=self._handle_auth_expired)
        self.main_window.show()

    def _handle_auth_expired(self) -> None:
        if self.main_window is not None:
            self.main_window.close()
            self.main_window = None
        self.api_client.clear_state()
        if not self._show_login_window():
            self.app.quit()


def main() -> int:
    # 子命令模式：供打包后的 exe 内部调用具体脚本
    if len(sys.argv) >= 3 and sys.argv[1] == "--run-script":
        script_name = sys.argv[2]
        script_args = sys.argv[3:]
        return run_embedded_script(script_name, script_args)

    app = QApplication(sys.argv)
    app.setApplicationName("PyWechat Bot 控制台")
    controller = AppController(app)
    return controller.run()


if __name__ == "__main__":
    raise SystemExit(main())
