"""Control messages (PC -> device) as raw bytes.

Layout mirrors app/src/control_msg.c; the byte vectors in
app/tests/test_control_msg_serialize.c are reproduced in tests/unit.
All integers are big-endian.
"""

from __future__ import annotations

import struct
from enum import IntEnum

MAX_SIZE = 1 << 18  # SC_CONTROL_MSG_MAX_SIZE
INJECT_TEXT_MAX_LENGTH = 300  # bytes (UTF-8)
CLIPBOARD_TEXT_MAX_LENGTH = MAX_SIZE - 14  # bytes (UTF-8)

SEQUENCE_INVALID = 0  # clipboard ack not requested

_U64_MASK = (1 << 64) - 1


class MsgType(IntEnum):
    INJECT_KEYCODE = 0
    INJECT_TEXT = 1
    INJECT_TOUCH_EVENT = 2
    INJECT_SCROLL_EVENT = 3
    BACK_OR_SCREEN_ON = 4
    EXPAND_NOTIFICATION_PANEL = 5
    EXPAND_SETTINGS_PANEL = 6
    COLLAPSE_PANELS = 7
    GET_CLIPBOARD = 8
    SET_CLIPBOARD = 9
    SET_DISPLAY_POWER = 10
    ROTATE_DEVICE = 11


class KeyAction(IntEnum):
    DOWN = 0
    UP = 1


class MotionAction(IntEnum):
    DOWN = 0
    UP = 1
    MOVE = 2
    CANCEL = 3


class CopyKey(IntEnum):
    NONE = 0
    COPY = 1
    CUT = 2


class PointerId(IntEnum):
    """Signed values from control_msg.h; serialized as u64."""

    MOUSE = -1
    GENERIC_FINGER = -2
    VIRTUAL_FINGER = -3


BUTTON_PRIMARY = 1


class Keycode(IntEnum):
    """Subset of android/keycodes.h."""

    HOME = 3
    BACK = 4
    DPAD_UP = 19
    DPAD_DOWN = 20
    DPAD_LEFT = 21
    DPAD_RIGHT = 22
    DPAD_CENTER = 23
    VOLUME_UP = 24
    VOLUME_DOWN = 25
    POWER = 26
    CAMERA = 27
    TAB = 61
    SPACE = 62
    ENTER = 66
    DEL = 67  # backspace
    MENU = 82
    SEARCH = 84
    PAGE_UP = 92
    PAGE_DOWN = 93
    ESCAPE = 111
    FORWARD_DEL = 112
    MOVE_HOME = 122
    MOVE_END = 123
    APP_SWITCH = 187
    SLEEP = 223
    WAKEUP = 224
    CUT = 277
    COPY = 278
    PASTE = 279


# ---- fixed-point helpers (util/binary.h) -----------------------------------


def to_u16fp(f: float) -> int:
    """Map [0, 1] to u16 (truncating), 1.0 -> 0xffff."""
    if not 0.0 <= f <= 1.0:
        raise ValueError(f"pressure out of range: {f}")
    return min(int(f * 65536), 0xFFFF)


def to_i16fp(f: float) -> int:
    """Map [-1, 1] to i16 (truncating), 1.0 -> 0x7fff, -1.0 -> 0x8000."""
    f = max(-1.0, min(1.0, f))
    return max(-0x8000, min(int(f * 32768), 0x7FFF))


# ---- text helpers ----------------------------------------------------------


def _truncate_utf8(text: str, max_bytes: int) -> bytes:
    data = text.encode("utf-8")
    if len(data) <= max_bytes:
        return data
    cut = max_bytes
    while cut > 0 and (data[cut] & 0xC0) == 0x80:  # do not split a multi-byte char
        cut -= 1
    return data[:cut]


def chunk_text(text: str, max_bytes: int = INJECT_TEXT_MAX_LENGTH) -> list[str]:
    """Split text into pieces whose UTF-8 size fits in max_bytes, never splitting a character."""
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for ch in text:
        n = len(ch.encode("utf-8"))
        if size + n > max_bytes and current:
            chunks.append("".join(current))
            current, size = [], 0
        current.append(ch)
        size += n
    if current:
        chunks.append("".join(current))
    return chunks


# ---- serializers -----------------------------------------------------------


def keycode(action: KeyAction, code: int, repeat: int = 0, metastate: int = 0) -> bytes:
    return struct.pack(">BBIII", MsgType.INJECT_KEYCODE, action, code, repeat, metastate)


def text(value: str) -> bytes:
    data = _truncate_utf8(value, INJECT_TEXT_MAX_LENGTH)
    return struct.pack(">BI", MsgType.INJECT_TEXT, len(data)) + data


def touch(
    action: MotionAction,
    pointer_id: int,
    x: int,
    y: int,
    screen_w: int,
    screen_h: int,
    pressure: float = 1.0,
    action_button: int = 0,
    buttons: int = 0,
) -> bytes:
    return struct.pack(
        ">BBQiiHHHII",
        MsgType.INJECT_TOUCH_EVENT,
        action,
        pointer_id & _U64_MASK,
        x,
        y,
        screen_w,
        screen_h,
        to_u16fp(pressure),
        action_button,
        buttons,
    )


def scroll(
    x: int,
    y: int,
    screen_w: int,
    screen_h: int,
    hscroll: float,
    vscroll: float,
    buttons: int = 0,
) -> bytes:
    """hscroll/vscroll are in "clicks" in [-16, 16], as in the C client."""
    return struct.pack(
        ">BiiHHhhI",
        MsgType.INJECT_SCROLL_EVENT,
        x,
        y,
        screen_w,
        screen_h,
        to_i16fp(hscroll / 16),
        to_i16fp(vscroll / 16),
        buttons,
    )


def back_or_screen_on(action: KeyAction) -> bytes:
    return struct.pack(">BB", MsgType.BACK_OR_SCREEN_ON, action)


def simple(msg_type: MsgType) -> bytes:
    """Messages without payload: expand/collapse panels, rotate device."""
    return struct.pack(">B", msg_type)


def get_clipboard(copy_key: CopyKey = CopyKey.NONE) -> bytes:
    return struct.pack(">BB", MsgType.GET_CLIPBOARD, copy_key)


def set_clipboard(sequence: int, value: str, paste: bool = False) -> bytes:
    data = _truncate_utf8(value, CLIPBOARD_TEXT_MAX_LENGTH)
    header = struct.pack(">BQBI", MsgType.SET_CLIPBOARD, sequence & _U64_MASK, int(paste), len(data))
    return header + data


def set_display_power(on: bool) -> bytes:
    return struct.pack(">BB", MsgType.SET_DISPLAY_POWER, int(on))


# Friendly names for the CLI and scripts (see Keycode).
KEY_NAMES: dict[str, int] = {
    "home": Keycode.HOME,
    "back": Keycode.BACK,
    "recents": Keycode.APP_SWITCH,
    "app_switch": Keycode.APP_SWITCH,
    "menu": Keycode.MENU,
    "search": Keycode.SEARCH,
    "enter": Keycode.ENTER,
    "delete": Keycode.DEL,
    "backspace": Keycode.DEL,
    "forward_delete": Keycode.FORWARD_DEL,
    "tab": Keycode.TAB,
    "space": Keycode.SPACE,
    "escape": Keycode.ESCAPE,
    "power": Keycode.POWER,
    "volume_up": Keycode.VOLUME_UP,
    "volume_down": Keycode.VOLUME_DOWN,
    "wakeup": Keycode.WAKEUP,
    "sleep": Keycode.SLEEP,
    "up": Keycode.DPAD_UP,
    "down": Keycode.DPAD_DOWN,
    "left": Keycode.DPAD_LEFT,
    "right": Keycode.DPAD_RIGHT,
    "center": Keycode.DPAD_CENTER,
    "page_up": Keycode.PAGE_UP,
    "page_down": Keycode.PAGE_DOWN,
    "copy": Keycode.COPY,
    "cut": Keycode.CUT,
    "paste": Keycode.PASTE,
}
