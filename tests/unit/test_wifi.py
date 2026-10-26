"""Wireless connection helpers and flows (adb is replaced by a scripted fake)."""
import pytest

from phonectl.core.adb import (
    AdbDevice,
    AdbError,
    connect_succeeded,
    is_wireless_serial,
    normalize_address,
    pair_succeeded,
    parse_devices,
    parse_wlan_ip,
)
from phonectl.core.wifi import connect_with_retry, enable_wireless

WLAN_OUTPUT = """    inet 192.168.102.22/23 brd 192.168.103.255 scope global wlan0
       valid_lft forever preferred_lft forever
"""


def test_parse_wlan_ip():
    assert parse_wlan_ip(WLAN_OUTPUT) == "192.168.102.22"


def test_parse_wlan_ip_missing():
    assert parse_wlan_ip("") is None
    assert parse_wlan_ip("inet6 fe80::1/64 scope link") is None


def test_normalize_address_adds_default_port():
    assert normalize_address("192.168.1.23") == "192.168.1.23:5555"
    assert normalize_address(" 192.168.1.23:37123 ") == "192.168.1.23:37123"


@pytest.mark.parametrize("bad", ["", "192.168.1.23:", "192.168.1.23:abc", ":5555"])
def test_normalize_address_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        normalize_address(bad)


def test_wireless_serial_detection():
    assert is_wireless_serial("192.168.102.22:5555")
    assert not is_wireless_serial("2156f725")
    assert not is_wireless_serial("emulator-5554")


def test_devices_mark_wireless_entries():
    out = (
        "List of devices attached\n"
        "2156f725  device usb:1-1 model:22021211RC\n"
        "192.168.102.22:5555  device model:22021211RC\n"
    )
    devs = parse_devices(out)
    assert [d.wireless for d in devs] == [False, True]
    assert isinstance(devs[0], AdbDevice)


def test_connect_output_detection():
    assert connect_succeeded("connected to 192.168.1.5:5555")
    assert connect_succeeded("already connected to 192.168.1.5:5555")
    assert not connect_succeeded("failed to connect to '192.168.1.5:5555': Connection refused")


def test_pair_output_detection():
    assert pair_succeeded("Successfully paired to 192.168.1.5:37000 [guid=adb-x]")
    assert not pair_succeeded("Failed: wrong password")


class FakeAdb:
    """Records calls; `connect_failures` makes the first N connect attempts fail."""

    def __init__(self, ip="192.168.102.22", connect_failures=0):
        self.ip = ip
        self.connect_failures = connect_failures
        self.calls: list[tuple] = []

    def wlan_ip(self, serial):
        self.calls.append(("wlan_ip", serial))
        return self.ip

    def tcpip(self, serial, port):
        self.calls.append(("tcpip", serial, port))

    def connect(self, address):
        self.calls.append(("connect", address))
        if self.connect_failures > 0:
            self.connect_failures -= 1
            raise AdbError(f"failed to connect to {address}")


def test_enable_wireless_reads_ip_then_tcpip_then_connects():
    adb = FakeAdb()
    address = enable_wireless(adb, "2156f725", port=5555, sleep=lambda s: None)
    assert address == "192.168.102.22:5555"
    assert adb.calls == [
        ("wlan_ip", "2156f725"),
        ("tcpip", "2156f725", 5555),
        ("connect", "192.168.102.22:5555"),
    ]


def test_enable_wireless_retries_while_adbd_restarts():
    adb = FakeAdb(connect_failures=2)
    sleeps = []
    enable_wireless(adb, "2156f725", sleep=sleeps.append)
    assert [c for c in adb.calls if c[0] == "connect"] == [("connect", "192.168.102.22:5555")] * 3
    assert len(sleeps) == 2


def test_enable_wireless_without_wifi_address_fails_before_tcpip():
    adb = FakeAdb(ip=None)
    with pytest.raises(AdbError, match="Wi-Fi address"):
        enable_wireless(adb, "2156f725", sleep=lambda s: None)
    assert all(c[0] != "tcpip" for c in adb.calls)


def test_connect_with_retry_gives_up_with_clear_error():
    adb = FakeAdb(connect_failures=99)
    with pytest.raises(AdbError, match="after 3 tries"):
        connect_with_retry(adb, "192.168.1.9:5555", attempts=3, sleep=lambda s: None)


def test_adb_timeout_is_reported_as_adb_error():
    import sys

    from phonectl.core.adb import Adb

    slow = Adb(adb_path=sys.executable)  # "adb" here is python, running a command that hangs
    with pytest.raises(AdbError, match="timed out"):
        slow.run(["-c", "import time; time.sleep(5)"], timeout=0.3)
