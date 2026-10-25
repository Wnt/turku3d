"""World-local structures built from measured profiles; no shipped artifacts."""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import numpy as np
from turku3d.col.materials import mat_id


@dataclass
class Mesh:
    object_id: str
    material: str
    positions: np.ndarray
    indices: np.ndarray
    uvs: np.ndarray
    visual: bool = True
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        self.positions = np.asarray(self.positions, dtype=np.float64).reshape(-1, 3)
        self.indices = np.asarray(self.indices, dtype=np.uint32).reshape(-1, 3)
        self.uvs = np.asarray(self.uvs, dtype=np.float64).reshape(-1, 2)
        if len(self.positions) != len(self.uvs) or not np.isfinite(self.positions).all():
            raise ValueError(f"{self.object_id}: invalid positions/UVs")
        if not np.isfinite(self.uvs).all() or not len(self.indices):
            raise ValueError(f"{self.object_id}: invalid UVs/empty mesh")
        if self.indices.max() >= len(self.positions):
            raise ValueError(f"{self.object_id}: index out of range")
        if mat_id(self.material) == 0:
            raise ValueError("unknown material cannot ship")


class Faces:
    """Unshared face vertices preserve hard normals and metre UVs."""
    def __init__(self):
        self.positions, self.indices, self.uvs = [], [], []

    def face(self, points, uv=None):
        p = np.asarray(points, dtype=np.float64)
        if uv is None:
            # Facades: u along the face from its first vertex, v above its base.
            horizontal = p[1, [0, 2]] - p[0, [0, 2]]
            if np.linalg.norm(horizontal) < 1e-10:
                horizontal = p[-1, [0, 2]] - p[0, [0, 2]]
            horizontal /= max(np.linalg.norm(horizontal), 1e-10)
            uv = np.column_stack(((p[:, [0, 2]] - p[0, [0, 2]]) @ horizontal, p[:, 1] - p[:, 1].min()))
        offset = len(self.positions)
        self.positions.extend(p)
        self.uvs.extend(uv)
        for k in range(1, len(p) - 1):
            if np.linalg.norm(np.cross(p[k] - p[0], p[k + 1] - p[0])) > 1e-12:
                self.indices.append([offset, offset + k, offset + k + 1])

    def mesh(self, object_id, material, **kwargs):
        return Mesh(object_id, material, self.positions, self.indices, self.uvs, **kwargs)


def planar_uv(p):
    """E/N metre directions, translated by the world origin for float32 UV precision."""
    p = np.asarray(p)
    return p[:, [0, 2]] * [1, -1]


def profile_wall(object_id, xz, bottom, top, *, thickness=0.3, material="wall"):
    """Closed mitered wall shell, including a vertical lower-DTM-to-top face.

    Adjacent segment ends share a miter, so bends have no collider gaps.
    """
    xz, bottom, top = np.asarray(xz, float), np.asarray(bottom, float), np.asarray(top, float)
    if xz.ndim != 2 or xz.shape[1] != 2 or len(xz) < 2:
        raise ValueError("wall needs an (N,2) polyline")
    if bottom.shape != (len(xz),) or top.shape != bottom.shape:
        raise ValueError("wall profile lengths do not match")
    if thickness <= 0 or not np.isfinite([*bottom, *top, thickness]).all() or np.any(top <= bottom):
        raise ValueError("wall requires positive height and thickness")
    d = np.diff(xz, axis=0)
    lengths = np.linalg.norm(d, axis=1)
    if np.any(lengths < 1e-6):
        raise ValueError("wall contains a zero-length segment")
    d /= lengths[:, None]
    normals = np.column_stack((-d[:, 1], d[:, 0]))
    offsets = [normals[0] * thickness / 2]
    for a, b in zip(normals[:-1], normals[1:]):
        denom = 1 + a @ b
        if denom < 0.1:
            raise ValueError("wall reverses direction; split the survey line at the corner")
        offsets.append((a + b) * (thickness / 2) / denom)
    offsets.append(normals[-1] * thickness / 2)
    left, right = xz + offsets, xz - offsets
    def point(side, k, heights):
        return [side[k, 0], heights[k], side[k, 1]]
    f = Faces()
    for k in range(len(xz) - 1):
        a, b, c, d = (point(left, k, bottom), point(left, k + 1, bottom),
                       point(right, k + 1, bottom), point(right, k, bottom))
        A, B, C, D = (point(left, k, top), point(left, k + 1, top),
                       point(right, k + 1, top), point(right, k, top))
        for face in ([a, b, B, A], [d, D, C, c]):
            f.face(face)
        if k == 0:
            f.face([a, A, D, d])
        if k == len(xz) - 2:
            f.face([b, c, C, B])
        f.face([A, B, C, D], planar_uv([A, B, C, D]))
        f.face([a, d, c, b], planar_uv([a, d, c, b]))
    return f.mesh(object_id, material)


def stairs(object_id, bottom, top, width):
    """One incline, matching query-only treads, and two closed cheeks.

    bottom/top are measured landing centres (engine xyz); never alter a steep flight
    to make it pass. Endpoint heights and the footprint are shared by all meshes.
    """
    bottom, top = np.asarray(bottom, float), np.asarray(top, float)
    if bottom.shape != (3,) or top.shape != (3,) or not np.isfinite([*bottom, *top, width]).all():
        raise ValueError("stairs require finite xyz landing centres and width")
    rise = top[1] - bottom[1]
    delta = top[[0, 2]] - bottom[[0, 2]]
    run = np.linalg.norm(delta)
    if rise <= 0 or run <= 0.05 or width <= 0.3:
        raise ValueError("stairs require positive rise/run and width > 0.3 m")
    side = np.array([-delta[1], delta[0]]) / run
    count = max(1, round(rise / 0.16))
    slope = math.degrees(math.atan2(rise, run))
    meta = dict(kind="stairs", bottom=bottom.tolist(), top=top.tolist(), width=float(width),
                rise_m=float(rise), run_m=float(run), risers=count, slope_deg=slope,
                flagged_steep=slope > 40)
    def p(t, h, offset):
        xz = bottom[[0, 2]] + t * delta + side * offset
        return [xz[0], h, xz[1]]
    half = width / 2
    b0, b1 = p(0, bottom[1], -half), p(0, bottom[1], half)
    t0, t1 = p(1, top[1], -half), p(1, top[1], half)
    f = Faces()
    f.face([b0, b1, t1, t0], [[0, 0], [width, 0], [width, run], [0, run]])
    slope_mesh = f.mesh(object_id + ":slope", "stair_slope", visual=False, metadata=meta)
    f = Faces()
    for k in range(count):
        t, u = k / count, (k + 1) / count
        lo, hi = bottom[1] + rise * t, bottom[1] + rise * u
        tread = [p(t, hi, -half), p(t, hi, half), p(u, hi, half), p(u, hi, -half)]
        f.face(tread, planar_uv(tread))
        f.face([p(t, lo, -half), p(t, lo, half), p(t, hi, half), p(t, hi, -half)])
    tread_mesh = f.mesh(object_id + ":treads", "stair_tread_helper", metadata=meta)
    cheeks = []
    for sign in (-1, 1):
        edge = np.array([bottom[[0, 2]], top[[0, 2]]]) + side * sign * (half + 0.075)
        # Flush with the stair slope; the 2 cm foundation avoids a zero-height prism.
        cheeks.append(profile_wall(object_id + f":cheek:{sign}", edge,
                                    [bottom[1] - 0.02] * 2, [bottom[1], top[1]], thickness=0.15))
    return [slope_mesh, tread_mesh, *cheeks], meta


def ramp(object_id, positions, indices):
    """Preserve the ground-layer triangles and their exact contact vertices."""
    p = np.asarray(positions, float)
    i = np.asarray(indices, np.uint32).copy()
    normals = np.cross(p[i[:, 1]] - p[i[:, 0]], p[i[:, 2]] - p[i[:, 0]])
    if np.any(np.abs(normals[:, 1]) < 1e-10):
        raise ValueError("ramp has degenerate or vertical triangles")
    flipped = normals[:, 1] < 0
    i[flipped] = i[flipped][:, [0, 2, 1]]
    angles = np.degrees(np.arctan2(np.linalg.norm(normals[:, [0, 2]], axis=1), np.abs(normals[:, 1])))
    return Mesh(object_id, "ramp", p, i, planar_uv(p),
                metadata={"kind": "ramp", "slope_deg": float(angles.max()), "flagged_steep": bool(angles.max() > 40)})


def quay(object_id, xz, lower, upper, railing_height=1.2):
    if not np.isfinite(railing_height) or railing_height < 0.35:
        raise ValueError("quay railing must block the character")
    wall = profile_wall(object_id + ":stone", xz, lower, upper, material="quay")
    rail = profile_wall(object_id + ":railing", xz, upper, np.asarray(upper) + railing_height,
                        thickness=0.08, material="railing")
    return [wall, rail]
