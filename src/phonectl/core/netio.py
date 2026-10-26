"""Small socket helpers shared by the video and control channels."""

from __future__ import annotations

import socket


class ConnectionClosed(EOFError):
    """The peer closed the socket."""


def read_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray(n)
    view = memoryview(buf)
    got = 0
    while got < n:
        chunk = sock.recv_into(view[got:], n - got)
        if chunk == 0:
            raise ConnectionClosed(f"connection closed after {got}/{n} bytes")
        got += chunk
    return bytes(buf)


def shutdown_and_close(sock: socket.socket | None) -> None:
    """Close a socket in a way that also unblocks a reader thread (Windows-safe)."""
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass
