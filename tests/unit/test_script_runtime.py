"""Scripts, the runner and Device API, exercised with fake sessions."""
import threading
import time

import cv2
import numpy as np
import pytest

from phonectl.automation.device import Device, ImageNotFound, ScriptStopped
from phonectl.automation.runner import RunState, ScriptError, ScriptRunner, load_script


class FakeFrame:
    def __init__(self, img):
        self.img = img

    def to_ndarray(self, format):
        assert format == "bgr24"
        return self.img


class FakeController:
    def __init__(self):
        self.calls = []

    def tap(self, x, y, hold=0.05):
        self.calls.append(("tap", x, y))

    def swipe(self, x1, y1, x2, y2, duration=0.3):
        self.calls.append(("swipe", x1, y1, x2, y2))

    def key(self, code, metastate=0):
        self.calls.append(("key", int(code)))

    def text(self, value):
        self.calls.append(("text", value))

    def set_clipboard(self, value, paste=False, timeout=2.0):
        self.calls.append(("clipboard", value))
        return True

    def wake(self):
        self.calls.append(("wake",))


class FakeSession:
    serial = "FAKE1"

    def __init__(self, screen_img=None):
        self.controller = FakeController()
        self.adb = None
        self._screen = screen_img if screen_img is not None else np.zeros((400, 240, 3), np.uint8)

    def wait_screen_size(self, timeout=10):
        h, w = self._screen.shape[:2]
        return w, h

    def wait_frame(self, timeout=10):
        return FakeFrame(self._screen)


def _write(tmp_path, body):
    path = tmp_path / "script.py"
    path.write_text(body, encoding="utf-8")
    return path


def test_script_runs_and_finishes(tmp_path):
    session = FakeSession()
    path = _write(tmp_path, "def run(dev):\n    dev.tap(10, 20)\n    dev.key('home')\n    dev.text('hi')\n")
    runner = ScriptRunner(session, log_fn=lambda msg: None)
    runner.run_file(path)
    runner.join(5)
    assert runner.state is RunState.FINISHED
    assert session.controller.calls == [("tap", 10, 20), ("key", 3), ("text", "hi")]


def test_stop_interrupts_a_long_sleep():
    session = FakeSession()
    runner = ScriptRunner(session, log_fn=lambda msg: None)
    runner.run(lambda dev: dev.sleep(60))
    time.sleep(0.1)
    start = time.monotonic()
    runner.stop()
    runner.join(5)
    assert time.monotonic() - start < 2
    assert runner.state is RunState.STOPPED


def test_script_exception_is_reported(tmp_path):
    path = _write(tmp_path, "def run(dev):\n    raise KeyError('boom')\n")
    logs = []
    runner = ScriptRunner(FakeSession(), log_fn=logs.append)
    runner.run_file(path)
    runner.join(5)
    assert runner.state is RunState.FAILED
    assert "KeyError" in runner.error
    assert any("Traceback" in line for line in logs)


def test_script_without_run_function_is_rejected(tmp_path):
    with pytest.raises(ScriptError, match=r"run\(dev\)"):
        load_script(_write(tmp_path, "x = 1\n"))


def test_script_top_level_code_only_runs_in_worker(tmp_path):
    marker = tmp_path / "executed.txt"
    path = _write(tmp_path, f"from pathlib import Path\nPath({str(marker)!r}).write_text('yes')\ndef run(dev):\n    pass\n")
    fn = load_script(path)
    assert not marker.exists()
    runner = ScriptRunner(FakeSession(), log_fn=lambda msg: None)
    runner.run(fn)
    runner.join(5)
    assert marker.read_text() == "yes"


def test_script_syntax_error_has_location(tmp_path):
    with pytest.raises(ScriptError, match=r":1: syntax error"):
        load_script(_write(tmp_path, "def run(dev)\n    pass\n"))


def test_cannot_start_two_scripts():
    runner = ScriptRunner(FakeSession(), log_fn=lambda msg: None)
    runner.run(lambda dev: dev.sleep(1), name="first")
    with pytest.raises(ScriptError, match="already running"):
        runner.run(lambda dev: None, name="second")
    runner.stop()
    runner.join(5)


def test_find_and_tap_image_taps_center(tmp_path):
    rng = np.random.default_rng(3)
    img = np.zeros((400, 240, 3), np.uint8)
    icon = rng.integers(0, 255, size=(40, 40, 3), dtype=np.uint8)
    img[200:240, 100:140] = icon
    template = tmp_path / "icon.png"
    assert cv2.imwrite(str(template), icon)
    session = FakeSession(img)
    match = Device(session, log_fn=lambda msg: None).tap_image(template, timeout=2, threshold=0.95)
    assert (match.x, match.y) == (100, 200)
    assert session.controller.calls == [("tap", 120, 220)]


def test_wait_for_times_out(tmp_path):
    tpl = np.random.default_rng(1).integers(0, 255, (30, 30, 3), dtype=np.uint8)
    path = tmp_path / "absent.png"
    assert cv2.imwrite(str(path), tpl)
    with pytest.raises(ImageNotFound, match="not found within"):
        Device(FakeSession(), log_fn=lambda msg: None).wait_for(path, timeout=0.25, threshold=0.99)


def test_relative_tap_and_swipe_use_screen_fractions():
    session = FakeSession(np.zeros((1000, 500, 3), np.uint8))
    dev = Device(session, log_fn=lambda msg: None)
    dev.tap_rel(0.5, 0.25)
    dev.swipe_up(distance=0.5)
    assert session.controller.calls == [("tap", 250, 250), ("swipe", 250, 750, 250, 250)]


def test_cjk_text_uses_clipboard_and_returns_false():
    logs = []
    session = FakeSession()
    dev = Device(session, log_fn=logs.append)
    assert dev.text("你好") is False
    assert session.controller.calls == [("clipboard", "你好")]
    assert any("paste" in line for line in logs)


def test_unknown_key_name_is_an_error():
    with pytest.raises(ValueError, match="unknown key"):
        Device(FakeSession(), log_fn=lambda msg: None).key("warp_drive")


def test_sleep_honours_stop_event():
    stop = threading.Event()
    stop.set()
    with pytest.raises(ScriptStopped):
        Device(FakeSession(), stop_event=stop, log_fn=lambda msg: None).sleep(5)
