"""Template matching on screen images (pure functions, no device needed)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

DEFAULT_SCALES: tuple[float, ...] = (1.0, 0.9, 1.1, 0.8, 1.2)


@dataclass(frozen=True)
class Match:
    x: int  # top-left corner, in image pixels
    y: int
    w: int
    h: int
    score: float  # normalized correlation, 1.0 = identical
    scale: float  # template scale that matched

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2


def load_image(source: str | Path | np.ndarray) -> np.ndarray:
    """Load a BGR image from a path (works with non-ASCII paths on Windows) or pass an array through."""
    if isinstance(source, np.ndarray):
        return source
    data = np.fromfile(str(source), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"cannot read image: {source}")
    return img


def to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def find_template(
    image: np.ndarray,
    template: np.ndarray,
    threshold: float = 0.85,
    scales: tuple[float, ...] = (1.0,),
    region: tuple[int, int, int, int] | None = None,
) -> Match | None:
    """Best match of `template` in `image` with score >= threshold, or None.

    `region` (x, y, w, h) restricts the search; returned coordinates are always in
    full-image pixels. Each scale resizes the template before matching.
    """
    img = to_gray(image)
    tpl = to_gray(template)
    offset_x = offset_y = 0
    if region is not None:
        rx, ry, rw, rh = region
        img = img[ry : ry + rh, rx : rx + rw]
        offset_x, offset_y = rx, ry

    best: Match | None = None
    for scale in scales:
        t = tpl
        if scale != 1.0:
            t = cv2.resize(tpl, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        th, tw = t.shape[:2]
        ih, iw = img.shape[:2]
        if th < 2 or tw < 2 or th > ih or tw > iw:
            continue
        if float(t.std()) < 1e-6:  # flat template: correlation is undefined
            continue
        result = cv2.matchTemplate(img, t, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if not np.isfinite(max_val):
            continue
        if best is None or max_val > best.score:
            best = Match(
                x=int(max_loc[0]) + offset_x,
                y=int(max_loc[1]) + offset_y,
                w=tw,
                h=th,
                score=float(max_val),
                scale=float(scale),
            )
    if best is None or best.score < threshold:
        return None
    return best
