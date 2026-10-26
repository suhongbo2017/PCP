"""Byte-exact checks against the vectors in app/tests/test_control_msg_serialize.c."""
import struct

import pytest

from phonectl.core.protocol import control as msg
from phonectl.core.protocol.control import (
    BUTTON_PRIMARY,
    CopyKey,
    KeyAction,
    MotionAction,
    MsgType,
    PointerId,
)


def test_keycode_matches_c_vector():
    # action UP, AKEYCODE_ENTER, repeat 5, meta SHIFT_ON|SHIFT_LEFT_ON
    data = msg.keycode(KeyAction.UP, 66, 5, 0x01 | 0x40)
    assert data == bytes([0x00, 0x01, 0x00, 0x00, 0x00, 0x42, 0x00, 0x00, 0x00, 0x05, 0x00, 0x00, 0x00, 0x41])
    assert len(data) == 14


def test_text_matches_c_vector():
    data = msg.text("hello, world!")
    assert data == bytes([0x01, 0x00, 0x00, 0x00, 0x0D]) + b"hello, world!"
    assert len(data) == 18


def test_text_long_is_truncated_to_300_bytes():
    data = msg.text("a" * 400)
    assert len(data) == 5 + 300
    assert data[1:5] == struct.pack(">I", 300)


def test_text_truncation_keeps_utf8_boundary():
    # 299 ASCII bytes + a 3-byte Chinese char: the char must not be split.
    value = "a" * 299 + "中"
    data = msg.text(value)
    payload = data[5:]
    assert payload == ("a" * 299).encode()
    payload.decode("utf-8")  # must be valid UTF-8


def test_touch_event_matches_c_vector():
    data = msg.touch(
        MotionAction.DOWN,
        pointer_id=0x1234567887654321,
        x=100,
        y=200,
        screen_w=1080,
        screen_h=1920,
        pressure=1.0,
        action_button=BUTTON_PRIMARY,
        buttons=BUTTON_PRIMARY,
    )
    expected = bytes(
        [0x02, 0x00]
        + [0x12, 0x34, 0x56, 0x78, 0x87, 0x65, 0x43, 0x21]
        + [0x00, 0x00, 0x00, 0x64, 0x00, 0x00, 0x00, 0xC8]
        + [0x04, 0x38, 0x07, 0x80]
        + [0xFF, 0xFF]
        + [0x00, 0x00, 0x00, 0x01]
        + [0x00, 0x00, 0x00, 0x01]
    )
    assert data == expected
    assert len(data) == 32


def test_scroll_event_matches_c_vector():
    data = msg.scroll(260, 1026, 1080, 1920, hscroll=16, vscroll=-16, buttons=1)
    expected = bytes(
        [0x03]
        + [0x00, 0x00, 0x01, 0x04, 0x00, 0x00, 0x04, 0x02]
        + [0x04, 0x38, 0x07, 0x80]
        + [0x7F, 0xFF]
        + [0x80, 0x00]
        + [0x00, 0x00, 0x00, 0x01]
    )
    assert data == expected
    assert len(data) == 21


def test_back_or_screen_on_matches_c_vector():
    assert msg.back_or_screen_on(KeyAction.UP) == bytes([0x04, 0x01])


def test_payload_free_messages_are_one_byte():
    assert msg.simple(MsgType.EXPAND_NOTIFICATION_PANEL) == bytes([0x05])
    assert msg.simple(MsgType.EXPAND_SETTINGS_PANEL) == bytes([0x06])
    assert msg.simple(MsgType.COLLAPSE_PANELS) == bytes([0x07])
    assert msg.simple(MsgType.ROTATE_DEVICE) == bytes([0x0B])


def test_get_clipboard_matches_c_vector():
    assert msg.get_clipboard(CopyKey.COPY) == bytes([0x08, 0x01])


def test_set_clipboard_matches_c_vector():
    data = msg.set_clipboard(0x0102030405060708, "hello, world!", paste=True)
    expected = bytes([0x09, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x01, 0x00, 0x00, 0x00, 0x0D])
    assert data == expected + b"hello, world!"
    assert len(data) == 27


def test_set_display_power_matches_c_vector():
    assert msg.set_display_power(True) == bytes([0x0A, 0x01])


@pytest.mark.parametrize(
    "value, expected",
    [(1.0, 0xFFFF), (0.0, 0), (0.5, 0x8000), (0.9999, 65529)],  # truncation, as in C
)
def test_u16fp(value, expected):
    assert msg.to_u16fp(value) == expected


@pytest.mark.parametrize("value, expected", [(1.0, 0x7FFF), (-1.0, -0x8000), (0.0, 0)])
def test_i16fp(value, expected):
    assert msg.to_i16fp(value) == expected


def test_pointer_ids_are_u64_two_complement():
    data = msg.touch(MotionAction.MOVE, PointerId.GENERIC_FINGER, 0, 0, 1, 1)
    assert data[2:10] == bytes([0xFF] * 7 + [0xFE])  # -2 as u64


def test_chunk_text_splits_on_char_boundary():
    text = "中" * 150  # 450 bytes
    chunks = msg.chunk_text(text, max_bytes=300)
    assert "".join(chunks) == text
    assert all(len(c.encode()) <= 300 for c in chunks)
    assert len(chunks) == 2


def test_keycode_enum_values_match_android():
    from phonectl.core.protocol.control import Keycode

    assert (Keycode.HOME, Keycode.BACK, Keycode.ENTER, Keycode.DEL) == (3, 4, 66, 67)
    assert (Keycode.APP_SWITCH, Keycode.WAKEUP, Keycode.PASTE) == (187, 224, 279)
