"""Decoded video frame -> QImage at display size."""

from __future__ import annotations

import av
import numpy as np
from PySide6.QtGui import QImage


def frame_to_qimage(frame: av.VideoFrame, width: int, height: int) -> QImage:
    """Scale a frame to width x height (pixels) and return an owned RGB888 QImage.

    Scaling happens in FFmpeg (swscale) so the GUI thread copies only the display-sized
    picture, not the full-resolution frame.
    """
    width, height = max(1, int(width)), max(1, int(height))
    # FFmpeg pads each row for alignment, so the array may not be C-contiguous;
    # QImage needs a contiguous buffer, so compact it (cheap at display size).
    rgb = np.ascontiguousarray(frame.reformat(width=width, height=height, format="rgb24").to_ndarray())
    image = QImage(rgb.data, width, height, rgb.strides[0], QImage.Format.Format_RGB888)
    return image.copy()  # detach from the numpy buffer, which is freed after this call
