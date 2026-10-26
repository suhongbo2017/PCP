"""Wireless (Wi-Fi) connection flows built on the adb wrapper. Shared by the CLI and the GUI."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from .adb import DEFAULT_TCP_PORT, Adb, AdbError

log = logging.getLogger(__name__)


def connect_with_retry(
    adb: Adb,
    address: str,
    *,
    attempts: int = 8,
    delay: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Connect, retrying: adbd takes a moment to come back after `adb tcpip`."""
    last: AdbError | None = None
    for attempt in range(1, attempts + 1):
        try:
            adb.connect(address)
            return
        except AdbError as exc:
            last = exc
            log.debug("connect %s attempt %d failed: %s", address, attempt, exc)
            if attempt < attempts:
                sleep(delay)
    raise AdbError(f"could not connect to {address} after {attempts} tries: {last}")


def enable_wireless(
    adb: Adb,
    usb_serial: str,
    port: int = DEFAULT_TCP_PORT,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Switch a USB-connected phone to TCP/IP and connect to it over Wi-Fi.

    Returns the address used ("192.168.x.y:5555"). The phone must be on the same
    network as the PC and the USB cable can be removed afterwards.
    """
    ip = adb.wlan_ip(usb_serial)
    if ip is None:
        raise AdbError("cannot read the phone's Wi-Fi address; connect the phone to Wi-Fi first")
    adb.tcpip(usb_serial, port)
    address = f"{ip}:{port}"
    connect_with_retry(adb, address, sleep=sleep)
    return address
