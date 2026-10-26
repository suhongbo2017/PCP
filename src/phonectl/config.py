"""Paths and constants shared across the project."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _app_root() -> Path:
    """Return the writable project/exe directory."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _resource_root() -> Path:
    """Return PyInstaller's bundled resource directory or the source root."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parents[2]


PROJECT_ROOT = _app_root()
VENDOR_DIR = _resource_root() / "vendor"

# The scrcpy protocol is internal and version-locked: the client and the
# server jar MUST be the same version (see scrcpy doc/develop.md).
SERVER_VERSION = "5.0.1"
SERVER_JAR = Path(
    os.environ.get("PHONECTL_SERVER", VENDOR_DIR / "server" / f"scrcpy-server-v{SERVER_VERSION}.jar")
)
DEVICE_SERVER_PATH = "/data/local/tmp/scrcpy-server.jar"

_VENDOR_ADB = VENDOR_DIR / "adb" / "adb.exe"
# Fall back to `adb` on PATH when the bundled copy is missing.
ADB_PATH = os.environ.get("PHONECTL_ADB") or (str(_VENDOR_ADB) if _VENDOR_ADB.exists() else "adb")

# Host-side TCP port the client listens on for reverse tunnels (scrcpy default).
DEFAULT_PORT = 27183
