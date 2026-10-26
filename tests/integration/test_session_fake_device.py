"""End-to-end session flow against a fake adb that plays the device side."""
import json
import socket
import struct
import sys
from pathlib import Path

import pytest
from support.stream import build_encoded_stream

from phonectl.core.adb import Adb
from phonectl.core.protocol import control as msg
from phonectl.core.protocol.control import MotionAction, PointerId
from phonectl.core.session import ScrcpyError, ScrcpySession, ServerOptions

HERE = Path(__file__).parent


@pytest.fixture
def fake_adb(tmp_path, monkeypatch):
    """Returns an Adb wired to fake_adb.py, with state in tmp_path."""
    monkeypatch.setenv("FAKE_ADB_STATE", str(tmp_path))
    cmd = tmp_path / "adb.cmd"
    cmd.write_text(f'@echo off\r\n"{sys.executable}" "{HERE / "fake_adb.py"}" %*\r\n', encoding="ascii")
    return Adb(cmd), tmp_path


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_session_video_control_and_tap(fake_adb, tmp_path):
    adb, state = fake_adb
    (state / "stream.bin").write_bytes(build_encoded_stream(320, 240, 6, device_name="Fake Phone"))
    jar = tmp_path / "server.jar"
    jar.write_bytes(b"not a real jar; fake adb does not run it")

    session = ScrcpySession(
        "FAKE123", adb=adb, options=ServerOptions(max_size=1024), server_jar=jar, port=_free_port()
    )
    with session:
        assert session.info.device_name == "Fake Phone"
        assert session.info.codec == "h264"
        frame = session.wait_frame(timeout=5)
        assert (frame.width, frame.height) == (320, 240)
        assert session.wait_screen_size(timeout=5) == (320, 240)
        session.controller.tap(10, 20, hold=0)

    # Closing the session closes the control socket; the fake device then writes what it got.
    shell = (state / "shell.txt").read_text()
    assert "app_process / com.genymobile.scrcpy.Server 5.0.1" in shell
    assert "max_size=1024" in shell and "audio=false" in shell and "control=true" in shell

    expected = msg.touch(MotionAction.DOWN, PointerId.GENERIC_FINGER, 10, 20, 320, 240, 1.0, 1, 0) + msg.touch(
        MotionAction.UP, PointerId.GENERIC_FINGER, 10, 20, 320, 240, 0.0, 0, 0
    )
    assert (state / "control.bin").read_bytes() == expected

    # reverse tunnel must be removed on close
    assert json.loads((state / "reverse.json").read_text()) == {}


def test_missing_server_jar_fails_fast(fake_adb, tmp_path):
    adb, _ = fake_adb
    session = ScrcpySession("FAKE123", adb=adb, server_jar=tmp_path / "nope.jar", port=_free_port())
    with pytest.raises(ScrcpyError, match="server jar not found"):
        session.start()
