"""Pure parsing of the scrcpy video socket format (no I/O here).

Reference: scrcpy doc/develop.md "Protocol", server Streamer.java and
DesktopConnection.java.

Wire layout of the video socket (server -> client):
  1. device name: 64 bytes, UTF-8, NUL padded          (first socket only)
  2. codec id:    u32 big-endian (e.g. "h264")
  3. packets, each starting with a 12-byte header:
       - session packet: MSB of byte 0 set
           u32 flags (0x80000000 | client_resized), u32 width, u32 height
       - media packet: MSB of byte 0 clear
           u64 (flags | pts), u32 payload size, then `size` bytes of payload
         flags: bit63 = 0 (media), bit62 = config packet, bit61 = key frame
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

DEVICE_NAME_LENGTH = 64
HEADER_LENGTH = 12

CODEC_IDS: dict[int, str] = {
    0x68323634: "h264",  # "h264"
    0x68323635: "h265",  # "h265"
    0x00617631: "av1",
    0x00767038: "vp8",
    0x00767039: "vp9",
}

_SESSION_FLAG = 0x8000_0000  # MSB of the first byte
_CLIENT_RESIZED_FLAG = 0x1
_CONFIG_BIT = 1 << 62
_KEY_FRAME_BIT = 1 << 61
_PTS_MASK = (1 << 61) - 1


class ProtocolError(ValueError):
    """Malformed data received from the server."""


def parse_device_name(data: bytes) -> str:
    if len(data) != DEVICE_NAME_LENGTH:
        raise ProtocolError(f"device name must be {DEVICE_NAME_LENGTH} bytes, got {len(data)}")
    return data.rstrip(b"\x00").decode("utf-8", errors="replace")


def parse_codec_id(data: bytes) -> str:
    (value,) = struct.unpack(">I", data)
    try:
        return CODEC_IDS[value]
    except KeyError:
        raise ProtocolError(f"unknown video codec id 0x{value:08x}") from None


@dataclass(frozen=True)
class SessionPacket:
    """Starts a capture session. Sent again when the device rotates or resizes."""

    width: int
    height: int
    client_resized: bool


@dataclass(frozen=True)
class MediaHeader:
    pts: int | None  # None for config packets (SPS/PPS/VPS)
    config: bool
    key_frame: bool
    size: int


def is_session_header(header: bytes) -> bool:
    return bool(header[0] & 0x80)


def parse_session_header(header: bytes) -> SessionPacket:
    if len(header) != HEADER_LENGTH:
        raise ProtocolError("session header must be 12 bytes")
    flags, width, height = struct.unpack(">III", header)
    if not flags & _SESSION_FLAG:
        raise ProtocolError("session header without session flag")
    return SessionPacket(
        width=width,
        height=height,
        client_resized=bool(flags & _CLIENT_RESIZED_FLAG),
    )


def parse_media_header(header: bytes) -> MediaHeader:
    if len(header) != HEADER_LENGTH:
        raise ProtocolError("media header must be 12 bytes")
    if is_session_header(header):
        raise ProtocolError("expected a media header, got a session header")
    pts_and_flags, size = struct.unpack(">QI", header)
    config = bool(pts_and_flags & _CONFIG_BIT)
    key_frame = bool(pts_and_flags & _KEY_FRAME_BIT)
    pts = None if config else pts_and_flags & _PTS_MASK
    return MediaHeader(pts=pts, config=config, key_frame=key_frame, size=size)
