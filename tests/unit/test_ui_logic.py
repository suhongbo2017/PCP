"""Pure UI logic: coordinate mapping, key mapping and frame conversion (no window needed)."""
import av
import numpy as np
import pytest
from PySide6.QtCore import Qt

from phonectl.core.protocol.control import Keycode
from phonectl.ui.frame_image import frame_to_qimage
from phonectl.ui.geometry import fit_rect, view_to_device
from phonectl.ui.key_map import android_keycode_for, is_text_input


# ---- geometry ------------------------------------------------------------


def test_fit_rect_letterboxes_wide_view():
    # 1080x2400 phone in a 1000x1000 view: height-limited, centered horizontally
    r = fit_rect(1000, 1000, 1080, 2400)
    assert r.h == pytest.approx(1000)
    assert r.w == pytest.approx(450)
    assert r.x == pytest.approx(275)
    assert r.y == pytest.approx(0)


def test_fit_rect_pillarbox_for_tall_view():
    r = fit_rect(400, 1000, 1000, 1000)  # square frame in a tall view
    assert (r.w, r.h, r.x, r.y) == (400, 400, 0, 300)


def test_view_to_device_maps_corners_and_center():
    # view 1000x1000, frame 1000x2000 -> picture 500x1000 centered at x=250
    assert view_to_device(250, 0, 1000, 1000, 1000, 2000) == (0, 0)
    assert view_to_device(749, 999, 1000, 1000, 1000, 2000) == (998, 1998)
    assert view_to_device(500, 500, 1000, 1000, 1000, 2000) == (500, 1000)


def test_view_to_device_ignores_letterbox_margin():
    assert view_to_device(10, 500, 1000, 1000, 1000, 2000) is None  # left margin
    assert view_to_device(990, 500, 1000, 1000, 1000, 2000) is None  # right margin


def test_view_to_device_clamps_to_frame():
    assert view_to_device(749.9, 999.9, 1000, 1000, 1000, 2000) == (999, 1999)


def test_view_to_device_handles_degenerate_sizes():
    assert view_to_device(0, 0, 0, 0, 1080, 2400) is None
    assert view_to_device(0, 0, 100, 100, 0, 0) is None


# ---- keys ----------------------------------------------------------------


@pytest.mark.parametrize(
    "qt_key, code",
    [
        (Qt.Key.Key_Return, Keycode.ENTER),
        (Qt.Key.Key_Backspace, Keycode.DEL),
        (Qt.Key.Key_Escape, Keycode.BACK),
        (Qt.Key.Key_Left, Keycode.DPAD_LEFT),
        (Qt.Key.Key_VolumeUp, Keycode.VOLUME_UP),
    ],
)
def test_special_keys_map_to_keycodes(qt_key, code):
    assert android_keycode_for(int(qt_key)) == code


def test_printable_keys_are_not_keycodes():
    assert android_keycode_for(int(Qt.Key.Key_A)) is None


def test_text_detection():
    assert is_text_input("a")
    assert is_text_input("你")
    assert not is_text_input("")
    assert not is_text_input("\r")
    assert not is_text_input("\x08")


# ---- frame conversion ----------------------------------------------------


def _frame(w: int, h: int, value: int) -> av.VideoFrame:
    arr = np.full((h, w, 3), value, dtype=np.uint8)
    return av.VideoFrame.from_ndarray(arr, format="rgb24")


def test_frame_to_qimage_scales_to_requested_size():
    img = frame_to_qimage(_frame(320, 240, 128), 160, 120)
    assert (img.width(), img.height()) == (160, 120)
    assert not img.isNull()


def test_frame_to_qimage_keeps_colors():
    img = frame_to_qimage(_frame(64, 64, 200), 32, 32)
    px = img.pixelColor(10, 10)
    assert abs(px.red() - 200) <= 2 and abs(px.green() - 200) <= 2 and abs(px.blue() - 200) <= 2


def test_frame_to_qimage_handles_padded_rows():
    # Widths that are not multiples of the FFmpeg alignment produce padded rows.
    img = frame_to_qimage(_frame(37, 23, 90), 37, 23)
    assert (img.width(), img.height()) == (37, 23)
    assert img.pixelColor(36, 22).red() == 90


def test_frame_to_qimage_is_detached_from_source():
    frame = _frame(16, 16, 50)
    img = frame_to_qimage(frame, 16, 16)
    del frame
    assert img.pixelColor(0, 0).red() == 50


def test_broadcast_controller_sends_to_all_devices():
    from phonectl.ui.main_window import _BroadcastController

    class Controller:
        def __init__(self):
            self.calls = []

        def home(self):
            self.calls.append("home")

    class Session:
        def __init__(self):
            self.controller = Controller()

    first, second = Session(), Session()
    _BroadcastController([first, second]).home()
    assert first.controller.calls == ["home"]
    assert second.controller.calls == ["home"]


def test_mirror_view_controller_records_input_for_macro():
    from phonectl.ui.main_window import _ViewController

    class RecorderStub:
        def __init__(self):
            self.calls = []

        def record(self, op, args, kwargs):
            self.calls.append((op, args, kwargs))

    class ControllerStub:
        def __init__(self):
            self.calls = []

        def touch_down(self, x, y):
            self.calls.append((x, y))

    class SessionStub:
        serial = "device-1"
        screen_size = (1080, 2400)
        controller = ControllerStub()

    class GroupStub:
        sync_enabled = False
        sessions = [SessionStub()]

        def __init__(self):
            self.recorder = RecorderStub()

        def record_call(self, op, args, kwargs):
            self.recorder.record(op, args, kwargs)

    group = GroupStub()
    _ViewController(group.sessions[0], group).touch_down(120, 240)
    assert group.sessions[0].controller.calls == [(120, 240)]
    assert group.recorder.calls == [("touch_down", (120, 240), {})]


def test_main_window_screen_power_buttons_send_matching_commands():
    from PySide6.QtWidgets import QApplication

    from phonectl.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    buttons = {button.text(): button for button in window._control_buttons}
    calls = []

    class ControllerStub:
        def set_display_power(self, on):
            calls.append(on)

    class SessionStub:
        controller = ControllerStub()

        def close(self):
            pass

    window._session = SessionStub()
    buttons["息屏"].setEnabled(True)
    buttons["亮屏"].setEnabled(True)
    buttons["息屏"].click()
    buttons["亮屏"].click()
    assert calls == [False, True]
    window.close()
