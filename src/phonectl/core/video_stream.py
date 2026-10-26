"""Reads the video socket: handshake, then a stream of session/media packets."""

from __future__ import annotations

import socket
from collections.abc import Iterator
from dataclasses import dataclass

from .netio import ConnectionClosed, read_exact
from .protocol.video import (
    DEVICE_NAME_LENGTH,
    HEADER_LENGTH,
    MediaHeader,
    SessionPacket,
    is_session_header,
    parse_codec_id,
    parse_device_name,
    parse_media_header,
    parse_session_header,
)

__all__ = ["ConnectionClosed", "MediaPacket", "StreamInfo", "VideoStream"]


@dataclass(frozen=True)
class StreamInfo:
    device_name: str
    codec: str


@dataclass(frozen=True)
class MediaPacket:
    header: MediaHeader
    data: bytes


class VideoStream:
    """Wraps the video socket (always the first socket of a scrcpy connection).

    The first socket carries the device name, so the handshake always expects it.
    """

    def __init__(self, sock: socket.socket, *, device_meta: bool = True) -> None:
        self._sock = sock
        self._device_meta = device_meta
        self.info: StreamInfo | None = None

    def handshake(self) -> StreamInfo:
        name = ""
        if self._device_meta:
            name = parse_device_name(read_exact(self._sock, DEVICE_NAME_LENGTH))
        codec = parse_codec_id(read_exact(self._sock, 4))
        self.info = StreamInfo(device_name=name, codec=codec)
        return self.info

    def packets(self) -> Iterator[SessionPacket | MediaPacket]:
        """Yield packets until the server closes the socket (a normal end of stream).

        A connection closed in the middle of a packet raises ConnectionClosed.
        """
        if self.info is None:
            raise RuntimeError("call handshake() before reading packets")
        while True:
            try:
                header = read_exact(self._sock, HEADER_LENGTH)
            except ConnectionClosed:
                return
            if is_session_header(header):
                yield parse_session_header(header)
                continue
            media = parse_media_header(header)
            data = read_exact(self._sock, media.size) if media.size else b""
            yield MediaPacket(header=media, data=data)
