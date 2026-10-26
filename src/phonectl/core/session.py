"""One scrcpy session with one Android device.

Lifecycle (reverse tunnel, the scrcpy default):
  1. bind a host TCP listener on 127.0.0.1:<port>
  2. adb push scrcpy-server.jar, adb reverse localabstract:scrcpy_<scid> tcp:<port>
  3. start the server with `adb shell app_process ...` (long-running)
  4. accept the video socket (handshake), then the control socket
  5. a background thread decodes video and keeps the latest frame
  6. close() tears everything down; the server exits when its sockets close

Socket order is fixed by the server: video first, then control (audio is disabled).
"""

from __future__ import annotations

import logging
import random
import socket
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import av

from .. import config
from .adb import Adb
from .control_channel import ControlChannel
from .controller import Controller
from .decoder import VideoDecoder
from .netio import shutdown_and_close
from .protocol.video import SessionPacket
from .video_stream import MediaPacket, StreamInfo, VideoStream

log = logging.getLogger(__name__)


class ScrcpyError(RuntimeError):
    """The session could not be started or the connection failed."""


@dataclass
class ServerOptions:
    video_codec: str = "h264"
    max_size: int = 0  # 0 = no limit
    max_fps: int = 0  # 0 = no limit
    video_bit_rate: int = 8_000_000
    clipboard_autosync: bool = True
    log_level: str = "info"

    def to_args(self) -> list[str]:
        args = [
            f"video_codec={self.video_codec}",
            f"video_bit_rate={self.video_bit_rate}",
            "audio=false",
            "control=true",
            f"clipboard_autosync={str(self.clipboard_autosync).lower()}",
            f"log_level={self.log_level}",
        ]
        if self.max_size:
            args.append(f"max_size={self.max_size}")
        if self.max_fps:
            args.append(f"max_fps={self.max_fps}")
        return args


class ScrcpySession:
    def __init__(
        self,
        serial: str,
        *,
        adb: Adb | None = None,
        options: ServerOptions | None = None,
        server_jar: Path = config.SERVER_JAR,
        port: int = config.DEFAULT_PORT,
        connect_timeout: float = 15.0,
    ) -> None:
        self.serial = serial
        self.adb = adb or Adb()
        self.options = options or ServerOptions()
        self.server_jar = Path(server_jar)
        self.port = port
        self.connect_timeout = connect_timeout

        self.scid: int | None = None
        self.info: StreamInfo | None = None
        self.control: ControlChannel | None = None
        self.controller: Controller | None = None

        self._cond = threading.Condition()  # guards the three fields below
        self._latest_frame: av.VideoFrame | None = None
        self._screen_size: tuple[int, int] | None = None
        self._video_done = False

        self._listener: socket.socket | None = None
        self._video_sock: socket.socket | None = None
        self._control_sock: socket.socket | None = None
        self._video_stream: VideoStream | None = None
        self._proc: subprocess.Popen | None = None
        self._server_log: deque[str] = deque(maxlen=200)
        self._closed = False

    # ---- lifecycle -------------------------------------------------------

    def __enter__(self) -> ScrcpySession:
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    @property
    def _socket_name(self) -> str:
        assert self.scid is not None
        return f"localabstract:scrcpy_{self.scid:08x}"

    def start(self) -> None:
        """Start the server and connect. On failure everything started so far is torn down."""
        try:
            self._start()
        except BaseException:
            self.close()
            raise

    def _start(self) -> None:
        if not self.server_jar.exists():
            raise ScrcpyError(f"server jar not found: {self.server_jar}")

        self.scid = random.getrandbits(31)  # 31-bit non-negative, random per session
        self._listener, self.port = self._bind_listener()

        log.info("pushing server to %s", self.serial)
        self.adb.push(self.server_jar, config.DEVICE_SERVER_PATH, self.serial)
        self.adb.reverse(self.serial, self._socket_name, f"tcp:{self.port}")

        command = " ".join(
            [
                f"CLASSPATH={config.DEVICE_SERVER_PATH}",
                "app_process / com.genymobile.scrcpy.Server",
                config.SERVER_VERSION,
                f"scid={self.scid:08x}",
                *self.options.to_args(),
            ]
        )
        log.info("starting server (scid=%08x)", self.scid)
        self._proc = self.adb.shell_popen(self.serial, command)
        threading.Thread(target=self._pump_server_output, name="server-log", daemon=True).start()

        # Video socket first: it carries the device name and codec id.
        self._video_sock = self._accept()
        self._video_stream = VideoStream(self._video_sock, device_meta=True)
        self.info = self._video_stream.handshake()
        log.info("video connected: device=%r codec=%s", self.info.device_name, self.info.codec)

        self._control_sock = self._accept()
        self.control = ControlChannel(self._control_sock)
        self.control.start()
        self.controller = Controller(self.control, self._require_screen_size)
        log.info("control connected")

        self._listener.close()
        self._listener = None

        threading.Thread(target=self._video_loop, name="video-decoder", daemon=True).start()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        if self.control is not None:
            self.control.close()
        shutdown_and_close(self._control_sock)
        shutdown_and_close(self._video_sock)
        if self._listener is not None:
            self._listener.close()

        # The server exits once its sockets are closed. Give it a moment, then
        # terminate the local adb client as a fallback.
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._proc.kill()

        if self.scid is not None:
            self.adb.reverse_remove(self.serial, self._socket_name)

    # ---- data access -----------------------------------------------------

    def wait_frame(self, timeout: float = 10.0) -> av.VideoFrame:
        """Block until a decoded frame is available and return the latest one."""
        with self._cond:
            self._cond.wait_for(lambda: self._latest_frame is not None or self._video_done, timeout)
            if self._latest_frame is None:
                reason = "video stream ended" if self._video_done else f"no frame within {timeout}s"
                raise ScrcpyError(reason)
            return self._latest_frame

    @property
    def latest_frame(self) -> av.VideoFrame | None:
        with self._cond:
            return self._latest_frame

    @property
    def screen_size(self) -> tuple[int, int] | None:
        """Video size from the last session packet (None before the first one)."""
        with self._cond:
            return self._screen_size

    def wait_screen_size(self, timeout: float = 10.0) -> tuple[int, int]:
        with self._cond:
            self._cond.wait_for(lambda: self._screen_size is not None or self._video_done, timeout)
            if self._screen_size is None:
                raise ScrcpyError("screen size unknown (no video session packet received)")
            return self._screen_size

    def _require_screen_size(self) -> tuple[int, int]:
        return self.wait_screen_size(timeout=5.0)

    # ---- internals -------------------------------------------------------

    def _video_loop(self) -> None:
        """Read video packets, decode, and publish the latest frame. Runs until the stream ends."""
        assert self._video_stream is not None and self.info is not None
        decoder = VideoDecoder(self.info.codec)
        try:
            for item in self._video_stream.packets():
                if isinstance(item, SessionPacket):
                    with self._cond:
                        self._screen_size = (item.width, item.height)
                        self._cond.notify_all()
                elif isinstance(item, MediaPacket):
                    for frame in decoder.feed(item.data):
                        self._publish(frame)
            for frame in decoder.flush():
                self._publish(frame)
        except (OSError, ValueError) as exc:  # socket errors and protocol errors
            if not self._closed:
                log.warning("video stream stopped: %s", exc)
        finally:
            with self._cond:
                self._video_done = True
                self._cond.notify_all()

    def _publish(self, frame: av.VideoFrame) -> None:
        with self._cond:
            self._latest_frame = frame
            self._cond.notify_all()

    def _bind_listener(self) -> tuple[socket.socket, int]:
        for port in range(self.port, self.port + 20):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind(("127.0.0.1", port))
                sock.listen(4)  # room for the video and control connections
                return sock, port
            except OSError:
                sock.close()
        raise ScrcpyError(f"no free TCP port in {self.port}..{self.port + 19}")

    def _accept(self) -> socket.socket:
        assert self._listener is not None and self._proc is not None
        deadline = time.monotonic() + self.connect_timeout
        self._listener.settimeout(0.5)
        while True:
            try:
                conn, _ = self._listener.accept()
                conn.settimeout(None)  # blocking reads; close() unblocks them
                return conn
            except socket.timeout:
                pass
            if self._proc.poll() is not None:
                raise ScrcpyError(f"server exited early (code {self._proc.returncode}):\n{self._log_tail()}")
            if time.monotonic() > deadline:
                raise ScrcpyError(f"server did not connect within {self.connect_timeout}s:\n{self._log_tail()}")

    def _pump_server_output(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for raw in iter(self._proc.stdout.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip()
            self._server_log.append(line)
            log.debug("[server] %s", line)

    def _log_tail(self, n: int = 20) -> str:
        return "\n".join(list(self._server_log)[-n:]) or "(no server output)"
