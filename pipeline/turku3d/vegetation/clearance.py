"""Tree trunks off walked and driven ground: one city-wide rule for the generator and ``path-trees@1``.

The ground a player walks or drives on is what the tile draws as paving, not only the street graph:
private driveways, service drives and yard paths (``access=private`` ways leave the graph, so the
minimap and the older path clearance never saw them) are drawn by ``coarse.surfaces.osm_surfaces``
with the same corridor widths used here. The city's street areas (GIS:Katualueet) add surveyed
carriageways, sidewalks, cycle paths, driveway mouths, squares and stop areas; medians and planting
strips (Välikaista), leased terraces and unclassified parts are not walked ground.

A trunk whose visible base comes within ``CLEARANCE`` of that ground is
- moved to the nearest valid spot within ``MOVE`` when it is a real tree (a register tree, a mapped
  OSM tree or a supported lidar crown), its crown still over its measured place (trees-lidar measures
  the lean from the new trunk);
- dropped otherwise (area scatter, canopy-peak fill and bushes are not evidence of a stem there),
  and dropped too when a real tree has no valid spot within reach, except a register tree, which is a
  surveyed stem (tree grates in squares and sidewalks) and stays where the survey put it.

Crowns may overhang the paving.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass

import numpy as np
import shapely
from shapely.ops import nearest_points

CLEARANCE = 0.5  # m between the visible trunk base and the edge of walked ground
MOVE = 2.5  # m a real trunk may shift to reach valid ground
NEIGHBOUR = 2.0  # m: a moved trunk this close to another tree is that tree
REGISTER, MAPPED, CROWN = "puut:", "osm:", "lidar:"
REAL = (REGISTER, MAPPED, CROWN)
# GIS:Katualueet Katuosalaji parts a player walks, cycles or drives on.
WALKED_PARTS = frozenset({
    "Ajorata", "Jalkakäytävä", "Pyörätie", "Yhdistetty kevyen liikenteen väylä", "Pyöräkaista",
    "Tonttiliittymä", "Portaat", "Silta", "Tasoristeys", "Muu liikennealue", "Odotustila", "Kävelytie",
    "Pelastustie/Huoltotie", "Alikulkukäytävä", "Joukkoliikennekatu",
})
WINDOW = 12.0  # m read around the tile so trunks near its edge see the neighbour's paving


def way_width(highway: str | None, tags: dict) -> float:
    """Drawn corridor width of an OSM way (``coarse.surfaces.osm_surfaces``, ``forest_paths.read_paths``)."""
    default = (2.5 if highway in ("path", "footway", "steps", "cycleway")
               else 4 if highway in ("service", "track") else 6)
    try:
        width = float(str(tags.get("width", default)).removesuffix(" m"))
    except ValueError:
        width = default
    return width if np.isfinite(width) and 0.3 <= width <= 30 else default


def on_ground(tags: dict) -> bool:
    """A feature drawn on the terrain: no bridge, tunnel (building passages excepted), building or layer."""
    return (tags.get("bridge", "no") == "no"
            and tags.get("tunnel", "no") in ("no", "building_passage")
            and not tags.get("building")
            and tags.get("layer", "0") in ("0", ""))


def walked_ground(tile, osm, streets) -> list:
    """Walked and driven ground polygons over the tile (plus ``WINDOW``), from OSM and GIS:Katualueet."""
    from turku3d.roads import CAR_CLASS, FOOT_ONLY
    from turku3d.spatial import frame, rows

    bbox = tile.bbox.buffered(WINDOW)
    window = shapely.box(*bbox.as_tuple())
    survey, parts = [], []
    if streets is not None:
        f = frame(streets, bbox)
        if len(f) and "Katuosalaji" in f:
            for g, kind in zip(f.geometry_local, f["Katuosalaji"], strict=True):
                if g is None or kind not in WALKED_PARTS:
                    continue
                g = shapely.make_valid(shapely.force_2d(g))
                parts.append(g)
                if kind == "Ajorata":
                    survey.append(g)
    surveyed = shapely.union_all(survey).buffer(0) if survey else shapely.Polygon()
    if osm is not None:
        for _, row in rows(osm, bbox.buffered(30)):
            tags = json.loads(row["tags"] or "{}")
            highway = tags.get("highway")
            geom = row["geometry_local"]
            if geom is None or not on_ground(tags):
                continue
            geom = shapely.force_2d(shapely.make_valid(geom))
            if (tags.get("amenity") == "parking" and tags.get("parking") not in ("multi-storey", "underground")
                    and not highway):
                if geom.geom_type == "LineString" and geom.is_ring:
                    geom = shapely.make_valid(shapely.Polygon(geom))
                if geom.geom_type in ("Polygon", "MultiPolygon"):
                    parts.append(geom)
                continue
            if highway not in CAR_CLASS and highway not in FOOT_ONLY:
                continue
            if geom.geom_type == "LineString" and geom.is_ring and tags.get("area") == "yes":
                parts.append(shapely.make_valid(shapely.Polygon(geom)))
                continue
            if geom.geom_type not in ("LineString", "MultiLineString"):
                continue
            half = way_width(highway, tags) / 2
            # A car centreline within half a carriageway of the survey is that surveyed street:
            # the survey owns its kerbs, verges and median holes.
            if highway in CAR_CLASS and not surveyed.is_empty:
                geom = geom.difference(surveyed.buffer(half))
                if geom.length <= 0.5:
                    continue
            parts.append(geom.buffer(half, cap_style="flat"))
    result = []
    for g in parts:
        if g.intersects(window):
            g = g.intersection(window)
            if g.area > 1e-3:
                result.append(g)
    return result


@dataclass
class Trunk:
    """What the rule needs of one drawn tree; ``index`` is the caller's handle."""

    index: object
    object_id: str
    x: float
    z: float
    base: float  # visible trunk base radius (m)

    @property
    def real(self) -> bool:
        return self.object_id.startswith(REAL)


def need(trunk: Trunk) -> float:
    return trunk.base + CLEARANCE


def place(trunks: list[Trunk], walked, blocked=None, bounds=None, stats=None):
    """``{index: None (drop) | (x, z) (move)}`` for trunks on walked ground; the others stay.

    ``blocked`` (buildings, water) bounds where a moved trunk may go; ``bounds`` (xmin, zmin, xmax,
    zmax) keeps a moved trunk in its tile. Trees are visited in object-id order so the generator's
    list order and a tile's per-variant instance order give the same result.
    """
    stats = Counter() if stats is None else stats
    walked = shapely.union_all(list(walked)) if isinstance(walked, (list, tuple)) else walked
    if walked is None or walked.is_empty or not trunks:
        return {}
    shapely.prepare(walked)
    blocked = shapely.union_all(list(blocked or ())) if not hasattr(blocked, "geom_type") else blocked
    forbidden = shapely.union_all([walked, blocked]) if blocked is not None and not blocked.is_empty else walked
    box = shapely.box(*bounds) if bounds is not None else None
    positions = {t.index: (t.x, t.z) for t in trunks}
    result = {}
    for trunk in sorted(trunks, key=lambda t: (t.object_id, t.x, t.z)):
        p = shapely.Point(trunk.x, trunk.z)
        if walked.distance(p) >= need(trunk):
            continue
        stats["on_walked"] += 1
        target = None
        if trunk.real:
            target = _nearest_valid(p, forbidden, need(trunk), box)
            if target is not None and any(
                    i != trunk.index and np.hypot(x - target[0], z - target[1]) < NEIGHBOUR
                    for i, (x, z) in positions.items()):
                stats["move_onto_neighbour"] += 1
                target = None
        kind = trunk.object_id.split(":", 1)[0]
        if target is not None:
            result[trunk.index] = target
            positions[trunk.index] = target
            stats[f"moved_{kind}"] += 1
        elif trunk.object_id.startswith(REGISTER):
            stats["kept_register"] += 1
        else:
            result[trunk.index] = None
            positions.pop(trunk.index)
            stats[f"dropped_{kind}"] += 1
    return result


def _nearest_valid(p, forbidden, distance, box):
    """The nearest point at least ``distance`` from ``forbidden``, within ``MOVE`` of ``p``, or None."""
    reach = p.buffer(MOVE + distance + 1)
    local = forbidden.intersection(reach)
    zone = local.buffer(distance + 0.02, quad_segs=16)
    free = p.buffer(MOVE, quad_segs=32).difference(zone)
    if box is not None:
        free = free.intersection(box)
    if free.is_empty:
        return None
    a, _ = nearest_points(free, p)
    if a.distance(p) > MOVE + 1e-6:
        return None
    return (float(a.x), float(a.y))


def apply_to_trees(trees, walked, blocked, bounds, height_at, base_radius, stats=None):
    """The generator's step on ``vegetation.trees.Tree`` lists (trees or bushes): drop or move in place."""
    trunks = [Trunk(i, t.object_id, t.x, t.z, max(base_radius(t.variant) * t.crown_radius, t.trunk_radius))
              for i, t in enumerate(trees)]
    decisions = place(trunks, walked, blocked, bounds, stats)
    result = []
    for i, t in enumerate(trees):
        if i not in decisions:
            result.append(t)
        elif decisions[i] is not None:
            t.x, t.z = decisions[i]
            t.y = height_at(t.x, t.z)
            result.append(t)
    return result
