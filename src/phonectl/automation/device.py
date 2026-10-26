"""The scripting API: what a user script sees as `dev`.

Wraps a running ScrcpySession. All coordinates are device pixels of the current
screen orientation unless the method name says `_rel` (fractions 0..1 of the screen).
Every action checks the stop flag, so a running script can always be stopped.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from ..core.protocol.control import KEY_NAMES, Keycode
from .vision import DEFAULT_SCALES, Match, find_template, load_image

log = logging.getLogger("phonectl.script")

_ACTIVITY_RE = re.compile(r"\s(\S+/\S+)\s+t\d+\}")


class ScriptStopped(Exception):
    """Raised inside a script when the user (or the runner) stopped it."""


class ImageNotFound(TimeoutError):
    """wait_for / tap_image did not see the template within the timeout."""


class Device:
    def __init__(
        self,
        session,
        *,
        stop_event: threading.Event | None = None,
        log_fn: Callable[[str], None] | None = None,
    ) -> None:
        self.session = session
        self.serial: str = session.serial
        self.adb = session.adb
        self._stop = stop_event or threading.Event()
        self._log = log_fn or (lambda msg: log.info("%s", msg))

    # ---- basics ----------------------------------------------------------

    @property
    def controller(self):
        return self.session.controller

    @property
    def size(self) -> tuple[int, int]:
        return self.session.wait_screen_size(timeout=10)

    def print(self, *parts) -> None:
        self._log(" ".join(str(p) for p in parts))

    def check_stop(self) -> None:
        if self._stop.is_set():
            raise ScriptStopped()

    def sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while True:
            self.check_stop()
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.05, remaining))

    # ---- touch -----------------------------------------------------------

    def tap(self, x: int, y: int, hold: float = 0.05) -> None:
        self.check_stop()
        self.controller.tap(int(x), int(y), hold=hold)

    def tap_rel(self, fx: float, fy: float) -> None:
        w, h = self.size
        self.tap(round(fx * w), round(fy * h))

    def long_press(self, x: int, y: int, duration: float = 0.8) -> None:
        self.check_stop()
        self.controller.touch_down(int(x), int(y))
        self.sleep(duration)
        self.controller.touch_up(int(x), int(y))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.3) -> None:
        self.check_stop()
        self.controller.swipe(int(x1), int(y1), int(x2), int(y2), duration=duration)

    def swipe_rel(self, fx1: float, fy1: float, fx2: float, fy2: float, duration: float = 0.3) -> None:
        w, h = self.size
        self.swipe(round(fx1 * w), round(fy1 * h), round(fx2 * w), round(fy2 * h), duration)

    def swipe_up(self, distance: float = 0.5, duration: float = 0.3) -> None:
        """Finger moves up from the middle; the common 'scroll down the page' gesture."""
        self.swipe_rel(0.5, 0.5 + distance / 2, 0.5, 0.5 - distance / 2, duration)

    def swipe_down(self, distance: float = 0.5, duration: float = 0.3) -> None:
        self.swipe_rel(0.5, 0.5 - distance / 2, 0.5, 0.5 + distance / 2, duration)

    def swipe_left(self, distance: float = 0.6, duration: float = 0.3) -> None:
        self.swipe_rel(0.5 + distance / 2, 0.5, 0.5 - distance / 2, 0.5, duration)

    def swipe_right(self, distance: float = 0.6, duration: float = 0.3) -> None:
        self.swipe_rel(0.5 - distance / 2, 0.5, 0.5 + distance / 2, 0.5, duration)

    # ---- keys and text ---------------------------------------------------

    def key(self, name_or_code: str | int) -> None:
        self.check_stop()
        if isinstance(name_or_code, int):
            code = name_or_code
        else:
            name = name_or_code.lower()
            if name not in KEY_NAMES:
                raise ValueError(f"unknown key {name_or_code!r}; choose from {sorted(KEY_NAMES)}")
            code = KEY_NAMES[name]
        self.controller.key(code)

    def back(self) -> None:
        self.controller.key(Keycode.BACK)

    def home(self) -> None:
        self.controller.key(Keycode.HOME)

    def recents(self) -> None:
        self.controller.app_switch()

    def wake(self) -> None:
        self.controller.wake()

    def text(self, value: str) -> bool:
        """Type ASCII text. Returns False (and puts the text on the phone clipboard) for CJK.

        INJECT_TEXT cannot type Chinese; the clipboard route needs a long press + paste.
        """
        self.check_stop()
        if value.isascii():
            self.controller.text(value)
            return True
        ok = self.controller.set_clipboard(value)
        self.print(f"text needs a paste on the phone (clipboard set={ok}): {value[:20]!r}")
        return False

    # ---- screen ----------------------------------------------------------

    def screenshot(self) -> np.ndarray:
        """Current frame as a BGR image (the video resolution, see `size`)."""
        frame = self.session.wait_frame(timeout=10)
        return np.ascontiguousarray(frame.to_ndarray(format="bgr24"))

    def find(
        self,
        template: str | Path | np.ndarray,
        threshold: float = 0.85,
        scales: tuple[float, ...] = DEFAULT_SCALES,
        region: tuple[int, int, int, int] | None = None,
    ) -> Match | None:
        self.check_stop()
        return find_template(self.screenshot(), load_image(template), threshold, scales, region)

    def exists(self, template, threshold: float = 0.85) -> bool:
        return self.find(template, threshold) is not None

    def wait_for(
        self,
        template,
        timeout: float = 10.0,
        threshold: float = 0.85,
        interval: float = 0.4,
    ) -> Match:
        tpl = load_image(template)  # fail fast on a bad path
        end = time.monotonic() + timeout
        while True:
            self.check_stop()
            m = find_template(self.screenshot(), tpl, threshold, DEFAULT_SCALES)
            if m is not None:
                return m
            if time.monotonic() >= end:
                raise ImageNotFound(f"{template} not found within {timeout}s")
            self.sleep(interval)

    def tap_image(self, template, timeout: float = 10.0, threshold: float = 0.85) -> Match:
        m = self.wait_for(template, timeout=timeout, threshold=threshold)
        cx, cy = m.center
        self.print(f"tap image at ({cx}, {cy}) score={m.score:.3f}")
        self.tap(cx, cy)
        return m

    # ---- apps (through adb, independent of the video stream) --------------

    def shell(self, command: str) -> str:
        self.check_stop()
        return self.adb.run(["shell", command], serial=self.serial, timeout=20)

    def current_app(self) -> str:
        """'package/activity' of the resumed activity, or '' if unknown."""
        for line in self.shell("dumpsys activity activities").splitlines():
            if "topResumedActivity" in line:
                m = _ACTIVITY_RE.search(line)
                return m.group(1) if m else ""
        return ""

    def start_app(self, package: str) -> None:
        """Launch the app's main activity (the launcher intent)."""
        out = self.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
        if "Events injected: 1" not in out:  # monkey's success line
            raise RuntimeError(f"could not start {package}: {out.strip()[:200]}")

    def wait_app(self, package: str, timeout: float = 10.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.current_app().startswith(package + "/"):
                return True
            self.sleep(0.5)
        return False
