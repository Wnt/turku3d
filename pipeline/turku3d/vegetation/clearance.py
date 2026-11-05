"""Tree trunks off walked and driven ground: one city-wide rule for the generator and ``path-trees@1``.

The ground a player walks or drives on is what the tile draws as paving, not only the street graph:
private driveways, service drives and yard paths (``access=private`` ways leave the graph, so the
minimap and the older path clearance never saw them) are drawn by ``coarse.surfaces.osm_surfaces``
with the same corridor widths used here. Two kinds of walked ground:
- lanes: every drawn OSM way corridor, and the surveyed (GIS:Katualueet) carriageways, cycle tracks
  and lanes, driveway mouths, steps, bridges and crossings. No trunk stands in a lane;
- paved areas: surveyed sidewalks, shared foot/cycle ways, squares, stop areas, other traffic areas,
  mapped parking and pedestrian areas. Where such paving is at least ``WIDE`` across, real trees stand
  in it beside the mapped way (tree grates, quay rows, parking-lot islands); a narrower strip is a lane.
Medians and planting strips (Välikaista), leased terraces and unclassified parts are not walked.

A trunk whose visible base comes within ``CLEARANCE`` of a lane (a register tree: whose stem stands on
it; its position is surveyed, the corridor widths are estimates) is moved to the nearest valid spot
within ``MOVE`` when it is a real tree (a register tree, a mapped OSM tree or a supported lidar crown),
its crown still over its measured place (trees-lidar measures the lean from the new trunk). Generated
trees (area scatter, canopy-peak fill, bushes) on any walked ground are dropped, and so is a real tree
with no valid spot within reach, except a register tree: a surveyed stem stays where the survey put it.
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
WIDE = 5.0  # m: paving at least this wide holds real trees (grates, islands); narrower is a lane
# GIS:Katualueet Katuosalaji parts a player walks, cycles or drives on: lanes and paved areas.
LANE_PARTS = frozenset({
    "Ajorata", "Pyörätie", "Pyöräkaista", "Tonttiliittymä", "Portaat", "Silta", "Tasoristeys",
    "Pelastustie/Huoltotie", "Alikulkukäytävä", "Joukkoliikennekatu",
})
# Foot paving: where it is wide (quays, squares, city sidewalks with tree rows) people walk the mapped
# way through it and real trees stand beside it; a narrow strip of it is all lane.
PAVED_PARTS = frozenset({"Jalkakäytävä", "Yhdistetty kevyen liikenteen väylä", "Kävelytie", "Muu liikennealue",
                         "Odotustila"})
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
    """``(polygon, lane)`` of walked and driven ground over the tile (plus ``WINDOW``), from OSM and
    GIS:Katualueet; ``lane`` False for paved areas."""
    from turku3d.roads import CAR_CLASS, FOOT_ONLY
    from turku3d.spatial import frame, rows

    bbox = tile.bbox.buffered(WINDOW)
    window = shapely.box(*bbox.as_tuple())
    survey, parts = [], []
    if streets is not None:
        f = frame(streets, bbox)
        if len(f) and "Katuosalaji" in f:
            for g, kind in zip(f.geometry_local, f["Katuosalaji"], strict=True):
                if g is None or kind not in LANE_PARTS | PAVED_PARTS:
                    continue
                g = shapely.make_valid(shapely.force_2d(g))
                parts.append((g, kind in LANE_PARTS))
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
                    parts.append((geom, False))
                continue
            if highway not in CAR_CLASS and highway not in FOOT_ONLY:
                continue
            if geom.geom_type == "LineString" and geom.is_ring and tags.get("area") == "yes":
                parts.append((shapely.make_valid(shapely.Polygon(geom)), False))
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
            parts.append((geom.buffer(half, cap_style="flat"), True))
    result = []
    for g, lane in parts:
        if g.intersects(window):
            g = g.intersection(window)
            if g.area > 1e-3:
                result.append((g, lane))
    return result


def split(walked):
    """(lanes, wide paving) of ``walked_ground`` rows; paving narrower than ``WIDE`` joins the lanes."""
    lanes = shapely.union_all([g for g, lane in walked if lane])
    paved = shapely.union_all([g for g, lane in walked if not lane])
    wide = paved.buffer(-WIDE / 2).buffer(WIDE / 2).intersection(paved) if not paved.is_empty else paved
    narrow = paved.difference(wide.buffer(0.01)) if not paved.is_empty else paved
    return shapely.union_all([lanes, narrow]), wide


@dataclass
class Trunk:
    """What the rule needs of one drawn tree; ``index`` is the caller's handle."""

    index: object
    object_id: str
    x: float
    z: float
    base: float  # visible trunk base radius (m)
    stem: float = 0.0  # trunk radius (m)

    @property
    def real(self) -> bool:
        return self.object_id.startswith(REAL)


def need(trunk: Trunk) -> float:
    """Distance the trunk keeps from a lane. A register stem is surveyed, and corridor widths are
    estimates: it yields only where its stem itself stands on the drawn lane."""
    if trunk.object_id.startswith(REGISTER):
        return trunk.stem
    return trunk.base + CLEARANCE


def place(trunks: list[Trunk], walked, blocked=None, bounds=None, stats=None):
    """``{index: None (drop) | (x, z) (move)}`` for trunks on walked ground; the others stay.

    ``walked`` is ``walked_ground``'s rows; ``blocked`` (buildings, water) bounds where a moved trunk
    may go; ``bounds`` (xmin, zmin, xmax, zmax) keeps a moved trunk in its tile. Trees are visited in
    object-id order so the generator's list order and a tile's per-variant instance order agree.
    """
    stats = Counter() if stats is None else stats
    if not walked or not trunks:
        return {}
    lanes, wide = split(walked)
    shapely.prepare(lanes)
    shapely.prepare(wide)
    blocked = shapely.union_all(list(blocked or ()))
    forbidden = shapely.union_all([lanes, blocked]) if not blocked.is_empty else lanes
    box = shapely.box(*bounds) if bounds is not None else None
    positions = {t.index: (t.x, t.z) for t in trunks}
    result = {}
    for trunk in sorted(trunks, key=lambda t: (t.object_id, t.x, t.z)):
        p = shapely.Point(trunk.x, trunk.z)
        # The distance to an empty geometry is NaN: no lanes or no paving is nothing to clear.
        in_lane = not lanes.is_empty and lanes.distance(p) < need(trunk)
        on_paving = not wide.is_empty and wide.distance(p) < need(trunk)
        if not in_lane and (trunk.real or not on_paving):
            continue
        stats["on_lane" if in_lane else "on_paving"] += 1
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
    trunks = [Trunk(i, t.object_id, t.x, t.z, max(base_radius(t.variant) * t.crown_radius, t.trunk_radius),
                    t.trunk_radius) for i, t in enumerate(trees)]
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
