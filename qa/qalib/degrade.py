"""Deterministic image degradations with a record of what was planted (for judge self-tests).

Each op returns the new image and a list of planted differences:
  {"what": str, "bbox": [x0, y0, x1, y1] (normalised, image-wide if global), "category": str}
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter


def _px(b, w, h):
    return int(b[0] * w), int(b[1] * h), int(b[2] * w), int(b[3] * h)


def blur(img: Image.Image, radius: float = 4.0):
    out = img.filter(ImageFilter.GaussianBlur(radius))
    return out, [
        {
            "what": f"whole image blurred (Gaussian r={radius}px): textures lack detail",
            "bbox": [0, 0, 1, 1],
            "category": "texture",
        }
    ]


def recolour(
    img: Image.Image, bbox, hue_shift: float = 0.5, sat_gain: float = 1.8, label: str = "facade"
):
    """Rotate the hue (fraction of the circle) and boost saturation inside bbox."""
    w, h = img.size
    x0, y0, x1, y1 = _px(bbox, w, h)
    hsv = np.asarray(img.convert("HSV")).astype(np.float32)
    reg = hsv[y0:y1, x0:x1]
    # leave bright, unsaturated pixels (sky, clouds) alone so only the facade changes colour
    m = ~((reg[..., 2] > 200) & (reg[..., 1] < 40))
    shifted = reg.copy()
    shifted[..., 0] = (reg[..., 0] + hue_shift * 255) % 256
    shifted[..., 1] = np.clip(np.maximum(reg[..., 1], 50) * sat_gain, 0, 255)
    reg[m] = shifted[m]
    hsv[y0:y1, x0:x1] = reg
    region = Image.fromarray(hsv.astype(np.uint8), "HSV").convert("RGB").crop((x0, y0, x1, y1))
    out = img.convert("RGB").copy()
    out.paste(region, (x0, y0))  # pixels outside bbox stay bit-exact (no HSV round trip)
    return out, [
        {
            "what": f"{label} recoloured (hue rotated {int(hue_shift * 360)} deg, saturated)",
            "bbox": list(bbox),
            "category": "material",
        }
    ]


def grey_mask(img: Image.Image, bbox, grey: int = 128, label: str = "building"):
    """Replace a region with flat grey: an untextured / missing building."""
    out = img.copy()
    w, h = img.size
    out.paste((grey, grey, grey), _px(bbox, w, h))
    return out, [
        {
            "what": f"{label} replaced by a flat grey block (no texture/detail)",
            "bbox": list(bbox),
            "category": "texture",
        }
    ]


def chain(img: Image.Image, ops):
    """ops: list of (fn, kwargs). Returns (image, planted)."""
    planted = []
    for fn, kw in ops:
        img, p = fn(img, **kw)
        planted += p
    return img, planted


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def overlap_frac(a, b) -> float:
    """Fraction of box b covered by box a."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    ab = (b[2] - b[0]) * (b[3] - b[1])
    return ix * iy / ab if ab > 0 else 0.0
