"""The control socket: thread-safe writes, a reader thread for device messages."""

from __future__ import annotations

import logging
import socket
import threading
from collections.abc import Callable

from .netio import ConnectionClosed, read_exact, shutdown_and_close
from .protocol.device import AckClipboardMsg, ClipboardMsg, ProtocolError, read_device_message

log = logging.getLogger(__name__)


class ControlError(RuntimeError):
    """The control channel is closed or cannot be written."""


class ControlChannel:
    def __init__(
        self,
        sock: socket.socket,
        *,
        on_clipboard: Callable[[str], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
    ) -> None:
        self._sock = sock
        self._on_clipboard = on_clipboard
        self._on_disconnect = on_disconnect
        self._send_lock = threading.Lock()
        self._cond = threading.Condition()
        self._acks: set[int] = set()
        self._clipboard: str | None = None
        self._clipboard_count = 0
        self._closed = False
        self._reader = threading.Thread(target=self._read_loop, name="control-reader", daemon=True)

    # ---- lifecycle -------------------------------------------------------

    def start(self) -> None:
        self._reader.start()

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        shutdown_and_close(self._sock)

    # ---- writing ---------------------------------------------------------

    def send(self, data: bytes) -> None:
        if self._closed:
            raise ControlError("control channel is closed")
        try:
            with self._send_lock:
                self._sock.sendall(data)
        except OSError as exc:
            raise ControlError(f"control write failed: {exc}") from exc

    # ---- device -> PC ----------------------------------------------------

    @property
    def clipboard(self) -> str | None:
        """Last clipboard text pushed by the device (None if never received)."""
        with self._cond:
            return self._clipboard

    def wait_ack(self, sequence: int, timeout: float) -> bool:
        """Wait for the device to acknowledge a SET_CLIPBOARD with this sequence."""
        with self._cond:
            self._cond.wait_for(lambda: sequence in self._acks or self._closed, timeout)
            if sequence in self._acks:
                self._acks.discard(sequence)
                return True
            return False

    def request_clipboard(self, data: bytes, timeout: float) -> str | None:
        """Send a clipboard request and wait for the device's next clipboard push.

        The push counter is sampled *before* sending, so a reply that arrives
        immediately after the write is not missed. Returns None on timeout.
        """
        with self._cond:
            seen = self._clipboard_count
        self.send(data)
        with self._cond:
            self._cond.wait_for(lambda: self._clipboard_count != seen or self._closed, timeout)
            if self._clipboard_count != seen:
                return self._clipboard
            return None

    def _read_loop(self) -> None:
        try:
            while True:
                msg = read_device_message(lambda n: read_exact(self._sock, n))
                if isinstance(msg, ClipboardMsg):
                    with self._cond:
                        self._clipboard = msg.text
                        self._clipboard_count += 1
                        self._cond.notify_all()
                    if self._on_clipboard:
                        self._on_clipboard(msg.text)
                elif isinstance(msg, AckClipboardMsg):
                    with self._cond:
                        self._acks.add(msg.sequence)
                        self._cond.notify_all()
                else:
                    log.debug("ignored device message: %r", msg)
        except ConnectionClosed:
            log.debug("control socket closed by server")
        except (ProtocolError, OSError) as exc:
            if self._closed:  # we closed the socket ourselves: expected
                log.debug("control reader stopped on close: %s", exc)
            else:
                log.warning("control reader stopped: %s", exc)
        finally:
            with self._cond:
                was_closed = self._closed
                self._closed = True
                self._cond.notify_all()
            if not was_closed and self._on_disconnect:
                self._on_disconnect()
