"""Benches against the survey: lidar evidence first, then the nearest barrier.

Register (GIS:Varusteet) and OSM bench points carry survey offsets of up to several metres and no
heading. ``orientation.heading`` faces a bench away from a carriageway centreline within 12 m and
otherwise toward the nearest carriageway at any distance, so quay and park benches stood across
hedges and faced distant streets (edit session 20261103-221501-zd82ho). These rules, in order,
are shared by the furniture stage (``coarse.furniture.produce``) and the ``benches`` tile
migration, so a regeneration and a migrated tile place every bench alike:

1. ``lidar_snap``: where the classified survey point cloud covers a bench, it stands on the
   nearest bench-shaped blob within SNAP_REACH: non-ground returns in BAND above the local ground
   with nothing in CLEAR above them (a seat and backrest; not a bush, hedge, car, person or pole),
   BLOB_LENGTH long and BLOB_WIDTH wide. One blob carries one bench (minimum-cost matching).
   The long axis follows the blob, the back is on its taller (backrest) side, else on the side
   of the nearest barrier, else the old heading's side.
2. ``lidar_absent``: the cloud covers the point (GROUND_MIN ground returns within ABSENT_REACH),
   shows at most ABSENT_MAX returns at bench height there and no blob within SNAP_REACH: nothing
   stands there (removed or never built). The bench is dropped.
   ``lidar_duplicate``: a covered bench left without a blob while every blob within SNAP_REACH
   carries another bench is one more record (register or OSM) of a bench already placed: dropped.
3. ``barrier_back``: otherwise a bench within BARRIER_REACH of a hedge, wall, fence, retaining
   wall or facade turns parallel to it, back to it, and steps out to BACK_GAP off its face when
   it overlaps the barrier.
4. ``water_view``: otherwise a bench within WATER_REACH of a shore faces the water.
5. Otherwise the street heading stays.
"""

from __future__ import annotations

import math
import warnings
from collections import Counter

import numpy as np
import shapely
import shapely.ops
from shapely.strtree import STRtree

SNAP_REACH = 6.0  # m between a register point and its bench's blob (survey offsets up to ~5 m)
BAND = (0.3, 1.1)  # m above ground: seat and backrest returns
CLEAR = (1.25, 2.4)  # m above ground: any return here within CLEAR_RADIUS is not a bench
CLEAR_RADIUS = 0.3
CELL = 0.2  # m raster connecting a blob's returns
BLOB_LENGTH = (1.0, 2.6)  # 5-95 % extents of a seat's returns (the model is 1.8 x 0.55 m)
BLOB_WIDTH = (0.25, 1.5)  # a seat plus what stands at it (bin, armrest, bag)
EXTENT = (5, 95)
BLOB_MIN = 15  # returns
DENSITY_MIN = 10.0  # returns per m2 of the blob's box (seats are solid, racks and rails sparse)
ABSENT_REACH = 1.5
ABSENT_MAX = 4
GROUND_MIN = 40  # class-2 returns within ABSENT_REACH: the cloud covers the point
GROUND_REACH = 1.5  # m for a return's local ground height
BACKREST = 0.62  # m above ground: backrest returns, above the seat
BACK_MIN = 0.05  # m: the backrest centroid this far off the blob centre names the back side
BARRIER_REACH = 3.0  # m from a bench to the face of a barrier it stands against
BENCH_BACK = 0.27  # m from the bench origin to its back face (props.models bench backrest)
BACK_GAP = 0.15  # m between a moved bench's back and the barrier face
MAX_SHIFT = 2.0  # m a barrier may push a bench out of itself
WATER_REACH = 6.0
WINDOW = SNAP_REACH + 1.5  # m of cloud read around a bench
CLASSES = (1, 2, 3, 4, 5, 12)  # 12: the second flight line's unclassified overlap returns
# Half thickness of each barrier (coarse.fences STYLES and the measured hedge width).
HALF = {"hedge": 0.33, "wall": 0.15, "city_wall": 0.15, "retaining_wall": 0.15, "fence": 0.06,
        "railing": 0.06, "guard_rail": 0.06}


def yaw_of(fx, fz):
    """three.js rotation.y of a bench whose front (+Z) points along (fx, fz)."""
    return math.atan2(fx, fz)


class Cloud:
    """Survey returns around a tile's benches: ground model and bench-height returns."""

    def __init__(self, points, classes):
        from scipy.spatial import cKDTree

        points = np.asarray(points, np.float64).reshape(-1, 3)
        classes = np.asarray(classes).reshape(-1)
        ground = classes == 2
        self.ground = points[ground]
        self.ground_tree = cKDTree(self.ground[:, [0, 2]]) if len(self.ground) else None
        other = points[~ground]
        height = self._height(other)
        ok = np.isfinite(height)
        other, height = other[ok], height[ok]
        clear = other[(height >= CLEAR[0]) & (height < CLEAR[1])]
        band = (height >= BAND[0]) & (height < BAND[1])
        self.band, self.band_height = other[band], height[band]
        if len(clear) and len(self.band):
            blocked = cKDTree(clear[:, [0, 2]]).query_ball_point(self.band[:, [0, 2]], CLEAR_RADIUS)
            free = np.array([not b for b in blocked], bool)
        else:
            free = np.ones(len(self.band), bool)
        self.seat, self.seat_height = self.band[free], self.band_height[free]
        self.band_tree = cKDTree(self.band[:, [0, 2]]) if len(self.band) else None

    def _height(self, points):
        if self.ground_tree is None or not len(points):
            return np.full(len(points), np.nan)
        dist, idx = self.ground_tree.query(points[:, [0, 2]], k=8, distance_upper_bound=GROUND_REACH)
        found = np.isfinite(dist)
        heights = np.where(found, self.ground[np.minimum(idx, len(self.ground) - 1), 1], np.nan)
        with np.errstate(all="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            ground = np.nanmedian(heights, axis=1)
        return points[:, 1] - ground

    def covered(self, x, z):
        return self.ground_tree is not None and len(
            self.ground_tree.query_ball_point([x, z], ABSENT_REACH)) >= GROUND_MIN

    def band_near(self, x, z):
        return 0 if self.band_tree is None else len(self.band_tree.query_ball_point([x, z], ABSENT_REACH))

    def blobs(self):
        """Bench-shaped blobs: (x, z, axis (ax, az), back sign along the axis normal or 0)."""
        from scipy import ndimage

        if len(self.seat) < BLOB_MIN:
            return []
        xz = self.seat[:, [0, 2]]
        lo = xz.min(0)
        cell = np.floor((xz - lo) / CELL).astype(int)
        grid = np.zeros(cell.max(0) + 1, bool)
        grid[cell[:, 0], cell[:, 1]] = True
        labels, count = ndimage.label(grid, structure=np.ones((3, 3), bool))
        label = labels[cell[:, 0], cell[:, 1]]
        found = []
        for k in range(1, count + 1):
            members = label == k
            if members.sum() < BLOB_MIN:
                continue
            pts, h = xz[members], self.seat_height[members]
            mean = pts.mean(0)
            d = pts - mean
            _, vectors = np.linalg.eigh(d.T @ d)
            axis = vectors[:, 1]
            if axis[0] < 0 or (axis[0] == 0 and axis[1] < 0):
                axis = -axis  # deterministic sign
            normal = np.array([-axis[1], axis[0]])
            u, n = d @ axis, d @ normal
            # Robust extents: stray returns at a blob's rim (paving clutter, a hedge edge) do not
            # widen the seat.
            u0, u1 = np.percentile(u, EXTENT)
            n0, n1 = np.percentile(n, EXTENT)
            length, width = u1 - u0, n1 - n0
            if not (BLOB_LENGTH[0] <= length <= BLOB_LENGTH[1] and BLOB_WIDTH[0] <= width <= BLOB_WIDTH[1]):
                continue
            if members.sum() / max(length * width, 1e-6) < DENSITY_MIN:
                continue
            centre = mean + axis * (u0 + u1) / 2 + normal * (n0 + n1) / 2
            high = h >= BACKREST
            back = 0
            if high.sum() >= 4:
                offset = float(((pts[high] - centre) @ normal).mean())
                back = 0 if abs(offset) < BACK_MIN else (1 if offset > 0 else -1)
            found.append((float(centre[0]), float(centre[1]), (float(axis[0]), float(axis[1])), back))
        return sorted(found)


def barrier_lines(barriers, footprints):
    """(geometry, half thickness) of what a bench backs onto: mapped barriers and facades."""
    lines = []
    for geometry, tags, *_ in barriers or ():
        kind = (tags or {}).get("barrier")
        if kind not in HALF or geometry is None or geometry.is_empty:
            continue
        half = HALF[kind]
        if kind == "hedge" and (tags or {}).get("_hedge_width"):
            half = float(tags["_hedge_width"]) / 2
        lines.append((geometry, half))
    for footprint in footprints or ():
        if footprint is not None and not footprint.is_empty:
            lines.append((footprint.boundary, 0.0))
    return lines


def _tangent(line, point):
    """Unit tangent of the line part nearest the point."""
    parts = [p for p in shapely.get_parts(line) if p.geom_type in ("LineString", "LinearRing")]
    part = min(parts, key=lambda p: p.distance(point)) if parts else None
    if part is None or part.length < 1e-6:
        return None
    at = part.project(point)
    a = part.interpolate(max(0.0, at - 0.5))
    b = part.interpolate(min(part.length, at + 0.5))
    tx, tz = b.x - a.x, b.y - a.y
    length = math.hypot(tx, tz)
    return (tx / length, tz / length) if length > 1e-9 else None


def _blocked(x, z, footprints, water):
    point = shapely.Point(x, z)
    return any(g.covers(point) for g in (*footprints, *water) if g is not None)


def _barrier(x, z, lines, tree, footprints, water):
    """(centre distance, half, nearest point, outward unit normal) of the nearest barrier face
    within BARRIER_REACH of (x, z), or None. The normal points from the barrier to the open side."""
    if tree is None:
        return None
    point = shapely.Point(x, z)
    best = None
    for i in tree.query(point, predicate="dwithin", distance=BARRIER_REACH + 1.0):
        geometry, half = lines[int(i)]
        centre = geometry.distance(point)
        if centre - half <= BARRIER_REACH and (best is None or centre - half < best[0] - best[1]):
            best = (centre, half, geometry)
    if best is None:
        return None
    centre, half, geometry = best
    tangent = _tangent(geometry, point)
    if tangent is None:
        return None
    nearest = shapely.ops.nearest_points(geometry, point)[0]
    nx, nz = -tangent[1], tangent[0]
    side = (x - nearest.x) * nx + (z - nearest.y) * nz
    if abs(side) < 0.05:
        # On the line itself: the open side is the one a step forward does not block.
        side = -1 if _blocked(nearest.x + nx, nearest.y + nz, footprints, water) else 1
    if side < 0:
        nx, nz = -nx, -nz
    return centre, half, nearest, (nx, nz)


def _back_to(bench, lines, tree, footprints, water, stats):
    """Turn (and step out) a bench against the nearest barrier face within BARRIER_REACH."""
    found = _barrier(bench.x, bench.z, lines, tree, footprints, water)
    if found is None:
        return False
    centre, half, nearest, (nx, nz) = found
    target = half + BACK_GAP + BENCH_BACK
    if centre < target:
        x, z = nearest.x + nx * target, nearest.y + nz * target
        if math.hypot(x - bench.x, z - bench.z) <= MAX_SHIFT and not _blocked(x, z, footprints, water):
            bench.x, bench.z = x, z
            bench.adjusted = "barrier_clearance"
            stats["bench_barrier_clearance"] += 1
    bench.yaw, bench.orientation = yaw_of(nx, nz), "barrier_back"
    stats["bench_barrier_back"] += 1
    return True


def _face_water(bench, water, stats):
    point = shapely.Point(bench.x, bench.z)
    shores = [w for w in water if w is not None and not w.is_empty and w.boundary.distance(point) <= WATER_REACH]
    if not shores:
        return False
    shore = min(shores, key=lambda w: w.boundary.distance(point))
    edge = shapely.ops.nearest_points(shore.boundary, point)[0]
    tangent = _tangent(shore.boundary, point)
    dx, dz = edge.x - bench.x, edge.y - bench.z
    if tangent is not None:
        nx, nz = -tangent[1], tangent[0]
        if nx * dx + nz * dz < 0:
            nx, nz = -nx, -nz
    elif math.hypot(dx, dz) > 1e-6:
        nx, nz = dx, dz
    else:
        return False
    bench.yaw, bench.orientation = yaw_of(nx, nz), "water_view"
    stats["bench_water_view"] += 1
    return True


def _matching(benches, blobs):
    """Minimum total distance assignment of benches to blobs within SNAP_REACH."""
    from scipy.optimize import linear_sum_assignment

    if not benches or not blobs:
        return {}
    cost = np.array([[math.hypot(b.x - x, b.z - z) for x, z, *_ in blobs] for b in benches])
    big = 1e6
    rows, cols = linear_sum_assignment(np.where(cost <= SNAP_REACH, cost, big))
    return {int(r): int(c) for r, c in zip(rows, cols) if cost[r, c] <= SNAP_REACH}


def _snap(bench, blob, lines, tree, footprints, water, stats):
    """The bench on its blob. A barrier within reach sets its axis and back (the blob's own axis
    and backrest side are noisy on sparse or cluttered returns); else the blob's axis with its
    backrest side, else the old heading's side."""
    x, z, (ax, az), back = blob
    bench.x, bench.z, bench.adjusted = x, z, "lidar_snap"
    stats["bench_lidar_snap"] += 1
    found = _barrier(x, z, lines, tree, footprints, water)
    if found is not None:
        bench.yaw, bench.orientation = yaw_of(*found[3]), "lidar_barrier_back"
        return
    nx, nz = -az, ax  # blob normal; the backrest is at +normal when back > 0
    if back == 0:
        fx, fz = math.sin(bench.yaw), math.cos(bench.yaw)
        back = -1 if fx * nx + fz * nz > 0 else 1
        bench.orientation = "lidar_heading_side"
    else:
        bench.orientation = "lidar_backrest"
    bench.yaw = yaw_of(-nx * back, -nz * back)


def place(benches, cloud, barriers=(), footprints=(), water=(), stats=None):
    """Benches placed by the rules above, in input order; dropped ones are left out.

    ``cloud`` is a ``Cloud`` of the survey around the benches, or None where the tile has none.
    Benches are changed in place (x, z, yaw, orientation, adjusted); y is the caller's."""
    stats = Counter() if stats is None else stats
    footprints = [f for f in footprints or () if f is not None and not f.is_empty]
    water = [w for w in water or () if w is not None and not w.is_empty]
    lines = barrier_lines(barriers, footprints)
    tree = STRtree([g for g, _ in lines]) if lines else None
    blobs = cloud.blobs() if cloud is not None else []
    covered = [cloud is not None and cloud.covered(b.x, b.z) for b in benches]
    matched = _matching([b for b, c in zip(benches, covered) if c],
                        blobs)
    index = [i for i, c in enumerate(covered) if c]
    snapped = {index[r]: blobs[c] for r, c in matched.items()}
    kept = []
    for i, bench in enumerate(benches):
        if i in snapped:
            _snap(bench, snapped[i], lines, tree, footprints, water, stats)
            stats[f"bench_{bench.orientation}"] += 1
            kept.append(bench)
            continue
        if covered[i] and cloud.band_near(bench.x, bench.z) <= ABSENT_MAX:
            stats["bench_lidar_absent"] += 1
            continue
        if covered[i] and any(math.hypot(bench.x - x, bench.z - z) <= SNAP_REACH for x, z, *_ in blobs):
            # Every bench the survey shows in reach already carries another record of it.
            stats["bench_lidar_duplicate"] += 1
            continue
        if tree is not None and _back_to(bench, lines, tree, footprints, water, stats):
            kept.append(bench)
            continue
        if _face_water(bench, water, stats):
            kept.append(bench)
            continue
        stats["bench_street_heading"] += 1
        kept.append(bench)
    return kept


def survey_cloud(tile_bbox, benches, laz_paths):
    """The survey returns around the benches (``Cloud``), or None without covering point cloud."""
    from turku3d import config as C
    from turku3d.lidar import TileStore

    if not benches or not laz_paths:
        return None
    xs = [b.x for b in benches]
    zs = [b.z for b in benches]
    bounds = C.BBoxLocal(min(xs) - WINDOW, min(zs) - WINDOW, max(xs) + WINDOW, max(zs) + WINDOW)
    by_name = {p.stem: p for p in laz_paths}
    blocks = [block for block in C.laz_tiles_for_bbox(bounds) if block.name in by_name]
    if not blocks:
        return None
    store = TileStore(source_paths=by_name)
    windows = shapely.union_all([shapely.Point(b.x, b.z).buffer(WINDOW, quad_segs=4) for b in benches])
    parts = []
    for block in blocks:  # only blocks on disk: a bench past the survey's edge has no evidence
        b = block.bbox_en.to_local()
        part = C.BBoxLocal(max(bounds.xmin, b.xmin), max(bounds.zmin, b.zmin),
                           min(bounds.xmax, b.xmax), min(bounds.zmax, b.zmax))
        if part.xmin < part.xmax and part.zmin < part.zmax:
            parts.append(store.query(part, polygon=windows, classes=list(CLASSES)).records)
    records = np.concatenate(parts) if parts else None
    if records is None or not len(records):
        return None
    return Cloud(records["xyz"], records["classification"])
