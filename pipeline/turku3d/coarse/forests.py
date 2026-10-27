"""Bounded draft vegetation: mapped areas/rows, optional canopy peaks, Poisson fallback."""

from __future__ import annotations

import hashlib
import json

import numpy as np
import shapely

from turku3d.vegetation.trees import VARIANTS, Tree, deduplicate, variant

TREE_CAP = 192
BUSH_CAP = 24
SPACING = 5.0
# Trees / square metre. Parks keep open lawns; scrub/heath stay low and sparse.
DENSITY = {
    "forest": 0.018,
    "wood": 0.018,
    "scrub": 0.006,
    "heath": 0.002,
    "park": 0.004,
    "garden": 0.006,
}


def read_areas(tile, path):
    import pyarrow.parquet as pq

    window = shapely.box(*tile.bbox.as_tuple())
    result = []
    for i, row in enumerate(pq.read_table(path, columns=["tags", "geometry_local"]).to_pylist()):
        tags = json.loads(row["tags"]) if row["tags"] else {}
        kind = next(
            (
                tags[k]
                for k in ("natural", "landuse", "leisure")
                if tags.get(k) in (*DENSITY, "tree_row")
            ),
            "",
        )
        if kind not in (*DENSITY, "tree_row"):
            continue
        geom = shapely.force_2d(shapely.make_valid(shapely.from_wkb(row["geometry_local"])))
        if not geom.intersects(window):
            continue
        if kind == "tree_row" and geom.geom_type in ("LineString", "MultiLineString"):
            geom = geom.intersection(window)
        elif geom.geom_type in ("Polygon", "MultiPolygon"):
            geom = geom.intersection(window)
        else:
            continue
        result.append((f"area:{i}", geom, kind, tags.get("leaf_type", "")))
    return result


def scatter(geometry, density, blocked, seed, spacing=SPACING, limit=TREE_CAP, occupied=()):
    """Seeded rejection Poisson-disc samples with bounded work and a strict clearance radius."""
    rng = np.random.default_rng(seed)
    target = min(limit, int(geometry.area * density))
    if target <= 0 or geometry.is_empty:
        return []
    allowed = geometry.difference(blocked)
    shapely.prepare(allowed)
    cells = {}

    def insert(x, z):
        cell = (int(np.floor(x / spacing)), int(np.floor(z / spacing)))
        for i in range(cell[0] - 1, cell[0] + 2):
            for j in range(cell[1] - 1, cell[1] + 2):
                if any((x - a) ** 2 + (z - b) ** 2 < spacing**2 for a, b in cells.get((i, j), ())):
                    return False
        cells.setdefault(cell, []).append((x, z))
        return True

    for x, z in occupied:
        cell = (int(np.floor(x / spacing)), int(np.floor(z / spacing)))
        cells.setdefault(cell, []).append((x, z))
    x0, z0, x1, z1 = geometry.bounds
    result = []
    for x, z in rng.uniform([x0, z0], [x1, z1], size=(max(64, target * 30), 2)):
        if allowed.contains(shapely.Point(x, z)) and insert(x, z):
            result.append((float(x), float(z)))
            if len(result) >= target:
                break
    return result


def produce(tile, inputs, height_at, mapped):
    """Reuse vegetation Tree/prototypes; only trees (never edge bushes) receive trunk collision."""
    blocked = shapely.union_all(
        [
            *inputs.water,
            *(b.footprint.buffer(1) for b in inputs.buildings),
            *(g.buffer(1) for g, _ in inputs.streets),
        ]
    )
    # OSM paths cover park paths absent from the city street polygons.
    roads = inputs.roads
    if roads and roads.get("json"):
        graph = json.loads(roads["json"])
        nodes = np.asarray(graph["nodes"]).reshape(-1, 2)
        paths = []
        for edge in graph["edges"]:
            coords = [nodes[edge[0]], *np.asarray(edge[4:]).reshape(-1, 2), nodes[edge[1]]]
            paths.append(shapely.LineString(coords).buffer(2))
        blocked = shapely.union_all([blocked, *paths])
    trees = [t for t in mapped if not blocked.covers(shapely.Point(t.x, t.z))][:TREE_CAP]
    bushes = []
    canopy = getattr(inputs, "canopy", ())
    for identity, geom, kind, leaf in inputs.forests:
        seed = int(hashlib.sha256(f"{tile.id}:{identity}".encode()).hexdigest()[:8], 16)
        rng = np.random.default_rng(seed)
        candidates = []
        if kind == "tree_row":
            for d in np.arange(0, geom.length, 8):
                p = geom.interpolate(d)
                candidates.append((p.x, p.y, 10.0, False))
        else:
            # Highest classified return per 5 m cell approximates canopy peaks.
            peaks = {}
            for x, top, z in canopy:
                h = top - height_at(x, z)
                if 3 <= h <= 35 and geom.contains(shapely.Point(x, z)):
                    cell = (int(x // SPACING), int(z // SPACING))
                    if cell not in peaks or h > peaks[cell][2]:
                        peaks[cell] = (x, z, h, True)
            candidates.extend(sorted(peaks.values(), key=lambda p: -p[2]))
            if not candidates:
                points = scatter(
                    geom, DENSITY[kind], blocked, seed, occupied=[(t.x, t.z) for t in trees]
                )
                candidates = [
                    (
                        x,
                        z,
                        float(
                            rng.uniform(7, 18) if kind in ("forest", "wood") else rng.uniform(3, 11)
                        ),
                        False,
                    )
                    for x, z in points
                ]
        for x, z, height, measured in candidates:
            if not (tile.bbox.xmin <= x < tile.bbox.xmax and tile.bbox.zmin <= z < tile.bbox.zmax):
                continue
            if len(trees) >= TREE_CAP:
                break
            if blocked.covers(shapely.Point(x, z)) or any(
                np.hypot(t.x - x, t.z - z) < SPACING for t in trees
            ):
                continue
            conifer = leaf == "needleleaved" or (leaf != "broadleaved" and rng.random() < 0.4)
            t = Tree(
                f"{identity}:{x:.2f}:{z:.2f}",
                x,
                z,
                "lidar" if measured else "osm-area",
                y=height_at(x, z),
                height=height,
                measured=measured,
            )
            t.crown_radius = float(np.clip(height * 0.28, 1.2, 6))
            t.trunk_radius = float(np.clip(height * 0.018, 0.09, 0.55))
            t.variant = VARIANTS[3] if conifer else variant("", height)
            trees.append(t)
        if kind in ("forest", "wood", "scrub") and len(bushes) < BUSH_CAP:
            edge = geom.intersection(geom.boundary.buffer(3))
            for x, z in scatter(
                edge,
                0.008,
                blocked,
                seed + 1,
                spacing=3,
                limit=BUSH_CAP - len(bushes),
                occupied=[(t.x, t.z) for t in trees + bushes],
            ):
                bushes.append(
                    Tree(
                        f"bush:{identity}:{x:.2f}:{z:.2f}",
                        x,
                        z,
                        "bush",
                        y=height_at(x, z),
                        height=float(rng.uniform(0.7, 1.6)),
                        crown_radius=1.1,
                        trunk_radius=0,
                        variant=VARIANTS[0],
                    )
                )
    return deduplicate(trees, SPACING), bushes
