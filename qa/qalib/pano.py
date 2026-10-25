"""Equirectangular panorama -> perspective view (numpy only, bilinear).

Equirect convention (Google Street View, verified in docs/research/streetview.md):
  column u -> azimuth = heading + (u/W - 0.5) * 360  (clockwise from north; image centre = heading)
  row v    -> elevation = (0.5 - v/H) * 180
The pano's own pitch/roll tilt (typically < 2 deg) is ignored; record that in pose_confidence.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from .camera import pixel_rays


def perspective(eq: Image.Image | np.ndarray, pano_heading: float, pose: dict, w: int, h: int) -> Image.Image:
    src = np.asarray(eq.convert("RGB") if isinstance(eq, Image.Image) else eq)  # uint8, no full float copy
    H, W = src.shape[:2]
    d = pixel_rays(pose, w, h)
    az = np.degrees(np.arctan2(d[..., 0], d[..., 1]))
    el = np.degrees(np.arcsin(np.clip(d[..., 2], -1, 1)))
    u = (((az - pano_heading) / 360.0 + 0.5) % 1.0) * W - 0.5
    v = (0.5 - el / 180.0) * H - 0.5
    u0 = np.floor(u).astype(np.int64)
    v0 = np.floor(v).astype(np.int64)
    fu = (u - u0)[..., None]
    fv = (v - v0)[..., None]
    u1 = (u0 + 1) % W
    u0 = u0 % W
    v0c = np.clip(v0, 0, H - 1)
    v1c = np.clip(v0 + 1, 0, H - 1)
    g = lambda vv, uu: src[vv, uu].astype(np.float32)  # noqa: E731
    out = (g(v0c, u0) * (1 - fu) * (1 - fv) + g(v0c, u1) * fu * (1 - fv)
           + g(v1c, u0) * (1 - fu) * fv + g(v1c, u1) * fu * fv)
    return Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8))


def equirect_pixel(pano_heading: float, W: int, H: int, az: float, el: float) -> tuple[float, float]:
    """Where a direction (compass az, elevation deg) lands in the equirect (for tests)."""
    u = (((az - pano_heading) / 360.0 + 0.5) % 1.0) * W
    v = (0.5 - el / 180.0) * H
    return u, v

