"""Rectified wall crops with metric scale, and context views of a building.

A wall crop samples the panorama on the wall's own plane: one pixel is a fixed number of millimetres of wall, the
horizontal axis is metres from the wall's left end and the vertical axis metres above its foot. Rulers are drawn in
a margin outside the picture. Pose errors of a metre or two shift a crop on its ruler; the reader is asked where
the facade really starts and ends. Crops are measurements aids and stay outside Git.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import shapely
from PIL import Image, ImageDraw

from .lod2 import Building
from .panoramas import Pano, Panoramas
from .views import WallView

SIDE_MARGIN_M = 2.5  # beyond the seen stretch, so the facade's ends and the neighbours show
BELOW_M = 1.2
ABOVE_M = 2.5  # eaves, cornice and the start of the roof
MAX_SIDE_PX = 1568  # the reader's useful image size
MAX_PX_PER_M = 56.0
RULER_PX = 30


def sample(src: np.ndarray, heading: float, directions: np.ndarray, up=None) -> tuple[np.ndarray, np.ndarray]:
    """Panorama colours along world directions (..., 3), bilinear, and where the panorama has no picture."""
    d = directions / np.linalg.norm(directions, axis=-1, keepdims=True)
    if up is not None:
        from qalib.upright import to_pano

        d = d @ to_pano(up).T
    h, w = src.shape[:2]
    az = np.degrees(np.arctan2(d[..., 0], d[..., 1]))
    el = np.degrees(np.arcsin(np.clip(d[..., 2], -1, 1)))
    u = (((az - heading) / 360.0 + 0.5) % 1.0) * w - 0.5
    v = (0.5 - el / 180.0) * h - 0.5
    u0, v0 = np.floor(u).astype(np.int64), np.floor(v).astype(np.int64)
    fu, fv = (u - u0)[..., None].astype(np.float32), (v - v0)[..., None].astype(np.float32)
    u1, u0 = (u0 + 1) % w, u0 % w
    v0c, v1c = np.clip(v0, 0, h - 1), np.clip(v0 + 1, 0, h - 1)
    out = (src[v0c, u0] * (1 - fu) * (1 - fv) + src[v0c, u1] * fu * (1 - fv)
           + src[v1c, u0] * (1 - fu) * fv + src[v1c, u1] * fu * fv)
    rgb = np.clip(out + 0.5, 0, 255).astype(np.uint8)
    return rgb, rgb.max(-1) < 4


def _ruler(image: Image.Image, x_left: float, z_top: float, ppm: float, wall_length: float, wall_height: float):
    """The picture with rulers left and below: x in metres from the wall's left end, z in metres above its foot."""
    w, h = image.size
    sheet = Image.new("RGB", (w + RULER_PX, h + RULER_PX), (255, 255, 255))
    sheet.paste(image, (RULER_PX, 0))
    draw = ImageDraw.Draw(sheet)
    span_x, span_z = w / ppm, h / ppm
    step = 5 if max(span_x, span_z) > 22 else 2 if max(span_x, span_z) > 9 else 1
    # The wall's own extent on both rulers.
    xa, xb = RULER_PX + (0 - x_left) * ppm, RULER_PX + (wall_length - x_left) * ppm
    draw.rectangle([max(RULER_PX, xa), h, min(w + RULER_PX, xb), h + 4], fill=(200, 30, 30))
    za, zb = (z_top - 0) * ppm, (z_top - wall_height) * ppm
    draw.rectangle([RULER_PX - 5, max(0, zb), RULER_PX - 1, min(h, za)], fill=(200, 30, 30))
    for m in range(math.ceil(x_left), math.floor(x_left + span_x) + 1):
        x = RULER_PX + (m - x_left) * ppm
        major = m % step == 0
        draw.line([x, h + 5, x, h + (12 if major else 8)], fill=(0, 0, 0))
        if major:
            draw.text((x + 2, h + 13), str(m), fill=(0, 0, 0))
    for m in range(math.ceil(z_top - span_z), math.floor(z_top) + 1):
        y = (z_top - m) * ppm
        major = m % step == 0
        draw.line([RULER_PX - (13 if major else 9), y, RULER_PX - 6, y], fill=(0, 0, 0))
        if major:
            draw.text((1, y - 11 if y > 12 else y + 1), str(m), fill=(0, 0, 0))
    return sheet


def wall_crop(view: WallView, panos: Panoramas, path: Path) -> dict:
    """Write the rectified crop of a wall view; return its record (extent, scale, what the panorama lacks)."""
    wall, pano = view.wall, view.pano
    lows = [z for a, b, z in wall.exposed() if a < view.x1 and b > view.x0] or [wall.base]
    x_left, x_right = view.x0 - SIDE_MARGIN_M, view.x1 + SIDE_MARGIN_M
    z_low, z_high = min(lows) - BELOW_M, wall.top + ABOVE_M
    width_m, height_m = x_right - x_left, z_high - z_low
    ppm = min(MAX_PX_PER_M, 1.3 * view.px_per_m, (MAX_SIDE_PX - RULER_PX) / max(width_m, height_m))
    ppm = max(ppm, 6.0)
    w, h = max(16, round(width_m * ppm)), max(16, round(height_m * ppm))
    xs = x_left + (np.arange(w) + 0.5) / ppm
    zs = z_high - (np.arange(h) + 0.5) / ppm
    X, Z = np.meshgrid(xs, zs)
    points = np.stack([wall.a[0] + X * wall.tangent[0], wall.a[1] + X * wall.tangent[1], Z], -1)
    up = panos.upright(pano)
    rgb, empty = sample(panos.pixels(pano), pano.heading, points - pano.position, up["up"] if up["straightened"] else None)
    on_wall = (X >= view.x0) & (X <= view.x1) & (Z >= min(lows)) & (Z <= wall.top)
    missing = float(empty[on_wall].mean()) if on_wall.any() else 1.0
    path.parent.mkdir(parents=True, exist_ok=True)
    _ruler(Image.fromarray(rgb), x_left, z_high - wall.base, ppm, wall.length, wall.top - wall.base).save(path, quality=90)
    return {**view.to_dict(), "kind": "wall", "file": str(path), "x_left_m": round(x_left, 2),
            "x_right_m": round(x_right, 2), "z_low_m": round(z_low - wall.base, 2),
            "z_high_m": round(z_high - wall.base, 2), "px_per_m": round(ppm, 2), "size": [w + RULER_PX, h + RULER_PX],
            "missing_fraction": round(missing, 3), "upright": up["method"]}


def context_view(building: Building, pano: Pano, panos: Panoramas, path: Path, size=(1280, 960)) -> dict:
    """A plain perspective view of the whole building from a panorama: roof, depth and setting."""
    from qalib.camera import pixel_rays

    cam = pano.position
    ring = np.asarray(building.footprint.convex_hull.exterior.coords)
    corners = np.array([[e, n, z] for e, n in ring for z in (building.base, building.top + 1.0)])
    rel = corners - cam
    centre = building.footprint.centroid
    yaw0 = math.degrees(math.atan2(centre.x - cam[0], centre.y - cam[1]))
    az = (np.degrees(np.arctan2(rel[:, 0], rel[:, 1])) - yaw0 + 180) % 360 - 180
    el = np.degrees(np.arctan2(rel[:, 2], np.hypot(rel[:, 0], rel[:, 1])))
    half_w = min(55.0, max(abs(az.min()), abs(az.max())) + 4)
    yaw = yaw0 + float(np.clip((az.min() + az.max()) / 2, -15, 15))
    top, bottom = min(80.0, el.max() + 3), max(-35.0, el.min() - 3)
    pitch = float(np.clip((top + bottom) / 2, -5.0, 35.0))
    half_h = max(top - pitch, pitch - bottom)
    aspect = size[0] / size[1]
    fov = 2 * max(half_h, math.degrees(math.atan(math.tan(math.radians(half_w)) / aspect)))
    fov = float(np.clip(fov, 40.0, 100.0))
    pose = {"yaw": yaw % 360, "pitch": pitch, "roll": 0.0, "fov": fov}
    up = panos.upright(pano)
    rgb, _ = sample(panos.pixels(pano), pano.heading, pixel_rays(pose, *size), up["up"] if up["straightened"] else None)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgb).save(path, quality=90)
    return {"kind": "context", "file": str(path), "panorama": pano.to_dict(),
            "pose": {**{k: round(v, 2) for k, v in pose.items()}, "E": round(float(cam[0]), 2),
                     "N": round(float(cam[1]), 2), "H": round(float(cam[2]), 2)},
            "size": list(size), "distance_m": round(float(building.footprint.distance(shapely.Point(*cam[:2]))), 1)}
