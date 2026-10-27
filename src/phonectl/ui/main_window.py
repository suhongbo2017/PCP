"""Main window: device picker, mirror view, side toolbar, status bar."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QMainWindow,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
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
    connect_failed = Signal(object, str)
    disconnected = Signal()
    device_lost = Signal(object, str)
    message = Signal(str)
    script_log = Signal(str)
    script_done = Signal(object, str)
    replay_done = Signal(str)


class _BroadcastController:
    def __init__(self, sessions: list[ScrcpySession]) -> None:
        self._controllers = [s.controller for s in sessions if s.controller is not None]

    def __getattr__(self, name):
        def call(*args, **kwargs):
            result = None
            errors = []
            for controller in self._controllers:
                try:
                    value = getattr(controller, name)(*args, **kwargs)
                    if result is None:
                        result = value
                except Exception as exc:
                    errors.append(str(exc))
            if errors:
                raise ControlError("; ".join(errors))
            return result
        return call


class _SessionView:
    def __init__(self, session: ScrcpySession, group: _SessionGroup) -> None:
        self._session = session
        self._group = group
        self.controller = _ViewController(session, group)

    @property
    def screen_size(self):
        return self._session.screen_size


class _ViewController:
    def __init__(self, session: ScrcpySession, group: _SessionGroup) -> None:
        self._session = session
        self._group = group

    def __getattr__(self, name):
        def call(*args, **kwargs):
            coordinate_actions = {"touch_down", "touch_move", "touch_up", "scroll"}
            if not self._group.sync_enabled:
                self._group.record_call(name, args, kwargs)
                return getattr(self._session.controller, name)(*args, **kwargs)
            self._group.record_call(name, args, kwargs)
            targets = self._group.sessions if name in coordinate_actions else []
            if not targets:
                return getattr(self._group.broadcast_controller, name)(*args, **kwargs)
            source_size = self._session.screen_size
            if source_size is None:
                return getattr(self._session.controller, name)(*args, **kwargs)
            results = []
            errors = []
            for target in targets:
                try:
                    target_size = target.screen_size
                    mapped = args
                    if target is not self._session and target_size is not None and len(args) >= 2:
                        sx = target_size[0] / source_size[0]
                        sy = target_size[1] / source_size[1]
                        mapped = (round(args[0] * sx), round(args[1] * sy), *args[2:])
                    results.append(getattr(target.controller, name)(*mapped, **kwargs))
                except Exception as exc:
                    errors.append(str(exc))
            if errors:
                raise ControlError("; ".join(errors))
            return next((value for value in results if value is not None), None)
        return call


class _SessionGroup:
    def __init__(self, sessions: list[ScrcpySession]) -> None:
        self.sessions = sessions
        self.sync_enabled = False
        self.active_serial = sessions[0].serial
        self.primary = sessions[0]
        self.serial = ",".join(s.serial for s in sessions)
        self.controllers = {s.serial: s.controller for s in sessions if s.controller is not None}
        self.broadcast_controller = _BroadcastController(sessions)
        self._controller_override = None
        self._recorder: Recorder | None = None

    @property
    def controller(self):
        if self._controller_override is not None:
            return self._controller_override
        if self.sync_enabled:
            return self.broadcast_controller
        return self.controllers.get(self.active_serial, self.controllers[self.primary.serial])

    @controller.setter
    def controller(self, value):
        self._controller_override = value

    def record_call(self, name: str, args: tuple, kwargs: dict) -> None:
        if self._recorder is not None:
            self._recorder.record(name, args, kwargs)

    @property
    def latest_frame(self):
        return self.primary.latest_frame

    @property
    def screen_size(self):
        return self.primary.screen_size

    @property
    def info(self):
        return self.primary.info

    def wait_frame(self, timeout=10):
        return self.primary.wait_frame(timeout)

    def page_video(self, direction: int, *, sync: bool) -> None:
        source = self.controllers.get(self.active_serial, self.controllers[self.primary.serial])
        source_size = next(
            (session.screen_size for session in self.sessions if session.serial == self.active_serial),
            self.primary.screen_size,
        )
        if source_size is None:
            raise ControlError("设备屏幕尺寸尚未就绪")
        targets = self.sessions if sync else [self.primary if self.active_serial == self.primary.serial else next(
            session for session in self.sessions if session.serial == self.active_serial
        )]
        errors = []
        source_width, source_height = source_size
        source_delta = round(source_height * 0.325) * direction
        self.record_call(
            "swipe",
            (source_width // 2, source_height // 2 + source_delta,
             source_width // 2, source_height // 2 - source_delta),
            {"duration": 0.25},
        )
        for target in targets:
            size = target.screen_size
            if size is None:
                errors.append(f"{target.serial}: 屏幕尺寸尚未就绪")
                continue
            width, height = size
            center_x = width // 2
            center_y = height // 2
            delta = round(height * 0.325) * direction
            try:
                target.controller.swipe(
                    center_x, center_y + delta, center_x, center_y - delta, duration=0.25
                )
            except Exception as exc:
                errors.append(f"{target.serial}: {exc}")
        if errors:
            raise ControlError("; ".join(errors))

    def close(self):
        for session in self.sessions:
            session.close()


class MainWindow(QMainWindow):
    def __init__(self, serial: str | None = None) -> None:
        super().__init__()
        self.setWindowTitle("phonectl")
        self.resize(560, 960)
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.center() - self.rect().center())  # start fully visible

        self._adb = Adb()
        self._session: _SessionGroup | None = None
        self._sessions: dict[str, ScrcpySession] = {}
        self._active_serial: str | None = None
        self._connecting = False
        self._connect_generation = 0
        self._connect_cancel: threading.Event | None = None
        self._last_frame = None
        self._shown_size: tuple[int, int] | None = None
        self._view_key: tuple | None = None
        self._control_buttons: list[QPushButton] = []
        self._script_runner: ScriptRunner | None = None
        self._recorder: Recorder | None = None
        self._replay_stop = threading.Event()
        self._replay_thread: threading.Thread | None = None
        self._fps_frames = 0
        self._fps_since = time.monotonic()
        self._bridge = _Bridge(self)
        self._bridge.connected.connect(self._on_connected)
        self._bridge.connect_failed.connect(self._on_connect_failed)
        self._bridge.disconnected.connect(self._on_disconnected)
        self._bridge.device_lost.connect(self._on_device_lost)
        self._bridge.message.connect(self.set_status)
        self._bridge.script_log.connect(self._append_script_log)
        self._bridge.script_done.connect(self._on_script_done)
        self._bridge.replay_done.connect(self._on_replay_done)

        self._build_ui()
        self.refresh_devices(preferred=serial)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(POLL_MS)
        self._page_timer = QTimer(self)
        self._page_timer.timeout.connect(self.next_video)
        self._update_buttons()

    # ---- UI construction -------------------------------------------------

    def _build_ui(self) -> None:
        top = QWidget()
        top_row = QHBoxLayout(top)
        top_row.setContentsMargins(6, 6, 6, 6)

        self.device_box = QListWidget()
        self.device_box.setMinimumWidth(220)
        self.device_box.setMaximumHeight(88)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(lambda: self.refresh_devices())
        self.wifi_btn = QPushButton("无线连接")
        self.wifi_btn.clicked.connect(self.open_wifi_dialog)
        self.quality_box = QComboBox()
        for label, _ in QUALITY_CHOICES:
            self.quality_box.addItem(label)
        self.quality_box.setCurrentIndex(2)  # 1280
        self.video_mode_box = QComboBox()
        self.video_mode_box.addItem("全部显示", "all")
        self.video_mode_box.addItem("主设备显示", "primary")
        self.video_mode_box.addItem("仅控制", "control")
        self.video_mode_box.setToolTip("选择视频流模式，减少多设备连接时的网络与解码开销")
        self.max_fps_box = QSpinBox()
        self.max_fps_box.setRange(5, 60)
        self.max_fps_box.setValue(30)
        self.max_fps_box.setSuffix(" fps")
        self.bit_rate_box = QComboBox()
        for label, rate in (("2 Mbps", 2_000_000), ("4 Mbps", 4_000_000), ("6 Mbps", 6_000_000), ("8 Mbps", 8_000_000)):
            self.bit_rate_box.addItem(label, rate)
        self.bit_rate_box.setCurrentIndex(1)
        self.connect_btn = QPushButton("连接")
        self.connect_btn.clicked.connect(self._on_connect_clicked)

        top_row.addWidget(QLabel("设备（可多选）"))
        top_row.addWidget(self.device_box, 1)
        top_row.addWidget(self.refresh_btn)
        top_row.addWidget(self.wifi_btn)
        top_row.addWidget(QLabel("清晰度"))
        top_row.addWidget(self.quality_box)
        top_row.addWidget(self.video_mode_box)
        top_row.addWidget(self.max_fps_box)
        top_row.addWidget(self.bit_rate_box)
        top_row.addWidget(self.connect_btn)
        self.sync_checkbox = QCheckBox("同步操作")
        self.sync_checkbox.setToolTip("关闭时仅操作当前选中的设备；打开时将工具栏、键盘和脚本操作广播到全部设备")
        self.sync_checkbox.setChecked(False)
        self.sync_checkbox.toggled.connect(self._set_sync_enabled)
        top_row.addWidget(self.sync_checkbox)

        self.view = MirrorView()
        self._views: dict[str, MirrorView] = {}
        self._view_grid = QGridLayout()
        self._view_grid.setContentsMargins(0, 0, 0, 0)
        self._view_grid.addWidget(self.view, 0, 0)
        self._connect_view_signals(self.view)

        self._view_container = QWidget()
        self._view_container.setLayout(self._view_grid)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(top)
        layout.addWidget(self._view_container, 1)
        self.setCentralWidget(central)

        self._build_side_toolbar()
        self._build_automation_dock()

        self.fps_label = QLabel("")
        self.size_label = QLabel("")
        self.status_label = QLabel("未连接")
        self.statusBar().addPermanentWidget(self.size_label)
        self.statusBar().addPermanentWidget(self.fps_label)
        self.statusBar().addWidget(self.status_label, 1)

    def _connect_view_signals(self, view: MirrorView) -> None:
        view.activated.connect(lambda v=view: self._activate_view(v))
        view.paste_requested.connect(self.paste_pc_clipboard)
        view.unicode_typed.connect(self._on_unicode_typed)

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
        add("上一条", self.previous_video)
        add("下一条", self.next_video)
        self.auto_page_checkbox = QCheckBox("自动翻页")
        self.auto_page_checkbox.setToolTip("按设定间隔自动执行下一条")
        self.auto_page_checkbox.toggled.connect(self._set_auto_page)
        bar.addWidget(self.auto_page_checkbox)
        self.page_interval = QSpinBox()
        self.page_interval.setRange(1, 300)
        self.page_interval.setValue(5)
        self.page_interval.setSuffix(" 秒")
        self.page_interval.setToolTip("自动翻页间隔")
        self.page_interval.valueChanged.connect(self._update_page_timer)
        bar.addWidget(self.page_interval)
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

    def _update_page_timer(self, _value: int = 0) -> None:
        if self.auto_page_checkbox.isChecked():
            self._page_timer.setInterval(self.page_interval.value() * 1000)

    def _set_auto_page(self, enabled: bool) -> None:
        if enabled and self._session is not None:
            self._page_timer.setInterval(self.page_interval.value() * 1000)
            self._page_timer.start()
            self.set_status(f"自动翻页已开启：每 {self.page_interval.value()} 秒下一条")
        else:
            self._page_timer.stop()
            if not enabled:
                self.set_status("自动翻页已关闭")

    def next_video(self) -> None:
        if self._session is None:
            self.set_status("未连接")
            return
        try:
            self._session.page_video(1, sync=self.sync_checkbox.isChecked())
        except ControlError as exc:
            self.set_status(f"翻页失败：{exc}")

    def previous_video(self) -> None:
        if self._session is None:
            self.set_status("未连接")
            return
        try:
            self._session.page_video(-1, sync=self.sync_checkbox.isChecked())
        except ControlError as exc:
            self.set_status(f"翻页失败：{exc}")

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
            self._recorder.start()
            self._session._recorder = self._recorder
            self._append_script_log("开始录制：鼠标、键盘和工具栏操作将写入宏")
        else:
            recorder, self._recorder = self._recorder, None
            self._session._recorder = None
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
        controller = self._target_controller()
        if controller is None:
            self.set_status("未连接或未选择设备")
            return
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

    def selected_serials(self) -> list[str]:
        return [
            self.device_box.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.device_box.count())
            if self.device_box.item(i).checkState() == Qt.CheckState.Checked
        ]

    def refresh_devices(self, preferred: str | None = None) -> None:
        selected = set(self.selected_serials())
        if preferred:
            selected.add(preferred)
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
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, dev.serial)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if dev.serial in selected else Qt.CheckState.Unchecked)
            self.device_box.addItem(item)
        if self.device_box.count() == 0:
            self.set_status("没有检测到设备，请检查 USB 调试")

    def open_wifi_dialog(self) -> None:
        # Prefer the selected USB device; otherwise any ready USB device.
        usb = None
        selected = self.selected_serials()
        usb_serial = selected[0] if selected else None
        if usb_serial and not is_wireless_serial(usb_serial):
            usb = usb_serial
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
        serials = self.selected_serials()
        if not serials or self._connecting:
            return
        self._connect_generation += 1
        generation = self._connect_generation
        cancel = threading.Event()
        self._connect_cancel = cancel
        max_size = QUALITY_CHOICES[self.quality_box.currentIndex()][1]
        mode = self.video_mode_box.currentData()
        max_fps = self.max_fps_box.value()
        bit_rate = self.bit_rate_box.currentData()
        self._connecting = True
        self.set_status("正在连接…")
        self._update_buttons()

        def work() -> None:
            sessions = []
            try:
                devices = {d.serial: d for d in self._adb.devices()}
                invalid = [s for s in serials if s not in devices or not devices[s].ready]
                if invalid:
                    raise ScrcpyError(f"设备未就绪：{', '.join(invalid)}")
                for serial in serials:
                    if cancel.is_set():
                        break
                    video = mode == "all" or (mode == "primary" and serial == serials[0])
                    options = ServerOptions(
                        video=video,
                        max_size=max_size,
                        max_fps=max_fps,
                        video_bit_rate=bit_rate,
                    )
                    session = ScrcpySession(
                        serial,
                        adb=self._adb,
                        options=options,
                        on_disconnect=lambda s=serial, g=generation: self._bridge.device_lost.emit(g, s),
                    )
                    session.start()
                    sessions.append(session)
                    if cancel.is_set():
                        session.close()
                        break
                    if video:
                        session.wait_screen_size(timeout=15)
                    elif session.screen_size is None:
                        raise ScrcpyError(f"{serial}: 无法通过 ADB 获取屏幕尺寸")
                if cancel.is_set():
                    for session in sessions:
                        session.close()
                    return
            except (ScrcpyError, AdbError, OSError) as exc:
                for session in sessions:
                    session.close()
                self._bridge.connect_failed.emit(generation, str(exc))
                return
            self._bridge.connected.emit((generation, sessions))

        threading.Thread(target=work, name="connect", daemon=True).start()

    def _on_connected(self, result) -> None:
        generation, sessions = result
        if generation != self._connect_generation:
            for session in sessions:
                session.close()
            return
        self._connecting = False
        self._connect_cancel = None
        self._sessions = {session.serial: session for session in sessions}
        self._session = _SessionGroup(sessions)
        self._last_frame = None
        self._shown_size = None
        while self._view_grid.count():
            item = self._view_grid.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self.view:
                widget.setParent(None)
        self._views = {}
        columns = 1 if len(sessions) == 1 else 2
        for index, device_session in enumerate(sessions):
            view = self.view if index == 0 else MirrorView()
            if index:
                self._connect_view_signals(view)
            view.attach(_SessionView(device_session, self._session))
            view.setVisible(device_session.options.video)
            row, col = divmod(index, columns)
            self._view_grid.addWidget(view, row, col)
            self._views[device_session.serial] = view
        self._active_serial = sessions[0].serial
        self.view.setFocus()
        if not any(session.options.video for session in sessions):
            self.set_status("已连接，仅控制模式：视频流已关闭")
        self.setWindowTitle(f"phonectl - {len(sessions)} 台设备")
        if len(sessions) > 1:
            self.resize(max(self.width(), 1100), max(self.height(), 900))
        self.set_status(f"已连接 {len(sessions)} 台设备，当前设备：{sessions[0].info.device_name}；同步操作关闭")
        self._update_buttons()
        self._update_automation_buttons()
        if self.auto_page_checkbox.isChecked():
            self._set_auto_page(True)

    def _on_connect_failed(self, generation: int, message: str) -> None:
        if generation != self._connect_generation:
            return
        self._connecting = False
        self._connect_cancel = None
        self._update_buttons()
        self._update_automation_buttons()
        self.set_status("连接失败")
        QMessageBox.warning(self, "连接失败", message)

    def disconnect_device(self) -> None:
        if self._connecting:
            self._connect_generation += 1
            if self._connect_cancel is not None:
                self._connect_cancel.set()
            self._connecting = False
            self._connect_cancel = None
            self.set_status("正在取消连接…")
            self._update_buttons()
            self._update_automation_buttons()
            return
        group = self._session
        if group is None:
            return
        self._connect_generation += 1
        self._session = None
        self._sessions = {}
        self._active_serial = None
        self._page_timer.stop()
        for view in self._views.values():
            view.attach(None)
        self._views.clear()
        self._view_grid.addWidget(self.view, 0, 0)
        self.setWindowTitle("phonectl")
        self.fps_label.setText("")
        self.size_label.setText("")
        self.set_status("正在断开…")
        self._update_buttons()
        self._update_automation_buttons()

        def work() -> None:
            group.close()
            self._bridge.disconnected.emit()

        threading.Thread(target=work, name="disconnect", daemon=True).start()

    def _on_disconnected(self) -> None:
        self.set_status("未连接")
        self._update_buttons()
        self._update_automation_buttons()

    def _on_device_lost(self, generation: int, serial: str) -> None:
        if generation != self._connect_generation or self._session is None:
            return
        if serial not in self._sessions:
            return
        if self._recorder is not None:
            self._recorder.stop()
            self._recorder = None
        self._session._recorder = None
        self._connect_generation += 1
        group = self._session
        self._session = None
        self._sessions = {}
        self._active_serial = None
        self._page_timer.stop()
        self._set_sync_enabled(False)
        for view in self._views.values():
            view.attach(None)
            view.clear_image()
        self._views.clear()
        self._view_grid.addWidget(self.view, 0, 0)
        self.setWindowTitle("phonectl")
        self.fps_label.setText("")
        self.size_label.setText("")
        self.set_status(f"设备 {serial} 网络/控制连接已断开，正在清理会话…")
        self._update_buttons()
        self._update_automation_buttons()

        def work() -> None:
            try:
                group.close()
            finally:
                self._bridge.message.emit(f"设备 {serial} 已断开，请检查网络后重连")
                self._bridge.disconnected.emit()

        threading.Thread(target=work, name="device-lost-cleanup", daemon=True).start()

    def _update_buttons(self) -> None:
        connected = self._session is not None
        self.connect_btn.setText("断开全部" if connected else "连接选中")
        self.connect_btn.setEnabled(not self._connecting)
        self.device_box.setEnabled(not connected and not self._connecting)
        self.refresh_btn.setEnabled(not connected and not self._connecting)
        self.wifi_btn.setEnabled(not connected and not self._connecting)
        self.quality_box.setEnabled(not connected and not self._connecting)
        self.video_mode_box.setEnabled(not connected and not self._connecting)
        self.max_fps_box.setEnabled(not connected and not self._connecting)
        self.bit_rate_box.setEnabled(not connected and not self._connecting)
        for btn in self._control_buttons:
            btn.setEnabled(connected)
        self.auto_page_checkbox.setEnabled(connected)
        self.page_interval.setEnabled(connected)

    # ---- frames ----------------------------------------------------------

    def _tick(self) -> None:
        session = self._session
        if session is None:
            return
        lost = next((s.serial for s in session.sessions if s.disconnected), None)
        if lost is not None:
            self._on_device_lost(self._connect_generation, lost)
            return
        frames = []
        for serial, view in self._views.items():
            device_session = self._sessions.get(serial)
            frame = device_session.latest_frame if device_session is not None else None
            if frame is None:
                continue
            frames.append(frame)
            w, h = view.physical_size_for(frame.width, frame.height)
            image: QImage = frame_to_qimage(frame, w, h)
            image.setDevicePixelRatio(view.devicePixelRatioF())
            view.set_image(image)
        if not frames:
            return
        frame = frames[0]
        if frame is not self._last_frame:
            self._fps_frames += 1
            self._last_frame = frame
        self._shown_size = (frame.width, frame.height)

        now = time.monotonic()
        if now - self._fps_since >= 1.0:
            self.fps_label.setText(f"{self._fps_frames / (now - self._fps_since):.0f} fps")
            self._fps_frames = 0
            self._fps_since = now
        self.size_label.setText(f"{frame.width}×{frame.height}")

    # ---- actions ---------------------------------------------------------

    def _set_sync_enabled(self, enabled: bool) -> None:
        if self._session is not None:
            self._session.sync_enabled = enabled
            self._session._controller_override = None
        mode = "已开启，同步到全部设备" if enabled else "已关闭，仅操作当前设备"
        self.set_status(f"同步操作{mode}")

    def _activate_view(self, view: MirrorView) -> None:
        for serial, candidate in self._views.items():
            if candidate is view:
                self._active_serial = serial
                if self._session is not None:
                    self._session.active_serial = serial
                session = self._sessions.get(serial)
                if session is not None:
                    self.set_status(f"当前设备：{session.info.device_name}")
                return

    def _target_controller(self):
        if self._session is None:
            return None
        if self.sync_checkbox.isChecked():
            controller = getattr(self._session, "broadcast_controller", self._session.controller)
        else:
            controllers = getattr(self._session, "controllers", None)
            controller = self._session.controller if controllers is None else controllers.get(
                self._active_serial or "", self._session.controller
            )
        if self._recorder is not None:
            return RecordingController(controller, self._recorder)
        return controller

    def _send(self, action) -> None:
        controller = self._target_controller()
        if controller is None:
            self.set_status("未连接或未选择设备")
            return
        try:
            action(controller)
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
        controller = self._target_controller()
        if controller is None:
            self.set_status("未连接或未选择设备")
            return
        text = QGuiApplication.clipboard().text()
        if not text:
            self.set_status("电脑剪贴板为空")
            return
        ok = controller.set_clipboard(text)
        self.set_status("已发送到目标设备剪贴板（长按输入框即可粘贴）" if ok else "手机未确认剪贴板写入")

    def fetch_phone_clipboard(self) -> None:
        controller = self._target_controller()
        if controller is None:
            self.set_status("未连接或未选择设备")
            return
        text = controller.get_clipboard(timeout=2.0)
        if text is None:
            self.set_status("没有收到手机剪贴板内容")
            return
        QGuiApplication.clipboard().setText(text)
        self.set_status(f"已复制到电脑剪贴板：{text[:30]}")

    def _on_unicode_typed(self, text: str) -> None:
        # INJECT_TEXT cannot type CJK characters, so put them on the phone clipboard instead.
        controller = self._target_controller()
        if controller is None:
            return
        ok = controller.set_clipboard(text)
        self.set_status(
            "中文已放入手机剪贴板：长按输入框选择“粘贴”" if ok else "手机未确认剪贴板写入"
        )

    # ---- misc ------------------------------------------------------------

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def closeEvent(self, event) -> None:
        self._timer.stop()
        self._page_timer.stop()
        if self._script_runner is not None:
            self._script_runner.stop()
            self._script_runner.join(2)
        if self._replay_thread is not None and self._replay_thread.is_alive():
            self._replay_stop.set()
            self._replay_thread.join(2)
        if self._session is not None:
            session = self._session
            if self._recorder is not None:
                session._recorder = None
                self._recorder.stop()
                self._recorder = None
            self._session = None
            session.close()  # synchronous on exit; the server stops with its sockets
        super().closeEvent(event)
