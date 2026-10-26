"""Mapping between the mirror widget and device pixels (pure math, no Qt)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h


def fit_rect(view_w: float, view_h: float, frame_w: float, frame_h: float) -> Rect:
    """Largest rectangle with the frame's aspect ratio, centered in the view (letterboxed)."""
    if view_w <= 0 or view_h <= 0 or frame_w <= 0 or frame_h <= 0:
        return Rect(0, 0, 0, 0)
    scale = min(view_w / frame_w, view_h / frame_h)
    w, h = frame_w * scale, frame_h * scale
    return Rect((view_w - w) / 2, (view_h - h) / 2, w, h)


def view_to_device(
    px: float,
    py: float,
    view_w: float,
    view_h: float,
    frame_w: int,
    frame_h: int,
) -> tuple[int, int] | None:
    """Convert a point in widget coordinates to device pixels.

    Returns None when the point is in the letterbox margin (outside the picture).
    """
    rect = fit_rect(view_w, view_h, frame_w, frame_h)
    if rect.w <= 0 or not rect.contains(px, py):
        return None
    scale = rect.w / frame_w
    x = int((px - rect.x) / scale)
    y = int((py - rect.y) / scale)
    return (min(max(x, 0), frame_w - 1), min(max(y, 0), frame_h - 1))
