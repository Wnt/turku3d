"""Facade planes and roof planes of a description's LoD2 shell.

A facade is every coplanar, connected wall polygon of the shell seen as one wall: a frame (origin at its left end
as seen from outside, u along the wall, v up) and its outline in (u, v). Survey frame throughout: E, N, H.
"""

from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely.geometry import Polygon
from shapely.ops import unary_union

UP = np.array([0.0, 0.0, 1.0])
ANGLE_TOLERANCE = np.radians(3.0)
OFFSET_TOLERANCE_M = 0.08
GAP_M = 0.06


def newell(ring: np.ndarray) -> np.ndarray:
    """Area-weighted normal of a ring (counter-clockwise seen from where the normal points)."""
    a = ring
    b = np.roll(ring, -1, axis=0)
    return (
        np.array(
            [
                np.sum((a[:, 1] - b[:, 1]) * (a[:, 2] + b[:, 2])),
                np.sum((a[:, 2] - b[:, 2]) * (a[:, 0] + b[:, 0])),
                np.sum((a[:, 0] - b[:, 0]) * (a[:, 1] + b[:, 1])),
            ]
        )
        / 2.0
    )


@dataclass
class Facade:
    index: int
    origin: np.ndarray  # E, N, H of (u, v) = (0, 0): the left end at the base height
    u_dir: np.ndarray  # unit, horizontal, left to right as seen from outside
    normal: np.ndarray  # unit, horizontal, pointing out
    width: float
    height: float
    outline: object  # shapely Polygon or MultiPolygon in (u, v), v from the base height
    ground: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 2))
    )  # (u, v) of the terrain line along it
    grounded: bool = True  # False for a wall that starts above a roof: no plinth, no doors

    def point(self, u, v, d=0.0) -> np.ndarray:
        """Survey coordinates of wall coordinates; d is the distance out of the wall plane."""
        u, v, d = np.broadcast_arrays(
            np.asarray(u, float), np.asarray(v, float), np.asarray(d, float)
        )
        return (
            self.origin + u[..., None] * self.u_dir + v[..., None] * UP + d[..., None] * self.normal
        )

    @property
    def p0(self) -> np.ndarray:
        return self.origin[:2]

    @property
    def p1(self) -> np.ndarray:
        return self.origin[:2] + self.width * self.u_dir[:2]

    def ground_at(self, u: float) -> float:
        """Height of the ground above the facade base at u (0 when the shell has no terrain line here)."""
        if len(self.ground) == 0:
            return 0.0
        return float(np.interp(u, self.ground[:, 0], self.ground[:, 1]))


@dataclass
class RoofPlane:
    ring: np.ndarray  # (n, 3) outer ring, counter-clockwise seen from above
    holes: list
    normal: np.ndarray  # unit, pointing up and out


@dataclass
class Shell:
    facades: list
    roofs: list
    footprint: object  # shapely polygon in E, N
    base_h: float
    top_h: float


def _surfaces(shell: dict, kind: str):
    for surface in shell.get("surfaces", []):
        if surface["kind"] == kind:
            rings = [np.asarray(r, float) for r in surface["rings"]]
            if len(rings[0]) >= 3:
                yield rings


def read(shell: dict) -> Shell:
    walls = list(_surfaces(shell, "wall"))
    roofs = list(_surfaces(shell, "roof"))
    grounds = [Polygon(r[0][:, :2]) for r in _surfaces(shell, "ground")]
    footprint = unary_union([g.buffer(0) for g in grounds]) if grounds else None
    if footprint is None or footprint.is_empty:
        footprint = (
            unary_union([Polygon(r[0][:, :2]).buffer(0) for r in roofs]) if roofs else Polygon()
        )

    normals = []
    for rings in walls:
        n = newell(rings[0])
        n[2] = 0.0
        length = np.linalg.norm(n)
        normals.append(n / length if length > 1e-9 else None)
    # LoD2 rings should face out; when most do not (reversed winding in a record), flip the lot. Only a wall on
    # the footprint's edge can tell: a wall of a set-back upper part has the footprint on both sides.
    votes = 0
    for rings, n in zip(walls, normals, strict=True):
        if n is None:
            continue
        middle = rings[0][:, :2].mean(axis=0)
        front = footprint.contains(shapely.Point(middle + n[:2] * 0.4))
        back = footprint.contains(shapely.Point(middle - n[:2] * 0.4))
        if front != back:
            votes += -1 if front else 1
    if votes < 0:
        normals = [None if n is None else -n for n in normals]

    items = []
    for rings, n in zip(walls, normals, strict=True):
        if n is None:
            continue
        u_dir = np.array([-n[1], n[0], 0.0])
        offset = float(rings[0][:, :2].mean(axis=0) @ n[:2])
        u = rings[0] @ u_dir
        items.append(
            {"rings": rings, "n": n, "u_dir": u_dir, "offset": offset, "u0": u.min(), "u1": u.max()}
        )

    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(items):
        for j in range(i):
            b = items[j]
            if float(a["n"] @ b["n"]) < np.cos(ANGLE_TOLERANCE):
                continue
            # Offsets are compared along one normal so that nearly parallel planes far apart never merge.
            if (
                abs(float(b["rings"][0][:, :2].mean(axis=0) @ a["n"][:2]) - a["offset"])
                > OFFSET_TOLERANCE_M
            ):
                continue
            ub = b["rings"][0] @ a["u_dir"]
            if ub.min() > a["u1"] + GAP_M or ub.max() < a["u0"] - GAP_M:
                continue
            parent[find(i)] = find(j)

    groups = {}
    for i in range(len(items)):
        groups.setdefault(find(i), []).append(items[i])

    terrain = [np.asarray(line, float) for line in shell.get("terrain", []) if len(line) >= 2]
    facades = []
    for members in groups.values():
        weights = [abs(np.linalg.norm(newell(m["rings"][0]))) for m in members]
        n = np.average([m["n"] for m in members], axis=0, weights=weights)
        n /= np.linalg.norm(n)
        u_dir = np.array([-n[1], n[0], 0.0])
        points = np.vstack([m["rings"][0] for m in members])
        u_all = points @ u_dir
        u_min = float(u_all.min())
        base = float(points[:, 2].min())
        offset = float(np.average([m["offset"] for m in members], weights=weights))
        origin = n * offset + u_dir * u_min + UP * base
        polygons = []
        for m in members:
            outer = np.column_stack([m["rings"][0] @ u_dir - u_min, m["rings"][0][:, 2] - base])
            holes = [np.column_stack([h @ u_dir - u_min, h[:, 2] - base]) for h in m["rings"][1:]]
            polygon = Polygon(outer, holes).buffer(0)
            if not polygon.is_empty and polygon.area > 1e-4:
                polygons.append(polygon)
        if not polygons:
            continue
        outline = unary_union(polygons).buffer(GAP_M / 2).buffer(-GAP_M / 2)
        if outline.is_empty or outline.area < 0.05:
            continue
        _, _, width, height = outline.bounds
        facade = Facade(len(facades), origin, u_dir, n, float(width), float(height), outline)
        facade.ground = _ground_line(facade, terrain)
        facades.append(facade)
    lowest = min((float(f.origin[2]) for f in facades), default=0.0)
    for facade in facades:
        facade.grounded = len(facade.ground) > 0 or float(facade.origin[2]) - lowest < 2.0
    facades.sort(key=lambda f: -f.outline.area)
    for i, facade in enumerate(facades):
        facade.index = i

    planes = []
    for rings in roofs:
        n = newell(rings[0])
        length = np.linalg.norm(n)
        if length < 1e-6:
            continue
        n = n / length
        ring, holes = rings[0], rings[1:]
        if n[2] < 0:
            n, ring, holes = -n, ring[::-1], [h[::-1] for h in holes]
        planes.append(RoofPlane(ring, holes, n))

    heights = np.concatenate([r[0][:, 2] for r in walls + roofs]) if walls or roofs else np.zeros(1)
    return Shell(facades, planes, footprint, float(heights.min()), float(heights.max()))


def _ground_line(facade: Facade, terrain: list) -> np.ndarray:
    """The terrain line's vertices that lie on the facade, as (u, v) sorted by u."""
    out = []
    for line in terrain:
        relative = line - facade.origin
        distance = relative @ facade.normal
        u = relative @ facade.u_dir
        on = (np.abs(distance) < 0.35) & (u > -0.3) & (u < facade.width + 0.3)
        out.extend(zip(u[on], relative[on, 2], strict=True))
    if len(out) < 1:
        return np.zeros((0, 2))
    out = np.array(sorted(out))
    return out


def match(facades: list, p0, p1, tolerance_m: float = 1.5, tolerance_deg: float = 15.0):
    """The facade a described wall frame lies on: same direction, close to its plane, largest overlap along it."""
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    direction = p1 - p0
    length = np.linalg.norm(direction)
    if length < 1e-6:
        return None
    direction /= length
    best, best_overlap = None, 0.0
    for facade in facades:
        if float(direction @ facade.u_dir[:2]) < np.cos(np.radians(tolerance_deg)):
            continue
        middle = (p0 + p1) / 2 - facade.origin[:2]
        if abs(float(middle @ facade.normal[:2])) > tolerance_m:
            continue
        a = float((p0 - facade.origin[:2]) @ facade.u_dir[:2])
        b = float((p1 - facade.origin[:2]) @ facade.u_dir[:2])
        overlap = min(b, facade.width) - max(a, 0.0)
        if overlap > best_overlap:
            best, best_overlap = facade, overlap
    return best
