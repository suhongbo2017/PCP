"""The mirror widget: draws the device picture and turns mouse/keyboard input into device events."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent, QPainter, QWheelEvent
from PySide6.QtWidgets import QWidget

from .geometry import fit_rect, view_to_device
from .key_map import android_keycode_for, is_text_input

META_CTRL_ON = 0x1000
MOVE_INTERVAL_MS = 16  # ~60 Hz cap for touch moves while dragging
ANDROID_KEYCODE_A = 29  # KEYCODE_A .. KEYCODE_Z are consecutive from 29


class MirrorView(QWidget):
    """Shows the latest frame (letterboxed) and forwards input to the device.

    The view does not own the session; `attach()` gives it the session whose
    controller receives the events. Without a session, input is ignored.
    """

    paste_requested = Signal()  # Ctrl+V: main window copies the PC clipboard to the phone
    unicode_typed = Signal(str)  # non-ASCII text cannot be injected as text (see docs)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image: QImage | None = None
        self._session = None
        self._pressed = False
        self._last_point: tuple[int, int] | None = None
        self._pending_move: tuple[int, int] | None = None
        self._move_timer = QTimer(self)
        self._move_timer.setSingleShot(True)
        self._move_timer.timeout.connect(self._flush_move)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(200, 300)
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(self.backgroundRole(), QColor("black"))
        self.setPalette(palette)

    # ---- wiring ----------------------------------------------------------

    def attach(self, session) -> None:
        self._release_if_pressed()
        self._session = session
        if session is None:
            self._image = None
        self.update()

    def set_image(self, image: QImage) -> None:
        self._image = image
        self.update()

    def clear_image(self) -> None:
        self._image = None
        self.update()

    def physical_size_for(self, frame_w: int, frame_h: int) -> tuple[int, int]:
        """Pixel size to scale a frame to, for the current widget size and device pixel ratio."""
        dpr = self.devicePixelRatioF()
        rect = fit_rect(self.width(), self.height(), frame_w, frame_h)
        return max(1, int(rect.w * dpr)), max(1, int(rect.h * dpr))

    # ---- painting --------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("black"))
        if self._image is not None and not self._image.isNull():
            logical_w = self._image.width() / self._image.devicePixelRatio()
            logical_h = self._image.height() / self._image.devicePixelRatio()
            rect = fit_rect(self.width(), self.height(), logical_w, logical_h)
            painter.drawImage(QRectF(rect.x, rect.y, rect.w, rect.h), self._image)
        painter.end()

    # ---- coordinate mapping ---------------------------------------------

    def _device_point(self, pos: QPointF) -> tuple[int, int] | None:
        size = self._session.screen_size if self._session is not None else None
        if size is None:
            return None
        return view_to_device(pos.x(), pos.y(), self.width(), self.height(), size[0], size[1])

    def _controller(self):
        if self._session is None or self._session.controller is None:
            return None
        return self._session.controller

    # ---- mouse -----------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.setFocus()
        ctl = self._controller()
        if ctl is None:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            point = self._device_point(event.position())
            if point is None:
                return
            self._pressed = True
            self._last_point = point
            ctl.touch_down(*point)
        elif event.button() == Qt.MouseButton.RightButton:
            ctl.back()
        elif event.button() == Qt.MouseButton.MiddleButton:
            ctl.home()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._pressed or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        point = self._device_point(event.position())
        if point is None or point == self._last_point:
            return
        self._last_point = point
        self._pending_move = point
        if not self._move_timer.isActive():
            self._move_timer.start(MOVE_INTERVAL_MS)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._release_if_pressed()

    def wheelEvent(self, event: QWheelEvent) -> None:
        ctl = self._controller()
        point = self._device_point(event.position())
        if ctl is None or point is None:
            return
        delta = event.angleDelta()
        if delta.x() == 0 and delta.y() == 0:
            return
        ctl.scroll(point[0], point[1], hscroll=delta.x() / 120.0, vscroll=delta.y() / 120.0)

    def _flush_move(self) -> None:
        ctl = self._controller()
        if self._pending_move is not None and ctl is not None and self._pressed:
            ctl.touch_move(*self._pending_move)
        self._pending_move = None

    def _release_if_pressed(self) -> None:
        if not self._pressed:
            return
        self._move_timer.stop()
        ctl = self._controller()
        if ctl is not None and self._last_point is not None:
            if self._pending_move is not None:
                ctl.touch_move(*self._pending_move)  # keep ordering: move, then up
            ctl.touch_up(*self._last_point)
        self._pending_move = None
        self._pressed = False

    # ---- keyboard --------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        ctl = self._controller()
        if ctl is None:
            return super().keyPressEvent(event)

        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        key = event.key()

        if ctrl and key == Qt.Key.Key_V:
            self.paste_requested.emit()
            return
        if ctrl and Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            # Ctrl+letter (select all, copy, ...) as a keycode with the CTRL meta state.
            code = ANDROID_KEYCODE_A + (key - Qt.Key.Key_A)
            ctl.key(code, metastate=META_CTRL_ON)
            return

        code = android_keycode_for(key)
        if code is not None:
            ctl.key(code)
            return

        text = event.text()
        if ctrl or not is_text_input(text):
            return
        if text.isascii():
            ctl.text(text)
        else:
            self.unicode_typed.emit(text)

    # ---- helpers ---------------------------------------------------------

    def focusOutEvent(self, event) -> None:
        self._release_if_pressed()
        super().focusOutEvent(event)

