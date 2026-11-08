"""Bounded draft vegetation: mapped areas/rows, optional canopy peaks, Poisson fallback."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache

import numpy as np
import shapely

from turku3d.vegetation.trees import VARIANTS, Tree, deduplicate, variant

from .forest_paths import path_corridors, path_lines
from .tree_inputs import REGISTER_TREE

# Every measured crown of a closed forest (Nunnavuori: 1367 per 250 m tile); the client's per-tier instance budgets
# (render/quality.ts) bound the drawing cost, not this cap.
TREE_CAP = 2048
BUSH_CAP = 24
SPACING = 5.0  # the placement clearance of generated trees
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
    """OSM sports/open grounds and beaches where area vegetation must not invent trees."""
    if tags.get("natural") == "beach" or tags.get("leisure") == "beach_resort":
        return True
    return (
        tags.get("indoor") != "yes"
        and tags.get("building", "no") == "no"
        # Recreation land use can describe an entire wooded park, not a playing surface.
        and not any(tags.get(k) in DENSITY for k in ("natural", "landuse", "leisure"))
        and (
            tags.get("leisure")
            in {"track", "pitch", "stadium", "sports_centre", "playground", "recreation_ground"}
            or tags.get("landuse") == "recreation_ground"
            or tags.get("highway") == "raceway"
            or bool(set(tags.get("sport", "").split(";")) & {"bmx", "cycling"})
        )
    )


SURFACES = {"track", "pitch", "stadium", "playground"}
# Area kinds that keep invented trees out: playing surfaces ("open") and the sports/recreation grounds around them
# ("grounds"). Measured lidar crowns are real trees; only a playing surface rules them out.
OPEN_KINDS = ("open", "grounds")


def open_kind(tags):
    """"open" for a playing surface, "grounds" for the sports centre or recreation ground around it (often wooded:
    Impivaara, Kupittaa, the disc-golf parks, ski hills)."""
    sports = set(tags.get("sport", "").split(";"))
    # A beach invents no trees; the lidar's crowns on it (the shade trees at its back) are real and stay.
    if tags.get("natural") == "beach" or tags.get("leisure") == "beach_resort":
        return "grounds"
    surface = tags.get("leisure") in SURFACES or tags.get("highway") == "raceway" or "bmx" in sports
    return "open" if surface else "grounds"


def trail(tags):
    """A closed way that is a path, not an area: a fitness or ski loop (``leisure=track`` + ``highway=footway``,
    ``area=no``; Nunnavuoren kuntorata rings a 20 ha pine forest). Only its line excludes trees."""
    return tags.get("area") == "no" or (bool(tags.get("highway")) and tags.get("highway") != "raceway")


def open_outline(geom, line=False):
    """Fill closed way outlines (a trail's stays a line); buffer open track lines and area edges by three metres."""
    parts = []
    for part in shapely.get_parts(geom):
        if part.geom_type == "LineString" and part.is_ring and not line:
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
            geom = open_outline(geom, trail(tags))
            if not geom.is_empty and geom.intersects(window):
                result.append((f"open:{i}", geom.intersection(window), open_kind(tags), ""))
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


def scatter(geometry, density, blocked, seed, spacing=SPACING, limit=TREE_CAP, occupied=(), keep=None):
    """Seeded rejection Poisson-disc samples with bounded work and a strict clearance radius. ``keep``: a vectorised
    (x, z) -> bool mask (``canopy_trees.cover``); samples only where it holds, the target scaled by its share."""
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
    candidates = rng.uniform([x0, z0], [x1, z1], size=(max(64, target * 30), 2))
    if keep is not None:
        inside = shapely.contains_xy(allowed, candidates[:, 0], candidates[:, 1])
        kept = inside & keep(candidates[:, 0], candidates[:, 1])
        target = int(target * kept.sum() / max(1, inside.sum()))
        candidates = candidates[kept]
    for x, z in candidates:
        if (keep is not None or allowed.contains(shapely.Point(x, z))) and insert(x, z):
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


def clears_path(tree, paths):
    """Keep the visible trunk outside the path footprint; crowns may overhang it."""
    if paths.is_empty:
        return True
    distance = paths.distance(shapely.Point(tree.x, tree.z))
    return distance >= trunk_base_radius(tree.variant) * tree.crown_radius


def car_corridors(roads, surveyed=()):
    """Centreline lane buffers, and the rendered carriageway where no survey covers it.

    Surveyed carriageways (GIS:Katualueet) own their kerbs, verges and median
    holes. Elsewhere (bridges, new or private streets, tile areas without the
    survey) the drawn road is the graph's carriageway width, so trees clear all
    of it, not only two metres around the centreline. Tunnels do not occupy the
    ground above them.
    """
    from turku3d.roads import CLASS_SHIFT
    from turku3d.roads.attributes import EDGE_FLAGS

    lanes, unsurveyed = [], []
    if not roads or not roads.get("json"):
        return lanes, unsurveyed
    graph = json.loads(roads["json"])
    nodes = np.asarray(graph["nodes"]).reshape(-1, 2)
    columns = {name: i for i, name in enumerate(graph.get("edgeAttrColumns", ()))}
    attributes = graph.get("edgeAttrs") or [None] * len(graph["edges"])
    survey = shapely.union_all(list(surveyed))
    for edge, attrs in zip(graph["edges"], attributes, strict=True):
        if edge[2] >> CLASS_SHIFT == 0:
            continue
        coords = [nodes[edge[0]], *np.asarray(edge[4:]).reshape(-1, 2), nodes[edge[1]]]
        line = shapely.LineString(coords)
        lanes.append(line.buffer(2))
        if attrs is None or "width" not in columns:
            continue
        half, flags = float(attrs[columns["width"]]) / 2, int(attrs[columns["flags"]])
        if half <= 2 or flags & EDGE_FLAGS["TUNNEL"]:
            continue
        # A yard or parking drive without a tagged width is drawn narrower than
        # its two default lanes; its centreline clearance already covers it.
        if edge[2] >> CLASS_SHIFT == 1 and not flags & (
            EDGE_FLAGS["WIDTH_TAGGED"] | EDGE_FLAGS["LANES_TAGGED"]
        ):
            continue
        # A centreline within half a carriageway of the survey is that street:
        # OSM centrelines stray into median holes and onto kerbs.
        free = line.difference(survey.buffer(half)) if not survey.is_empty else line
        if free.length > 0.5:
            unsurveyed.append(free.buffer(half, cap_style="flat"))
    return lanes, unsurveyed


def built_surfaces(inputs):
    """Solid wall bases and supported ground decks, preserving planting holes.

    Crown maxima can belong to a neighbouring tree overhanging a wall. A crown
    is not evidence that its inferred trunk can stand inside that structure.
    Suspended balconies do not own the ground beneath them.
    """
    from .fences import STYLES, style

    walls = [
        geom.buffer(STYLES[style(tags)][1] / 2)
        for geom, tags, _ in (*inputs.barriers, *inputs.retaining)
        if tags.get("barrier") in ("wall", "city_wall", "retaining_wall")
    ]
    decks = [r["region"] for r in inputs.attachments if r["kind"] in ("terrace", "tread")]
    return shapely.union_all([*walls, *decks])


def clears_structure(tree, structures):
    """A trunk needs soil under its column; roots/crowns may overhang a low wall."""
    return structures.is_empty or structures.distance(shapely.Point(tree.x, tree.z)) > tree.trunk_radius


def structure_clearance(trees, inputs):
    """Shared final placement validity for draft sidecars and full tile geometry."""
    structures = built_surfaces(inputs)
    return [tree for tree in trees if clears_structure(tree, structures)]


def produce(tile, inputs, height_at, mapped, carried=None):
    """Reuse vegetation Tree/prototypes; only trees (never edge bushes) receive trunk collision."""
    blocked = shapely.union_all(
        [
            *inputs.water,
            *(b.footprint.buffer(1) for b in inputs.buildings),
            *(g.buffer(1) for g, _ in inputs.streets),
        ]
    )
    # Centreline clearance everywhere; unsurveyed streets clear their rendered
    # carriageway. Foot/cycle paths use their actual mapped footprint.
    lanes, unsurveyed = car_corridors(inputs.roads, inputs.vegetation_roads)
    blocked = shapely.union_all([blocked, *lanes])
    # Crown maxima are not surveyed trunks: an overhanging crown can peak over a
    # driving lane, well outside the old 2 m centreline buffer. Every source must
    # clear the complete carriageway, including its visible flared trunk base.
    paths = shapely.union_all([path_corridors(inputs), *inputs.vegetation_roads, *unsurveyed])
    blocked = shapely.union_all([blocked, paths])
    # A real trunk (a surveyed one, a measured lidar crown) stands where the survey saw it (a tree grate in a
    # pavement, a crown over a square): it clears the carriageways and the walked line of a mapped foot/cycle
    # path, not the path's estimated buffered width. The final trunk clearance (vegetation.clearance) places
    # it against the drawn paving, as it documents.
    walked = shapely.union_all([*path_lines(inputs.roads), *inputs.vegetation_roads, *unsurveyed])
    generated_blocked = shapely.union_all(
        [blocked, *(g for _, g, kind, _ in inputs.forests if kind in OPEN_KINDS)]
    )
    # Register trees are surveyed street and park trees: they stand in verges, tree grates and
    # squares, so only buildings, water and transport corridors block them,
    # never the street-part areas around them.
    surveyed = shapely.union_all([*inputs.water, *(b.footprint for b in inputs.buildings), *lanes])
    trees = [
        t
        for t in mapped
        if not (surveyed if t.object_id.startswith(REGISTER_TREE) else blocked).covers(
            shapely.Point(t.x, t.z)
        )
        and clears_path(t, walked if t.object_id.startswith(REGISTER_TREE) else paths)
    ][:TREE_CAP]
    # Ground key points usually contain no canopy. Use the full classified source,
    # reduced once per source hash and cached independently of the terrain material.
    from .canopy_trees import CONIFERS, cover, inventory, leaf_type_at

    measured_canopy = getattr(inputs, "vegetation_canopy", ())
    measured_blocked = shapely.union_all([
        surveyed, walked, *(b.footprint.buffer(1) for b in inputs.buildings),
        # Only a playing surface: crowns measured over a sports centre's or ski hill's woods are its trees.
        *(g for _, g, kind, _ in inputs.forests if kind == "open"),
    ])
    # Leaf type from the lidar: measured crowns, and the fill around them (canopy_trees.leaf_type_at).
    leaf_type = leaf_type_at(getattr(inputs, "vegetation_leaves", ()), height_at)
    trees = inventory(tile, measured_canopy, trees, height_at, measured_blocked, paths, walked, leaf_type)[:TREE_CAP]
    # With measured canopy in the tile, fills stand only under it: lidar clearings stay open.
    under_canopy = cover(tile, measured_canopy, height_at) if len(measured_canopy) else None
    bushes = []
    canopy = getattr(inputs, "canopy", ())
    for identity, geom, kind, leaf in inputs.forests:
        if kind in OPEN_KINDS:
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
                    keep=under_canopy,
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
            # The measured foliage where there is any, else the mapped leaf type, else a 40 % pine mix.
            measured_leaf = leaf_type(x, z)
            conifer = measured_leaf in CONIFERS if measured_leaf is not None else (
                leaf == "needleleaved" or (leaf != "broadleaved" and rng.random() < 0.4))
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
            t.species = CONIFERS.get(measured_leaf, "")
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
    measured_shrubs = getattr(inputs, "vegetation_shrubs", ())
    if len(measured_shrubs):
        # The lidar measured the tile's shrubs (class 4): bushes stand where it saw them, in parks, yards, scrub and
        # along forest edges, instead of the guessed edge bushes.
        from .canopy_trees import shrubs

        shrub_blocked = shapely.union_all([blocked, measured_blocked])
        bushes = [b for b in shrubs(tile, measured_shrubs, measured_canopy, height_at, shrub_blocked,
                                    [(t.x, t.z) for t in trees]) if clears_path(b, paths)]
    if carried is not None and not carried.is_empty:
        # A trunk outside the road is insufficient: its complete crown must clear the deck.
        trees = [t for t in trees if carried.distance(shapely.Point(t.x, t.z)) > t.crown_radius]
        bushes = [t for t in bushes if carried.distance(shapely.Point(t.x, t.z)) > t.crown_radius]
    # Crown fitting changes trunk dimensions after candidate admission. Apply the
    # final column clearance to every source before visual and collision output.
    trees = structure_clearance(trees, inputs)
    trees = deduplicate(trees, 3.5 if len(measured_canopy) else SPACING)
    return trunk_clearance(tile, inputs, height_at, trees, bushes)


def trunk_clearance(tile, inputs, height_at, trees, bushes):
    """The final step: no trunk on drawn paving (``vegetation.clearance``, also ``path-trees@1``)."""
    from turku3d.vegetation.clearance import apply_to_trees

    walked = getattr(inputs, "trunk_ground", ())
    if not walked:
        return trees, bushes
    blocked = [*inputs.water, *(b.footprint for b in inputs.buildings)]
    kept = apply_to_trees([*trees, *bushes], walked, blocked, tile.bbox.as_tuple(), height_at,
                          trunk_base_radius)
    return [t for t in kept if t.source != "bush"], [t for t in kept if t.source == "bush"]


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
    for object_id, x, z, height, conifer, *species in trees:
        if not blocked.is_empty and blocked.covers(shapely.Point(x, z)):
            continue
        tree = Tree(
            object_id, x, z, "register" if object_id.startswith(REGISTER_TREE) else "osm",
            species=species[0] if species else "havupuu" if conifer else "",
            y=grid_height(tile, heights, x, z), height=height,
        )
        tree.crown_radius = float(np.clip(height * 0.3, 1.5, 7.0))
        tree.trunk_radius = float(np.clip(height * 0.018, 0.09, 0.55))
        tree.variant = VARIANTS[3] if conifer else variant(tree.species, height)
        result.append(tree)
    return result
