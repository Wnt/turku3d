"""OSM ``railway=funicular``: an inclined track in a concrete trough, rails, sleepers and a car.

A funicular climbs at a steady grade between its stations, so the track does not drape
over the terrain grid. Its profile comes from the ground key points along the whole
mapped way (the same raw blocks for every tile it crosses, so the halves meet at a
seam): the median ground at each station, outside building footprints (a station roof
terrace is not the track), made monotonic from the lower end and smoothed. A station
building set into the slope at either end fixes that end at its exposed grade.

The trough is a bed slab with side walls from below the bed to the higher of a low
parapet and the terrain beside the track; where the terrain lies lower they continue
down to it as support walls. The terrain is cut along the trough, so a cutting shows
its walls instead of burying the bed. One car stands at the lower end, just outside
the station building. Everything is clipped to the owning tile.
"""

from __future__ import annotations

import json

import numpy as np
import shapely

STEP = 1.0  # profile station spacing along the way (m)
RADIUS = 1.6  # key points this close to a station describe its ground (m)
SMOOTH = 9  # moving-average window of the monotonic profile (stations)
BED = 3.0  # trough inner width (m)
WALL = 0.25  # trough wall thickness (m)
PARAPET = 0.55  # wall top above the bed where the terrain beside it is lower (m)
FOOT = 0.35  # walls and bed continue this far below the lower of bed and terrain (m)
SLEEPERS = 0.65  # sleeper spacing (m)
CAR = (4.2, 2.3, 2.6)  # car length, width, height (m)
DEFAULT_GAUGE = 1.0  # m, when the way has no gauge tag


def ways(path, bbox):
    """Funicular way lines (engine x, z) and tags reaching the bbox."""
    from turku3d.spatial import rows

    out = []
    for key, row in rows(path, bbox):
        tags = row.get("tags")
        tags = json.loads(tags) if isinstance(tags, str) else dict(tags or {})
        if tags.get("railway") != "funicular":
            continue
        line = shapely.force_2d(row["geometry_local"])
        if line.geom_type != "LineString" or line.length < 10:
            continue
        out.append((str(row.get("osm_id", key)), line, tags))
    return sorted(out, key=lambda w: w[0])


def ground(points, footprints, xz, radius=RADIUS):
    """Median key-point height around each xz outside footprints (NaN where none)."""
    from scipy.spatial import cKDTree

    out = np.full(len(xz), np.nan)
    if not len(points):
        return out
    keep = np.ones(len(points), bool)
    if footprints:
        blocked = shapely.union_all(footprints)
        keep = ~shapely.contains_xy(blocked, points[:, 0], points[:, 2])
    pts = points[keep]
    if not len(pts):
        return out
    tree = cKDTree(pts[:, [0, 2]])
    for i, hits in enumerate(tree.query_ball_point(xz, radius)):
        if hits:
            out[i] = float(np.median(pts[hits, 1]))
    return out


def monotonic(values):
    """Pool-adjacent-violators: the closest non-decreasing sequence (least squares)."""
    blocks = []
    for v in values:
        blocks.append([float(v), 1])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            v2, n2 = blocks.pop()
            v1, n1 = blocks.pop()
            blocks.append([(v1 * n1 + v2 * n2) / (n1 + n2), n1 + n2])
    return np.concatenate([np.full(n, v) for v, n in blocks])


def profile(line, points, buildings):
    """Stations (N,2), distances and the track bed height, lower end first."""
    s = np.linspace(0, line.length, max(2, int(np.ceil(line.length / STEP)) + 1))
    xz = np.array([line.interpolate(float(d)).coords[0] for d in s])
    footprints = [b.footprint for b in buildings]
    y = ground(points, footprints, xz)
    known = np.isfinite(y)
    if known.sum() < 2:
        return None
    y = np.interp(s, s[known], y[known])
    # The way's lower end is where the track starts to climb.
    if y[: len(y) // 4 + 1].mean() > y[-(len(y) // 4 + 1) :].mean():
        xz, s, y = xz[::-1], line.length - s[::-1], y[::-1]
    for end in (0, -1):
        point = shapely.Point(*xz[end])
        for b in buildings:
            evidence = b.attributes.get("set_into_slope")
            if evidence and b.footprint.distance(point) < 2.0:
                y[end] = evidence["exposed_grade"]
    y = monotonic(y)
    # Symmetric windows that shrink towards the ends keep the station levels exact.
    n = len(y)
    half = np.minimum(np.minimum(np.arange(n), n - 1 - np.arange(n)), SMOOTH // 2)
    total = np.r_[0.0, np.cumsum(y)]
    y = (total[np.arange(n) + half + 1] - total[np.arange(n) - half]) / (2 * half + 1)
    return xz, s, monotonic(y)


def offset(xz, distance):
    d = np.gradient(xz, axis=0)
    d /= np.maximum(np.linalg.norm(d, axis=1), 1e-9)[:, None]
    return xz + np.column_stack([-d[:, 1], d[:, 0]]) * distance


def car(key, xz, s, y, start):
    """A box cabin on the incline: level floor at its lower end, glazed upper half."""
    from turku3d.structures.geometry import Faces

    length, width, height = CAR
    a, b = start, start + length
    if b > s[-1]:
        return []
    pa = np.array([np.interp(a, s, xz[:, 0]), np.interp(a, s, xz[:, 1])])
    pb = np.array([np.interp(b, s, xz[:, 0]), np.interp(b, s, xz[:, 1])])
    ya, yb = np.interp(a, s, y) + 0.35, np.interp(b, s, y) + 0.35
    d = (pb - pa) / np.linalg.norm(pb - pa)
    n = np.array([-d[1], d[0]]) * width / 2
    corners = [pa + n, pb + n, pb - n, pa - n]
    bottom = [ya, yb, yb, ya]
    meshes = []
    for surface, low, high in (("steel", 0.0, 1.0), ("glass", 1.0, height - 0.25),
                               ("paint", height - 0.25, height)):
        f = Faces()
        ring = [[c[0], h + low, c[1]] for c, h in zip(corners, bottom, strict=True)]
        top = [[c[0], h + high, c[1]] for c, h in zip(corners, bottom, strict=True)]
        for i in range(4):
            j = (i + 1) % 4
            f.face([ring[i], ring[j], top[j], top[i]])
        if low == 0.0:
            f.face([ring[0], ring[3], ring[2], ring[1]])
        if high == height:
            f.face([top[0], top[1], top[2], top[3]])
        mesh = f.mesh(f"{key}:car:{surface}", "wall")
        mesh.metadata.update(surface=surface, kind="funicular")
        meshes.append(mesh)
    return meshes


def way_points(line, sources):
    """Ground key points (engine xyz) within reach of the whole way, from its raw blocks.

    Every tile the way crosses reads the same blocks, so their profiles agree.
    """
    from turku3d import config as C
    from turku3d.lidar.decoded import columns, source

    from .sources import NoCoverage

    x0, z0, x1, z1 = line.buffer(RADIUS + 3.0).bounds
    tiles = {C.VisualTile.at(x, z) for x in (x0, x1) for z in (z0, z1)}
    for x in np.arange(x0, x1 + 250, 250):
        for z in np.arange(z0, z1 + 250, 250):
            tiles.add(C.VisualTile.at(min(x, x1), min(z, z1)))
    paths = set()
    for t in tiles:
        try:
            found, _ = sources.lidar_paths(t)
        except NoCoverage:
            continue
        paths.update(found)
    corridor = line.buffer(RADIUS + 3.0)
    chunks = []
    for path in sorted(paths):
        las = columns(source(path))
        e, n, h = np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)
        x, y, z = C.en_to_local(e, n, h)
        near = (x >= x0) & (x <= x1) & (z >= z0) & (z <= z1)
        near &= ~np.isin(np.asarray(las.classification), [3, 4, 5])
        xyz = np.column_stack([x[near], y[near], z[near]])
        chunks.append(xyz[shapely.contains_xy(corridor, xyz[:, 0], xyz[:, 2])])
    return np.concatenate(chunks) if chunks else np.empty((0, 3))


def produce(tile, path, sources, buildings):
    """(meshes, ground openings) for every funicular reaching the tile."""
    from turku3d.structures.geometry import Faces, profile_wall

    from .bridge_details import wall as rail_wall
    from .geometry import clip_mesh

    if path is None:
        return [], []
    meshes, openings = [], []
    for way_id, line, tags in ways(path, tile.bbox.buffered(5)):
        points = way_points(line, sources)
        if len(points) < 4:
            continue
        result = profile(line, points, buildings)
        if result is None:
            continue
        xz, s, y = result
        key = f"funicular:{way_id}"
        parts = []
        half = BED / 2 + WALL / 2
        for side in (-1, 1):
            axis = offset(xz, side * half)
            beside = ground(points, [b.footprint for b in buildings], offset(xz, side * (half + 1.2)))
            beside = np.where(np.isfinite(beside), beside, y)
            bottom = np.minimum(y, beside) - FOOT
            top = np.maximum(y + PARAPET, beside + 0.1)
            wall = profile_wall(f"{key}:wall", axis, bottom, top, thickness=WALL)
            wall.metadata.update(surface="concrete", kind="funicular")
            parts.append(wall)
        bed = Faces()
        left, right = offset(xz, BED / 2), offset(xz, -BED / 2)
        for k in range(len(xz) - 1):
            bed.face([[left[k, 0], y[k], left[k, 1]], [left[k + 1, 0], y[k + 1], left[k + 1, 1]],
                      [right[k + 1, 0], y[k + 1], right[k + 1, 1]], [right[k, 0], y[k], right[k, 1]]])
        bed_mesh = bed.mesh(f"{key}:bed", "gravel")
        bed_mesh.metadata.update(surface="ballast", kind="funicular")
        parts.append(bed_mesh)
        gauge = DEFAULT_GAUGE
        try:
            gauge = float(tags.get("gauge", "")) / 1000 or DEFAULT_GAUGE
        except ValueError:
            pass
        for side in (-gauge / 2, gauge / 2):
            rail = rail_wall(key, offset(xz, side), y + 0.12, y + 0.27, 0.07, "steel", "railing")
            rail.metadata.update(collision=False, kind="funicular")
            parts.append(rail)
        for d in np.arange(0.5, s[-1] - 0.5, SLEEPERS):
            a, b = (np.array([np.interp(t, s, xz[:, 0]), np.interp(t, s, xz[:, 1])])
                    for t in (d - 0.2, d + 0.2))
            n = np.array([-(b - a)[1], (b - a)[0]]) / max(np.linalg.norm(b - a), 1e-9)
            p, h = (a + b) / 2, float(np.interp(d, s, y))
            sleeper = rail_wall(key, np.array([p - n * gauge * 0.8, p + n * gauge * 0.8]),
                                np.full(2, h + 0.02), np.full(2, h + 0.12), 0.22, "concrete", "gravel")
            sleeper.metadata.update(collision=False, kind="funicular")
            parts.append(sleeper)
        # The car waits at the lower station, just clear of its building.
        clear = [float(d) for d, p in zip(s, xz, strict=True)
                 if not any(b.footprint.distance(shapely.Point(*p)) < 0.5 for b in buildings)]
        if clear:
            parts.extend(car(key, xz, s, y, clear[0] + 0.5))
        for part in parts:
            clipped = clip_mesh(part, tile.bbox)
            if clipped is not None:
                clipped.metadata.update(part.metadata)
                meshes.append(clipped)
        corridor = shapely.LineString(xz).buffer(BED / 2 + WALL, cap_style="flat")
        openings.append(corridor.intersection(shapely.box(*tile.bbox.as_tuple())))
    return meshes, [o for o in openings if not o.is_empty]


def stats(meshes):
    return dict(
        ways=len({m.object_id.split(":")[1] for m in meshes if m.object_id.startswith("funicular:")}),
        triangles=sum(len(m.indices) for m in meshes if m.object_id.startswith("funicular:")),
    )
