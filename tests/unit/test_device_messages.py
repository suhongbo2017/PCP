"""Device -> PC parsing, and the ControlChannel reader over a socket pair."""
import socket
import struct
import threading
import time

import pytest

from phonectl.core.control_channel import ControlChannel, ControlError
from phonectl.core.protocol.device import (
    AckClipboardMsg,
    ClipboardMsg,
    ProtocolError,
    UhidOutputMsg,
    read_device_message,
)


def _reader(data: bytes):
    buf = memoryview(data)
    pos = [0]

    def read(n: int) -> bytes:
        if pos[0] + n > len(buf):
            raise EOFError("short")
        out = bytes(buf[pos[0]:pos[0] + n])
        pos[0] += n
        return out

    return read


def test_clipboard_message_c_vector():
    data = bytes([0x00, 0x00, 0x00, 0x00, 0x03]) + b"ABC"
    assert read_device_message(_reader(data)) == ClipboardMsg(text="ABC")


def test_ack_clipboard_c_vector():
    data = bytes([0x01, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08])
    assert read_device_message(_reader(data)) == AckClipboardMsg(sequence=0x0102030405060708)


def test_uhid_output_c_vector():
    data = bytes([0x02, 0, 42, 0, 5, 1, 2, 3, 4, 5])
    assert read_device_message(_reader(data)) == UhidOutputMsg(id=42, data=bytes([1, 2, 3, 4, 5]))


def test_unknown_type_is_protocol_error():
    with pytest.raises(ProtocolError):
        read_device_message(_reader(bytes([0x7F])))


def test_channel_receives_clipboard_and_acks():
    device, pc = socket.socketpair()
    seen = []
    channel = ControlChannel(pc, on_clipboard=seen.append)
    channel.start()

    device.sendall(bytes([0x00]) + struct.pack(">I", 2) + b"hi")
    device.sendall(bytes([0x01]) + struct.pack(">Q", 7))

    assert channel.wait_ack(7, timeout=2) is True
    assert channel.wait_ack(8, timeout=0.1) is False
    assert channel.clipboard == "hi"
    assert seen == ["hi"]

    device.close()
    channel.close()


def test_channel_send_writes_bytes_to_socket():
    device, pc = socket.socketpair()
    channel = ControlChannel(pc)
    channel.start()
    channel.send(b"\x04\x00")
    device.settimeout(2)
    assert device.recv(16) == b"\x04\x00"
    channel.close()
    device.close()


def test_channel_send_after_close_raises():
    device, pc = socket.socketpair()
    channel = ControlChannel(pc)
    channel.start()
    channel.close()
    with pytest.raises(ControlError):
        channel.send(b"\x00")
    device.close()


def test_request_clipboard_gets_device_reply():
    device, pc = socket.socketpair()
    channel = ControlChannel(pc)
    channel.start()
    device.settimeout(2)

    def device_side():
        req = device.recv(2)  # GET_CLIPBOARD request from the PC
        assert req == bytes([0x08, 0x00])
        device.sendall(bytes([0x00]) + struct.pack(">I", 3) + b"xyz")

    t = threading.Thread(target=device_side)
    t.start()
    assert channel.request_clipboard(bytes([0x08, 0x00]), timeout=2) == "xyz"
    t.join()
    channel.close()
    device.close()


def test_request_clipboard_times_out_quietly():
    device, pc = socket.socketpair()
    channel = ControlChannel(pc)
    channel.start()
    t0 = time.monotonic()
    assert channel.request_clipboard(bytes([0x08, 0x00]), timeout=0.2) is None
    assert time.monotonic() - t0 >= 0.19
    channel.close()
    device.close()
