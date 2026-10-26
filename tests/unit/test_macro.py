"""Recording and replaying input (fake controller, real timing)."""
import time

from phonectl.automation.macro import Macro, Recorder, RecordingController, replay
from phonectl.core.protocol.control import KeyAction, Keycode


class FakeController:
    def __init__(self):
        self.calls = []

    def touch_down(self, x, y):
        self.calls.append(("touch_down", x, y))

    def touch_move(self, x, y):
        self.calls.append(("touch_move", x, y))

    def touch_up(self, x, y):
        self.calls.append(("touch_up", x, y))

    def key(self, code, metastate=0):
        self.calls.append(("key", code, metastate))

    def text(self, value):
        self.calls.append(("text", value))

    def back(self):
        self.calls.append(("back",))

    def helper_not_recorded(self):
        self.calls.append(("helper",))


def test_recording_captures_input_calls_with_offsets():
    inner = FakeController()
    rec = Recorder()
    ctl = RecordingController(inner, rec)
    rec.start()
    ctl.touch_down(10, 20)
    time.sleep(0.05)
    ctl.touch_move(15, 25)
    ctl.touch_up(15, 25)
    ctl.key(Keycode.HOME)
    ctl.helper_not_recorded()  # not an input op -> not recorded, still forwarded
    macro = rec.stop()

    assert [s.op for s in macro.steps] == ["touch_down", "touch_move", "touch_up", "key"]
    assert macro.steps[1].t >= 0.04
    assert inner.calls[-1] == ("helper",)
    assert inner.calls[0] == ("touch_down", 10, 20)


def test_nothing_recorded_when_stopped():
    inner = FakeController()
    rec = Recorder()
    ctl = RecordingController(inner, rec)
    ctl.back()  # recorder idle
    assert rec.stop().steps == []
    assert inner.calls == [("back",)]


def test_macro_json_round_trip_keeps_chinese_and_enums(tmp_path):
    inner = FakeController()
    rec = Recorder()
    ctl = RecordingController(inner, rec)
    rec.start()
    ctl.key(Keycode.ENTER, metastate=0)
    ctl.text("你好 hello")
    ctl.touch_down(1, 2)  # IntEnum-free args stay plain
    macro = rec.stop()
    path = macro.save(tmp_path / "m.json")
    loaded = Macro.load(path)
    assert [s.op for s in loaded.steps] == ["key", "text", "touch_down"]
    assert loaded.steps[1].args == ["你好 hello"]
    assert "你好" in path.read_text(encoding="utf-8")


def test_replay_performs_same_calls_in_order(tmp_path):
    macro = Macro.from_dict(
        {
            "version": 1,
            "steps": [
                {"t": 0.0, "op": "touch_down", "args": [5, 6], "kwargs": {}},
                {"t": 0.05, "op": "touch_up", "args": [5, 6], "kwargs": {}},
                {"t": 0.10, "op": "key", "args": [Keycode.BACK], "kwargs": {"metastate": 0}},
            ],
        }
    )
    inner = FakeController()
    t0 = time.monotonic()
    done = replay(macro, inner)
    assert done == 3
    assert inner.calls == [("touch_down", 5, 6), ("touch_up", 5, 6), ("key", 4, 0)]
    assert time.monotonic() - t0 >= 0.09


def test_replay_can_be_stopped_midway():
    macro = Macro.from_dict({"version": 1, "steps": [
        {"t": 0.0, "op": "back", "args": [], "kwargs": {}},
        {"t": 5.0, "op": "back", "args": [], "kwargs": {}},
    ]})
    inner = FakeController()
    stop_at = time.monotonic() + 0.2
    done = replay(macro, inner, should_stop=lambda: time.monotonic() > stop_at)
    assert done == 1


def test_speed_scales_timing():
    macro = Macro.from_dict({"version": 1, "steps": [
        {"t": 0.0, "op": "back", "args": [], "kwargs": {}},
        {"t": 0.4, "op": "back", "args": [], "kwargs": {}},
    ]})
    t0 = time.monotonic()
    replay(macro, FakeController(), speed=4.0)
    assert time.monotonic() - t0 < 0.3


def test_invalid_macro_is_rejected():
    import pytest

    with pytest.raises(ValueError):
        Macro.from_dict({"version": 1, "steps": [{"t": 0, "op": "rm_rf", "args": []}]})
    with pytest.raises(ValueError):
        Macro.from_dict({"version": 9, "steps": []})
    with pytest.raises(ValueError, match="invalid timing"):
        Macro.from_dict({"version": 1, "steps": [{"t": float("nan"), "op": "back", "args": []}]})
    with pytest.raises(ValueError, match="ordered by time"):
        Macro.from_dict({"version": 1, "steps": [
            {"t": 2, "op": "back", "args": []},
            {"t": 1, "op": "back", "args": []},
        ]})
