"""Parked-car manoeuvre envelope against solid colliders; tools/validator shares manoeuvre.json."""

import json
import math
from pathlib import Path

import numpy as np
from shapely import STRtree, box

from turku3d.col.materials import mat_id
from turku3d.layers import PRIMITIVE_DTYPE

ENVELOPE_PATH = Path(__file__).with_name("manoeuvre.json")
ENVELOPE = json.loads(ENVELOPE_PATH.read_text())
SIDE, AXIAL, STEP = ENVELOPE["side_m"], ENVELOPE["axial_m"], ENVELOPE["step_m"]
CLEAR_LIMIT = ENVELOPE["clear_limit_m"]
OBSTACLE_MATERIALS = np.array(sorted(mat_id(n) for n in ENVELOPE["obstacle_materials"]))


def _clip(points, lo, hi):
    """Clip a convex 3D polygon to the horizontal slab lo <= y <= hi."""
    for sign, level in ((1, lo), (-1, hi)):
        out = []
        for k, current in enumerate(points):
            previous = points[k - 1]
            a, b = sign * (previous[1] - level), sign * (current[1] - level)
            if (a >= 0) != (b >= 0):
                out.append(previous + (current - previous) * (a / (a - b)))
            if b >= 0:
                out.append(current)
        points = out
        if not points:
            break
    return points


def _convex_hits(uv, half_width, half_length):
    """Separating-axis test of a convex (possibly degenerate) polygon against a centred rectangle."""
    axes = [(1.0, 0.0), (0.0, 1.0)]
    for k in range(len(uv)):
        du, dv = uv[k] - uv[k - 1]
        if du * du + dv * dv > 1e-18:
            axes.append((-dv, du))
    for au, av in axes:
        projection = uv[:, 0] * au + uv[:, 1] * av
        radius = half_width * abs(au) + half_length * abs(av)
        if projection.min() > radius or projection.max() < -radius:
            return False
    return True


class Obstacles:
    """Walls, fences, buildings, poles, trunks, bollards and furniture a parked car must clear."""

    def __init__(self, triangles=(), materials=(), primitives=None):
        triangles = np.asarray(triangles, dtype=float).reshape(-1, 3, 3)
        materials = np.asarray(materials, dtype=int).reshape(-1)
        keep = np.isin(materials, OBSTACLE_MATERIALS)
        self.triangles, self.triangle_materials = triangles[keep], materials[keep]
        primitives = np.empty(0, PRIMITIVE_DTYPE) if primitives is None else primitives
        self.primitives = primitives[np.isin(primitives["material"], OBSTACLE_MATERIALS)]
        p = self.primitives
        is_box = p["kind"] == "box"
        reach = np.where(
            is_box, np.hypot(p["half_extents"][:, 0], p["half_extents"][:, 2]), p["radius"]
        )
        half = np.where(
            is_box,
            p["half_extents"][:, 1],
            p["height"] / 2 + np.where(p["kind"] == "capsule", p["radius"], 0),
        )
        t = self.triangles
        lower = np.concatenate([t.min(axis=1), p["center"] - np.stack([reach, half, reach], 1)])
        upper = np.concatenate([t.max(axis=1), p["center"] + np.stack([reach, half, reach], 1)])
        self.ranges = np.stack([lower[:, 1], upper[:, 1]], axis=1)
        self.tree = STRtree(box(lower[:, 0], lower[:, 2], upper[:, 0], upper[:, 2]))

    def extended(self, primitives):
        """These obstacles plus extra solid primitive arrays, such as the other parked cars."""
        return Obstacles(
            self.triangles, self.triangle_materials, np.concatenate([self.primitives, *primitives])
        )

    def hit(self, x, z, yaw, half_width, near, far, lo, hi):
        """Material of the first obstacle inside the car-frame rectangle, or None.

        The rectangle spans |lateral| <= half_width and near <= axial <= far, where the car's
        +axial direction is its model +Z (sin(yaw), cos(yaw)); it is limited to lo <= y <= hi.
        """
        s, c = math.sin(yaw), math.cos(yaw)
        centre = (near + far) / 2
        half_length = (far - near) / 2
        cx, cz = x + s * centre, z + c * centre
        corners = [
            (cx + c * u + s * v, cz - s * u + c * v)
            for u, v in (
                (-half_width, -half_length),
                (half_width, -half_length),
                (half_width, half_length),
                (-half_width, half_length),
            )
        ]
        xs, zs = zip(*corners, strict=True)
        triangles = len(self.triangles)
        for i in sorted(self.tree.query(box(min(xs), min(zs), max(xs), max(zs)))):
            if self.ranges[i, 0] > hi or self.ranges[i, 1] < lo:
                continue
            if i < triangles:
                points = _clip(list(self.triangles[i]), lo, hi)
                if not points:
                    continue
                xz = np.asarray(points)[:, [0, 2]]
                material = self.triangle_materials[i]
            else:
                p = self.primitives[i - triangles]
                material = p["material"]
                if p["kind"] != "box":
                    du, dz = p["center"][0] - cx, p["center"][2] - cz
                    u, v = c * du - s * dz, s * du + c * dz
                    gap_u = max(abs(u) - half_width, 0)
                    gap_v = max(abs(v) - half_length, 0)
                    if gap_u * gap_u + gap_v * gap_v <= p["radius"] ** 2:
                        return int(material)
                    continue
                hx, _, hz = p["half_extents"]
                ps, pc = math.sin(p["yaw"]), math.cos(p["yaw"])
                xz = np.array(
                    [
                        (p["center"][0] + pc * a + ps * b, p["center"][2] - ps * a + pc * b)
                        for a, b in ((-hx, -hz), (hx, -hz), (hx, hz), (-hx, hz))
                    ]
                )
            du, dz = xz[:, 0] - cx, xz[:, 1] - cz
            uv = np.stack([c * du - s * dz, s * du + c * dz], axis=1)
            if _convex_hits(uv, half_width, half_length):
                return int(material)
        return None

    def boxed_in(self, x, z, yaw, width, length, base, height):
        """Obstacle material that boxes the car in, or None.

        The car needs its footprint plus SIDE metres on each side clear, and an exit lane of the
        same width that extends AXIAL metres either ahead of or behind it.
        """
        half_width, end = width / 2 + SIDE, length / 2
        lo, hi = base + STEP, base + height
        body = self.hit(x, z, yaw, half_width, -end, end, lo, hi)
        if body is not None:
            return body
        front = self.hit(x, z, yaw, half_width, end, end + AXIAL, lo, hi)
        if front is None:
            return None
        return self.hit(x, z, yaw, half_width, -end - AXIAL, -end, lo, hi)

    def free_length(self, x, z, yaw, width, length, base, height, direction):
        """Clear straight driving distance ahead (+1) or behind (-1) for the car body, to 0.1 m."""
        end = direction * length / 2

        def blocked(distance):
            a, b = sorted((end, end + direction * distance))
            return self.hit(x, z, yaw, width / 2, a, b, base + STEP, base + height) is not None

        if not blocked(CLEAR_LIMIT):
            return CLEAR_LIMIT
        clear, stop = 0.0, CLEAR_LIMIT
        while stop - clear > 0.1:
            middle = (clear + stop) / 2
            clear, stop = (clear, middle) if blocked(middle) else (middle, stop)
        return round(clear, 1)


def car_box(x, y, z, yaw, width, height, length, material="street_furniture"):
    p = np.zeros(1, PRIMITIVE_DTYPE)
    p["kind"] = "box"
    p["center"] = [x, y + height / 2, z]
    p["half_extents"] = [width / 2, height / 2, length / 2]
    p["yaw"] = yaw
    p["material"] = mat_id(material)
    return p
