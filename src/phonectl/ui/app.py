"""GUI entry point."""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

from .main_window import MainWindow


def run_gui(serial: str | None = None, *, auto_connect: bool = False) -> int:
    # Use the exact (fractional) DPI scale so the mirror is sharp and input mapping is exact.
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(serial=serial)
    window.show()
    if auto_connect and window.device_box.count() == 1:
        QTimer.singleShot(0, window.connect_device)
    return app.exec()
