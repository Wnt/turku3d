"""Calibrated Mapillary/OpenSfM perspective and fisheye projection.

The camera model and world-to-camera rotation follow MapillaryJS:
https://mapillary.github.io/mapillary-js/api/classes/viewer.Image/
https://github.com/mapillary/mapillary-js/tree/main/src/geometry/camera
Only EXIF orientation 1 is admitted by discovery; images are never assumed panoramic.
"""

from __future__ import annotations

import math

import numpy as np
from turku3d import config


class MapillaryCamera:
    def __init__(self, meta):
        self.kind = meta.extra["camera_type"]
        self.focal, self.k1, self.k2 = meta.extra["camera_parameters"]
        vector = np.asarray(meta.extra["computed_rotation"], dtype=float)
        angle = np.linalg.norm(vector)
        if angle < 1e-12:
            self.rotation = np.eye(3)
        else:
            x, y, z = vector / angle
            cross = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
            self.rotation = (
                np.eye(3) + math.sin(angle) * cross + (1 - math.cos(angle)) * (cross @ cross)
            )
        self.convergence = math.radians(config.true_to_grid_bearing(meta.E, meta.N, 0))

    def project(self, directions, width, height):
        # EPSG:3877 grid ENU -> true ENU -> OpenSfM camera (right, down, forward).
        c, s = math.cos(self.convergence), math.sin(self.convergence)
        enu = np.stack(
            [
                c * directions[..., 0] - s * directions[..., 1],
                s * directions[..., 0] + c * directions[..., 1],
                directions[..., 2],
            ],
            -1,
        )
        camera = enu @ self.rotation.T
        x, y, z = np.moveaxis(camera, -1, 0)
        valid = z > 1e-8
        if self.kind == "perspective":
            xp, yp = x / np.maximum(z, 1e-8), y / np.maximum(z, 1e-8)
            radius2 = xp * xp + yp * yp
        else:
            r = np.hypot(x, y)
            theta = np.arctan2(r, z)
            factor = np.divide(theta, r, out=np.zeros_like(theta), where=r > 1e-8)
            xp, yp, radius2 = x * factor, y * factor, theta * theta
        distortion = 1 + radius2 * (self.k1 + radius2 * self.k2)
        # Do not fold rays beyond the monotonic calibrated radial domain back into the photo.
        valid &= (1 + 3 * self.k1 * radius2 + 5 * self.k2 * radius2**2) > 0
        u = width / 2 + max(width, height) * self.focal * xp * distortion
        v = height / 2 + max(width, height) * self.focal * yp * distortion
        valid &= (
            np.isfinite(u)
            & np.isfinite(v)
            & (u >= 0)
            & (u < width - 1)
            & (v >= 0)
            & (v < height - 1)
        )
        return np.clip(u, 0, width - 1), np.clip(v, 0, height - 1), valid

    def uv_from_dir(self, directions, width, height):
        u, v, _ = self.project(directions, width, height)
        return u, v
