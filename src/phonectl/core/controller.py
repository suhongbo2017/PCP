"""High-level input API: taps, swipes, keys, text, clipboard, system actions.

Coordinates are in device pixels of the current screen orientation (the same
space as the decoded video frames). The server scales them to the real display.
"""

from __future__ import annotations

import itertools
import logging
import time
from collections.abc import Callable

from .control_channel import ControlChannel
from .protocol import control as msg
from .protocol.control import BUTTON_PRIMARY, Keycode, KeyAction, MotionAction, PointerId

log = logging.getLogger(__name__)

ScreenSize = Callable[[], tuple[int, int]]


class Controller:
    def __init__(self, channel: ControlChannel, screen_size: ScreenSize) -> None:
        self._channel = channel
        self._screen_size = screen_size
        self._sequence = itertools.count(1)  # clipboard ack ids, never 0 (= no ack)

    # ---- touch -----------------------------------------------------------

    def _touch(self, action: MotionAction, x: int, y: int) -> None:
        w, h = self._screen_size()
        # Same shape as the C client's virtual finger (input_manager.c).
        pressure = 0.0 if action is MotionAction.UP else 1.0
        self._channel.send(
            msg.touch(
                action,
                PointerId.GENERIC_FINGER,
                int(x),
                int(y),
                w,
                h,
                pressure=pressure,
                action_button=BUTTON_PRIMARY if action is MotionAction.DOWN else 0,
                buttons=0,
            )
        )

    def touch_down(self, x: int, y: int) -> None:
        self._touch(MotionAction.DOWN, x, y)

    def touch_move(self, x: int, y: int) -> None:
        self._touch(MotionAction.MOVE, x, y)

    def touch_up(self, x: int, y: int) -> None:
        self._touch(MotionAction.UP, x, y)

    def tap(self, x: int, y: int, hold: float = 0.05) -> None:
        self.touch_down(x, y)
        time.sleep(hold)
        self.touch_up(x, y)

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration: float = 0.3,
        interval: float = 0.016,
    ) -> None:
        """Linear swipe. Moves are sent every `interval` seconds (about 60 Hz by default)."""
        steps = max(1, int(duration / interval))
        self.touch_down(x1, y1)
        for i in range(1, steps + 1):
            t = i / steps
            self.touch_move(round(x1 + (x2 - x1) * t), round(y1 + (y2 - y1) * t))
            time.sleep(duration / steps)
        self.touch_up(x2, y2)

    def scroll(self, x: int, y: int, hscroll: float = 0.0, vscroll: float = 0.0) -> None:
        """Scroll at (x, y). Values are wheel clicks: positive vscroll scrolls up."""
        w, h = self._screen_size()
        self._channel.send(msg.scroll(int(x), int(y), w, h, hscroll, vscroll))

    # ---- keys ------------------------------------------------------------

    def key_down(self, code: int, metastate: int = 0) -> None:
        self._channel.send(msg.keycode(KeyAction.DOWN, int(code), 0, metastate))

    def key_up(self, code: int, metastate: int = 0) -> None:
        self._channel.send(msg.keycode(KeyAction.UP, int(code), 0, metastate))

    def key(self, code: int, metastate: int = 0) -> None:
        self.key_down(code, metastate)
        self.key_up(code, metastate)

    def back(self) -> None:
        self.key(Keycode.BACK)

    def home(self) -> None:
        self.key(Keycode.HOME)

    def app_switch(self) -> None:
        self.key(Keycode.APP_SWITCH)

    def wake(self) -> None:
        """Turn the screen on (no-op if already on)."""
        self.key(Keycode.WAKEUP)

    def text(self, value: str) -> None:
        """Type text. Long strings are split into several INJECT_TEXT messages."""
        for chunk in msg.chunk_text(value):
            self._channel.send(msg.text(chunk))

    # ---- system ----------------------------------------------------------

    def expand_notifications(self) -> None:
        self._channel.send(msg.simple(msg.MsgType.EXPAND_NOTIFICATION_PANEL))

    def expand_settings(self) -> None:
        self._channel.send(msg.simple(msg.MsgType.EXPAND_SETTINGS_PANEL))

    def collapse_panels(self) -> None:
        self._channel.send(msg.simple(msg.MsgType.COLLAPSE_PANELS))

    def rotate_device(self) -> None:
        self._channel.send(msg.simple(msg.MsgType.ROTATE_DEVICE))

    def set_display_power(self, on: bool) -> None:
        self._channel.send(msg.set_display_power(on))

    # ---- clipboard -------------------------------------------------------

    def set_clipboard(self, value: str, paste: bool = False, timeout: float = 2.0) -> bool:
        """Set the device clipboard and wait for the device to confirm. Returns True if acked."""
        seq = next(self._sequence)
        self._channel.send(msg.set_clipboard(seq, value, paste=paste))
        return self._channel.wait_ack(seq, timeout)

    def get_clipboard(self, timeout: float = 2.0) -> str | None:
        """Ask the device for its clipboard.

        With clipboard autosync (server default) the device pushes changes on its own, so
        `ControlChannel.clipboard` already holds the latest value; this method requests a
        fresh copy and waits for it, returning None on timeout.
        """
        return self._channel.request_clipboard(msg.get_clipboard(msg.CopyKey.NONE), timeout)

    @property
    def last_clipboard(self) -> str | None:
        return self._channel.clipboard
