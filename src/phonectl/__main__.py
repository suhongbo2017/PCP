"""Command-line entry point.

    python -m phonectl devices
    python -m phonectl info [-s SERIAL]
    python -m phonectl screenshot OUT.png [--max-size N]
    python -m phonectl tap X Y
    python -m phonectl swipe X1 Y1 X2 Y2 [--duration S]
    python -m phonectl key NAME            (home, back, recents, enter, ...)
    python -m phonectl text "hello"
    python -m phonectl wake
    python -m phonectl clip-set "text" [--paste]
    python -m phonectl clip-get
    python -m phonectl notify
    python -m phonectl run SCRIPT.py
    python -m phonectl replay MACRO.json
    python -m phonectl find TEMPLATE.png
    python -m phonectl crop OUT.png X Y W H
    python -m phonectl gui [-s SERIAL]

Coordinates are device pixels of the current orientation (see `info`).
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .core.adb import DEFAULT_TCP_PORT, Adb, AdbDevice, AdbError, normalize_address
from .core.control_channel import ControlError
from .core.protocol.control import KEY_NAMES
from .core.session import ScrcpyError, ScrcpySession, ServerOptions
from .core.wifi import connect_with_retry, enable_wireless
from .automation.device import Device
from .automation.macro import Macro, replay
from .automation.runner import ScriptError, ScriptRunner, load_script
from .automation.vision import load_image



class CliError(Exception):
    """A user-facing error: printed as 'error: ...' with exit code 1."""


def pick_device(adb: Adb, serial: str | None) -> AdbDevice:
    devices = adb.devices()
    if serial:
        for dev in devices:
            if dev.serial == serial:
                if not dev.ready:
                    raise CliError(f"device {serial} is {dev.state}")
                return dev
        raise CliError(f"device {serial!r} not found")
    ready = [d for d in devices if d.ready]
    if not ready:
        states = ", ".join(f"{d.serial}({d.state})" for d in devices) or "none"
        raise CliError(f"no ready device (found: {states}). Check USB debugging and authorization.")
    if len(ready) > 1:
        names = ", ".join(d.serial for d in ready)
        raise CliError(f"multiple devices, use -s SERIAL: {names}")
    return ready[0]


@contextmanager
def open_session(
    args: argparse.Namespace, max_size: int = 0, clipboard_autosync: bool = True
) -> Iterator[ScrcpySession]:
    adb = Adb()
    dev = pick_device(adb, getattr(args, "serial", None))
    options = ServerOptions(max_size=max_size, clipboard_autosync=clipboard_autosync)
    try:
        with ScrcpySession(dev.serial, adb=adb, options=options) as session:
            session.wait_screen_size(timeout=15)
            yield session
    except ScrcpyError as exc:
        raise CliError(str(exc)) from exc


# ---- commands --------------------------------------------------------------


def cmd_devices(args: argparse.Namespace) -> int:
    devices = Adb().devices()
    if not devices:
        print("no devices attached")
        return 1
    for dev in devices:
        kind = "wireless" if dev.wireless else "usb"
        print(f"{dev.serial}\t{dev.state}\t{dev.props.get('model', '')}\t{kind}")
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    with open_session(args) as session:
        w, h = session.wait_screen_size()
        print(f"device: {session.info.device_name}")
        print(f"codec: {session.info.codec}")
        print(f"screen: {w}x{h}")
    return 0


def cmd_screenshot(args: argparse.Namespace) -> int:
    out = Path(args.output)
    with open_session(args, max_size=args.max_size) as session:
        try:
            frame = session.wait_frame(timeout=args.timeout)
        except ScrcpyError as exc:
            raise CliError(str(exc)) from exc
        frame.to_image().save(out)
        print(f"saved {out} ({frame.width}x{frame.height})")
    return 0


def cmd_tap(args: argparse.Namespace) -> int:
    with open_session(args) as session:
        session.controller.tap(args.x, args.y)
    print(f"tap ({args.x}, {args.y})")
    return 0


def cmd_swipe(args: argparse.Namespace) -> int:
    with open_session(args) as session:
        session.controller.swipe(args.x1, args.y1, args.x2, args.y2, duration=args.duration)
    print(f"swipe ({args.x1}, {args.y1}) -> ({args.x2}, {args.y2}) in {args.duration}s")
    return 0


def cmd_key(args: argparse.Namespace) -> int:
    name = args.name.lower()
    if name not in KEY_NAMES:
        raise CliError(f"unknown key {args.name!r}; choose from: {', '.join(sorted(KEY_NAMES))}")
    with open_session(args) as session:
        session.controller.key(KEY_NAMES[name])
    print(f"key {name}")
    return 0


def cmd_text(args: argparse.Namespace) -> int:
    with open_session(args) as session:
        session.controller.text(args.value)
    print(f"typed {len(args.value)} characters")
    return 0


def cmd_wake(args: argparse.Namespace) -> int:
    with open_session(args) as session:
        session.controller.wake()
    print("wake sent")
    return 0


def cmd_clip_set(args: argparse.Namespace) -> int:
    with open_session(args) as session:
        ok = session.controller.set_clipboard(args.value, paste=args.paste)
    if not ok:
        raise CliError("device did not acknowledge the clipboard update")
    print("clipboard set" + (" and pasted" if args.paste else ""))
    return 0


def cmd_clip_get(args: argparse.Namespace) -> int:
    # GET_CLIPBOARD only replies when autosync is off (see Controller.java getClipboard).
    with open_session(args, clipboard_autosync=False) as session:
        text = session.controller.get_clipboard(timeout=args.timeout)
        if text is None:
            text = session.controller.last_clipboard
    if text is None:
        raise CliError("no clipboard content received from the device")
    print(text)
    return 0


def cmd_notify(args: argparse.Namespace) -> int:
    with open_session(args) as session:
        session.controller.expand_notifications()
    print("notification panel expanded")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    with open_session(args, max_size=args.max_size) as session:
        done = threading.Event()
        result = []
        runner = ScriptRunner(session, log_fn=print, on_done=lambda state, msg: (result.extend([state, msg]), done.set()))
        try:
            runner.run_file(args.script)
        except ScriptError as exc:
            raise CliError(str(exc)) from exc
        runner.join()
        if runner.state.value != "finished":
            raise CliError(runner.error or runner.state.value)
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    try:
        macro = Macro.load(args.macro)
    except (OSError, ValueError, KeyError) as exc:
        raise CliError(str(exc)) from exc
    with open_session(args, max_size=args.max_size) as session:
        count = replay(macro, session.controller, speed=args.speed)
    print(f"replayed {count}/{len(macro.steps)} steps")
    return 0


def cmd_find(args: argparse.Namespace) -> int:
    with open_session(args, max_size=args.max_size) as session:
        match = Device(session).find(args.template, threshold=args.threshold)
    if match is None:
        print("not found")
        return 1
    print(f"found x={match.x} y={match.y} w={match.w} h={match.h} score={match.score:.4f} scale={match.scale:g}")
    return 0


def cmd_crop(args: argparse.Namespace) -> int:
    import cv2
    import numpy as np

    with open_session(args, max_size=args.max_size) as session:
        image = Device(session).screenshot()
    x, y, w, h = args.x, args.y, args.width, args.height
    crop = image[y:y + h, x:x + w]
    if crop.shape[:2] != (h, w):
        raise CliError("crop rectangle extends outside the screenshot")
    ok, encoded = cv2.imencode(Path(args.output).suffix or ".png", crop)
    if not ok:
        raise CliError(f"cannot encode image: {args.output}")
    Path(args.output).write_bytes(encoded.tobytes())
    print(f"saved {args.output} ({w}x{h})")
    return 0


# ---- wifi ------------------------------------------------------------------


def pick_usb_device(adb: Adb, serial: str | None) -> AdbDevice:
    """Like pick_device, but only USB devices qualify (needed for `adb tcpip`)."""
    if serial and ":" in serial:
        raise CliError("this command needs a USB connection; give the USB serial, not an IP address")
    dev = pick_device(adb, serial)
    if dev.wireless:
        raise CliError(f"{dev.serial} is already wireless; connect the phone by USB to enable Wi-Fi")
    return dev


def cmd_wifi_enable(args: argparse.Namespace) -> int:
    adb = Adb()
    dev = pick_usb_device(adb, getattr(args, "serial", None))
    address = enable_wireless(adb, dev.serial, port=args.port)
    print(f"wireless debugging enabled: {address}")
    print("you can unplug the USB cable now")
    return 0


def cmd_wifi_connect(args: argparse.Namespace) -> int:
    adb = Adb()
    try:
        address = normalize_address(args.address)
    except ValueError as exc:
        raise CliError(str(exc)) from exc
    connect_with_retry(adb, address, attempts=args.attempts)
    print(f"connected to {address}")
    return 0


def cmd_wifi_pair(args: argparse.Namespace) -> int:
    code = args.code.strip()
    if not (code.isdigit() and len(code) == 6):
        raise CliError("the pairing code is 6 digits")
    adb = Adb()
    adb.pair(args.address, code)
    print(f"paired with {args.address}")
    print("now connect with the IP:PORT shown under 'Wireless debugging' (a different port):")
    print("  phonectl wifi connect IP:PORT")
    return 0


def cmd_wifi_disconnect(args: argparse.Namespace) -> int:
    address = normalize_address(args.address) if args.address else None
    Adb().disconnect(address)
    print("disconnected" + (f" {address}" if address else " all wireless devices"))
    return 0


def cmd_wifi_ip(args: argparse.Namespace) -> int:
    adb = Adb()
    dev = pick_device(adb, getattr(args, "serial", None))
    ip = adb.wlan_ip(dev.serial)
    if ip is None:
        raise CliError("no Wi-Fi address (is Wi-Fi on?)")
    print(ip)
    return 0


# ---- parser ----------------------------------------------------------------


def cmd_gui(args: argparse.Namespace) -> int:
    # Imported lazily so the CLI works without PySide6 installed.
    from .ui.app import run_gui

    return run_gui(serial=getattr(args, "serial", None))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="phonectl", description="Control Android phones from Windows.")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    # -v and -s are accepted after the subcommand too; SUPPRESS keeps the
    # subparser from overwriting values given before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS, help="debug logging")
    serial = argparse.ArgumentParser(add_help=False, parents=[common])
    serial.add_argument("-s", "--serial", default=argparse.SUPPRESS, help="device serial")

    def add(name: str, help_text: str, parents: list[argparse.ArgumentParser]) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=help_text, parents=parents)

    add("devices", "list adb devices", [common]).set_defaults(func=cmd_devices)
    add("info", "device name and screen size", [serial]).set_defaults(func=cmd_info)

    shot = add("screenshot", "save one frame of the screen", [serial])
    shot.add_argument("output", help="output image path (png/jpg)")
    shot.add_argument("--max-size", type=int, default=0, help="limit the longest side (0 = no limit)")
    shot.add_argument("--timeout", type=float, default=10.0, help="seconds to wait for a frame")
    shot.set_defaults(func=cmd_screenshot)

    tap = add("tap", "tap at x,y", [serial])
    tap.add_argument("x", type=int)
    tap.add_argument("y", type=int)
    tap.set_defaults(func=cmd_tap)

    swipe = add("swipe", "swipe from (x1,y1) to (x2,y2)", [serial])
    swipe.add_argument("x1", type=int)
    swipe.add_argument("y1", type=int)
    swipe.add_argument("x2", type=int)
    swipe.add_argument("y2", type=int)
    swipe.add_argument("--duration", type=float, default=0.3, help="seconds")
    swipe.set_defaults(func=cmd_swipe)

    key = add("key", "press a key by name", [serial])
    key.add_argument("name", help="e.g. home, back, recents, enter, delete")
    key.set_defaults(func=cmd_key)

    text = add("text", "type text into the focused field", [serial])
    text.add_argument("value")
    text.set_defaults(func=cmd_text)

    add("wake", "turn the screen on", [serial]).set_defaults(func=cmd_wake)

    clip_set = add("clip-set", "set the device clipboard", [serial])
    clip_set.add_argument("value")
    clip_set.add_argument("--paste", action="store_true", help="also paste (Android 7+)")
    clip_set.set_defaults(func=cmd_clip_set)

    clip_get = add("clip-get", "print the device clipboard", [serial])
    clip_get.add_argument("--timeout", type=float, default=2.0)
    clip_get.set_defaults(func=cmd_clip_get)

    add("notify", "pull down the notification panel", [serial]).set_defaults(func=cmd_notify)

    run = add("run", "run a Python automation script", [serial])
    run.add_argument("script")
    run.add_argument("--max-size", type=int, default=0)
    run.set_defaults(func=cmd_run)

    replay_cmd = add("replay", "replay a recorded macro", [serial])
    replay_cmd.add_argument("macro")
    replay_cmd.add_argument("--speed", type=float, default=1.0)
    replay_cmd.add_argument("--max-size", type=int, default=0)
    replay_cmd.set_defaults(func=cmd_replay)

    find = add("find", "find a template in the current screen", [serial])
    find.add_argument("template")
    find.add_argument("--threshold", type=float, default=0.85)
    find.add_argument("--max-size", type=int, default=0)
    find.set_defaults(func=cmd_find)

    crop = add("crop", "crop a template from the current screen", [serial])
    crop.add_argument("output")
    crop.add_argument("x", type=int)
    crop.add_argument("y", type=int)
    crop.add_argument("width", type=int)
    crop.add_argument("height", type=int)
    crop.add_argument("--max-size", type=int, default=0)
    crop.set_defaults(func=cmd_crop)

    add("gui", "open the mirror window", [serial]).set_defaults(func=cmd_gui)

    wifi = add("wifi", "wireless (Wi-Fi) connection", [common])
    wsub = wifi.add_subparsers(dest="wifi_command", required=True)

    def wadd(name: str, help_text: str, parents: list[argparse.ArgumentParser]) -> argparse.ArgumentParser:
        return wsub.add_parser(name, help=help_text, parents=parents)

    en = wadd("enable", "USB -> Wi-Fi: switch adbd to TCP and connect", [serial])
    en.add_argument("--port", type=int, default=DEFAULT_TCP_PORT, help="TCP port (default 5555)")
    en.set_defaults(func=cmd_wifi_enable)

    co = wadd("connect", "connect to a phone at HOST[:PORT]", [common])
    co.add_argument("address", help="e.g. 192.168.1.23 or 192.168.1.23:5555")
    co.add_argument("--attempts", type=int, default=3)
    co.set_defaults(func=cmd_wifi_connect)

    pa = wadd("pair", "pair using the Wireless debugging code (Android 11+)", [common])
    pa.add_argument("address", help="pairing address shown on the phone, HOST:PORT")
    pa.add_argument("code", help="6-digit pairing code")
    pa.set_defaults(func=cmd_wifi_pair)

    di = wadd("disconnect", "disconnect one or all wireless devices", [common])
    di.add_argument("address", nargs="?", help="HOST[:PORT]; omit to disconnect all")
    di.set_defaults(func=cmd_wifi_disconnect)

    ipc = wadd("ip", "print the phone's Wi-Fi IP address", [serial])
    ipc.set_defaults(func=cmd_wifi_ip)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        return args.func(args)
    except (CliError, AdbError, ScrcpyError, ControlError, ScriptError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
