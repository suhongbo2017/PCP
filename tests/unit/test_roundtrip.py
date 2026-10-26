"""Encode frames with FFmpeg, wrap them in the scrcpy wire format, decode them back."""
import socket
import threading

from support.stream import build_encoded_stream

from phonectl.core.decoder import VideoDecoder
from phonectl.core.video_stream import MediaPacket, VideoStream

W, H, N = 320, 240, 10


def test_encode_wrap_decode_roundtrip():
    payload = build_encoded_stream(W, H, N)
    server, client = socket.socketpair()
    threading.Thread(target=lambda: (server.sendall(payload), server.close()), daemon=True).start()

    stream = VideoStream(client)
    stream.handshake()
    decoder = VideoDecoder("h264")
    frames = []
    for item in stream.packets():
        if isinstance(item, MediaPacket):
            frames += decoder.feed(item.data)
    frames += decoder.flush()  # decoder may hold reordered frames at EOS
    client.close()

    assert len(frames) == N
    assert all((f.width, f.height) == (W, H) for f in frames)
