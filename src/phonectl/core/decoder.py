"""Video decoding (FFmpeg via PyAV). Keeps latency low: no reordering buffer, no delay."""

from __future__ import annotations

import logging

import av

log = logging.getLogger(__name__)

_AV_CODEC_FLAG_LOW_DELAY = 0x80000


class VideoDecoder:
    def __init__(self, codec: str = "h264") -> None:
        self._ctx = av.CodecContext.create(codec, "r")
        self._ctx.flags |= _AV_CODEC_FLAG_LOW_DELAY
        self._ctx.thread_type = "AUTO"

    def feed(self, data: bytes) -> list[av.VideoFrame]:
        """Feed one encoded packet (config or media). Returns zero or more decoded frames.

        Packets that cannot be decoded yet (e.g. P-frames before the first key frame)
        are dropped instead of failing the whole stream.
        """
        try:
            return self._ctx.decode(av.Packet(data))
        except av.InvalidDataError as exc:
            log.debug("dropped undecodable packet: %s", exc)
            return []

    def flush(self) -> list[av.VideoFrame]:
        """Drain frames still held by the decoder (call at end of stream)."""
        return self._ctx.decode(None)
