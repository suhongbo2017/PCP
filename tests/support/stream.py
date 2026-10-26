"""Helpers that synthesize a scrcpy-format video stream using FFmpeg."""
from __future__ import annotations

import struct
from fractions import Fraction

import av
import numpy as np
import pytest

from phonectl.core.protocol.video import DEVICE_NAME_LENGTH


def _encoder(w: int, h: int):
    for name in ("libx264", "h264"):
        try:
            ctx = av.CodecContext.create(name, "w")
            ctx.width, ctx.height = w, h
            ctx.pix_fmt = "yuv420p"
            ctx.time_base = Fraction(1, 30)
            ctx.framerate = 30
            ctx.open()
            return ctx
        except Exception:
            continue
    pytest.skip("no H.264 encoder available in this PyAV build")


def build_encoded_stream(w: int = 320, h: int = 240, n: int = 10, device_name: str = "Emu") -> bytes:
    """Full video-socket byte stream: device name, codec id, session packet, media packets."""
    enc = _encoder(w, h)
    packets = []
    for i in range(n):
        frame = av.VideoFrame.from_ndarray(np.full((h, w, 3), i * 20, dtype="uint8"), format="rgb24")
        frame.pts = i
        packets += enc.encode(frame)
    packets += enc.encode(None)

    out = device_name.encode().ljust(DEVICE_NAME_LENGTH, b"\x00") + b"h264"
    out += struct.pack(">III", 0x8000_0000, w, h)
    for p in packets:
        flags = (1 << 61 if p.is_keyframe else 0) | (p.pts or 0)
        out += struct.pack(">QI", flags, p.buffer_size) + bytes(p)
    return out
