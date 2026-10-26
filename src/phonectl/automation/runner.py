"""Load and run user scripts in a background thread, with stop and logging."""

from __future__ import annotations

import importlib.util
import logging
import ast
import threading
import traceback
from collections.abc import Callable
from enum import Enum
from pathlib import Path

from .device import Device, ScriptStopped

log = logging.getLogger(__name__)

ScriptFn = Callable[[Device], None]


class ScriptError(RuntimeError):
    """The script file cannot be loaded or does not define run(dev)."""


class RunState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    FINISHED = "finished"
    STOPPED = "stopped"
    FAILED = "failed"


def load_script(path: str | Path) -> ScriptFn:
    """Import a script file and return its `run(dev)` function."""
    path = Path(path)
    if not path.is_file():
        raise ScriptError(f"script not found: {path}")
    spec = importlib.util.spec_from_file_location(f"phonectl_script_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ScriptError(f"cannot load {path}")
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError, UnicodeError) as exc:
        line = f":{exc.lineno}" if isinstance(exc, SyntaxError) else ""
        message = exc.msg if isinstance(exc, SyntaxError) else str(exc)
        label = "syntax error" if isinstance(exc, SyntaxError) else "cannot load script"
        raise ScriptError(f"{path}{line}: {label}: {message}") from exc
    if not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run" for node in tree.body):
        raise ScriptError(f"{path} must define run(dev)")
    # Execute the script in its worker, not here on the GUI thread. Its top-level code
    # is still user code, but it cannot block the UI while the module is imported.
    def execute(dev: Device) -> None:
        namespace = {"__name__": f"phonectl_script_{path.stem}", "__file__": str(path)}
        exec(compile(tree, str(path), "exec"), namespace)
        namespace["run"](dev)

    return execute


class ScriptRunner:
    def __init__(
        self,
        session,
        *,
        log_fn: Callable[[str], None] | None = None,
        on_done: Callable[[RunState, str], None] | None = None,
    ) -> None:
        self._session = session
        self._log_fn = log_fn or (lambda msg: log.info("%s", msg))
        self._on_done = on_done
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.state = RunState.IDLE
        self.error: str = ""

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def run_file(self, path: str | Path) -> None:
        self.run(load_script(path), name=Path(path).name)

    def run(self, fn: ScriptFn, name: str = "script") -> None:
        if self.running:
            raise ScriptError("a script is already running")
        self._stop.clear()
        self.state = RunState.RUNNING
        self.error = ""
        self._log_fn(f"[START] {name}")
        self._thread = threading.Thread(target=self._main, args=(fn, name), name=f"script-{name}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Ask the script to stop. It stops at its next action or sleep."""
        if self.running:
            self._stop.set()
            self._log_fn("■ stop requested")

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _main(self, fn: ScriptFn, name: str) -> None:
        dev = Device(self._session, stop_event=self._stop, log_fn=self._log_fn)
        try:
            fn(dev)
            state, message = RunState.FINISHED, "finished"
        except ScriptStopped:
            state, message = RunState.STOPPED, "stopped"
        except Exception as exc:  # the script's own bug or a device error
            state = RunState.FAILED
            self.error = f"{type(exc).__name__}: {exc}"
            message = self.error
            self._log_fn(traceback.format_exc().rstrip())
        self.state = state
        marker = "[DONE]" if state is RunState.FINISHED else "[STOP]" if state is RunState.STOPPED else "[FAIL]"
        self._log_fn(f"{marker} {name} {message}")
        if self._on_done:
            self._on_done(state, message)
