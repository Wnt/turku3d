"""Bounded draft vegetation: mapped areas/rows, optional canopy peaks, Poisson fallback."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache

import numpy as np
import shapely

from turku3d.vegetation.trees import VARIANTS, Tree, deduplicate, variant

# Measured with runtime LOD at Kupittaa; retain the 5 m placement clearance.
TREE_CAP = 768
BUSH_CAP = 24
SPACING = 5.0
OPEN_MARGIN = 3.0
# Trees / square metre. Parks keep open lawns; scrub/heath stay low and sparse.
DENSITY = {
    "forest": 0.018,
    "wood": 0.018,
    "scrub": 0.006,
    "heath": 0.002,
    "park": 0.004,
    "garden": 0.006,
}


def open_surface(tags):
    """OSM sports/open grounds where area vegetation must not invent trees."""
    return (
        tags.get("indoor") != "yes"
        and tags.get("building", "no") == "no"
        and (
            tags.get("leisure")
            in {"track", "pitch", "stadium", "sports_centre", "playground", "recreation_ground"}
            or tags.get("landuse") == "recreation_ground"
            or tags.get("highway") == "raceway"
            or bool(set(tags.get("sport", "").split(";")) & {"bmx", "cycling"})
        )
    )


def open_outline(geom):
    """Fill closed way outlines; buffer open track lines and area edges by three metres."""
    parts = []
    for part in shapely.get_parts(geom):
        if part.geom_type == "LineString" and part.is_ring:
            part = shapely.make_valid(shapely.Polygon(part))
        if part.geom_type in ("Polygon", "MultiPolygon", "LineString", "MultiLineString"):
            parts.append(part.buffer(OPEN_MARGIN))
    return shapely.union_all(parts)


def read_areas(tile, path):
    from turku3d.spatial import rows

    window = shapely.box(*tile.bbox.as_tuple())
    result = []
    for i, row in rows(path, tile.bbox.buffered(OPEN_MARGIN)):
        tags = json.loads(row["tags"]) if row["tags"] else {}
        excluded = open_surface(tags)
        kind = next(
            (
                tags[k]
                for k in ("natural", "landuse", "leisure")
                if tags.get(k) in (*DENSITY, "tree_row")
            ),
            "",
        )
        if not excluded and kind not in (*DENSITY, "tree_row"):
            continue
        geom = shapely.force_2d(shapely.make_valid(row["geometry_local"]))
        if excluded:
            geom = open_outline(geom)
            if not geom.is_empty and geom.intersects(window):
                result.append((f"open:{i}", geom.intersection(window), "open", ""))
            continue
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


@lru_cache(maxsize=len(VARIANTS))
def trunk_base_radius(name):
    """Radius of the visible flared base in the same unit-radius mesh as instancing."""
    from turku3d.vegetation.visual import prototypes

    bases = [
        mesh.positions[mesh.positions[:, 1] <= 0.1][:, [0, 2]]
        for mesh in prototypes(name)
        if mesh.extras["material_key"] == "tree_bark"
    ]
    return float(np.linalg.norm(np.concatenate(bases), axis=1).max())


def path_lines(roads):
    """Foot/cycle-class graph lines; car-road clearance remains the existing two metres."""
    from turku3d.roads import CLASS_SHIFT
    from turku3d.roads.layer import graph_ways

    if not roads or not roads.get("json"):
        return []
    return [
        shapely.LineString(coords)
        for coords, flags in graph_ways(json.loads(roads["json"]))
        if flags >> CLASS_SHIFT == 0
    ]


def clears_path(tree, paths):
    """Keep the visible trunk outside ±2 m and the crown outside the inner ±1 m."""
    if paths.is_empty:
        return True
    distance = paths.distance(shapely.Point(tree.x, tree.z))
    return distance >= max(
        2.0 + trunk_base_radius(tree.variant) * tree.crown_radius,
        1.0 + tree.crown_radius,
    )


def path_surfaces(inputs):
    """Expose forest paths in visual ground only, preserving terrain/structural caches."""
    from turku3d.col.materials import mat_id

    forest = shapely.union_all([
        geom for _, geom, kind, _ in inputs.forests if kind in DENSITY
    ])
    if forest.is_empty:
        return []
    corridors = polygonal(shapely.union_all(path_lines(inputs.roads)).buffer(1.25).intersection(forest))
    if corridors.is_empty:
        return []
    # Gravel fallback for forest tracks; retain mapped asphalt/paving/soil where available.
    mapped = [
        (polygonal(geom.intersection(corridors)), material)
        for geom, material in inputs.surfaces
        if material != mat_id("grass") and geom.intersects(corridors)
    ]
    return [(corridors, mat_id("gravel")), *[(g, m) for g, m in mapped if not g.is_empty]]


def polygonal(geom):
    # Snapped, valid, area-only regions: raw buffer/intersection slivers made the surface overlay
    # in split_boundaries fail with GEOS "Unable to determine overlay result geometry dimension".
    geom = shapely.make_valid(shapely.set_precision(geom, 0.01))
    parts = [g for g in shapely.get_parts(geom) if g.geom_type in ("Polygon", "MultiPolygon") and g.area > 1e-4]
    return shapely.union_all(parts) if parts else shapely.Polygon()


def path_ground(tile, ortho, regions):
    """Replace aerial forest-floor foliage over mapped paths with their ground material."""
    from turku3d.col.materials import mat_id

    from .surfaces import BINDINGS, fallback

    if not regions:
        return ortho
    output = ortho.copy()
    rows, cols = np.indices(ortho.shape[:2])
    x = tile.origin[0] + (cols + 0.5) * tile.size / ortho.shape[1]
    z = tile.origin[2] + (rows + 0.5) * tile.size / ortho.shape[0]
    channels = {mat_id(key): channel for key, channel in BINDINGS.items()}
    for geom, material in regions:
        mask = shapely.intersects_xy(geom, x, z)
        texture = fallback(channels[material])
        # Match the existing ground fallback's world-anchored 128 px/metre sampling.
        iy = np.floor(z[mask] * 128).astype(int) % texture.shape[0]
        ix = np.floor(x[mask] * 128).astype(int) % texture.shape[1]
        output[mask] = texture[iy, ix]
    return output


def produce(tile, inputs, height_at, mapped, carried=None):
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
    paths = shapely.union_all(path_lines(roads))
    generated_blocked = shapely.union_all(
        [blocked, *(g for _, g, kind, _ in inputs.forests if kind == "open")]
    )
    trees = [
        t for t in mapped
        if not blocked.covers(shapely.Point(t.x, t.z)) and clears_path(t, paths)
    ][:TREE_CAP]
    bushes = []
    canopy = getattr(inputs, "canopy", ())
    for identity, geom, kind, leaf in inputs.forests:
        if kind == "open":
            continue
        placement_blocked = blocked if kind == "tree_row" else generated_blocked
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
                    geom,
                    DENSITY[kind],
                    placement_blocked,
                    seed,
                    occupied=[(t.x, t.z) for t in trees],
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
            if placement_blocked.covers(shapely.Point(x, z)) or any(
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
            if clears_path(t, paths):
                trees.append(t)
        if kind in ("forest", "wood", "scrub") and len(bushes) < BUSH_CAP:
            edge = geom.intersection(geom.boundary.buffer(3))
            for x, z in scatter(
                edge,
                0.008,
                generated_blocked,
                seed + 1,
                spacing=3,
                limit=BUSH_CAP - len(bushes),
                occupied=[(t.x, t.z) for t in trees + bushes],
            ):
                bush = Tree(
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
                if clears_path(bush, paths):
                    bushes.append(bush)
    if carried is not None and not carried.is_empty:
        # A trunk outside the road is insufficient: its complete crown must clear the deck.
        trees = [t for t in trees if carried.distance(shapely.Point(t.x, t.z)) > t.crown_radius]
        bushes = [t for t in bushes if carried.distance(shapely.Point(t.x, t.z)) > t.crown_radius]
    return deduplicate(trees, SPACING), bushes


def trunks(trees, ct):
    """Trunk cylinders of the trees standing in collision tile ct (tile-local centres)."""
    from turku3d import config as C
    from turku3d.col.format import Primitive
    from turku3d.col.materials import mat_id

    result = []
    for t in trees:
        if C.CollisionTile.at(t.x, t.z) != ct:
            continue
        height = min(t.height * 0.55, 8.0)
        centre = (t.x - ct.origin[0], t.y + height / 2, t.z - ct.origin[2])
        result.append(Primitive.cylinder(mat_id("tree_trunk"), centre, height / 2, t.trunk_radius))
    return result


def draft_trees(tile, grid_pos, trees, water, shells):
    """OSM trees on dry ground outside buildings, as vegetation layer trees (unmeasured)."""
    from .inputs import CELLS
    from .surfaces import grid_height

    heights = grid_pos[:, 1].reshape(CELLS + 1, CELLS + 1)

    blocked = shapely.union_all([*water, *(b.footprint for b, _ in shells)])
    result = []
    for object_id, x, z, height, conifer in trees:
        if not blocked.is_empty and blocked.covers(shapely.Point(x, z)):
            continue
        tree = Tree(object_id, x, z, "osm", y=grid_height(tile, heights, x, z), height=height)
        tree.crown_radius = float(np.clip(height * 0.3, 1.5, 7.0))
        tree.trunk_radius = float(np.clip(height * 0.018, 0.09, 0.55))
        tree.variant = VARIANTS[3] if conifer else variant("", height)
        result.append(tree)
    return result
