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


def entrance_face(faces, base, entrance):
    """Select one usable principal face from the building's declared orientation."""
    if not entrance:
        return None
    if entrance.get("face_id"):
        return next((face.identity for face in faces if face.identity == entrance["face_id"]), None)
    direction = {"west": [-1, 0], "east": [1, 0], "north": [0, -1], "south": [0, 1]}.get(
        entrance.get("facade", "").lower()
    )
    if direction is None:
        return None
    from .layout import arch_polygon

    candidates = []
    for face in faces:
        if face.normal @ direction < np.cos(np.deg2rad(20)):
            continue
        portal = arch_polygon(
            face.width / 2,
            base + entrance.get("sill_m", 0.1),
            entrance["width_m"],
            entrance["height_m"],
            True,
        )
        if face.polygon.covers(portal.buffer(0.16)):
            candidates.append(face)
    return (
        max(candidates, key=lambda face: (face.polygon.area, face.identity)).identity
        if candidates
        else None
    )
