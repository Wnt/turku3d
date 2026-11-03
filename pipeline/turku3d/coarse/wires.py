"""Overhead wires from the survey lidar: lighting span wires with their hung lamps, utility lines.

A class rule over the city ``pistepilvi`` returns, with no per-street data:

1. **Candidates** are returns of the classes a wire is filed under (unclassified, vegetation and
   strip overlap, ``CLASSES``) between ``MIN_HEIGHT`` and ``MAX_HEIGHT`` above the ground (class 2
   on a ``GROUND_CELL`` grid). Crowns and walls fill space: a return whose ``VOXEL`` neighbourhood
   is crowded (``CROWDED``) is not a wire.
2. **Linear** returns have a thin, straight neighbourhood (``LINEAR_RADIUS``): principal component
   share ``LINEARITY`` and a cross-section under ``THICKNESS``.
3. **Pieces** grow over neighbours along the same direction (``ALIGN``, ``ALIGN_OFFSET``); each
   piece is a straight line in plan and a parabola (a catenary at small sag) in its vertical
   plane, kept when ``MIN_LENGTH`` long with RMS residuals under ``RESIDUAL``.
4. **Wires** join collinear pieces across gaps (``JOIN_GAP``) when one profile fits them all.
5. **Ends** reach to what the wire hangs from: a facade (building returns ahead, ``REACH``), a pole
   (a column of returns below the end) or another wire (a junction).
6. **Lamps** hang where span wires meet in mid air (two or more wire ends at one point over the
   street, ``KNOT``), or as a compact blob of returns just below a wire (``LAMP``).
7. **Kind**: ``span`` wires cross the street between facades or carry lamps; ``utility`` wires run
   from pole to pole; ``power`` lines hang higher than ``POWER_HEIGHT`` over the ground.

Each tile lists the wires whose middle it owns (half-open bounds) and the lamps standing in it,
read from returns ``SEARCH`` around the tile, so neighbour tiles agree whatever the load order.
Coordinates are engine metres (x east, y up, z south). Output is deterministic: inputs are sorted,
and there is no random sampling.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

FORMAT_VERSION = 1
CLASSES = (1, 3, 4, 5, 12)  # unclassified, low/medium/high vegetation, overlap
BUILDING = 6
GROUND = 2
GROUND_CELL = 2.0  # m
MIN_HEIGHT = 3.0  # m above ground: below it are vehicles, signs and people
MAX_HEIGHT = 45.0
VOXEL = 0.5  # m
CROWDED = 6  # occupied voxels in the 3x3x3 neighbourhood of a wire return at most
LINEAR_RADIUS = 1.0  # m
LINEARITY = 0.85  # (l1 - l2) / l1 of the neighbourhood covariance
THICKNESS = 0.08  # m: sqrt(l2) of a wire neighbourhood at most
ALIGN = math.cos(math.radians(12.0))  # neighbour directions of one piece
ALIGN_OFFSET = 0.2  # m off a neighbour's direction
LINK = 1.5  # m between consecutive returns of one piece
MIN_POINTS = 6
MIN_LENGTH = 3.0  # m of a piece
MIN_WIRE = 5.0  # m of a drawn wire (after ends are reached)
RESIDUAL = 0.12  # m RMS, plan and profile
JOIN_GAP = 14.0  # m between pieces of one wire
JOIN_ANGLE = math.cos(math.radians(4.0))
JOIN_OFFSET = 0.35  # m
REACH = 4.0  # m from the last return to the pole a wire hangs from
FAR_REACH = 10.0  # m from the last return to the facade ahead
FACADE_RADIUS = 0.6  # m around the extended wire where building returns are its facade
POLE_RADIUS = 0.5  # m: a pole column under a wire end
POLE_SHARE = 0.6  # of the height bins below the end that a pole fills
KNOT = 1.2  # m between knots that are one lamp
KNOT_GAP = 2.5  # m between facing wire ends that meet at the lamp hung between them
LAMP = {"below": 1.6, "size": 1.4, "points": 8, "clear": 2.5}
POWER_HEIGHT = 16.0  # m above ground
SEARCH = 60.0  # m around the tile
SPAN_MAX = 45.0  # m: a lighting span wire is shorter
KINDS = ("span", "utility", "power")
CLUTTER = {"radius": 1.0, "count": 3, "share": 0.5}  # other returns around a branch, not a wire
CROWN_END = 2.0  # m from a free wire end to the crown it disappears into
ROOF = 0.3  # share of a wire over building returns: roof edges, railings and antennas


@dataclass
class Piece:
    origin: np.ndarray  # plan point (x, z)
    direction: np.ndarray  # unit plan direction
    s0: float
    s1: float
    profile: np.ndarray  # parabola c0 + c1 s + c2 s^2 of the height
    points: np.ndarray  # member returns (n, 3)

    def height(self, s):
        c0, c1, c2 = self.profile
        return c0 + c1 * s + c2 * s * s

    def at(self, s) -> np.ndarray:
        p = self.origin + self.direction * s
        return np.array([p[0], self.height(s), p[1]])


@dataclass
class Wire:
    a: np.ndarray  # end points (x, y, z)
    b: np.ndarray
    sag: float  # m below the chord at mid span
    kind: str = "utility"
    ends: tuple = ("free", "free")  # facade | pole | knot | free
    lamps: list = field(default_factory=list)

    @property
    def length(self) -> float:
        return float(np.hypot(self.b[0] - self.a[0], self.b[2] - self.a[2]))

    def point(self, t: float) -> np.ndarray:
        p = self.a + (self.b - self.a) * t
        p[1] -= self.sag * 4 * t * (1 - t)
        return p


# -- ground ---------------------------------------------------------------------------------------
class Ground:
    """Lowest class-2 return per cell, holes filled from the nearest surveyed cell."""

    def __init__(self, xyz, bounds, cell=GROUND_CELL):
        from scipy import ndimage

        x0, z0, x1, z1 = bounds
        self.x0, self.z0, self.cell = x0, z0, cell
        nx, nz = max(1, math.ceil((x1 - x0) / cell)), max(1, math.ceil((z1 - z0) / cell))
        grid = np.full((nz, nx), np.inf)
        if len(xyz):
            i = np.clip(((xyz[:, 0] - x0) / cell).astype(np.int64), 0, nx - 1)
            k = np.clip(((xyz[:, 2] - z0) / cell).astype(np.int64), 0, nz - 1)
            np.minimum.at(grid, (k, i), xyz[:, 1])
        missing = ~np.isfinite(grid)
        if missing.all():
            grid[:] = 0.0
        elif missing.any():
            index = ndimage.distance_transform_edt(missing, return_distances=False, return_indices=True)
            grid = grid[tuple(index)]
        self.grid = grid

    def __call__(self, x, z):
        nz, nx = self.grid.shape
        i = np.clip(((np.asarray(x) - self.x0) / self.cell).astype(np.int64), 0, nx - 1)
        k = np.clip(((np.asarray(z) - self.z0) / self.cell).astype(np.int64), 0, nz - 1)
        return self.grid[k, i]


# -- candidates -------------------------------------------------------------------------------------
def _voxel_keys(v):
    return (v[:, 0] + (1 << 20)) * (1 << 42) + (v[:, 1] + (1 << 20)) * (1 << 21) + (v[:, 2] + (1 << 20))


def sparse(points, voxel=VOXEL, crowded=CROWDED):
    """Mask of returns in uncrowded space: at most ``crowded`` occupied voxels around theirs."""
    if not len(points):
        return np.zeros(0, bool)
    v = np.floor(points / voxel).astype(np.int64)
    keys = _voxel_keys(v)
    unique, inverse = np.unique(keys, return_inverse=True)
    cells = v[np.unique(keys, return_index=True)[1]]
    count = np.zeros(len(unique), np.int64)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                probe = _voxel_keys(cells + np.array([dx, dy, dz]))
                i = np.clip(np.searchsorted(unique, probe), 0, len(unique) - 1)
                count += unique[i] == probe
    return count[inverse] <= crowded


def linear(points, radius=LINEAR_RADIUS):
    """(mask, unit directions) of returns whose neighbourhood is a thin line."""
    from scipy.spatial import cKDTree

    n = len(points)
    directions = np.zeros((n, 3))
    mask = np.zeros(n, bool)
    if n < MIN_POINTS:
        return mask, directions
    tree = cKDTree(points)
    for i, near in enumerate(tree.query_ball_point(points, r=radius)):
        if len(near) < 4:
            continue
        q = points[near] - points[near].mean(0)
        values, vectors = np.linalg.eigh(q.T @ q / len(near))
        if values[2] <= 1e-9 or values[1] > THICKNESS**2:
            continue
        if (values[2] - values[1]) / values[2] >= LINEARITY:
            mask[i] = True
            d = vectors[:, 2]
            directions[i] = d if (d[0], d[2]) >= (0, 0) else -d
    return mask, directions


# -- pieces -----------------------------------------------------------------------------------------
def fit_profile(s, y):
    """Least-squares parabola of height over distance; a sag never hogs (c2 >= 0)."""
    if len(s) < 3 or np.ptp(s) < 1e-6:
        return np.array([float(np.mean(y)), 0.0, 0.0])
    m = float(np.mean(s))
    t = s - m
    c2, c1, c0 = np.polyfit(t, y, 2)
    if c2 < 0:
        c1, c0 = np.polyfit(t, y, 1)
        c2 = 0.0
    # Back to the un-centred parameter.
    return np.array([c0 - c1 * m + c2 * m * m, c1 - 2 * c2 * m, c2])


def fit_piece(points):
    """Plan line and height profile of a set of returns, or None when it is not one wire."""
    xz = points[:, [0, 2]]
    centre = xz.mean(0)
    q = xz - centre
    values, vectors = np.linalg.eigh(q.T @ q)
    direction = vectors[:, 1]
    if (direction[0], direction[1]) < (0, 0):
        direction = -direction
    s = q @ direction
    across = q @ np.array([-direction[1], direction[0]])
    profile = fit_profile(s, points[:, 1])
    c0, c1, c2 = profile
    vertical = points[:, 1] - (c0 + c1 * s + c2 * s * s)
    if np.sqrt(np.mean(across**2)) > RESIDUAL or np.sqrt(np.mean(vertical**2)) > RESIDUAL:
        return None
    return Piece(centre, direction, float(s.min()), float(s.max()), profile, points)


def split(points) -> list[Piece]:
    """Fit a run of returns; a run that bends (a lamp's weight, a junction) is cut where two
    separate fits leave the least residual, and each side is fitted again."""
    piece = fit_piece(points)
    if piece is not None:
        return [piece] if piece.s1 - piece.s0 >= MIN_LENGTH else []
    xz = points[:, [0, 2]]
    q = xz - xz.mean(0)
    direction = np.linalg.eigh(q.T @ q)[1][:, 1]
    s = q @ direction
    order = np.argsort(s, kind="stable")
    points, s = points[order], s[order]
    if s[-1] - s[0] < 2 * MIN_LENGTH or len(points) < 2 * MIN_POINTS:
        return []
    best = None
    for cut in np.linspace(s[0] + MIN_LENGTH, s[-1] - MIN_LENGTH, 16):
        k = int(np.searchsorted(s, cut))
        if k < MIN_POINTS or len(points) - k < MIN_POINTS:
            continue
        cost = _cost(points[:k]) + _cost(points[k:])
        if best is None or cost < best[0]:
            best = (cost, k)
    if best is None:
        return []
    return [*split(points[: best[1]]), *split(points[best[1]:])]


def _cost(points) -> float:
    xz = points[:, [0, 2]]
    q = xz - xz.mean(0)
    values, vectors = np.linalg.eigh(q.T @ q)
    s = q @ vectors[:, 1]
    profile = fit_profile(s, points[:, 1])
    vertical = points[:, 1] - (profile[0] + profile[1] * s + profile[2] * s * s)
    return float(values[0] + vertical @ vertical)


def pieces(points, directions, clutter=None):
    """Straight runs of linear returns: neighbours along one direction, then fits per run.

    ``clutter`` (a KD-tree of the other candidate returns) drops runs inside crowns: leafless
    branches are short straight runs too, but always among other returns."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree

    if len(points) < MIN_POINTS:
        return []
    pairs = cKDTree(points).query_pairs(r=LINK, output_type="ndarray")
    if not len(pairs):
        return []
    i, j = pairs[:, 0], pairs[:, 1]
    aligned = np.abs(np.einsum("ij,ij->i", directions[i], directions[j])) >= ALIGN
    delta = points[j] - points[i]
    off = np.linalg.norm(np.cross(delta, directions[i]), axis=1)
    keep = aligned & (off <= ALIGN_OFFSET)
    graph = coo_matrix((np.ones(keep.sum()), (i[keep], j[keep])), shape=(len(points), len(points)))
    _, labels = connected_components(graph, directed=False)
    out = []
    for label in np.unique(labels):
        members = points[labels == label]
        if len(members) < MIN_POINTS:
            continue
        for piece in split(members):
            if clutter is not None and _cluttered(piece, clutter):
                continue
            out.append(piece)
    return out


def _cluttered(piece: Piece, clutter) -> bool:
    """Most of the piece's returns have other candidate returns around them."""
    counts = np.array([len(n) for n in clutter.query_ball_point(piece.points, r=CLUTTER["radius"])])
    return float(np.mean(counts > CLUTTER["count"])) > CLUTTER["share"]


def _joinable(a: Piece, b: Piece) -> bool:
    if abs(float(a.direction @ b.direction)) < JOIN_ANGLE:
        return False
    normal = np.array([-a.direction[1], a.direction[0]])
    ends_b = [b.origin + b.direction * b.s0, b.origin + b.direction * b.s1]
    if max(abs(float((e - a.origin) @ normal)) for e in ends_b) > JOIN_OFFSET:
        return False
    s = sorted(float((e - a.origin) @ a.direction) for e in ends_b)
    gap = max(s[0] - a.s1, a.s0 - s[1])
    return gap <= JOIN_GAP


def join(parts: list[Piece]) -> list[Piece]:
    """Merge collinear pieces whose returns one profile fits (greedy, longest first)."""
    parts = sorted(parts, key=lambda p: (-(p.s1 - p.s0), *np.round(p.origin, 3)))
    merged = True
    while merged:
        merged = False
        for x in range(len(parts)):
            for y in range(x + 1, len(parts)):
                if not _joinable(parts[x], parts[y]):
                    continue
                piece = fit_piece(np.concatenate([parts[x].points, parts[y].points]))
                if piece is None:
                    continue
                parts[x] = piece
                del parts[y]
                merged = True
                break
            if merged:
                break
    return parts


# -- ends -------------------------------------------------------------------------------------------
class Index:
    """Plan grid of returns for radius queries."""

    def __init__(self, points):
        from scipy.spatial import cKDTree

        self.points = points
        self.tree = cKDTree(points[:, [0, 2]]) if len(points) else None

    def near(self, x, z, r):
        if self.tree is None:
            return self.points[:0]
        return self.points[self.tree.query_ball_point([x, z], r=r)]


def reach_end(piece: Piece, at_start: bool, buildings: Index, columns: Index, ground) -> tuple[np.ndarray, str]:
    """The point a piece end hangs from: a pole or facade within REACH, a facade straight ahead
    within FAR_REACH (glancing returns near a wall are often missing), else its last return."""
    sign = -1.0 if at_start else 1.0
    s_end = piece.s0 if at_start else piece.s1
    step = 0.25
    ahead = [piece.at(s_end + sign * k * step) for k in range(int(FAR_REACH / step) + 1)]
    near = ahead[: int(REACH / step) + 1]
    for p in near:
        if _facade(buildings.near(p[0], p[2], FACADE_RADIUS), p[1]):
            return p, "facade"
    for p in near:
        if _pole(columns.near(p[0], p[2], POLE_RADIUS), p[1], float(ground(p[0], p[2]))):
            return p, "pole"
    for p in ahead[len(near):]:
        if _facade(buildings.near(p[0], p[2], FACADE_RADIUS), p[1]):
            return p, "facade"
    return piece.at(s_end), "free"


def _facade(hits, y) -> bool:
    """Building returns spanning the wire's height, or a wall top up to 1.5 m below it (eaves)."""
    return bool(len(hits)) and float(hits[:, 1].min()) <= y + 1.0 and float(hits[:, 1].max()) >= y - 1.5


def _pole(column, top, base) -> bool:
    """A column of returns filling most 0.5 m bins from 1 m over the ground to under the wire."""
    if len(column) < 6 or top - base < 3.0:
        return False
    bins = np.arange(base + 1.0, top - 0.5, 0.5)
    if len(bins) < 3:
        return False
    filled = np.unique(np.clip(np.searchsorted(bins, column[:, 1]) - 1, -1, len(bins)))
    return np.isin(np.arange(len(bins) - 1), filled).mean() >= POLE_SHARE


def to_wire(piece: Piece, buildings: Index, columns: Index, ground) -> Wire:
    a, end_a = reach_end(piece, True, buildings, columns, ground)
    b, end_b = reach_end(piece, False, buildings, columns, ground)
    s_a = float((np.array([a[0], a[2]]) - piece.origin) @ piece.direction)
    s_b = float((np.array([b[0], b[2]]) - piece.origin) @ piece.direction)
    mid = piece.height((s_a + s_b) / 2)
    sag = max(0.0, (a[1] + b[1]) / 2 - mid)
    return Wire(a, b, sag, ends=(end_a, end_b))


# -- lamps ------------------------------------------------------------------------------------------
def _plan(w: Wire):
    a, b = np.array([w.a[0], w.a[2]]), np.array([w.b[0], w.b[2]])
    length = float(np.linalg.norm(b - a))
    return a, (b - a) / max(length, 1e-9), length


def knots(wires: list[Wire]) -> list[tuple[np.ndarray, list[int]]]:
    """Mid-air points where a free wire end, carried on by up to REACH, meets another wire at the
    same height: span wires pulled down by the lamp they carry. The ends move to that point."""
    found = []
    # Collinear halves of one span, bent down by the lamp between them: their ends face each other.
    ends = [(i, e) for i, w in enumerate(wires) for e in (0, 1) if w.ends[e] == "free"]
    for x, (i, e) in enumerate(ends):
        for j, f in ends[x + 1:]:
            if j == i or wires[i].ends[e] != "free" or wires[j].ends[f] != "free":
                continue
            p, q = (wires[i].a if e == 0 else wires[i].b), (wires[j].a if f == 0 else wires[j].b)
            if np.linalg.norm(p - q) <= KNOT_GAP and abs(p[1] - q[1]) <= 0.6:
                point = (p + q) / 2
                _set_end(wires[i], e, point)
                _set_end(wires[j], f, point)
                found.append((point, sorted({i, j})))
    for i, w in enumerate(wires):
        for e in (0, 1):
            if w.ends[e] != "free":
                continue
            pa, pd, length = _plan(w)
            p, d = (pa, -pd) if e == 0 else (pa + pd * length, pd)
            best = None
            for j, v in enumerate(wires):
                if j == i:
                    continue
                va, vd, vlength = _plan(v)
                cross = d[0] * vd[1] - d[1] * vd[0]
                if abs(cross) < math.sin(math.radians(15.0)):
                    continue
                r = va - p
                t = (r[0] * vd[1] - r[1] * vd[0]) / cross
                u = (r[0] * d[1] - r[1] * d[0]) / cross
                lo = -REACH if v.ends[0] == "free" else 0.0
                hi = vlength + (REACH if v.ends[1] == "free" else 0.0)
                if not (-KNOT <= t <= REACH and lo <= u <= hi):
                    continue
                own = (length + t) / length if e == 1 else -t / length
                h_w = w.point(own)[1]
                h_v = v.point(u / vlength)[1]
                if abs(h_w - h_v) > 1.0:
                    continue
                if best is None or t < best[0]:
                    point = p + d * t
                    best = (t, j, u, np.array([point[0], (h_w + h_v) / 2, point[1]]))
            if best is None:
                continue
            _, j, u, point = best
            _set_end(w, e, point)
            v = wires[j]
            _, _, vlength = _plan(v)
            for f, at in ((0, 0.0), (1, vlength)):
                if v.ends[f] == "free" and abs(u - at) <= REACH:
                    _set_end(v, f, point)
            found.append((point, sorted({i, j})))
    # One lamp per knot: points within KNOT are one.
    merged: list[tuple[np.ndarray, list[int]]] = []
    for point, members in found:
        for k, (q, others) in enumerate(merged):
            if np.linalg.norm(q - point) <= KNOT:
                merged[k] = ((q + point) / 2, sorted({*others, *members}))
                break
        else:
            merged.append((point, members))
    return merged


def _set_end(w: Wire, e: int, point):
    if e == 0:
        w.a = point.copy()
        w.ends = ("knot", w.ends[1])
    else:
        w.b = point.copy()
        w.ends = (w.ends[0], "knot")


def _held(w: Wire, crowns) -> bool:
    """Every end hangs from something, or vanishes into a crown (where nothing shows it ending)."""
    for e, p in zip(w.ends, (w.a, w.b), strict=True):
        if e != "free":
            continue
        if crowns is None or not len(crowns.query_ball_point(p, r=CROWN_END)):
            return False
    return True


def over_roof(w: Wire, buildings: Index, ground) -> bool:
    """Most of the wire's middle runs over building returns: roof edges, railings, antenna stays."""
    hits = 0
    samples = np.linspace(0.15, 0.85, 8)
    for t in samples:
        p = w.point(float(t))
        below = buildings.near(p[0], p[2], 0.5)
        floor = float(ground(p[0], p[2])) + 2.0
        hits += bool(len(below) and np.any((below[:, 1] < p[1] + 0.3) & (below[:, 1] > floor)))
    return hits / len(samples) > ROOF


def blobs(points, wires: list[Wire]):
    """Compact clusters of returns hanging just below a wire, clear of anything else: hung lamps.

    ``points`` are the candidate returns that are not wire returns."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree

    if len(points) < LAMP["points"] or not wires:
        return []
    # Only returns near a wire can be a lamp on it.
    near = np.zeros(len(points), bool)
    tree = cKDTree(points[:, [0, 2]])
    for w in wires:
        for t in np.linspace(0, 1, max(2, int(w.length / 0.5) + 1)):
            q = w.point(float(t))
            for i in tree.query_ball_point([q[0], q[2]], r=LAMP["size"]):
                near[i] |= q[1] - LAMP["below"] - LAMP["size"] <= points[i, 1] <= q[1] + 0.3
    if near.sum() < LAMP["points"]:
        return []
    local = points[near]
    pairs = cKDTree(local).query_pairs(r=0.35, output_type="ndarray")
    graph = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])) if len(pairs) else ([], ([], [])),
                       shape=(len(local), len(local)))
    _, labels = connected_components(graph, directed=False)
    everything = cKDTree(points)
    out = []
    for label in np.unique(labels):
        members = local[labels == label]
        if len(members) < LAMP["points"]:
            continue
        lo, hi = members.min(0), members.max(0)
        if max(hi[0] - lo[0], hi[2] - lo[2]) > LAMP["size"] or hi[1] - lo[1] > LAMP["size"]:
            continue
        centre = members.mean(0)
        if len(everything.query_ball_point(centre, r=LAMP["clear"])) > len(members):
            continue  # part of something bigger: a crown, a sign gantry, a balcony
        best = None
        for i, w in enumerate(wires):
            t, d, h = _project(w, centre)
            if d <= 0.6 and 0.0 <= h - hi[1] <= LAMP["below"] and 0.05 < t < 0.95 and (best is None or d < best[1]):
                best = (i, d, t)
        if best is None:
            continue
        hang = wires[best[0]].point(best[2])
        out.append((np.array([centre[0], hang[1], centre[2]]), best[0], best[2]))
    return out


def _project(w: Wire, p):
    """(t, plan distance, wire height) of the wire point nearest to ``p`` in plan."""
    a, b = np.array([w.a[0], w.a[2]]), np.array([w.b[0], w.b[2]])
    d = b - a
    length2 = float(d @ d)
    t = 0.0 if length2 < 1e-9 else float(np.clip((np.array([p[0], p[2]]) - a) @ d / length2, 0, 1))
    q = w.point(t)
    return t, float(np.hypot(q[0] - p[0], q[2] - p[2])), float(q[1])


# -- the tile ---------------------------------------------------------------------------------------
def detect(xyz, classes, bounds, search=SEARCH):
    """Wires and hung lamps a tile owns, from survey returns around it.

    ``xyz`` engine metres (n, 3), ``classes`` ASPRS codes (n,), ``bounds`` the tile's owned
    (x0, z0, x1, z1). Returns (wires, lamps) with lamps as (point, wire index)."""
    from scipy.spatial import cKDTree

    xyz = np.asarray(xyz, dtype=np.float64)
    classes = np.asarray(classes)
    order = np.lexsort((xyz[:, 1], xyz[:, 2], xyz[:, 0]))
    xyz, classes = xyz[order], classes[order]
    x0, z0, x1, z1 = bounds
    outer = (x0 - search, z0 - search, x1 + search, z1 + search)
    inside = (xyz[:, 0] >= outer[0]) & (xyz[:, 0] < outer[2]) & (xyz[:, 2] >= outer[1]) & (xyz[:, 2] < outer[3])
    xyz, classes = xyz[inside], classes[inside]
    ground = Ground(xyz[classes == GROUND], outer)
    height = xyz[:, 1] - ground(xyz[:, 0], xyz[:, 2])
    candidate = np.isin(classes, CLASSES) & (height >= MIN_HEIGHT) & (height <= MAX_HEIGHT)
    points = xyz[candidate]
    open_space = sparse(points)
    loose = points[open_space]
    mask, directions = linear(loose)
    others = np.concatenate([points[~open_space], loose[~mask]])
    clutter = cKDTree(others) if len(others) else None
    parts = join(pieces(loose[mask], directions[mask], clutter))
    buildings = Index(xyz[(classes == BUILDING) & (height >= MIN_HEIGHT - 1.0)])
    columns = Index(xyz[np.isin(classes, CLASSES) & (height >= 0.8)])
    wires = [to_wire(p, buildings, columns, ground) for p in parts]
    wires = [w for w in wires if not over_roof(w, buildings, ground)]
    found = knots(wires)
    crowns = cKDTree(points[~open_space]) if (~open_space).any() else None
    kept = [i for i, w in enumerate(wires) if (w.length >= MIN_WIRE or "knot" in w.ends) and _held(w, crowns)]
    renumber = {old: new for new, old in enumerate(kept)}
    wires = [wires[i] for i in kept]
    lamps = []
    for point, members in found:
        owners = [renumber[j] for j in members if j in renumber]
        if owners:
            lamps.append((point, owners[0]))
    for point, i, _ in blobs(np.concatenate([points[~open_space], loose[~mask]]), wires):
        if all(np.linalg.norm(point[[0, 2]] - q[[0, 2]]) > 2.0 for q, _ in lamps):
            lamps.append((point, i))
    for point, i in lamps:
        wires[i].lamps.append(point)
    for w in wires:
        w.kind = classify(w, ground)
    owned = [i for i, w in enumerate(wires) if _owns(bounds, *(w.point(0.5)[[0, 2]]))]
    index = {old: new for new, old in enumerate(owned)}
    owned_lamps = [(p, index[i]) for p, i in lamps if i in index and _owns(bounds, p[0], p[2])]
    # A lamp the tile owns on a wire it does not: hang it on the wire's owner's copy is not
    # possible here, so the lamp keeps only its point (wire -1).
    owned_lamps += [(p, -1) for p, i in lamps if i not in index and _owns(bounds, p[0], p[2])]
    return [wires[i] for i in owned], owned_lamps


def classify(w: Wire, ground) -> str:
    mid = w.point(0.5)
    clearance = mid[1] - float(ground(mid[0], mid[2]))
    if clearance >= POWER_HEIGHT and w.length > SPAN_MAX:
        return "power"
    if w.lamps or ("facade" in w.ends and w.length <= SPAN_MAX):
        return "span"
    return "utility"


def _owns(bounds, x, z) -> bool:
    return bounds[0] <= x < bounds[2] and bounds[1] <= z < bounds[3]


def record(wires: list[Wire], lamps) -> dict | None:
    """The ``lights.wires`` field: wires as [ax, ay, az, bx, by, bz, sag, kind] (``kinds`` names
    the kind index; the wire hangs ``sag`` below its chord at mid span, a parabola), lamps as
    [x, y, z, wire]: the point on the wire the lamp hangs from (wire -1: a neighbour tile's)."""
    if not wires:
        return None
    r = lambda v: round(float(v), 2)  # noqa: E731
    return {
        "version": FORMAT_VERSION,
        "kinds": list(KINDS),
        "wires": [[*map(r, w.a), *map(r, w.b), r(w.sag), KINDS.index(w.kind)] for w in wires],
        "lamps": [[*map(r, p), int(i)] for p, i in lamps],
    }


def survey_paths(tile) -> dict | None:
    """The tile's ``pistepilvi`` LAZ blocks, SEARCH around it; None unless every one is on disk."""
    from turku3d import config as C

    paths = {}
    for block in C.laz_tiles_for_bbox(tile.bbox.buffered(SEARCH)):
        path = C.raw_dir("laz") / "pistepilvi" / f"{block.name}.laz"
        if not path.is_file():
            return None
        paths[block.name] = path
    return paths


def survey(paths, bbox):
    """(engine xyz, classes) of the returns inside ``bbox``, streamed from the LAZ blocks once.

    Nothing is cached or written: a city migration reads each block a few times instead of
    keeping decoded copies on the farm's disk."""
    import laspy

    from turku3d import config as C

    xyz, classes = [], []
    for name in sorted(paths):
        with laspy.open(paths[name]) as reader:
            (sx, sy, sz), (ox, oy, oz) = reader.header.scales, reader.header.offsets
            for chunk in reader.chunk_iterator(2_000_000):
                x = np.asarray(chunk.X) * sx + ox - C.ORIGIN_E
                z = C.ORIGIN_N - (np.asarray(chunk.Y) * sy + oy)
                keep = (x >= bbox.xmin) & (x < bbox.xmax) & (z >= bbox.zmin) & (z < bbox.zmax)
                if not keep.any():
                    continue
                y = np.asarray(chunk.Z)[keep] * sz + oz - C.ORIGIN_H
                xyz.append(np.c_[x[keep], y, z[keep]])
                classes.append(np.asarray(chunk.classification)[keep])
    if not xyz:
        return np.empty((0, 3)), np.empty(0, np.uint8)
    return np.concatenate(xyz), np.concatenate(classes)


def tile_wires(tile) -> dict | None:
    """The ``lights.wires`` record of a visual tile, or None without survey or wires."""
    paths = survey_paths(tile)
    if not paths:
        return None
    xyz, classes = survey(paths, tile.bbox.buffered(SEARCH))
    if not len(xyz):
        return None
    b = tile.bbox
    found, lamps = detect(xyz, classes, (b.xmin, b.zmin, b.xmax, b.zmax))
    return record(found, lamps)
