"""Measured water surfaces: one level field per water body piece, from the city's water key points.

The city ground model (maanpinta) carries key points on open water at its surface. The
sea and the tidal lower Aura measure 0.1-0.3 m and keep the shared datum (``water.LEVEL``);
inland reaches above rapids and dams, streams and field ditches measure metres higher.

A body is split at mapped dams, weirs, waterfalls and lock gates, so each pool measures
only its own surface and the step stays at the structure. Within a piece the level is the
lower quartile of its water key points (those clear of the shore when there are enough)
within ``RADIUS`` of nodes on an absolute ``GRID``,
interpolated bilinearly: flat across a channel and along a pool, gently sloped along a
stream, and ramped over rapids where the measured surface falls. Nodes and their support
are canonical (absolute grid, points read ``MARGIN`` beyond the tile), so both tiles of a
seam compute the same surface. Pieces without any water key points fall back to their
lowest shore returns.
"""

import json
from functools import lru_cache

import numpy as np
import shapely

from turku3d import config as C

from .water import LEVEL

GRID = 8.0
RADIUS = 30.0
MARGIN = GRID + RADIUS + 4.0
SHORE = 1.5  # m outside a piece: bank returns that bound an unmeasured surface
OFFSHORE = 1.5  # m inside a piece: returns clear of ledges, quay feet and banks
MIN_POINTS = 4
# Lower-quartile surface: bank, ledge and pier returns inside a mapped outline are higher.
QUANTILE = 25
# Measured surfaces up to 0.8 m belong to the sea datum; from 1.3 m the measurement holds.
TIDAL = (0.8, 1.3)
STEPS = ("dam", "weir", "waterfall", "lock_gate")
RAPIDS_REACH = 25.0  # m around a mapped rapids line or point
# A piece read whole from at most this many lidar blocks has one canonical reference level,
# shared by every tile that sees any part of it (the sea and its estuary are read per tile).
PIECE_BLOCKS = 24
# The reference is the most populated 10 cm height bin of the piece's offshore water returns:
# neighbouring tiles may outline a piece slightly differently, which moves a quantile but
# hardly ever the modal bin of a flat pool.
REFERENCE_STEP = 0.1


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


def modal_surface(points, piece):
    """Centre of the most populated height bin of a piece's water key points (offshore first)."""
    if not len(points):
        return np.nan
    inside = shapely.contains_xy(piece, points[:, 0], points[:, 2])
    wet = points[inside]
    if len(wet) < MIN_POINTS:
        return np.nan
    clear = shapely.distance(shapely.points(wet[:, [0, 2]]), piece.boundary) >= OFFSHORE
    heights = wet[clear, 1] if clear.sum() >= MIN_POINTS else wet[:, 1]
    bins, counts = np.unique(np.floor(heights / REFERENCE_STEP).astype(int), return_counts=True)
    return float((bins[np.argmax(counts)] + 0.5) * REFERENCE_STEP)


def piece_blocks(piece):
    from .sources import block_bbox

    candidates = C.laz_tiles_for_bbox(C.BBoxLocal(*piece.bounds))
    if len(candidates) > 4 * PIECE_BLOCKS:
        return None
    blocks = [b for b in candidates if piece.intersects(shapely.box(*block_bbox(b).as_tuple()))]
    if len(blocks) > PIECE_BLOCKS:
        return None
    root = C.raw_dir("laz") / "maanpinta"
    return tuple(str(p) for b in blocks if (p := root / f"{b.name}.laz").exists())


@lru_cache(maxsize=64)
def _reference(wkb, paths):
    from turku3d.lidar.decoded import columns, source

    piece = shapely.from_wkb(wkb)
    shapely.prepare(piece)
    x0, z0, x1, z1 = piece.bounds
    chunks = []
    for path in paths:
        las = columns(source(path))
        x, y, z = C.en_to_local(np.asarray(las.x), np.asarray(las.y), np.asarray(las.z))
        keep = (x >= x0) & (x <= x1) & (z >= z0) & (z <= z1)
        keep &= ~np.isin(np.asarray(las.classification), [3, 4, 5])
        chunks.append(np.column_stack([x[keep], y[keep], z[keep]]))
    return modal_surface(np.concatenate(chunks) if chunks else np.empty((0, 3)), piece)


def reference(piece):
    """The whole piece's modal surface, or NaN when the piece is too large to read whole."""
    paths = piece_blocks(piece)
    if not paths:
        return np.nan
    return _reference(piece.normalize().wkb, paths)


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
        self.fallback = fallback  # (pieces,) canonical piece surface, or the tile's own reading

    def owner(self, xz):
        """The nearest piece of each point."""
        xz = np.asarray(xz, float).reshape(-1, 2)
        points = shapely.points(xz)
        return np.argmin(np.stack([shapely.distance(points, p) for p in self.pieces]), axis=0)

    def at(self, xz):
        xz = np.asarray(xz, float).reshape(-1, 2)
        result = np.full(len(xz), LEVEL)
        if not self.pieces or not len(xz):
            return result
        owner = self.owner(xz)
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
        fallback[k] = reference(piece)
        if not near.any() or not len(points):
            continue
        inside = shapely.contains_xy(piece, points[:, 0], points[:, 2])
        shore = ~inside & (shapely.distance(xy, piece) <= SHORE)
        values = np.full(len(grid), np.nan)
        wet = points[inside]
        if len(wet) >= MIN_POINTS:
            clear = shapely.distance(xy[inside], piece.boundary) >= OFFSHORE
            tree = cKDTree(wet[:, [0, 2]])
            for n, ids in zip(np.flatnonzero(near), tree.query_ball_point(grid[near], RADIUS)):
                if len(ids) < MIN_POINTS:
                    continue
                ids = np.asarray(ids)
                inner = ids[clear[ids]]
                values[n] = np.percentile(wet[inner if len(inner) >= MIN_POINTS else ids, 1], QUANTILE)
            if not np.isfinite(fallback[k]):
                fallback[k] = np.percentile(wet[clear, 1] if clear.sum() >= MIN_POINTS else wet[:, 1], QUANTILE)
        bank = points[shore]
        missing = near & ~np.isfinite(values)
        if len(wet) < MIN_POINTS and not np.isfinite(fallback[k]) and len(bank) >= MIN_POINTS:
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


def stage(cache, tile, inputs):
    """The tile's level field, cached on its water, mapped steps and key-point blocks."""
    from pathlib import Path

    from turku3d.stage_cache import source_key

    if not inputs.water:
        return None
    roads = inputs.sources.get("roads", ())
    steps, rapids = features(roads[0] if roads else None)
    paths = lidar_paths(tile)
    wholes = sorted({p for piece in pieces(inputs.water, steps) for p in (piece_blocks(piece) or ())})
    identity = [(str(p), Path(p).stat().st_size, Path(p).stat().st_mtime_ns)
                for p in sorted({*map(str, paths), *wholes})]
    return cache.run(
        "water_levels", source_key(Path(__file__)), [tile, inputs.water, steps, rapids, identity],
        lambda: measure(tile, inputs.water, steps, paths, rapids),
    )


INLAND = 0.01  # m above the datum: an inland surface


def surface(levels, geometry):
    """Inland surface under a footprint or line (its lowest measured level), or None."""
    if levels is None or not levels.pieces or geometry is None or geometry.is_empty:
        return None
    samples = shapely.get_coordinates(shapely.segmentize(geometry, 2.0))
    if not len(samples):
        return None
    level = float(np.min(levels.at(samples)))
    return level if level > LEVEL + INLAND else None


def follow(inputs, levels):
    """Moored vessels, piers and bridge footings over inland water take its local surface.

    Prepared inputs carry the sea datum; above a dam a floating pier sits on the pool.
    Values are set from the local surface, so applying this twice changes nothing.
    """
    if levels is None:
        return
    for boat in inputs.boats or ():
        # Harbour cranes, masts and towers carry no waterline or deck.
        if boat.get("kind") not in ("pier", "ship") or "deck" not in boat or "water" not in boat:
            continue
        water = surface(levels, boat.get("outline"))
        if water is None:
            continue
        if boat.get("kind") == "pier":
            if boat.get("deck_source") in (None, "waterline-fallback") or boat["deck"] < water + 0.18:
                boat["deck"] = water + (0.55 if boat.get("floating") else 0.9)
        else:
            boat["deck"] = max(boat["deck"], water + 0.9)
        boat["water"] = water
    wet = shapely.union_all(inputs.water)
    for way in (inputs.roads or {}).get("bridge_ways", ()):
        line = way.get("line")
        if line is None or not line.intersects(wet):
            continue
        water = surface(levels, line.intersection(wet))
        if water is not None:
            way.setdefault("survey", {})["water_level"] = water
