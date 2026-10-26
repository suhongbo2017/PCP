"""Device -> PC messages on the control socket.

Layout mirrors app/src/device_msg.c (tests: app/tests/test_device_msg_deserialize.c).
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum


class DeviceMsgType(IntEnum):
    CLIPBOARD = 0
    ACK_CLIPBOARD = 1
    UHID_OUTPUT = 2


class ProtocolError(ValueError):
    """Unexpected bytes on the control socket."""


@dataclass(frozen=True)
class ClipboardMsg:
    text: str


@dataclass(frozen=True)
class AckClipboardMsg:
    sequence: int


@dataclass(frozen=True)
class UhidOutputMsg:
    id: int
    data: bytes


DeviceMessage = ClipboardMsg | AckClipboardMsg | UhidOutputMsg


def read_device_message(read: Callable[[int], bytes]) -> DeviceMessage:
    """Parse one message. `read(n)` must return exactly n bytes (or raise)."""
    raw_type = read(1)[0]
    try:
        msg_type = DeviceMsgType(raw_type)
    except ValueError:
        raise ProtocolError(f"unknown device message type {raw_type}") from None

    if msg_type is DeviceMsgType.CLIPBOARD:
        (length,) = struct.unpack(">I", read(4))
        return ClipboardMsg(text=read(length).decode("utf-8", errors="replace"))
    if msg_type is DeviceMsgType.ACK_CLIPBOARD:
        (sequence,) = struct.unpack(">Q", read(8))
        return AckClipboardMsg(sequence=sequence)
    # UHID_OUTPUT: only produced when UHID input is used (not in this project yet).
    msg_id, size = struct.unpack(">HH", read(4))
    return UhidOutputMsg(id=msg_id, data=read(size))
