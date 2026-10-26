"""Rough performance check of the mirror pipeline on a real phone (run with the phone unlocked).

Measures decoded frames per second and the GUI-side cost of scaling each frame,
while the picture changes continuously (repeated swipes).
"""
import argparse
import statistics
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from phonectl.ui.frame_image import frame_to_qimage  # noqa: E402
from phonectl.ui.main_window import QUALITY_CHOICES, MainWindow  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-s", "--serial")
    ap.add_argument("--quality", type=int, default=1, help="index into quality list (default 1280)")
    ap.add_argument("--seconds", type=float, default=6.0)
    args = ap.parse_args()

    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow(serial=args.serial)
    win.resize(560, 960)
    win.show()
    win.quality_box.setCurrentIndex(args.quality)
    win.connect_device()
    t0 = time.monotonic()
    while win._session is None and time.monotonic() - t0 < 25:
        app.processEvents()
        time.sleep(0.02)
    if win._session is None:
        print("not connected:", win.status_label.text())
        return 1
    session = win._session
    while session.latest_frame is None and time.monotonic() - t0 < 30:
        app.processEvents()
        time.sleep(0.02)
    print("quality:", QUALITY_CHOICES[args.quality][0], "screen:", session.screen_size)

    ctl = session.controller
    w, h = session.screen_size
    stop = threading.Event()
    stats = {"frames": 0, "last": None}
    convert_ms: list[float] = []

    def sampler() -> None:
        # Runs independently of the swipes (which block), so it sees every decoded frame.
        while not stop.is_set():
            frame = session.latest_frame
            if frame is not None and frame is not stats["last"]:  # new decoded frame (identity)
                stats["last"] = frame  # keep a reference so identity stays meaningful
                stats["frames"] += 1
                t = time.perf_counter()
                frame_to_qimage(frame, 538, 897)  # what the GUI does per new frame
                convert_ms.append((time.perf_counter() - t) * 1000)
            time.sleep(0.002)

    threading.Thread(target=sampler, daemon=True).start()
    start = time.monotonic()
    direction = 0
    while time.monotonic() - start < args.seconds:  # swipes keep the picture changing
        y1, y2 = (h * 0.8, h * 0.3) if direction == 0 else (h * 0.3, h * 0.8)
        ctl.swipe(w // 2, int(y1), w // 2, int(y2), duration=0.3)
        direction ^= 1
    stop.set()
    elapsed = time.monotonic() - start

    print(f"decoded frames/s: {stats['frames'] / elapsed:.1f}")
    if convert_ms:
        ordered = sorted(convert_ms)
        print(f"GUI scale cost per frame: median {statistics.median(ordered):.1f} ms, "
              f"p95 {ordered[int(len(ordered) * 0.95) - 1]:.1f} ms")
    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
