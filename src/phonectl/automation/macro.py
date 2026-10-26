"""Record user input and replay it later.

While recording, the session's controller is swapped for a `RecordingController`: a
proxy that forwards every call to the real Controller and logs the input methods with
their time offsets. A macro is just that list, saved as JSON, and `replay` calls the same
methods again with the same timing.
"""

from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

FORMAT_VERSION = 1

# Controller methods that are user input. Internal helper calls (e.g. tap -> touch_down)
# are not recorded separately because the proxy only sees the outer call.
RECORDED_OPS = frozenset(
    {
        "touch_down",
        "touch_move",
        "touch_up",
        "tap",
        "swipe",
        "back",
        "home",
        "key",
        "key_down",
        "key_up",
        "text",
        "scroll",
        "app_switch",
        "wake",
        "expand_notifications",
        "expand_settings",
        "collapse_panels",
        "rotate_device",
        "set_display_power",
    }
)


@dataclass
class Step:
    t: float  # seconds since the start of the recording
    op: str
    args: list = field(default_factory=list)
    kwargs: dict = field(default_factory=dict)


@dataclass
class Macro:
    steps: list[Step] = field(default_factory=list)
    created: str = ""

    @property
    def duration(self) -> float:
        return self.steps[-1].t if self.steps else 0.0

    def to_dict(self) -> dict:
        return {
            "version": FORMAT_VERSION,
            "created": self.created,
            "steps": [{"t": round(s.t, 4), "op": s.op, "args": s.args, "kwargs": s.kwargs} for s in self.steps],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Macro:
        if data.get("version") != FORMAT_VERSION:
            raise ValueError(f"unsupported macro version {data.get('version')!r}")
        steps = []
        for i, raw in enumerate(data.get("steps", [])):
            op = raw.get("op")
            if op not in RECORDED_OPS:
                raise ValueError(f"step {i}: unknown operation {op!r}")
            t = float(raw["t"])
            args, kwargs = raw.get("args", []), raw.get("kwargs", {})
            if not math.isfinite(t) or t < 0 or not isinstance(args, list) or not isinstance(kwargs, dict):
                raise ValueError(f"step {i}: invalid timing or arguments")
            steps.append(Step(t=t, op=op, args=args, kwargs=kwargs))
        if any(a.t > b.t for a, b in zip(steps, steps[1:])):
            raise ValueError("macro steps must be ordered by time")
        return cls(steps=steps, created=data.get("created", ""))

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path) -> Macro:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


class Recorder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._t0 = 0.0
        self._steps: list[Step] = []
        self.recording = False

    def start(self) -> None:
        with self._lock:
            self._steps = []
            self._t0 = time.monotonic()
            self.recording = True

    def record(self, op: str, args: tuple, kwargs: dict) -> None:
        with self._lock:
            if not self.recording:
                return
            t = time.monotonic() - self._t0
            self._steps.append(Step(t=t, op=op, args=_jsonable(list(args)), kwargs=_jsonable(dict(kwargs))))

    def stop(self) -> Macro:
        with self._lock:
            self.recording = False
            steps, self._steps = self._steps, []
        return Macro(steps=steps, created=datetime.now().isoformat(timespec="seconds"))


class RecordingController:
    """Forwards everything to `inner`; records calls to RECORDED_OPS while the recorder is on."""

    def __init__(self, inner, recorder: Recorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def __getattr__(self, name: str):
        attr = getattr(self._inner, name)
        if name in RECORDED_OPS and callable(attr):

            def recorded(*args, **kwargs):
                self._recorder.record(name, args, kwargs)
                return attr(*args, **kwargs)

            return recorded
        return attr


def replay(macro: Macro, controller, *, should_stop=lambda: False, speed: float = 1.0) -> int:
    """Perform the macro's steps with their original timing. Returns the number of steps done.

    `should_stop` is polled between steps; `speed` 2.0 replays twice as fast.
    """
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError("speed must be a positive finite number")
    start = time.monotonic()
    done = 0
    for step in macro.steps:
        target = start + step.t / speed
        while True:
            if should_stop():
                return done
            remaining = target - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.05, remaining))
        getattr(controller, step.op)(*step.args, **step.kwargs)
        done += 1
    return done


def _jsonable(value):
    """Tuples -> lists and enums -> ints so the macro survives a JSON round trip."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "value") and isinstance(getattr(value, "value"), int):  # IntEnum
        return int(value.value)
    return value
