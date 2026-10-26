"""Thin wrapper around the adb command-line tool (Windows-friendly), including Wi-Fi helpers."""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .. import config

log = logging.getLogger(__name__)

# Do not pop up console windows when running adb from a GUI process.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

DEFAULT_TCP_PORT = 5555
_INET_RE = re.compile(r"inet (\d{1,3}(?:\.\d{1,3}){3})/\d+")


class AdbError(RuntimeError):
    """adb returned an error."""


@dataclass(frozen=True)
class AdbDevice:
    serial: str
    state: str  # "device", "unauthorized", "offline", ...
    props: dict[str, str] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return self.state == "device"

    @property
    def wireless(self) -> bool:
        return is_wireless_serial(self.serial)


# ---- pure helpers (unit tested) --------------------------------------------


def parse_devices(output: str) -> list[AdbDevice]:
    """Parse the output of `adb devices -l`."""
    devices: list[AdbDevice] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        props = dict(p.split(":", 1) for p in parts[2:] if ":" in p)
        devices.append(AdbDevice(serial=parts[0], state=parts[1], props=props))
    return devices


def is_wireless_serial(serial: str) -> bool:
    """Wireless devices are addressed as host:port. USB and emulator serials have no colon."""
    return ":" in serial


def normalize_address(address: str, default_port: int = DEFAULT_TCP_PORT) -> str:
    """'192.168.1.5' -> '192.168.1.5:5555'; keeps an explicit port as given."""
    address = address.strip()
    if not address:
        raise ValueError("empty address")
    if ":" in address:
        host, _, port = address.rpartition(":")
        if not host or not port.isdigit():
            raise ValueError(f"invalid address {address!r}, expected HOST[:PORT]")
        return f"{host}:{int(port)}"
    return f"{address}:{default_port}"


def parse_wlan_ip(output: str) -> str | None:
    """IPv4 address from `ip -f inet addr show wlan0` output."""
    for line in output.splitlines():
        if "inet " in line:
            m = _INET_RE.search(line)
            if m:
                return m.group(1)
    return None


def connect_succeeded(output: str) -> bool:
    text = output.lower()
    return "connected to" in text or "already connected" in text


def pair_succeeded(output: str) -> bool:
    return "successfully paired" in output.lower()


# ---- adb wrapper -----------------------------------------------------------


class Adb:
    def __init__(self, adb_path: str | Path = config.ADB_PATH) -> None:
        self.adb = str(adb_path)

    def _argv(self, args: list[str], serial: str | None) -> list[str]:
        argv = [self.adb]
        if serial:
            argv += ["-s", serial]
        return argv + args

    def run(self, args: list[str], serial: str | None = None, timeout: float = 30) -> str:
        argv = self._argv(args, serial)
        log.debug("adb: %s", " ".join(argv))
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdbError(f"adb {' '.join(args)} timed out after {timeout:g}s") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            raise AdbError(f"adb {' '.join(args)} failed: {detail}")
        return proc.stdout

    def start_server(self) -> None:
        self.run(["start-server"])

    def devices(self) -> list[AdbDevice]:
        return parse_devices(self.run(["devices", "-l"]))

    def push(self, local: str | Path, remote: str, serial: str) -> None:
        self.run(["push", str(local), remote], serial=serial, timeout=120)

    def reverse(self, serial: str, remote: str, local: str) -> None:
        """Device-side `remote` connects to host-side `local` (e.g. localabstract:x, tcp:27183)."""
        self.run(["reverse", remote, local], serial=serial)

    def reverse_remove(self, serial: str, remote: str) -> None:
        try:
            self.run(["reverse", "--remove", remote], serial=serial)
        except AdbError as exc:
            log.debug("reverse --remove ignored: %s", exc)

    def shell_popen(self, serial: str, command: str) -> subprocess.Popen:
        """Start a long-running `adb shell <command>` and return the process handle."""
        argv = self._argv(["shell", command], serial)
        log.debug("adb shell (async): %s", command)
        return subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=_NO_WINDOW,
        )

    # ---- wireless --------------------------------------------------------

    def wlan_ip(self, serial: str) -> str | None:
        """Phone's Wi-Fi IPv4 address (works over USB or an existing wireless connection)."""
        out = self.run(["shell", "ip -f inet addr show wlan0"], serial=serial)
        return parse_wlan_ip(out)

    def tcpip(self, serial: str, port: int = DEFAULT_TCP_PORT) -> None:
        """Restart adbd on the phone listening on TCP `port`. Needs a USB connection first."""
        self.run(["tcpip", str(port)], serial=serial, timeout=20)

    def connect(self, address: str) -> None:
        # A reachable phone answers in well under a second; an unreachable one must not stall the UI.
        out = self.run(["connect", address], timeout=8)
        if not connect_succeeded(out):
            raise AdbError(out.strip() or f"could not connect to {address}")

    def disconnect(self, address: str | None = None) -> None:
        self.run(["disconnect", address] if address else ["disconnect"], timeout=15)

    def pair(self, address: str, code: str) -> None:
        """Pair with a phone using the Wireless debugging pairing code (Android 11+)."""
        out = self.run(["pair", address, code], timeout=30)
        if not pair_succeeded(out):
            raise AdbError(out.strip() or f"pairing with {address} failed")
