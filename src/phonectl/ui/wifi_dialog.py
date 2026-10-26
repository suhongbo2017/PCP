"""Dialog for wireless (Wi-Fi) connections: USB -> Wi-Fi, IP connect, Wireless-debugging pairing."""

from __future__ import annotations

import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.adb import Adb, AdbError, is_wireless_serial, normalize_address
from ..core.wifi import connect_with_retry, enable_wireless


class _Task(QObject):
    """Carries a worker's outcome back to the GUI thread.

    The signal is connected to a method of the dialog (a QObject living in the GUI
    thread), so Qt delivers it through the event queue instead of calling widget code
    from the worker thread.
    """

    done = Signal(object, object, object)  # (on_done callback, result, error)


class WifiDialog(QDialog):
    connected = Signal(str)  # host:port of a newly connected wireless device

    def __init__(self, adb: Adb, usb_serial: str | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("无线连接")
        self.setMinimumWidth(460)
        self._adb = adb
        self._usb_serial = usb_serial if usb_serial and not is_wireless_serial(usb_serial) else None
        self._busy = False
        self._tasks: list[_Task] = []  # keep references while workers run

        layout = QVBoxLayout(self)

        # 1. USB -> Wi-Fi
        usb_box = QGroupBox("方式一：用 USB 线开启无线（所有 Android 版本）")
        usb_row = QVBoxLayout(usb_box)
        self.usb_label = QLabel(
            f"当前 USB 设备：{self._usb_serial}" if self._usb_serial else "请先用 USB 线连接手机并授权"
        )
        self.enable_btn = QPushButton("开启无线调试并连接")
        self.enable_btn.setEnabled(self._usb_serial is not None)
        self.enable_btn.clicked.connect(self._enable_clicked)
        usb_row.addWidget(self.usb_label)
        usb_row.addWidget(self.enable_btn)
        layout.addWidget(usb_box)

        # 2. Connect by address
        addr_box = QGroupBox("方式二：输入手机 IP 地址连接")
        addr_row = QHBoxLayout(addr_box)
        self.address_edit = QLineEdit()
        self.address_edit.setPlaceholderText("192.168.1.23  或  192.168.1.23:5555")
        self.connect_btn = QPushButton("连接")
        self.connect_btn.clicked.connect(self._connect_clicked)
        self.address_edit.returnPressed.connect(self._connect_clicked)
        addr_row.addWidget(self.address_edit, 1)
        addr_row.addWidget(self.connect_btn)
        layout.addWidget(addr_box)

        # 3. Wireless debugging pairing (Android 11+)
        pair_box = QGroupBox("方式三：无线调试配对（Android 11+，无需 USB）")
        pair_form = QVBoxLayout(pair_box)
        pair_row = QHBoxLayout()
        self.pair_addr_edit = QLineEdit()
        self.pair_addr_edit.setPlaceholderText("配对地址 IP:端口")
        self.pair_code_edit = QLineEdit()
        self.pair_code_edit.setPlaceholderText("6 位配对码")
        self.pair_code_edit.setMaxLength(6)
        self.pair_code_edit.setFixedWidth(110)
        self.pair_btn = QPushButton("配对")
        self.pair_btn.clicked.connect(self._pair_clicked)
        pair_row.addWidget(self.pair_addr_edit, 1)
        pair_row.addWidget(self.pair_code_edit)
        pair_row.addWidget(self.pair_btn)
        hint = QLabel("手机：开发者选项 → 无线调试 → 使用配对码配对。配对后，用上面的 IP:端口（另一个端口）连接。")
        hint.setWordWrap(True)
        pair_form.addLayout(pair_row)
        pair_form.addWidget(hint)
        layout.addWidget(pair_box)

        # Bottom row
        bottom = QHBoxLayout()
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.disconnect_btn = QPushButton("断开全部无线设备")
        self.disconnect_btn.clicked.connect(self._disconnect_clicked)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.disconnect_btn)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

        self._set_busy(False)

    # ---- async helper ----------------------------------------------------

    def _run(self, work: Callable[[], object], on_done: Callable[[object], None], message: str) -> None:
        """Run adb work off the GUI thread; `on_done(result)` or show the error."""
        if self._busy:
            return
        self._set_busy(True)
        self.status.setText(message)
        task = _Task(self)
        self._tasks.append(task)
        task.done.connect(self._deliver)

        def job() -> None:
            try:
                task.done.emit(on_done, work(), None)
            except Exception as exc:  # any failure must reach the GUI, or the buttons stay disabled
                task.done.emit(on_done, None, exc)

        threading.Thread(target=job, name="wifi-adb", daemon=True).start()

    def _deliver(self, on_done, result, error) -> None:
        """Runs on the GUI thread."""
        self._set_busy(False)
        self._tasks.clear()  # only one task runs at a time (see _busy)
        if error is not None:
            self.status.setText(f"失败：{error}")
        else:
            on_done(result)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for btn in (self.enable_btn, self.connect_btn, self.pair_btn, self.disconnect_btn):
            btn.setEnabled(not busy)
        self.enable_btn.setEnabled(not busy and self._usb_serial is not None)

    # ---- actions ---------------------------------------------------------

    def _enable_clicked(self) -> None:
        serial = self._usb_serial
        if serial is None:
            return
        self._run(
            lambda: enable_wireless(self._adb, serial),
            self._connected_ok,
            "正在切换到无线调试（约 5 秒）…",
        )

    def _connect_clicked(self) -> None:
        try:
            address = normalize_address(self.address_edit.text())
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self._run(
            lambda: connect_with_retry(self._adb, address, attempts=2, delay=1.0),
            lambda _: self._connected_ok(address),
            f"正在连接 {address} …",
        )

    def _pair_clicked(self) -> None:
        address = self.pair_addr_edit.text().strip()
        code = self.pair_code_edit.text().strip()
        if not (code.isdigit() and len(code) == 6):
            self.status.setText("配对码必须是 6 位数字")
            return
        self._run(
            lambda: self._adb.pair(address, code),
            lambda _: self.status.setText(
                "配对成功。请在手机“无线调试”页面找到 IP:端口（不同于配对端口），填到方式二中连接。"
            ),
            f"正在配对 {address} …",
        )

    def _disconnect_clicked(self) -> None:
        self._run(
            lambda: self._adb.disconnect(),
            lambda _: self.status.setText("已断开全部无线设备"),
            "正在断开…",
        )

    def _connected_ok(self, address: str) -> None:
        self.address_edit.setText(address)
        self.status.setText(f"已连接：{address}")
        self.connected.emit(address)
