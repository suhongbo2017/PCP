"""Feed a synthetic scrcpy video stream through a real socket pair."""
import socket
import struct
import threading

from phonectl.core.protocol.video import DEVICE_NAME_LENGTH
from phonectl.core.session import ServerOptions
from phonectl.core.video_stream import MediaPacket, VideoStream
from phonectl.core.protocol.video import SessionPacket


def _stream_bytes() -> bytes:
    out = b"Test Device".ljust(DEVICE_NAME_LENGTH, b"\x00")
    out += b"h264"  # codec id
    out += struct.pack(">III", 0x8000_0000, 640, 360)  # session packet
    out += struct.pack(">QI", 1 << 62, 4) + b"CFG!"  # config packet
    out += struct.pack(">QI", (1 << 61) | 1000, 3) + b"KEY"  # key frame
    out += struct.pack(">QI", 2000, 2) + b"PF"  # P frame
    return out


def test_handshake_and_packets_over_socketpair():
    server, client = socket.socketpair()
    threading.Thread(target=lambda: (server.sendall(_stream_bytes()), server.close()), daemon=True).start()

    stream = VideoStream(client)
    info = stream.handshake()
    assert (info.device_name, info.codec) == ("Test Device", "h264")

    items = list(stream.packets())
    assert items[0] == SessionPacket(width=640, height=360, client_resized=False)
    assert isinstance(items[1], MediaPacket) and items[1].header.config and items[1].data == b"CFG!"
    assert items[2].header.key_frame and items[2].header.pts == 1000 and items[2].data == b"KEY"
    assert items[3].header.pts == 2000 and items[3].data == b"PF"
    assert len(items) == 4
    client.close()


def test_server_options_args():
    args = ServerOptions(max_size=1024, max_fps=30).to_args()
    assert "max_size=1024" in args and "max_fps=30" in args
    assert "audio=false" in args and "control=true" in args
