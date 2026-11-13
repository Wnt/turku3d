"""A rectified strip of a panorama along a fence line: what a reader measures a fence from.

The strip is sampled on the vertical plane through the line, as a wall crop is (observe/crops.py, whose sampling and
rulers it uses): the horizontal axis is metres along the fence as the camera sees it, left to right, the vertical
axis metres above the fence's foot. Strips are measurement aids and stay outside Git.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import shapely
from PIL import Image

from ...observe import crops

REACH = (2.5, 22.0)  # m between the camera and the line: nearer is too oblique, farther too few pixels
SPAN = 16.0  # m of fence in one strip, centred on the point nearest the camera
SIDE = 1.0  # m shown beyond each end
BELOW, ABOVE = 0.6, 3.2  # m under and over the foot
PX_PER_M = 56.0


def strip(line, panos, path: Path, height: float = 0.0) -> dict | None:
    """Writes the strip of a fence line ((n, 3): E, N, H of its foot) from the nearest panorama that stands in
    `REACH` of it; returns its record, or None when no panorama does. `height` marks the drawn height on the ruler."""
    line = np.asarray(line, dtype=np.float64)
    course = shapely.LineString(line[:, :2])
    spots = [(course.distance(shapely.Point(spot["E"], spot["N"])), spot) for spot in panos.spots(course.bounds, margin=REACH[1])]
    spots = sorted((item for item in spots if REACH[0] <= item[0] <= REACH[1]), key=lambda item: item[0])
    pano = next((found for found in (panos.resolve(spot, base_h=float(np.median(line[:, 2]))) for _, spot in spots[:3])
                 if found is not None), None)
    if pano is None:
        return None
    along = course.project(shapely.Point(*pano.position[:2]))
    lo, hi = max(along - SPAN / 2, 0.0), min(along + SPAN / 2, course.length)
    a, b = (np.array(course.interpolate(t).coords[0]) for t in (lo, hi))
    length = float(np.hypot(*(b - a)))
    if length < 2.0:
        return None
    tangent = (b - a) / length
    if tangent[0] * (pano.position[1] - a[1]) - tangent[1] * (pano.position[0] - a[0]) > 0:
        a, tangent = b, -tangent  # the camera stands left of the course: turned, so x runs left to right as seen
    steps = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(line[:, :2], axis=0).T))])
    foot = float(np.interp(along, steps, line[:, 2]))
    w, h = round((length + 2 * SIDE) * PX_PER_M), round((BELOW + ABOVE) * PX_PER_M)
    xs = -SIDE + (np.arange(w) + 0.5) / PX_PER_M
    zs = ABOVE - (np.arange(h) + 0.5) / PX_PER_M
    X, Z = np.meshgrid(xs, zs)
    points = np.stack([a[0] + X * tangent[0], a[1] + X * tangent[1], foot + Z], -1)
    up = panos.upright(pano)
    rgb, empty = crops.sample(panos.pixels(pano), pano.heading, points - pano.position, up["up"] if up["straightened"] else None)
    path.parent.mkdir(parents=True, exist_ok=True)
    crops._ruler(Image.fromarray(rgb), -SIDE, ABOVE, PX_PER_M, length, height).save(path, quality=90)
    return {"file": str(path), "panorama": pano.to_dict(), "distance_m": round(float(course.distance(shapely.Point(*pano.position[:2]))), 1),
            "from_m": round(lo, 2), "to_m": round(hi, 2), "length_m": round(length, 2), "px_per_m": PX_PER_M,
            "missing_fraction": round(float(empty.mean()), 3), "upright": up["method"]}
