"""Gesture and message sequencing, checked against a recording fake channel."""
import struct

from phonectl.core.controller import Controller
from phonectl.core.protocol.control import KeyAction, Keycode, MotionAction, MsgType


class FakeChannel:
    def __init__(self, ack=True):
        self.sent: list[bytes] = []
        self._ack = ack
        self.clipboard = None

    def send(self, data: bytes) -> None:
        self.sent.append(data)

    def wait_ack(self, sequence: int, timeout: float) -> bool:
        return self._ack

    def request_clipboard(self, data: bytes, timeout: float):
        self.sent.append(data)
        return self.clipboard


def _ctl(ch=None):
    ch = ch or FakeChannel()
    return Controller(ch, screen_size=lambda: (1080, 2400)), ch


def _touch_action(data: bytes) -> int:
    assert data[0] == MsgType.INJECT_TOUCH_EVENT
    return data[1]


def test_tap_sends_down_then_up_at_point():
    ctl, ch = _ctl()
    ctl.tap(100, 200, hold=0)
    assert [_touch_action(d) for d in ch.sent] == [MotionAction.DOWN, MotionAction.UP]
    x, y, w, h = struct.unpack(">iiHH", ch.sent[0][10:22])
    assert (x, y, w, h) == (100, 200, 1080, 2400)
    # DOWN has pressure 1.0, UP has pressure 0.0 (as the C client does)
    assert struct.unpack(">H", ch.sent[0][22:24])[0] == 0xFFFF
    assert struct.unpack(">H", ch.sent[1][22:24])[0] == 0


def test_swipe_has_down_moves_and_up_with_linear_interpolation():
    ctl, ch = _ctl()
    ctl.swipe(0, 0, 1000, 500, duration=0.05, interval=0.01)  # 5 steps
    actions = [_touch_action(d) for d in ch.sent]
    assert actions == [MotionAction.DOWN] + [MotionAction.MOVE] * 5 + [MotionAction.UP]

    def xy(d):
        return struct.unpack(">ii", d[10:18])

    assert xy(ch.sent[1]) == (200, 100)
    assert xy(ch.sent[5]) == (1000, 500)
    assert xy(ch.sent[-1]) == (1000, 500)


def test_key_sends_down_and_up():
    ctl, ch = _ctl()
    ctl.key(Keycode.HOME)
    assert ch.sent == [
        bytes([MsgType.INJECT_KEYCODE, KeyAction.DOWN]) + struct.pack(">III", 3, 0, 0),
        bytes([MsgType.INJECT_KEYCODE, KeyAction.UP]) + struct.pack(">III", 3, 0, 0),
    ]


def test_wake_uses_wakeup_keycode():
    ctl, ch = _ctl()
    ctl.wake()
    assert struct.unpack(">BBIII", ch.sent[0])[2] == Keycode.WAKEUP


def test_long_text_is_chunked_into_messages():
    ctl, ch = _ctl()
    ctl.text("中" * 150)  # 450 bytes -> 2 messages
    assert len(ch.sent) == 2
    assert all(d[0] == MsgType.INJECT_TEXT for d in ch.sent)


def test_set_clipboard_uses_nonzero_sequence_and_reports_ack():
    ctl, ch = _ctl(FakeChannel(ack=True))
    assert ctl.set_clipboard("abc") is True
    seq = struct.unpack(">Q", ch.sent[0][1:9])[0]
    assert seq != 0

    ctl2, _ = _ctl(FakeChannel(ack=False))
    assert ctl2.set_clipboard("abc") is False


def test_sequence_increments_between_clipboard_sets():
    ctl, ch = _ctl()
    ctl.set_clipboard("a")
    ctl.set_clipboard("b")
    seqs = [struct.unpack(">Q", d[1:9])[0] for d in ch.sent]
    assert seqs[1] == seqs[0] + 1


def test_system_actions_are_single_byte_messages():
    ctl, ch = _ctl()
    ctl.expand_notifications()
    ctl.collapse_panels()
    ctl.rotate_device()
    assert ch.sent == [b"\x05", b"\x07", b"\x0b"]
