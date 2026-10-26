"""Qt key -> Android keycode for keys that are not plain text."""

from __future__ import annotations

from PySide6.QtCore import Qt

from ..core.protocol.control import Keycode

# Keys sent as KEYCODE_* events. Printable characters are sent as text instead.
_QT_TO_ANDROID_ENUM = {
    Qt.Key.Key_Return: Keycode.ENTER,
    Qt.Key.Key_Enter: Keycode.ENTER,
    Qt.Key.Key_Backspace: Keycode.DEL,
    Qt.Key.Key_Delete: Keycode.FORWARD_DEL,
    Qt.Key.Key_Tab: Keycode.TAB,
    Qt.Key.Key_Space: Keycode.SPACE,
    Qt.Key.Key_Escape: Keycode.BACK,  # Esc acts as Back, the usual phone shortcut
    Qt.Key.Key_Up: Keycode.DPAD_UP,
    Qt.Key.Key_Down: Keycode.DPAD_DOWN,
    Qt.Key.Key_Left: Keycode.DPAD_LEFT,
    Qt.Key.Key_Right: Keycode.DPAD_RIGHT,
    Qt.Key.Key_Home: Keycode.MOVE_HOME,
    Qt.Key.Key_End: Keycode.MOVE_END,
    Qt.Key.Key_PageUp: Keycode.PAGE_UP,
    Qt.Key.Key_PageDown: Keycode.PAGE_DOWN,
    Qt.Key.Key_VolumeUp: Keycode.VOLUME_UP,
    Qt.Key.Key_VolumeDown: Keycode.VOLUME_DOWN,
}
# Qt.Key members are not guaranteed to hash like plain ints; key lookups use ints.
QT_TO_ANDROID: dict[int, int] = {int(k): v for k, v in _QT_TO_ANDROID_ENUM.items()}


def android_keycode_for(qt_key: int) -> int | None:
    """Android keycode for a Qt key, or None if the key should be handled as text."""
    try:
        return QT_TO_ANDROID.get(int(qt_key))
    except (TypeError, ValueError):
        return None


def is_text_input(text: str) -> bool:
    """True when a key press produced printable text (not a control character)."""
    return bool(text) and all(ch.isprintable() for ch in text)
