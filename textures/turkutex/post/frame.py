"""Remove paired near-white presentation margins from generated facade canvases.

Only full rows/columns that are nearly uniform neutral white qualify. Architectural
detail is never segmented, filled, or cropped to an estimated bay pattern.
"""

from __future__ import annotations

import numpy as np
from PIL import Image


def facade_content_box(image: Image.Image) -> tuple[int, int, int, int]:
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    height, width = rgb.shape[:2]
    white = (rgb.min(-1) >= 240) & ((rgb.max(-1) - rgb.min(-1)) <= 8)

    def bounds(axis):
        # A photograph's sky has texture/colour; a presentation frame is flat white.
        blank = (white.mean(axis=axis) >= 0.995) & (rgb.std(axis=(axis, 2)) <= 4)
        content = np.flatnonzero(~blank)
        if not len(content):
            raise ValueError("Generated facade contains only a blank white canvas")
        lo, hi = int(content[0]), int(content[-1]) + 1
        minimum = max(3, round(len(blank) * 0.01))
        # Require both opposing margins, to preserve a real light cornice/plinth.
        return (lo, hi) if lo >= minimum and len(blank) - hi >= minimum else (0, len(blank))

    top, bottom = bounds(1)
    left, right = bounds(0)
    return left, top, right, bottom
