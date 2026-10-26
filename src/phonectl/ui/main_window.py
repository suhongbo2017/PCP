"""Main window: device picker, mirror view, side toolbar, status bar."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .. import config
from ..core.adb import Adb, AdbError, is_wireless_serial
from ..core.control_channel import ControlError
from ..core.protocol.control import Keycode
from ..core.session import ScrcpyError, ScrcpySession, ServerOptions
from ..automation.macro import Macro, Recorder, RecordingController, replay
from ..automation.runner import RunState, ScriptRunner
from .frame_image import frame_to_qimage
from .mirror_view import MirrorView
from .wifi_dialog import WifiDialog

log = logging.getLogger(__name__)

POLL_MS = 16  # frame polling interval (~60 Hz)
QUALITY_CHOICES: list[tuple[str, int]] = [
    ("720p", 720),
    ("1080p", 1080),
    ("1280", 1280),
    ("1600", 1600),
    ("原始分辨率", 0),
]


class _Bridge(QObject):
    """Carries results from worker threads to the GUI thread (queued signals)."""

    connected = Signal(object)  # ScrcpySession
    connect_failed = Signal(str)
    disconnected = Signal()
    message = Signal(str)
    script_log = Signal(str)
    script_done = Signal(object, str)
    replay_done = Signal(str)


class MainWindow(QMainWindow):
    def __init__(self, serial: str | None = None) -> None:
        super().__init__()
        self.setWindowTitle("phonectl")
        self.resize(560, 960)
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.center() - self.rect().center())  # start fully visible

        self._adb = Adb()
        self._session: ScrcpySession | None = None
        self._connecting = False
        self._last_frame = None
        self._shown_size: tuple[int, int] | None = None
        self._view_key: tuple | None = None
        self._control_buttons: list[QPushButton] = []
        self._script_runner: ScriptRunner | None = None
        self._recorder: Recorder | None = None
        self._recording_controller = None
        self._replay_stop = threading.Event()
        self._replay_thread: threading.Thread | None = None
        self._fps_frames = 0
        self._fps_since = time.monotonic()
        self._bridge = _Bridge(self)
        self._bridge.connected.connect(self._on_connected)
        self._bridge.connect_failed.connect(self._on_connect_failed)
        self._bridge.disconnected.connect(self._on_disconnected)
        self._bridge.message.connect(self.set_status)
        self._bridge.script_log.connect(self._append_script_log)
        self._bridge.script_done.connect(self._on_script_done)
        self._bridge.replay_done.connect(self._on_replay_done)

        self._build_ui()
        self.refresh_devices(preferred=serial)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(POLL_MS)
        self._update_buttons()

    # ---- UI construction -------------------------------------------------

    def _build_ui(self) -> None:
        top = QWidget()
        top_row = QHBoxLayout(top)
        top_row.setContentsMargins(6, 6, 6, 6)

        self.device_box = QComboBox()
        self.device_box.setMinimumWidth(200)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(lambda: self.refresh_devices())
        self.wifi_btn = QPushButton("无线连接")
        self.wifi_btn.clicked.connect(self.open_wifi_dialog)
        self.quality_box = QComboBox()
        for label, _ in QUALITY_CHOICES:
            self.quality_box.addItem(label)
        self.quality_box.setCurrentIndex(2)  # 1280
        self.connect_btn = QPushButton("连接")
        self.connect_btn.clicked.connect(self._on_connect_clicked)

        top_row.addWidget(QLabel("设备"))
        top_row.addWidget(self.device_box, 1)
        top_row.addWidget(self.refresh_btn)
        top_row.addWidget(self.wifi_btn)
        top_row.addWidget(QLabel("清晰度"))
        top_row.addWidget(self.quality_box)
        top_row.addWidget(self.connect_btn)

        self.view = MirrorView()
        self.view.paste_requested.connect(self.paste_pc_clipboard)
        self.view.unicode_typed.connect(self._on_unicode_typed)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(top)
        layout.addWidget(self.view, 1)
        self.setCentralWidget(central)

        self._build_side_toolbar()
        self._build_automation_dock()

        self.fps_label = QLabel("")
        self.size_label = QLabel("")
        self.status_label = QLabel("未连接")
        self.statusBar().addPermanentWidget(self.size_label)
        self.statusBar().addPermanentWidget(self.fps_label)
        self.statusBar().addWidget(self.status_label, 1)

    def _build_side_toolbar(self) -> None:
        bar = QToolBar("控制")
        bar.setOrientation(Qt.Orientation.Vertical)
        bar.setMovable(False)
        self.addToolBar(Qt.ToolBarArea.LeftToolBarArea, bar)

        def add(label: str, handler) -> QPushButton:
            btn = QPushButton(label)
            btn.setMinimumHeight(32)
            btn.clicked.connect(handler)
            bar.addWidget(btn)
            self._control_buttons.append(btn)
            return btn

        add("返回", lambda: self._send(lambda c: c.back()))
        add("主页", lambda: self._send(lambda c: c.home()))
        add("多任务", lambda: self._send(lambda c: c.app_switch()))
        bar.addSeparator()
        add("音量+", lambda: self._send(lambda c: c.key(Keycode.VOLUME_UP)))
        add("音量-", lambda: self._send(lambda c: c.key(Keycode.VOLUME_DOWN)))
        add("电源键", lambda: self._send(lambda c: c.key(Keycode.POWER)))
        add("息屏", lambda: self._send(lambda c: c.set_display_power(False)))
        add("亮屏", lambda: self._send(lambda c: c.set_display_power(True)))
        bar.addSeparator()
        add("通知栏", lambda: self._send(lambda c: c.expand_notifications()))
        add("收起面板", lambda: self._send(lambda c: c.collapse_panels()))
        add("旋转", lambda: self._send(lambda c: c.rotate_device()))
        bar.addSeparator()
        add("截图", self.save_screenshot)
        add("电脑→手机剪贴板", self.paste_pc_clipboard)
        add("手机→电脑剪贴板", self.fetch_phone_clipboard)

    def _build_automation_dock(self) -> None:
        dock = QDockWidget("自动化", self)
        dock.setObjectName("automationDock")
        panel = QWidget()
        layout = QVBoxLayout(panel)
        row = QHBoxLayout()
        self.run_script_btn = QPushButton("运行脚本…")
        self.run_script_btn.clicked.connect(self.choose_script)
        self.stop_script_btn = QPushButton("停止")
        self.stop_script_btn.clicked.connect(self.stop_script)
        self.record_btn = QPushButton("开始录制")
        self.record_btn.clicked.connect(self.toggle_recording)
        self.replay_btn = QPushButton("回放宏…")
        self.replay_btn.clicked.connect(self.choose_macro)
        for button in (self.run_script_btn, self.stop_script_btn, self.record_btn, self.replay_btn):
            row.addWidget(button)
        layout.addLayout(row)
        self.script_log = QPlainTextEdit()
        self.script_log.setReadOnly(True)
        self.script_log.setMaximumBlockCount(1000)
        self.script_log.setPlaceholderText("脚本输出与录制状态")
        layout.addWidget(self.script_log)
        dock.setWidget(panel)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
        self._automation_dock = dock
        self._update_automation_buttons()

    def _append_script_log(self, text: str) -> None:
        self.script_log.appendPlainText(text)

    def _on_script_done(self, state: RunState, message: str) -> None:
        self._script_runner = None
        self._append_script_log(f"脚本 {state.value}: {message}")
        self._update_automation_buttons()

    def _update_automation_buttons(self) -> None:
        connected = self._session is not None
        running = self._script_runner is not None and self._script_runner.running
        replaying = self._replay_thread is not None and self._replay_thread.is_alive()
        recording = self._recorder is not None
        self.run_script_btn.setEnabled(connected and not running and not replaying)
        self.stop_script_btn.setEnabled(running or replaying)
        self.record_btn.setEnabled(connected and not running and not replaying)
        self.record_btn.setText("停止录制" if recording else "开始录制")
        self.replay_btn.setEnabled(connected and not running and not replaying and not recording)

    def choose_script(self) -> None:
        if self._session is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "运行 Python 脚本", str(config.PROJECT_ROOT), "Python (*.py)")
        if not path:
            return
        runner = ScriptRunner(
            self._session,
            log_fn=self._bridge.script_log.emit,
            on_done=lambda state, msg: self._bridge.script_done.emit(state, msg),
        )
        self._script_runner = runner
        self._update_automation_buttons()
        try:
            runner.run_file(path)
        except Exception as exc:
            self._script_runner = None
            self._append_script_log(f"脚本加载失败：{exc}")
            self._update_automation_buttons()

    def stop_script(self) -> None:
        if self._script_runner is not None:
            self._script_runner.stop()
        if self._replay_thread is not None and self._replay_thread.is_alive():
            self._replay_stop.set()

    def toggle_recording(self) -> None:
        if self._session is None or self._session.controller is None:
            return
        if self._recorder is None:
            self._recorder = Recorder()
            self._recording_controller = self._session.controller
            self._recorder.start()
            self._session.controller = RecordingController(self._recording_controller, self._recorder)
            self._append_script_log("开始录制：鼠标和工具栏操作将写入宏")
        else:
            recorder, self._recorder = self._recorder, None
            self._session.controller = self._recording_controller
            self._recording_controller = None
            macro = recorder.stop()
            out_dir = config.PROJECT_ROOT / "macros"
            name = time.strftime("macro_%Y%m%d_%H%M%S.json")
            macro.save(out_dir / name)
            self._append_script_log(f"已保存宏：{out_dir / name}（{len(macro.steps)} 步，{macro.duration:.1f}s）")
        self._update_automation_buttons()

    def choose_macro(self) -> None:
        if self._session is None or self._session.controller is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "回放宏", str(config.PROJECT_ROOT / "macros"), "Macro (*.json)")
        if not path:
            return
        try:
            macro = Macro.load(path)
        except (OSError, ValueError, KeyError) as exc:
            self._append_script_log(f"宏文件无效：{exc}")
            return
        self._replay_stop.clear()
        controller = self._session.controller
        self._replay_thread = threading.Thread(
            target=self._replay_macro, args=(macro, controller), name="macro-replay", daemon=True
        )
        self._replay_thread.start()
        self._update_automation_buttons()

    def _replay_macro(self, macro: Macro, controller) -> None:
        try:
            count = replay(macro, controller, should_stop=self._replay_stop.is_set)
            message = f"宏回放完成：{count}/{len(macro.steps)} 步"
        except Exception as exc:
            message = f"宏回放失败：{exc}"
        self._bridge.replay_done.emit(message)

    def _on_replay_done(self, message: str) -> None:
        self._replay_thread = None
        self._append_script_log(message)
        self._update_automation_buttons()

    # ---- devices & connection -------------------------------------------

    def refresh_devices(self, preferred: str | None = None) -> None:
        current = preferred or self.device_box.currentData()
        self.device_box.clear()
        try:
            devices = self._adb.devices()
        except AdbError as exc:
            self.set_status(f"adb 错误：{exc}")
            return
        for dev in devices:
            model = dev.props.get("model", "")
            tag = " [无线]" if dev.wireless else ""
            label = f"{model or dev.serial} ({dev.serial}){tag}" + ("" if dev.ready else f" [{dev.state}]")
            self.device_box.addItem(label, dev.serial)
        if current is not None:
            idx = self.device_box.findData(current)
            if idx >= 0:
                self.device_box.setCurrentIndex(idx)
        if self.device_box.count() == 0:
            self.set_status("没有检测到设备，请检查 USB 调试")

    def open_wifi_dialog(self) -> None:
        # Prefer the selected USB device; otherwise any ready USB device.
        usb = None
        selected = self.device_box.currentData()
        if selected and not is_wireless_serial(selected):
            usb = selected
        else:
            usb = next((d.serial for d in self._adb.devices() if d.ready and not d.wireless), None)
        dlg = WifiDialog(self._adb, usb, self)
        dlg.connected.connect(lambda address: self.refresh_devices(preferred=address))
        dlg.exec()
        self.refresh_devices()

    def _on_connect_clicked(self) -> None:
        if self._session is not None:
            self.disconnect_device()
        else:
            self.connect_device()

    def connect_device(self) -> None:
        serial = self.device_box.currentData()
        if not serial or self._connecting:
            return
        dev_state = next((d for d in self._adb.devices() if d.serial == serial), None)
        if dev_state is None or not dev_state.ready:
            self.set_status("设备未就绪，请检查 USB 调试授权")
            return

        max_size = QUALITY_CHOICES[self.quality_box.currentIndex()][1]
        self._connecting = True
        self.set_status("正在连接…")
        self._update_buttons()

        def work() -> None:
            session = ScrcpySession(serial, adb=self._adb, options=ServerOptions(max_size=max_size))
            try:
                session.start()
                session.wait_screen_size(timeout=15)
            except (ScrcpyError, AdbError, OSError) as exc:
                session.close()
                self._bridge.connect_failed.emit(str(exc))
                return
            self._bridge.connected.emit(session)

        threading.Thread(target=work, name="connect", daemon=True).start()

    def _on_connected(self, session: ScrcpySession) -> None:
        self._connecting = False
        self._session = session
        self._last_frame = None
        self._shown_size = None
        self.view.attach(session)
        self.view.setFocus()
        self.setWindowTitle(f"phonectl - {session.info.device_name}")
        self.set_status(f"已连接：{session.info.device_name}")
        self._update_buttons()
        self._update_automation_buttons()

    def _on_connect_failed(self, message: str) -> None:
        self._connecting = False
        self._update_buttons()
        self._update_automation_buttons()
        self.set_status("连接失败")
        QMessageBox.warning(self, "连接失败", message)

    def disconnect_device(self) -> None:
        session = self._session
        if session is None:
            return
        self._session = None
        self.view.attach(None)
        self.setWindowTitle("phonectl")
        self.fps_label.setText("")
        self.size_label.setText("")
        self.set_status("正在断开…")
        self._update_buttons()
        self._update_automation_buttons()

        def work() -> None:
            session.close()
            self._bridge.disconnected.emit()

        threading.Thread(target=work, name="disconnect", daemon=True).start()

    def _on_disconnected(self) -> None:
        self.set_status("未连接")
        self._update_buttons()
        self._update_automation_buttons()

    def _update_buttons(self) -> None:
        connected = self._session is not None
        self.connect_btn.setText("断开" if connected else "连接")
        self.connect_btn.setEnabled(not self._connecting)
        self.device_box.setEnabled(not connected and not self._connecting)
        self.refresh_btn.setEnabled(not connected and not self._connecting)
        self.wifi_btn.setEnabled(not connected and not self._connecting)
        self.quality_box.setEnabled(not connected and not self._connecting)
        for btn in self._control_buttons:
            btn.setEnabled(connected)

    # ---- frames ----------------------------------------------------------

    def _tick(self) -> None:
        session = self._session
        if session is None:
            return
        frame = session.latest_frame
        if frame is None:
            return

        size_changed = (frame.width, frame.height) != self._shown_size
        view_key = (self.view.width(), self.view.height(), self.view.devicePixelRatioF())
        if frame is self._last_frame and not size_changed and view_key == self._view_key:
            return

        if frame is not self._last_frame:
            self._fps_frames += 1
        self._last_frame = frame
        self._view_key = view_key
        self._shown_size = (frame.width, frame.height)

        w, h = self.view.physical_size_for(frame.width, frame.height)
        image: QImage = frame_to_qimage(frame, w, h)
        image.setDevicePixelRatio(self.view.devicePixelRatioF())
        self.view.set_image(image)

        now = time.monotonic()
        if now - self._fps_since >= 1.0:
            self.fps_label.setText(f"{self._fps_frames / (now - self._fps_since):.0f} fps")
            self._fps_frames = 0
            self._fps_since = now
        self.size_label.setText(f"{frame.width}×{frame.height}")

    # ---- actions ---------------------------------------------------------

    def _send(self, action) -> None:
        if self._session is None or self._session.controller is None:
            self.set_status("未连接")
            return
        try:
            action(self._session.controller)
        except ControlError as exc:
            self.set_status(f"发送失败：{exc}")

    def save_screenshot(self) -> None:
        if self._session is None or self._last_frame is None:
            self.set_status("没有可保存的画面")
            return
        out_dir = config.PROJECT_ROOT / "screenshots"
        out_dir.mkdir(exist_ok=True)
        path = Path(out_dir) / time.strftime("screenshot_%Y%m%d_%H%M%S.png")
        self._last_frame.to_image().save(path)
        self.set_status(f"已保存截图：{path}")

    def paste_pc_clipboard(self) -> None:
        if self._session is None or self._session.controller is None:
            self.set_status("未连接")
            return
        text = QGuiApplication.clipboard().text()
        if not text:
            self.set_status("电脑剪贴板为空")
            return
        ok = self._session.controller.set_clipboard(text)
        self.set_status("已同步到手机剪贴板（长按输入框即可粘贴）" if ok else "手机未确认剪贴板写入")

    def fetch_phone_clipboard(self) -> None:
        if self._session is None or self._session.controller is None:
            self.set_status("未连接")
            return
        text = self._session.controller.get_clipboard(timeout=2.0)
        if text is None:
            self.set_status("没有收到手机剪贴板内容")
            return
        QGuiApplication.clipboard().setText(text)
        self.set_status(f"已复制到电脑剪贴板：{text[:30]}")

    def _on_unicode_typed(self, text: str) -> None:
        # INJECT_TEXT cannot type CJK characters, so put them on the phone clipboard instead.
        if self._session is None or self._session.controller is None:
            return
        ok = self._session.controller.set_clipboard(text)
        self.set_status(
            "中文已放入手机剪贴板：长按输入框选择“粘贴”" if ok else "手机未确认剪贴板写入"
        )

    # ---- misc ------------------------------------------------------------

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def closeEvent(self, event) -> None:
        self._timer.stop()
        if self._script_runner is not None:
            self._script_runner.stop()
            self._script_runner.join(2)
        if self._replay_thread is not None and self._replay_thread.is_alive():
            self._replay_stop.set()
            self._replay_thread.join(2)
        if self._session is not None:
            session = self._session
            if self._recorder is not None:
                session.controller = self._recording_controller
                self._recorder.stop()
                self._recorder = None
            self._session = None
            session.close()  # synchronous on exit; the server stops with its sockets
        super().closeEvent(event)
