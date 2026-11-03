"""Playground equipment of a coarse tile, placed from the playground survey (turku3d.playgrounds).

A class rule for every surveyed site (Turku and Kaarina register playgrounds, school and daycare
yards) and every other mapped OSM ``leisure=playground`` area:

* **Located outlines** (aerial survey, about 0.5 m): one model per outline, centred on its minimum
  rotated rectangle, long axis as the model x axis, sizes from the rectangle clamped to plausible
  ranges and quantised so equal sizes share a prototype. The Finnish label picks variants (seat
  count, basket/baby seats, play house, stepping stones). The site's lidar heights of tall
  structures go to its largest matching tower, net or climbing frame. The tile holding an
  outline's centre places it.
* **Unlocated counts** (survey entries without an outline): placed by the tile holding the site's
  register point, inside the site polygon (a placeholder polygon becomes the outlines' hull plus a
  margin) on free cells: clear of outlines, buildings, street areas, foot paths, tree trunks and
  water, next to the equipment already there, inside that tile.
* **Benches and tables**: register and OSM furniture (the furniture stage) counts first; a site
  with fewer than its size's share gets benches along its edge facing the play area, and a site
  over ``TABLE_AREA`` without a table gets a picnic table.
* **Safety surfacing**: a pad of the site's aerial surface class (sand, rubber, gravel) under
  swings, slides, frames and spinners on near-level ground.

Models are the parametric prototypes of ``turku3d.playgrounds.models``, written as standard glTF
mesh instances in one props GLB (merged with the furniture into the tile's palette batch, which the
client hides beyond 125 m). Collision is a few static boxes per item.
"""

from __future__ import annotations

import math
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import shapely

from turku3d import config as C

KIND = {
    "swing": "play_swing", "basket_swing": "play_swing", "baby_swing": "play_swing",
    "slide": "play_slide", "climbing": "play_climbing", "net": "play_net", "tower": "play_tower",
    "sandbox": "play_sandbox", "spring": "play_spring", "carousel": "play_carousel",
    "seesaw": "play_seesaw", "zipline": "play_zipline", "trampoline": "play_trampoline",
    "balance": "play_balance", "gym": "play_gym", "ballcourt": "play_ballcourt",
    "skate": "play_skate", "water": "play_pool", "shelter": "play_shelter",
    "bench": "bench", "table": "table",
}
# Footprint (long, short) of an unlocated item, metres.
SIZE = {
    "swing": (4.6, 2.2), "basket_swing": (3.0, 2.6), "baby_swing": (4.6, 2.2), "slide": (3.5, 0.8),
    "climbing": (3.0, 2.0), "net": (5.0, 5.0), "tower": (2.2, 2.2), "sandbox": (4.0, 4.0),
    "spring": (1.0, 0.4), "carousel": (2.0, 2.0), "seesaw": (3.0, 0.4), "zipline": (24.0, 2.2),
    "trampoline": (2.0, 2.0), "balance": (4.0, 0.4), "gym": (4.0, 1.2), "ballcourt": (18.0, 10.0),
    "skate": (4.0, 3.0), "water": (8.0, 5.0), "shelter": (3.0, 3.0), "bench": (1.8, 0.6),
    "table": (1.8, 1.6),
}
PADDED = {"swing": 3.0, "basket_swing": 3.0, "baby_swing": 3.0, "slide": 1.5, "climbing": 1.5,
          "net": 1.0, "tower": 1.5, "carousel": 1.5, "spring": 1.0, "seesaw": 1.0}
PAD_RANGE = 0.15  # m of ground relief under a pad; steeper ground keeps its own surface
CLEAR = 1.2  # m kept free around located equipment and between placed items
PATH_CLEAR = 1.0  # m from a foot path centre line
TREE_CLEAR = 1.0  # m from a tree trunk
FALLBACK = {"swing": 1, "sandbox": 1}  # an OSM playground the survey does not know
TABLE_AREA = 600.0  # m²
STEP = 1.0  # m: free-cell grid
DUPLICATE = 0.3  # intersection over union: two sites' outlines of one object


@dataclass
class Item:
    object_id: str
    kind: str
    variant: str
    x: float
    y: float
    z: float
    yaw: float
    site: str
    category: str
    source: str

    @property
    def prototype(self):
        return self.kind, self.variant


EMPTY = {"glb": None, "collision": {}, "stats": {}, "sites": {}, "items": []}


def sources():
    base = Path(__file__).parents[1]
    return [Path(__file__), base / "playgrounds/__init__.py", base / "playgrounds/models.py",
            base / "props/models.py", base / "package/gltf.py", Path(__file__).with_name("furniture.py")]


def stage(cache, tile, inputs, grid_pos, trees, furniture, play_areas, common):
    from turku3d.stage_cache import source_key

    from .build import grid_height

    footprints = [b.footprint for b in (*inputs.buildings, *inputs.wall_context)]
    paths = _foot_paths(inputs.roads["json"] if inputs.roads else None)
    streets = [g for g, _ in inputs.streets]
    trunks = [(t.x, t.z) for t in trees]
    seats = [(p.kind, p.x, p.z) for p in furniture if p.kind in ("bench", "table")]
    return cache.run(
        "playgrounds",
        source_key(*sources(), grid_height, *common),
        [tile, list(inputs.playgrounds), play_areas, grid_pos, footprints, list(inputs.water),
         streets, paths, trunks, seats],
        lambda: produce(tile, inputs.playgrounds, play_areas, lambda x, z: grid_height(tile, grid_pos, x, z),
                        footprints, list(inputs.water), streets, paths, trunks, seats),
    )


def _foot_paths(graph):
    if graph is None:
        return []
    from turku3d.props.lights import street_edges

    edges, _ = street_edges(graph)
    return [line for line, _ in edges["path"]]


def q(value, step, lo, hi):
    return round(min(max(value, lo), hi) / step) * step


def rectangle(polygon):
    """(cx, cz, long, short, yaw) of a polygon's minimum rotated rectangle."""
    with np.errstate(divide="ignore", invalid="ignore"):
        rect = shapely.minimum_rotated_rectangle(polygon)
    if rect.geom_type != "Polygon":
        c = polygon.centroid
        return c.x, c.y, 0.5, 0.5, 0.0
    ring = np.asarray(rect.exterior.coords)[:4]
    edges = np.diff(np.vstack([ring, ring[:1]]), axis=0)
    lengths = np.linalg.norm(edges, axis=1)
    k = int(np.argmax(lengths))
    dx, dz = edges[k] / max(lengths[k], 1e-9)
    if dx < 0 or (abs(dx) < 1e-9 and dz < 0):
        dx, dz = -dx, -dz
    c = rect.centroid
    return c.x, c.y, float(lengths[k]), float(min(lengths)), math.atan2(-dz, dx)


def variant(category, long, short, label="", height=None, key=""):
    """(prop kind, quantised parameter code) of one item."""
    from turku3d.playgrounds import SEATS, seats
    from turku3d.playgrounds.models import code

    label = (label or "").lower()
    kind = KIND[category]
    if category in ("bench", "table"):
        return kind, ""
    if category in ("swing", "baby_swing", "basket_swing"):
        if category == "basket_swing" or "pesä" in label:
            n = 2 if long > 5.5 else 1
            return kind, code(n=n, s=2, l=q(long, 0.5, 2.5 * n, 3.5 * n))
        n = seats(label) if any(k in label for k, _ in SEATS) else int(q(long / 1.6, 1, 1, 4))
        style = 1 if category == "baby_swing" or "vauva" in label else 0
        return kind, code(n=n, s=style, l=q(long, 0.5, 1.4 * n + 0.6, 1.8 * n + 1.0))
    if category == "slide":
        length = q(long, 0.5, 2.0, 6.0)
        return kind, code(l=length, h=q(0.3 * length, 0.25, 1.0, 2.0))
    if category in ("climbing", "tower"):
        lo = 1.5 if category == "tower" else 1.2
        h = height or (1.4 if "matala" in label or "pieni" in label else
                       2.6 if "suuri" in label else 3.4 if category == "tower" else 2.0)
        return kind, code(l=q(long, 0.5, lo, 10.0 if category == "climbing" else 5.0),
                          w=q(short, 0.5, 1.0, 6.0 if category == "climbing" else 5.0),
                          h=q(h, 0.25, 1.2, 6.5))
    if category == "net":
        r = q(max(long, short) / 2, 0.5, 1.5, 4.5)
        return kind, code(r=r, h=q(height or 1.1 * r + 1.5, 0.25, 2.5, 6.5))
    if category == "sandbox":
        return kind, code(l=q(long, 0.5, 1.5, 12.0), w=q(short, 0.5, 1.5, 12.0))
    if category == "spring":
        return kind, code(c=zlib.crc32(key.encode()) % 6)
    if category == "seesaw":
        return kind, code(l=q(long, 0.5, 2.5, 3.5))
    if category == "carousel":
        return kind, code(r=q(max(long, short) / 2, 0.25, 0.8, 1.75))
    if category == "balance":
        stones = any(k in label for k in ("askel", "kivi", "rengas", "pölkky", "pylväs"))
        return kind, code(l=q(long, 0.5, 1.5, 12.0), t=int(stones))
    if category == "zipline":
        return kind, code(l=q(long, 1.0, 10.0, 40.0))
    if category == "trampoline":
        return kind, code(l=q(long, 0.5, 1.5, 6.0), w=q(short, 0.5, 1.5, 6.0))
    if category == "gym":
        return kind, code(l=q(long, 1.0, 2.0, 10.0))
    if category == "ballcourt":
        if any(k in label for k in ("maali", "kori")):
            return kind, code(l=q(long, 0.5, 1.0, 3.0), w=q(short, 0.5, 0.5, 2.0))
        return kind, code(l=q(long, 1.0, 6.0, 40.0), w=q(short, 1.0, 4.0, 25.0))
    if category == "skate":
        return kind, code(l=q(long, 0.5, 2.0, 12.0), w=q(short, 0.5, 1.5, 12.0))
    if category == "water":
        return kind, code(l=q(long, 0.5, 2.0, 20.0), w=q(short, 0.5, 2.0, 15.0))
    if category == "shelter":
        house = "mökki" in label or "talo" in label
        return kind, code(l=q(long, 0.5, 1.5, 8.0), w=q(short, 0.5, 1.5, 6.0), t=int(house))
    raise KeyError(category)


def _tall(site, objects):
    """Lidar height per object id: each tall structure to the largest matching outline."""
    result = {}
    for s in sorted(site["big_structures"], key=lambda s: -(s.get("height_m") or 0)):
        label = (s.get("label") or "").lower()
        wanted = ("net",) if any(k in label for k in ("verkko", "pyramid", "köysi")) else \
            ("tower",) if any(k in label for k in ("torni", "linna", "laiva", "talo")) else \
            ("climbing", "tower", "net")
        free = [o for o in objects if o["category"] in wanted and o["id"] not in result]
        if free and s.get("height_m"):
            best = max(free, key=lambda o: shapely.Polygon(o["polygon"]).area)
            result[best["id"]] = float(s["height_m"])
    return result


def _domain(site):
    """The site's play area: its polygon, or around its outlines when the register only has a
    placeholder circle around the point."""
    polygon = shapely.union_all(site["polygons"]) if site["polygons"] else shapely.Polygon()
    if site["placeholder"] or polygon.is_empty:
        parts = [shapely.Polygon(o["polygon"]) for o in site["objects"]]
        hull = shapely.union_all(parts).convex_hull.buffer(4.0) if parts else shapely.Polygon()
        polygon = shapely.union_all([polygon, hull, shapely.Point(site["point"]).buffer(10.0)])
    return shapely.make_valid(polygon)


def _rects(xs, zs, long, short, yaw):
    c, s = math.cos(yaw), math.sin(yaw)
    corners = []
    for a, b in ((-long / 2, -short / 2), (long / 2, -short / 2), (long / 2, short / 2), (-long / 2, short / 2)):
        corners.append(np.stack([xs + c * a + s * b, zs - s * a + c * b], axis=-1))
    return shapely.polygons(np.stack(corners, axis=1))


def _rect(x, z, long, short, yaw):
    return _rects(np.array([x]), np.array([z]), long, short, yaw)[0]


def place_free(domain, blocked, occupied, long, short, yaw, inside):
    """The free cell for one long x short item: in the domain and the owner tile, clear of
    blocked ground, as close as allowed to the equipment already there (else to the centre)."""
    area = domain.intersection(inside).buffer(-0.3)
    if area.is_empty:
        return None
    x0, z0, x1, z1 = area.bounds
    gx, gz = np.meshgrid(np.arange(math.floor(x0), x1 + STEP, STEP), np.arange(math.floor(z0), z1 + STEP, STEP))
    xs, zs = gx.ravel(), gz.ravel()
    keep = shapely.contains_xy(area, xs, zs)
    xs, zs = xs[keep], zs[keep]
    if not len(xs):
        return None
    rects = _rects(xs, zs, long, short, yaw)
    ok = shapely.covers(area, rects)
    if not blocked.is_empty:
        shapely.prepare(blocked)
        ok &= ~shapely.intersects(blocked, rects)
    if not ok.any():
        return None
    xs, zs, rects = xs[ok], zs[ok], rects[ok]
    anchor = occupied if not occupied.is_empty else domain.centroid
    distance = shapely.distance(anchor, rects)
    k = int(np.lexsort((zs, xs, distance))[0])
    return float(xs[k]), float(zs[k])


def _edge_benches(domain, blocked, count, inside):
    """Benches 1 m inside the edge of the largest part, facing into the play area."""
    parts = sorted(shapely.get_parts(domain), key=lambda p: -p.area)
    if not parts or count <= 0:
        return []
    ring = parts[0].buffer(-1.0)
    if ring.is_empty:
        return []
    ring = max(shapely.get_parts(ring), key=lambda p: p.area).exterior
    stations = 24
    result, taken = [], shapely.Polygon()
    for k in range(stations):
        d = ring.length * (k + 0.5) / stations
        p, a, b = ring.interpolate(d), ring.interpolate(max(0, d - 0.5)), ring.interpolate(min(ring.length, d + 0.5))
        tx, tz = b.x - a.x, b.y - a.y
        length = math.hypot(tx, tz) or 1.0
        tx, tz = tx / length, tz / length
        nx, nz = -tz, tx
        if not parts[0].contains(shapely.Point(p.x + nx, p.y + nz)):
            nx, nz = -nx, -nz
        yaw = math.atan2(nx, nz)  # model +Z (the seat's front) along the inward normal
        rect = _rect(p.x, p.y, 1.8, 0.6, yaw)
        if not inside.covers(rect) or (not blocked.is_empty and blocked.intersects(rect)):
            continue
        result.append((k, p.x, p.y, yaw, rect))
    if not result:
        return []
    # Evenly around the ring: every len/count-th feasible station.
    chosen = []
    for n in range(count):
        target = n * stations / count
        best = min(result, key=lambda r: (min(abs(r[0] - target), stations - abs(r[0] - target)), r[0]))
        if best[4].distance(taken) > 3.0 or taken.is_empty:
            chosen.append(best)
            taken = taken.union(best[4])
        result = [r for r in result if r is not best]
        if not result:
            break
    return [(x, z, yaw) for _, x, z, yaw, _ in chosen]


def _near(region, geometries):
    """The valid union of the geometries that reach the region."""
    if not geometries:
        return shapely.Polygon()
    array = shapely.make_valid(np.asarray(geometries, dtype=object))
    return shapely.union_all(array[shapely.intersects(array, region)])


def bench_share(area):
    return 1 if area < 150 else 2 if area < 600 else 3 if area < 2000 else 4


def produce(tile, sites, play_areas, height_at, footprints, water, streets, paths, trunks, seats):
    from turku3d.playgrounds.models import PAD_COLOURS, code

    if not sites and not play_areas:
        return dict(EMPTY)
    stats = Counter()
    per_site = defaultdict(Counter)
    items = []
    box = shapely.box(*tile.bbox.as_tuple())
    inside = shapely.box(*tile.bbox.buffered(-0.5).as_tuple())
    # Obstacles only matter near the play areas; invalid survey rings are repaired first.
    region = shapely.union_all([*(_domain(site) for site in sites), *(a for _, a in play_areas)]).buffer(5.0)
    hard = _near(region, [*footprints, *water, *streets])
    soft = _near(region, [*(shapely.buffer(p, PATH_CLEAR) for p in paths),
                          *(shapely.Point(t).buffer(TREE_CLEAR) for t in trunks)])
    blocked_base = shapely.union_all([hard.buffer(0.3), soft])

    def add(site_id, category, source, oid, x, z, long, short, yaw, label="", height=None, surface=None):
        kind, var = variant(category, long, short, label, height, oid)
        y = height_at(x, z)
        items.append(Item(oid, kind, var, x, y, z, yaw, site_id, category, source))
        per_site[site_id][f"{category}:{source}"] += 1
        stats[f"placed:{category}"] += 1
        stats[f"source:{source}"] += 1
        pad = PADDED.get(category)
        colour = PAD_COLOURS.get(surface or "")
        if pad and colour is not None and source != "fallback":
            pl, pw = q(long + pad * 0.5, 1.0, 2.0, 12.0), q(short + pad, 1.0, 2.0, 10.0)
            c, s = math.cos(yaw), math.sin(yaw)
            heights = [height_at(x + c * a + s * b, z - s * a + c * b)
                       for a in (-pl / 2, 0, pl / 2) for b in (-pw / 2, pw / 2)]
            low, high = min(heights), max(heights)
            if high - low <= PAD_RANGE and inside.covers(_rect(x, z, pl, pw, yaw)):
                t = q(high - low + 0.07, 0.05, 0.05, 0.25)
                items.append(Item(f"{oid}:pad", "play_pad", code(l=pl, w=pw, c=colour, t=t),
                                  x, high + 0.02 - t, z, yaw, site_id, "pad", source))
                stats["pads"] += 1

    known = []
    # Overlapping register sites (a park and its sub-area) list the same outlines: the first
    # site in id order keeps an outline, later near-identical ones are duplicates.
    kept, everything, checked = [], [], {}
    for site in sorted(sites, key=lambda s: s["id"]):
        checked[site["id"]] = []
        for o in site["objects"]:
            polygon = shapely.make_valid(shapely.Polygon(o["polygon"]))
            everything.append(polygon)
            twin = any(polygon.intersection(k).area > DUPLICATE * polygon.union(k).area for k in kept
                       if k.intersects(polygon))
            checked[site["id"]].append((o, polygon, twin))
            if not twin:
                kept.append(polygon)
    every_outline = shapely.union_all(everything) if everything else shapely.Polygon()
    taken = shapely.Polygon()  # unlocated items placed for earlier sites
    for site in sorted(sites, key=lambda s: s["id"]):
        domain = _domain(site)
        known.append(domain)
        tall = _tall(site, site["objects"])
        outlines = []
        for o, polygon, twin in checked[site["id"]]:
            outlines.append(polygon)
            x, z, long, short, yaw = rectangle(polygon)
            if not tile.bbox.contains(x, z):
                continue
            if twin:
                stats["duplicate_outline"] += 1
                per_site[site["id"]][f"{o['category']}:duplicate"] += 1
                continue
            point = shapely.Point(x, z)
            if hard.covers(point):
                stats["dropped_in_building_street_or_water"] += 1
                per_site[site["id"]][f"{o['category']}:dropped"] += 1
                continue
            add(site["id"], o["category"], "outline", f"play:{o['id']}", x, z, long, short, yaw,
                o["label"], tall.get(o["id"]), site.get("surface"))
        owner = C.VisualTile.at(*site["point"]) == tile
        if not owner:
            continue
        occupied = shapely.union_all(outlines) if outlines else shapely.Polygon()
        blocked = shapely.union_all([blocked_base, every_outline.buffer(CLEAR), taken.buffer(CLEAR)])
        _, _, _, _, axis = rectangle(domain) if not domain.is_empty else (0, 0, 0, 0, 0.0)
        wanted = [(c, n) for c, n in site["unlocated"].items() if c in KIND]
        order = sorted(((c, k) for c, n in wanted for k in range(n)),
                       key=lambda ck: (-SIZE[ck[0]][0] * SIZE[ck[0]][1], ck))
        for category, k in order:
            long, short = SIZE[category]
            spot = place_free(domain, blocked, occupied, long, short, axis, inside)
            if spot is None:
                stats[f"unplaced:{category}"] += 1
                per_site[site["id"]][f"{category}:unplaced"] += 1
                continue
            rect = _rect(*spot, long, short, axis)
            add(site["id"], category, "count", f"play:{site['id']}:{category}:{k}", *spot, long, short, axis,
                surface=site.get("surface"))
            occupied = occupied.union(rect)
            blocked = blocked.union(rect.buffer(CLEAR))
            taken = taken.union(rect)
        seat_points = [shapely.Point(x, z) for kind, x, z in seats]
        near = domain.buffer(4.0)
        located_seats = Counter(o["category"] for o in site["objects"] if o["category"] in ("bench", "table"))
        benches = sum(1 for (kind, _, _), p in zip(seats, seat_points) if kind == "bench" and near.covers(p))
        tables = sum(1 for (kind, _, _), p in zip(seats, seat_points) if kind == "table" and near.covers(p))
        benches += located_seats["bench"]
        tables += located_seats["table"]
        per_site[site["id"]]["bench:existing"] += benches
        per_site[site["id"]]["table:existing"] += tables
        missing = bench_share(domain.area) - benches
        for n, (x, z, yaw) in enumerate(_edge_benches(domain, blocked, missing, inside)):
            add(site["id"], "bench", "edge_rule", f"play:{site['id']}:bench:{n}", x, z, 1.8, 0.6, yaw)
            blocked = blocked.union(_rect(x, z, 1.8, 0.6, yaw).buffer(0.8))
        if domain.area > TABLE_AREA and not tables:
            spot = place_free(domain, blocked, occupied, *SIZE["table"], axis, inside)
            if spot is not None:
                add(site["id"], "table", "area_rule", f"play:{site['id']}:table", *spot, *SIZE["table"], axis)
    # Mapped OSM play areas the survey does not cover get a swing and a sandbox, as before.
    surveyed = shapely.union_all(known) if known else shapely.Polygon()
    for identity, area in play_areas:
        if not surveyed.is_empty and area.intersects(surveyed):
            continue
        anchor = area.representative_point()
        if C.VisualTile.at(anchor.x, anchor.y) != tile:
            continue
        _, _, _, _, axis = rectangle(area)
        occupied = shapely.Polygon()
        blocked = blocked_base
        for category, n in FALLBACK.items():
            for k in range(n):
                long, short = SIZE[category]
                spot = place_free(area, blocked, occupied, long, short, axis, inside)
                if spot is None:
                    stats[f"unplaced:{category}"] += 1
                    continue
                rect = _rect(*spot, long, short, axis)
                add(f"osm:{identity}", category, "fallback", f"play:osm:{identity}:{category}:{k}",
                    *spot, long, short, axis)
                occupied = occupied.union(rect)
                blocked = blocked.union(rect.buffer(CLEAR))
    items = [i for i in items if box.contains(shapely.Point(i.x, i.z))]
    stats["items"] = len(items)
    return {
        "glb": visual_bytes(items, tile) if items else None,
        "collision": collision(items),
        "stats": dict(sorted(stats.items())),
        "sites": {k: dict(sorted(v.items())) for k, v in sorted(per_site.items())},
        "items": items,
    }


def collision(items):
    from turku3d.col.format import Primitive
    from turku3d.col.materials import mat_id
    from turku3d.playgrounds.models import boxes, params

    from .furniture import _yaw_quaternion

    result = defaultdict(list)
    for item in items:
        ct = C.CollisionTile.at(item.x, item.z)
        c, s = math.cos(item.yaw), math.sin(item.yaw)
        for (cx, cy, cz), half in boxes(item.kind, params(item.variant)):
            centre = (item.x + c * cx + s * cz - ct.origin[0], item.y + cy,
                      item.z - s * cx + c * cz - ct.origin[2])
            result[ct.id].append(Primitive.box(mat_id("street_furniture"), centre, half,
                                               _yaw_quaternion(item.yaw)))
    return dict(result)


def visual_bytes(items, tile):
    """Standard glTF mesh reuse: one prototype mesh per kind and size class, one node per item."""
    import json
    import struct

    from turku3d.package.gltf import build_glb, read_glb
    from turku3d.playgrounds.models import model

    keys = sorted({i.prototype for i in items})
    js, blob = read_glb(build_glb([model(*k) for k in keys], root_translation=tile.origin))
    js["nodes"] = [js["nodes"][0]]
    js["nodes"][0]["children"] = list(range(1, len(items) + 1))
    for i in items:
        js["nodes"].append({
            "name": i.object_id,
            "mesh": keys.index(i.prototype),
            "translation": [i.x - tile.origin[0], i.y, i.z - tile.origin[2]],
            "rotation": [0, math.sin(i.yaw / 2), 0, math.cos(i.yaw / 2)],
            "extras": {"layer": "props", "object_id": i.object_id, "material_key": f"props_{i.kind}",
                       "prop_type": i.kind, "play_site": i.site, "play_category": i.category,
                       "texture_uv": "normalized"},
        })
    data = json.dumps(js, separators=(",", ":")).encode()
    data += b" " * ((-len(data)) % 4)
    return (struct.pack("<III", 0x46546C67, 2, 28 + len(data) + len(blob))
            + struct.pack("<II", len(data), 0x4E4F534A) + data
            + struct.pack("<II", len(blob), 0x004E4942) + blob)
