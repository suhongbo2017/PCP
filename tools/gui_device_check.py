"""Manual end-to-end check of the mirror window against a real phone.

Drives MainWindow with Qt's QTest (real Qt event handling, no OS-level mouse), so it
does not depend on which window is in front. Each step is verified on the phone with
`adb shell dumpsys` (focused window).

    .venv\\Scripts\\python.exe tools\\gui_device_check.py [-s SERIAL]

Requires: a phone on the home screen, unlocked, USB debugging authorized.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import re  # noqa: E402

import numpy as np  # noqa: E402
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from phonectl.config import ADB_PATH  # noqa: E402
from phonectl.ui.geometry import fit_rect  # noqa: E402
from phonectl.ui.main_window import MainWindow  # noqa: E402


# Fraction of the screen (x, y) of an app icon on the launcher page used for the launch test.
# The home layout changes, so this is a parameter: --icon 0.147,0.746 ("笔记" on the current page).
DEFAULT_ICON = (0.147, 0.746)


def adb(serial: str, *args: str) -> str:
    out = subprocess.run([ADB_PATH, "-s", serial, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return out.stdout


def top_activity(serial: str) -> str:
    """Resumed activity. Stays valid while a scrcpy session runs (mCurrentFocus does not)."""
    for line in adb(serial, "shell", "dumpsys activity activities").splitlines():
        if "topResumedActivity" in line:
            m = re.search(r"\s(\S+/\S+?)[\s}]", line)
            return m.group(1) if m else line.strip()
    return "?"


def frame_small(frame) -> np.ndarray:
    return frame.reformat(width=72, height=160, format="gray").to_ndarray().astype(np.int16)


def pump(app: QApplication, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


class Checker:
    def __init__(self, win: MainWindow, serial: str) -> None:
        self.win = win
        self.serial = serial
        self.results: list[tuple[str, bool, str]] = []

    def record(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append((name, ok, detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")

    def device_point_to_view(self, dx: float, dy: float) -> QPoint:
        """Widget point corresponding to a device pixel (inverse of view_to_device)."""
        size = self.win._session.screen_size
        view = self.win.view
        rect = fit_rect(view.width(), view.height(), size[0], size[1])
        scale = rect.w / size[0]
        return QPoint(round(rect.x + dx * scale), round(rect.y + dy * scale))

    def swipe_view(self, p1: QPoint, p2: QPoint, steps: int = 12) -> None:
        view = self.win.view
        QTest.mousePress(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p1)
        for i in range(1, steps + 1):
            t = i / steps
            p = QPoint(round(p1.x() + (p2.x() - p1.x()) * t), round(p1.y() + (p2.y() - p1.y()) * t))
            QTest.mouseMove(view, p)
            pump(QApplication.instance(), 0.02)
            QTest.mouseMove(view, p)
        QTest.mouseRelease(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-s", "--serial")
    parser.add_argument("--icon", default="%g,%g" % DEFAULT_ICON, help="x,y fraction of an app icon")
    args = parser.parse_args()
    icon_fx, icon_fy = (float(v) for v in args.icon.split(","))

    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow()
    win.resize(560, 960)
    win.show()
    win.refresh_devices(preferred=args.serial)
    serial = win.device_box.currentData()
    if not serial:
        print("no device")
        return 2

    # 1. connect through the real button
    QTest.mouseClick(win.connect_btn, Qt.MouseButton.LeftButton)
    t0 = time.monotonic()
    while win._session is None and time.monotonic() - t0 < 25:
        pump(app, 0.05)
    if win._session is None:
        print("connect failed:", win.status_label.text())
        return 1
    session = win._session
    while session.latest_frame is None and time.monotonic() - t0 < 30:
        pump(app, 0.05)
    session.controller.wake()  # screen may have timed out; WAKEUP is harmless if it is on
    pump(app, 1.5)
    if "isKeyguardShowing=true" in adb(serial, "shell", "dumpsys", "window"):
        print("phone is locked: unlock it (or leave it on the home screen) and rerun")
        win.close()
        return 3
    pump(app, 0.5)
    w, h = session.screen_size
    print(f"connected: {session.info.device_name}, screen {w}x{h}, view {win.view.width()}x{win.view.height()}")

    chk = Checker(win, serial)
    chk.record("connect via button", True, f"{w}x{h}")

    def settle(sec: float = 1.6) -> None:
        pump(app, sec)

    def frame_change() -> float:
        """Mean absolute change of the picture between two samples (0..255)."""
        a = frame_small(session.latest_frame)
        pump(app, 0.4)
        b = frame_small(session.latest_frame)
        return float(np.abs(a - b).mean())

    def click_view(p: QPoint, button=Qt.MouseButton.LeftButton) -> None:
        # Hold for ~90 ms like a real click; a zero-length press is not always a tap on Android.
        QTest.mousePress(win.view, button, Qt.KeyboardModifier.NoModifier, p)
        pump(app, 0.09)
        QTest.mouseRelease(win.view, button, Qt.KeyboardModifier.NoModifier, p)

    home_before = top_activity(serial)

    # 1. left click on an app icon -> that app becomes the resumed activity
    icon = chk.device_point_to_view(w * icon_fx, h * icon_fy)
    click_view(icon)
    settle(2.5)
    opened = top_activity(serial)
    chk.record("left click launches an app", opened != home_before, f"{home_before} -> {opened}")

    # 2. right click -> Back: returns to the launcher
    click_view(QPoint(8, 8), Qt.MouseButton.RightButton)
    settle(1.8)
    after_back = top_activity(serial)
    chk.record("right click sends Back", after_back == home_before, after_back)

    # 3. launch again, then middle click -> Home
    click_view(icon)
    settle(2.5)
    click_view(QPoint(8, 8), Qt.MouseButton.MiddleButton)
    settle(1.8)
    after_home = top_activity(serial)
    chk.record("middle click sends Home", after_home == home_before, after_home)

    # 4. mouse drag from bottom to middle -> app drawer (large change of the picture)
    before = frame_small(session.latest_frame)
    start = chk.device_point_to_view(w / 2, h * 0.92)
    end = chk.device_point_to_view(w / 2, h * 0.33)
    chk.swipe_view(start, end)
    settle(1.8)
    delta = float(np.abs(before - frame_small(session.latest_frame)).mean())
    chk.record("mouse drag swipe changes the picture (app drawer)", delta > 8, f"mean change {delta:.1f}")
    click_view(QPoint(8, 8), Qt.MouseButton.RightButton)  # Back closes the drawer
    settle(1.5)

    # 5. toolbar button "通知栏" -> notification shade (picture changes)
    btn = next(b for b in win._control_buttons if b.text() == "通知栏")
    before = frame_small(session.latest_frame)
    QTest.mouseClick(btn, Qt.MouseButton.LeftButton)
    settle(1.8)
    delta = float(np.abs(before - frame_small(session.latest_frame)).mean())
    chk.record("toolbar button expands notification shade", delta > 8, f"mean change {delta:.1f}")
    click_view(QPoint(8, 8), Qt.MouseButton.RightButton)
    settle(1.2)

    passed = sum(1 for _, ok, _ in chk.results if ok)
    print(f"\n{passed}/{len(chk.results)} checks passed")

    win.close()
    return 0 if passed == len(chk.results) else 1


if __name__ == "__main__":
    sys.exit(main())
