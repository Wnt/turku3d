"""Measured water surfaces: one level field per water body piece, from the city's water key points.

The city ground model (maanpinta) carries key points on open water at its surface. The
sea and the tidal lower Aura measure 0.1-0.3 m and keep the shared datum (``water.LEVEL``);
inland reaches above rapids and dams, streams and field ditches measure metres higher.

A body is split at mapped dams, weirs, waterfalls and lock gates, so each pool measures
only its own surface and the step stays at the structure. Within a piece the level is the
median of its water key points within ``RADIUS`` of nodes on an absolute ``GRID``,
interpolated bilinearly: flat across a channel and along a pool, gently sloped along a
stream, and ramped over rapids where the measured surface falls. Nodes and their support
are canonical (absolute grid, points read ``MARGIN`` beyond the tile), so both tiles of a
seam compute the same surface. Pieces without any water key points fall back to their
lowest shore returns.
"""

import json

import numpy as np
import shapely

from turku3d import config as C

from .water import LEVEL

GRID = 8.0
RADIUS = 30.0
MARGIN = GRID + RADIUS + 4.0
SHORE = 1.5  # m outside a piece: bank returns that bound an unmeasured surface
MIN_POINTS = 4
# Measured surfaces up to 0.5 m belong to the sea datum; from 1 m the measurement holds.
TIDAL = (0.5, 1.0)
STEPS = ("dam", "weir", "waterfall", "lock_gate")
RAPIDS_REACH = 25.0  # m around a mapped rapids line or point


def step_lines(osm):
    """Mapped dam, weir, waterfall and lock-gate lines (OSM ``waterway``)."""
    return features(osm)[0]


def features(osm):
    """(step lines, rapids areas) mapped in OSM."""
    if not osm:
        return [], []
    from turku3d.spatial import frame

    lines, rapids = [], []
    for row in frame(osm).itertuples():
        tags = json.loads(row.tags or "{}")
        geometry = shapely.force_2d(row.geometry_local)
        if tags.get("waterway") in STEPS and geometry.geom_type in ("LineString", "MultiLineString"):
            lines.append(geometry)
        elif tags.get("water") == "rapids" and geometry.geom_type in ("Polygon", "MultiPolygon"):
            rapids.append(geometry)
        elif tags.get("waterway") == "rapids" or tags.get("whitewater"):
            rapids.append(geometry.buffer(RAPIDS_REACH))
    return lines, rapids


def pieces(water, steps):
    """Water bodies split at mapped steps; each piece carries its own surface."""
    cut = shapely.union_all(steps).buffer(0.05) if steps else None
    result = []
    for body in water:
        parts = [body]
        if cut is not None and body.intersects(cut):
            parts = shapely.get_parts(shapely.make_valid(body.difference(cut)))
        result.extend(p for p in parts if p.geom_type == "Polygon" and p.area > 1.0)
    return result


def lidar_paths(tile):
    """Canonical key-point blocks under the tile plus ``MARGIN``."""
    root = C.raw_dir("laz") / "maanpinta"
    return [p for b in C.laz_tiles_for_bbox(tile.bbox.buffered(MARGIN))
            if (p := root / f"{b.name}.laz").exists()]


def key_points(tile, paths):
    from turku3d.lidar.decoded import columns, source

    e0, n0, e1, n1 = tile.bbox.buffered(MARGIN).to_en().as_tuple()
    chunks = []
    for path in paths:
        las = columns(source(path))
        e, n, h = np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)
        keep = (e >= e0) & (e <= e1) & (n >= n0) & (n <= n1)
        keep &= ~np.isin(np.asarray(las.classification), [3, 4, 5])
        x, y, z = C.en_to_local(e[keep], n[keep], h[keep])
        chunks.append(np.column_stack([x, y, z]))
    return np.concatenate(chunks) if chunks else np.empty((0, 3))


def datum(measured):
    """Tidal water keeps the sea datum; inland measurements are used as they are."""
    lo, hi = TIDAL
    measured = np.asarray(measured, float)
    blend = np.clip((measured - lo) / (hi - lo), 0, 1)
    return np.where(np.isfinite(measured), LEVEL + (measured - LEVEL) * blend, np.nan)


class Levels:
    """Surface heights of every piece near one tile; ``at`` answers any point near water."""

    def __init__(self, pieces, origin, shape, nodes, fallback):
        self.pieces = pieces  # shapely polygons
        self.origin = origin  # (x0, z0) of node (0, 0), a multiple of GRID
        self.shape = shape  # (rows along z, columns along x)
        self.nodes = nodes  # (pieces, rows, cols) surface heights, NaN where unmeasured
        self.fallback = fallback  # (pieces,) surface of a piece without nearby nodes

    def at(self, xz):
        xz = np.asarray(xz, float).reshape(-1, 2)
        result = np.full(len(xz), LEVEL)
        if not self.pieces or not len(xz):
            return result
        points = shapely.points(xz)
        distance = np.stack([shapely.distance(points, p) for p in self.pieces])
        owner = np.argmin(distance, axis=0)
        u = (xz[:, 0] - self.origin[0]) / GRID
        v = (xz[:, 1] - self.origin[1]) / GRID
        i0 = np.clip(np.floor(v).astype(int), 0, self.shape[0] - 2)
        j0 = np.clip(np.floor(u).astype(int), 0, self.shape[1] - 2)
        fu, fv = np.clip(u - j0, 0, 1), np.clip(v - i0, 0, 1)
        total = np.zeros(len(xz))
        weight = np.zeros(len(xz))
        for di, dj, w in ((0, 0, (1 - fu) * (1 - fv)), (0, 1, fu * (1 - fv)),
                          (1, 0, (1 - fu) * fv), (1, 1, fu * fv)):
            value = self.nodes[owner, i0 + di, j0 + dj]
            ok = np.isfinite(value)
            total[ok] += w[ok] * value[ok]
            weight[ok] += w[ok]
        measured = weight > 1e-9
        result[measured] = total[measured] / weight[measured]
        fallback = self.fallback[owner]
        use = ~measured & np.isfinite(fallback)
        result[use] = fallback[use]
        return result


def measure(tile, water, steps, paths, rapids=()):
    """Level field of the tile's water pieces from canonical key points.

    A piece whose own measurement is tidal (the sea and the lower Aura) keeps the datum
    everywhere except in mapped rapids, where its surface climbs to the pool above:
    piers and pontoons inside a mapped sea outline never lift the sea.
    """
    from scipy.spatial import cKDTree

    parts = pieces(water, steps)
    x0 = np.floor((tile.bbox.xmin - GRID) / GRID) * GRID
    z0 = np.floor((tile.bbox.zmin - GRID) / GRID) * GRID
    cols = int(np.ceil((tile.bbox.xmax + GRID - x0) / GRID)) + 1
    rows = int(np.ceil((tile.bbox.zmax + GRID - z0) / GRID)) + 1
    nodes = np.full((len(parts), rows, cols), np.nan)
    fallback = np.full(len(parts), np.nan)
    if not parts:
        return Levels(parts, (x0, z0), (rows, cols), nodes, fallback)
    points = key_points(tile, paths)
    gx, gz = np.meshgrid(x0 + np.arange(cols) * GRID, z0 + np.arange(rows) * GRID)
    grid = np.column_stack([gx.ravel(), gz.ravel()])
    xy = shapely.points(points[:, [0, 2]]) if len(points) else np.empty(0, object)
    for k, piece in enumerate(parts):
        shapely.prepare(piece)
        near = shapely.distance(shapely.points(grid), piece) <= RADIUS
        if not near.any() or not len(points):
            continue
        inside = shapely.contains_xy(piece, points[:, 0], points[:, 2])
        shore = ~inside & (shapely.distance(xy, piece) <= SHORE)
        values = np.full(len(grid), np.nan)
        wet = points[inside]
        if len(wet) >= MIN_POINTS:
            tree = cKDTree(wet[:, [0, 2]])
            for n, ids in zip(np.flatnonzero(near), tree.query_ball_point(grid[near], RADIUS)):
                if len(ids) >= MIN_POINTS:
                    values[n] = np.median(wet[ids, 1])
            fallback[k] = np.median(wet[:, 1])
        bank = points[shore]
        missing = near & ~np.isfinite(values)
        if len(wet) < MIN_POINTS and len(bank) >= MIN_POINTS:
            # Without water returns, the lowest bank returns bound the surface. A measured
            # piece never takes them: beside quays they are the quay tops.
            tree = cKDTree(bank[:, [0, 2]])
            for n, ids in zip(np.flatnonzero(missing), tree.query_ball_point(grid[missing], RADIUS)):
                if len(ids) >= MIN_POINTS:
                    values[n] = np.percentile(bank[ids, 1], 10) - 0.2
            if not np.isfinite(fallback[k]):
                fallback[k] = np.percentile(bank[:, 1], 10) - 0.2
        values = datum(values)
        if datum(fallback[k]) == LEVEL:
            climbing = (shapely.contains_xy(shapely.union_all(rapids), grid[:, 0], grid[:, 1])
                        if rapids else np.zeros(len(grid), bool))
            values[~climbing & np.isfinite(values)] = LEVEL
        nodes[k] = values.reshape(rows, cols)
    fallback = datum(fallback)
    return Levels(parts, (x0, z0), (rows, cols), nodes, fallback)
