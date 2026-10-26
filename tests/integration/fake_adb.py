"""A stand-in for adb.exe that simulates one device whose server connects back over the reverse tunnel.

The "device" opens two connections in the same order as the real server:
  1. video:   sends $FAKE_ADB_STATE/stream.bin, then closes
  2. control: reads everything the PC sends until EOF -> $FAKE_ADB_STATE/control.bin

State lives in $FAKE_ADB_STATE (a directory):
  reverse.json  - reverse mappings created by `adb reverse`
  stream.bin    - the video-socket bytes the "device" sends
  shell.txt     - the last `adb shell` command received
  control.bin   - bytes received on the control socket
"""
import json
import os
import socket
import sys
import time
from pathlib import Path

state = Path(os.environ["FAKE_ADB_STATE"])
args = sys.argv[1:]
if args[:1] == ["-s"]:
    args = args[2:]
cmd, rest = args[0], args[1:]

if cmd == "devices":
    print("List of devices attached")
    print("FAKE123  device usb:1-1 product:fake model:Fake_Phone device:fake transport_id:1")
elif cmd == "push":
    pass
elif cmd == "reverse":
    mappings = json.loads((state / "reverse.json").read_text()) if (state / "reverse.json").exists() else {}
    if rest[0] == "--remove":
        mappings.pop(rest[1], None)
    else:
        mappings[rest[0]] = rest[1]
    (state / "reverse.json").write_text(json.dumps(mappings))
elif cmd == "shell":
    (state / "shell.txt").write_text(rest[0])
    mappings = json.loads((state / "reverse.json").read_text())
    remote = next(k for k in mappings if k.startswith("localabstract:scrcpy_"))
    port = int(mappings[remote].split(":")[1])

    video = socket.create_connection(("127.0.0.1", port))
    video.sendall((state / "stream.bin").read_bytes())
    video.close()

    control = socket.create_connection(("127.0.0.1", port))
    received = bytearray()
    while True:
        chunk = control.recv(4096)
        if not chunk:
            break
        received += chunk
    control.close()
    (state / "control.bin").write_bytes(bytes(received))
    time.sleep(0.1)
    print("fake server exited")
else:
    print(f"fake adb: unsupported {cmd}", file=sys.stderr)
    sys.exit(1)
