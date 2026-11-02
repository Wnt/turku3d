"""Stable architectural faces reconstructed from connected survey-wall fragments."""

import hashlib
from dataclasses import dataclass

import numpy as np
import shapely
from shapely.affinity import translate


@dataclass
class Face:
    polygon: object
    start: np.ndarray
    direction: np.ndarray
    identity: str

    @property
    def width(self):
        return self.polygon.bounds[2]

    @property
    def low(self):
        return self.polygon.bounds[1]

    @property
    def high(self):
        return self.polygon.bounds[3]

    @property
    def normal(self):
        return np.array([self.direction[1], -self.direction[0]])

    def rectangle(self):
        xy = np.array(
            [[0, self.low], [self.width, self.low], [self.width, self.high], [0, self.high]]
        )
        p = np.empty((4, 3))
        p[:, [0, 2]] = self.start + xy[:, :1] * self.direction
        p[:, 1] = xy[:, 1]
        return p


def architectural_faces(walls, tolerance=0.02):
    """Join only connected, same-facing planes; preserve corners and setbacks.

    Coordinates are physical metres, independent of triangulation and roof-part
    subdivision. Two-centimetre plane tolerance absorbs survey rounding only.
    """
    groups = []
    for q in sorted(np.asarray(walls).reshape(-1, 4, 3), key=lambda q: tuple(q.flatten())):
        direction = q[1, [0, 2]] - q[0, [0, 2]]
        width = np.linalg.norm(direction)
        if width < 0.001:
            continue
        direction /= width
        normal = np.array([direction[1], -direction[0]])
        offset = float(q[0, [0, 2]] @ normal)
        group = next(
            (
                g
                for g in groups
                if g["direction"] @ direction > 0.999
                and np.max(np.abs(q[:, [0, 2]] @ g["normal"] - g["offset"])) <= tolerance
            ),
            None,
        )
        if group is None:
            group = dict(direction=direction, normal=normal, offset=offset, polygons=[])
            groups.append(group)
        x = q[:, [0, 2]] @ group["direction"]
        polygon = shapely.make_valid(shapely.Polygon(np.c_[x, q[:, 1]]))
        group["polygons"].append(polygon)
    faces = []
    for group in groups:
        combined = shapely.union_all(group["polygons"], grid_size=tolerance / 2)
        for polygon in shapely.get_parts(combined):
            if polygon.geom_type != "Polygon" or polygon.area < 0.001:
                continue
            left, low, right, high = polygon.bounds
            start = group["direction"] * left + group["normal"] * group["offset"]
            key = np.round([*start, *group["direction"], right - left, low, high], 3)
            identity = hashlib.sha256(key.tobytes()).hexdigest()[:16]
            faces.append(Face(translate(polygon, xoff=-left), start, group["direction"], identity))
    return sorted(faces, key=lambda face: face.identity)
