"""GUI 主程序别名入口。"""

from wechat_bot.pyqt_app import AppController, LoginWindow, MainWindow, main

__all__ = [
    "AppController",
    "LoginWindow",
    "MainWindow",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
