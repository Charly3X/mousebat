"""Точка входа: python3 -m battary."""

from __future__ import annotations

import signal
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon

from .tray import Tray


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("battary")
    app.setApplicationDisplayName("battary")
    app.setQuitOnLastWindowClosed(False)  # окон нет, живём только иконкой

    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("Системный трей недоступен.", file=sys.stderr)
        return 1

    tray = Tray(app)
    tray.start()

    # Ctrl+C доходит до Qt только когда интерпретатор получает управление,
    # поэтому держим таймер, который регулярно возвращает его в Python.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    heartbeat = QTimer()
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start(500)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
