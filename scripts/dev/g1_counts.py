#!/usr/bin/env python3
"""G1 approval numbers: what street furniture, lamps and register trees G1 would add per tile.

Read-only analysis of the raw source data already on disk (``$TURKU3D_DATA/raw``); it changes no
generator code and fetches nothing. Run it in the pipeline environment:

    bash pipeline/env/run.sh python scripts/dev/g1_counts.py \\
        --json /tmp/g1.json --markdown /tmp/g1.md

G1 rules modelled here (street-richness-plan.md, "G1"):

* register first: ``GIS:Varusteet`` (benches, bins, bollards, other furniture by its class),
  ``GIS:Polkupyoraparkki`` (bike racks), ``GIS:Follariasemat`` (bike-share stations),
  ``GIS:Pysakointiautomaatit`` (parking meters), ``GIS:Puut`` (register trees) and
  ``frn:CityFurniture_LOD2`` (lamp poles; reported missing when not on disk);
* OSM fallback per kind (``highway=street_lamp``, ``amenity=bench``/``waste_basket``/
  ``bicycle_parking``, ``vending=parking_tickets``, ``barrier=bollard``, ``natural=tree``),
  dropped when the same kind already lies within 3 m;
* the 30 m ``lit=yes`` lamp rule only where both sources are empty: per station (no register or
  OSM lamp within 20 m, today's ``turku3d.props`` reach) and, as an alternative, per tile (no
  lamp at all in the tile).

Today's counts come from the published tiles through ``richness_scorecard.py``. Per 100 m uses
the OSM street centreline length in the tile (primary to living_street and pedestrian; service
roads are reported separately). Timings call the current, unchanged ``turku3d.props`` readers
and ``visual_bytes`` on today's and G1's prop lists.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import shapely

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "pipeline"))
sys.path.insert(0, str(Path(__file__).parent))

import richness_scorecard as rs  # noqa: E402
from turku3d import config as C  # noqa: E402
from turku3d.coarse.forests import TREE_CAP  # noqa: E402
from turku3d.coarse.sources import Sources  # noqa: E402
from turku3d.props import LAMP_REACH, LAMP_SPACING, LIT_STREETS, Prop, classify  # noqa: E402

DEDUP_M = 3.0
CENTRE_3X3 = [f"t0_{i}_{j}" for j in (-1, 0, 1) for i in (-1, 0, 1)]
KUPITTAA = "t0_5_2"
STREET_CLASSES = {
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
    "unclassified",
    "residential",
    "living_street",
    "pedestrian",
}
STREET_REACH = 15.0  # furniture "on the street": within this of a street centreline
REGISTER = {
    "GIS:Varusteet": None,  # classified per row (turku3d.props.classify)
    "GIS:Polkupyoraparkki": "bike_rack",
    "GIS:Follariasemat": "bike_station",
    "GIS:Pysakointiautomaatit": "parking_meter",
    "GIS:Puut": "tree",
    "frn:CityFurniture_LOD2": "lamp",
}
FURNITURE = ("bench", "bin", "bike_rack", "bike_station", "parking_meter", "bollard", "other")
# Varusteet rows classify() calls "post", by Varustelaji (no 3D model exists for them yet).
OTHER_NAMES = {
    "Tuhkakuppi": "ash_tray",
    "Opastetaulu/tiedote": "board",
    "Opastetaulu leikkipaikoille": "board",
    "Opaste liikunta": "board",
    "Opasteviitta": "signpost",
    "Opastuspyloni": "pylon",
    "Pelastusväline": "life_saving",
    "Pöytä": "table",
    "Pelipöytä": "table",
    "Kalusteryhmä": "table",
    "Koirankakkapussiteline": "dog_bag_stand",
}


def osm_kind(tags: dict) -> str | None:
    if tags.get("highway") == "street_lamp":
        return "lamp"
    if tags.get("amenity") == "bench":
        return "bench"
    if tags.get("amenity") == "waste_basket":
        return "bin"
    if tags.get("amenity") == "bicycle_parking":
        return "bike_rack"
    if tags.get("amenity") == "vending" and tags.get("vending") == "parking_tickets":
        return "parking_meter"
    if tags.get("barrier") == "bollard":
        return "bollard"
    if tags.get("natural") == "tree":
        return "tree"
    return None


class Accepted:
    """Accepted points per kind with a 3 m duplicate test (grid hash)."""

    def __init__(self, reach=DEDUP_M):
        self.reach = reach
        self.cells: dict[tuple, list] = defaultdict(list)
        self.items: list[dict] = []

    def near(self, kind, x, z, reach=None) -> bool:
        r = self.reach if reach is None else reach
        cx, cz = math.floor(x / r), math.floor(z / r)
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for px, pz in self.cells[(kind, cx + dx, cz + dz)]:
                    if math.hypot(px - x, pz - z) < r:
                        return True
        return False

    def add(self, item: dict) -> bool:
        if self.near(item["kind"], item["x"], item["z"]):
            return False
        self.cells[
            (item["kind"], math.floor(item["x"] / self.reach), math.floor(item["z"] / self.reach))
        ].append((item["x"], item["z"]))
        self.items.append(item)
        return True


def points(path: Path, bbox):
    from turku3d.spatial import frame

    rows = frame(path, bbox)
    for row in rows.to_dict("records"):
        g = row.get("geometry_local")
        if g is None or g.is_empty:
            continue
        yield row, g


def lit_rule(osm_path: Path, bbox, lamps: list[tuple[float, float]]):
    """The 30 m lit-street stations of turku3d.props.osm_props, with their 20 m skip test reported
    separately so both G1 interpretations can be counted."""
    from turku3d.spatial import frame

    tree = shapely.STRtree([shapely.Point(p) for p in lamps]) if lamps else None
    out = []
    for row in frame(osm_path, bbox.buffered(LAMP_REACH)).to_dict("records"):
        g = row.get("geometry_local")
        tags = (
            json.loads(row["tags"]) if isinstance(row.get("tags"), str) else (row.get("tags") or {})
        )
        if g is None or g.geom_type != "LineString":
            continue
        if tags.get("lit") != "yes" or tags.get("highway") not in LIT_STREETS:
            continue
        try:
            half = float(tags.get("width", float(tags.get("lanes", 2)) * 3)) / 2
        except ValueError:
            half = 3.0
        side = g.offset_curve(half + 1.0)
        if side.is_empty or side.geom_type != "LineString":
            continue
        for n, distance in enumerate(np.arange(LAMP_SPACING / 2, side.length, LAMP_SPACING)):
            s = side.interpolate(float(distance))
            if not bbox.contains(s.x, s.y):
                continue
            covered = (
                tree is not None
                and len(tree.query(s, predicate="dwithin", distance=LAMP_REACH)) > 0
            )
            out.append(
                dict(
                    kind="lamp",
                    x=s.x,
                    z=s.y,
                    source="osm_lit_street",
                    id=f"osm:{row.get('osm_id')}:lamp:{n}",
                    covered=covered,
                )
            )
    return out


def street_length(osm_path: Path, square) -> tuple[float, float, list]:
    """(street m, service m, street lines) of OSM highway centrelines clipped to the tile square."""
    from turku3d.spatial import frame

    box = shapely.box(*square)
    bbox = C.BBoxLocal(*square)
    street = service = 0.0
    lines = []
    for row in frame(osm_path, bbox).to_dict("records"):
        g = row.get("geometry_local")
        hw = row.get("highway")
        if g is None or g.geom_type != "LineString" or not hw:
            continue
        clipped = g.intersection(box)
        if clipped.is_empty:
            continue
        if hw in STREET_CLASSES:
            street += clipped.length
            lines.append(g)
        elif hw == "service":
            service += clipped.length
    return street, service, lines


def g1_items(tile: str, sources: Sources, missing: dict) -> dict:
    """All G1 candidates of a tile (accepted after the 3 m rule), with provenance counts."""
    vt = C.VisualTile.parse(tile)
    square = rs.tile_square(tile)
    bbox = C.BBoxLocal(*square)
    outer = bbox.buffered(LAMP_REACH)
    acc = Accepted()
    stats = Counter()
    layer_paths = {}
    for name, default in REGISTER.items():
        found, path = sources.wfs_source(name, vt)
        layer_paths[name] = str(path) if path else ("empty" if found else None)
        if not found:
            missing.setdefault(name, []).append(tile)
            continue
        if path is None:
            continue
        for row, g in points(path, outer):
            if g.geom_type != "Point":
                stats["register_non_point"] += 1
                continue
            kind = default or classify(row)
            sub = ""
            if kind == "post":
                kind, sub = "other", OTHER_NAMES.get(str(row.get("Varustelaji") or ""), "other")
            # One row per point: ObjectId is shared by the racks of one street part and 13
            # Varusteet Ids repeat, so the row's feature_key is the identity; the 3 m rule dedupes.
            ident = f"{name}:{row.get('feature_key')}"
            item = dict(kind=kind, sub=sub, x=g.x, z=g.y, source=name, id=ident)
            if acc.add(item):
                stats[f"register:{kind}"] += 1
            else:
                stats[f"register_dup:{kind}"] += 1
    osm = sources.osm_source(vt)
    layer_paths["osm"] = str(osm) if osm else None
    if osm is None:
        missing.setdefault("osm", []).append(tile)
        return dict(items=acc.items, stats=stats, layers=layer_paths, rule=[], osm=None)
    for row, g in points(osm, outer):
        if g.geom_type != "Point":
            continue
        tags = (
            json.loads(row["tags"]) if isinstance(row.get("tags"), str) else (row.get("tags") or {})
        )
        kind = osm_kind(tags)
        if kind is None:
            continue
        item = dict(kind=kind, sub="", x=g.x, z=g.y, source="osm", id=f"osm:{row.get('osm_id')}")
        if acc.add(item):
            stats[f"osm:{kind}"] += 1
        else:
            stats[f"osm_dup:{kind}"] += 1
    lamps = [(i["x"], i["z"]) for i in acc.items if i["kind"] == "lamp"]
    rule = lit_rule(osm, bbox, lamps)
    return dict(items=acc.items, stats=stats, layers=layer_paths, rule=rule, osm=osm)


def own(items, square):
    if not items:
        return []
    keep = rs.in_square(np.array([[i["x"], i["z"]] for i in items]), square)
    return [i for i, k in zip(items, keep, strict=True) if k]


def final(tile: str, data: dict, today: rs.TileData | None) -> dict:
    """G1's accepted additions in the tile's own square: lamps (register, OSM, station rule),
    furniture (bollards apart: today's barrier layer draws OSM bollards already) and register or
    OSM trees that are not within 3 m of a tree drawn today."""
    square = rs.tile_square(tile)
    items = own(data["items"], square)
    lamps = [i for i in items if i["kind"] == "lamp"]
    acc = Accepted()
    for i in lamps:
        acc.add(i)
    station = [r for r in own(data["rule"], square) if not r["covered"] and acc.add(r)]
    tile_rule = own(data["rule"], square) if not lamps else []
    drawn = Accepted()
    for it in today.items if today else []:
        if it.category == "tree":
            drawn.add(dict(kind="tree", x=it.x, z=it.z))
    trees = [i for i in items if i["kind"] == "tree"]
    # OSM trees are today's source; the ones not drawn were dropped by today's clearance rules or
    # cap, so only register trees count as G1 additions.
    new_trees = [
        i for i in trees if i["source"] != "osm" and not drawn.near("tree", i["x"], i["z"])
    ]
    undrawn_osm = sum(
        1 for i in trees if i["source"] == "osm" and not drawn.near("tree", i["x"], i["z"])
    )
    furniture = [i for i in items if i["kind"] in FURNITURE and i["kind"] != "bollard"]
    bollards = [i for i in items if i["kind"] == "bollard"]
    return dict(
        lamps=lamps,
        station=station,
        tile_rule=tile_rule,
        furniture=furniture,
        bollards=bollards,
        trees=trees,
        new_trees=new_trees,
        undrawn_osm=undrawn_osm,
    )


def as_items(f: dict, today: rs.TileData | None) -> list[rs.Item]:
    """Scorecard items with G1 in place: today's items plus G1's lamps, furniture and new trees."""
    out = list(today.items) if today else []
    for i in f["lamps"] + f["station"]:
        out.append(rs.Item("lamp", "lamp", i["id"], i["x"], i["z"]))
    for i in f["furniture"]:
        out.append(rs.Item("furniture", i["kind"], i["id"], i["x"], i["z"]))
    for i in f["new_trees"]:
        out.append(rs.Item("tree", "register_tree", i["id"], i["x"], i["z"]))
    return out


def summarise(tile: str, data: dict, today: rs.TileData | None) -> dict:
    square = rs.tile_square(tile)
    f = final(tile, data, today)
    src = Counter(
        (i["kind"], "register" if i["source"] != "osm" else "osm")
        for i in data["items"]
        if rs.in_square(np.array([[i["x"], i["z"]]]), square)[0]
    )
    other = Counter(i["sub"] for i in f["furniture"] if i["kind"] == "other")
    street_m, service_m, lines = (
        street_length(data["osm"], square) if data["osm"] else (0.0, 0.0, [])
    )
    near_street = 0
    if lines and f["furniture"]:
        tree = shapely.STRtree(lines)
        near_street = sum(
            1
            for i in f["furniture"]
            if len(
                tree.query(
                    shapely.Point(i["x"], i["z"]), predicate="dwithin", distance=STREET_REACH
                )
            )
        )
    by = Counter(i["kind"] for i in f["furniture"])
    register_lamps = sum(1 for i in f["lamps"] if i["source"] != "osm")
    osm_lamps = len(f["lamps"]) - register_lamps
    lamps_total = len(f["lamps"]) + len(f["station"])
    trees_today = sum(1 for it in (today.items if today else []) if it.category == "tree")
    out = {
        "layers": data["layers"],
        "street_m": round(street_m, 1),
        "service_m": round(service_m, 1),
        "g1": {
            "lamps": {
                "register": register_lamps,
                "osm": osm_lamps,
                "lit_rule_station": len(f["station"]),
                "lit_rule_tile_level": len(f["tile_rule"]),
                "total_station_rule": lamps_total,
                "total_tile_rule": len(f["lamps"]) + len(f["tile_rule"]),
            },
            "furniture": {
                k: {"register": src[(k, "register")], "osm": src[(k, "osm")], "total": by[k]}
                for k in FURNITURE
                if k != "bollard"
            },
            "furniture_total": len(f["furniture"]),
            "furniture_near_street": near_street,
            "other_by_class": dict(other.most_common()),
            "bollards": {"register": src[("bollard", "register")], "osm": src[("bollard", "osm")]},
            "trees": {
                "register": src[("tree", "register")],
                "osm_points": src[("tree", "osm")],
                "osm_not_drawn_today": f["undrawn_osm"],
                "new_register": len(f["new_trees"]),
            },
            "dedup_dropped": {k: v for k, v in data["stats"].items() if "_dup:" in k},
        },
    }
    if today is not None and not today.missing:
        t = Counter(i.category for i in today.items)
        tk = Counter(i.kind for i in today.items if i.category == "furniture")
        out["today"] = {
            "lamps": t["lamp"],
            "furniture": dict(tk),
            "trees": t["tree"],
            "sign_poles": t["sign_pole"],
            "parked_cars": t["parked_car"],
            "generation": today.generation,
        }
        out["added"] = {
            "lamps": lamps_total,
            "furniture_props": len(f["furniture"]),
            "trees": len(f["new_trees"]),
            "trees_total_after": min(trees_today + len(f["new_trees"]), TREE_CAP),
            "props_total": lamps_total + len(f["furniture"]),
        }
    per = 100.0 / street_m if street_m > 0 else None
    out["per_100m"] = (
        None
        if per is None
        else {
            "lamps": round(lamps_total * per, 2),
            "furniture": round(len(f["furniture"]) * per, 2),
            "furniture_near_street": round(near_street * per, 2),
        }
    )
    return out


def camera_projection(
    camera: dict, tiles: dict[str, dict], today: dict[str, rs.TileData], targets: dict
) -> dict:
    """The scorecard at a camera with G1 in place (today's tile items plus G1's additions)."""
    names = rs.needed_tiles(camera)
    if any(n not in tiles for n in names):
        return {"skipped": f"tiles not analysed: {[n for n in names if n not in tiles]}"}
    fake = []
    for n in names:
        t = rs.TileData(n)
        t.items = as_items(final(n, tiles[n]["data"], today.get(n)), today.get(n))
        if today.get(n) is not None:
            t.paint, t.roads, t.generation = today[n].paint, today[n].roads, today[n].generation
        fake.append(t)
    result = rs.score_camera(camera, fake, targets)
    result["missing_layers"] = sorted(
        {k for n in names for k, v in tiles[n]["data"]["layers"].items() if v is None}
    )
    for key in ("ground_y", "eye_above_ground_m", "missing_tiles", "pass_kind"):
        result.pop(key, None)
    return result


def timings(
    tiles: list[str], g1: dict[str, dict], today: dict[str, rs.TileData], sources: Sources
) -> dict:
    """Seconds of the current, unchanged turku3d.props readers and visual_bytes on today's and
    G1's prop lists, plus cold parquet loads of the new layers (the per-worker frame cache cleared)."""
    from turku3d import spatial
    from turku3d.props import read_sources, visual_bytes

    out = {"tiles": {}}
    for tile in tiles:
        vt = C.VisualTile.parse(tile)
        names = ("GIS:Liikennemerkit", "kanta:Pysakki", "GIS:Keskilinjat", "GIS:Katualueet")
        paths = [sources.wfs_source(n, vt)[1] for n in names]
        osm = sources.osm_source(vt)
        if not all(paths) or osm is None:
            out["tiles"][tile] = {"skipped": "coarse pole inputs missing"}
            continue
        keys = ("GIS_Liikennemerkit", "kanta_Pysakki", "GIS_Keskilinjat", "GIS_Katualueet")
        t0 = time.perf_counter()
        poles, _ = read_sources(
            vt.bbox, source_paths=dict(zip(keys, paths, strict=True)), osm_path=osm
        )
        t1 = time.perf_counter()
        current = [p for p in poles if p.kind in ("sign", "bus_stop")]
        f = final(tile, g1[tile]["data"], today.get(tile))
        extra = [
            Prop(
                i["id"],
                "post" if i["kind"] == "other" else i["kind"],
                i["x"],
                0,
                i["z"],
                source=i["source"],
            )
            for i in f["lamps"] + f["station"] + f["furniture"]
        ]
        t2 = time.perf_counter()
        a = visual_bytes(current, vt) if current else b""
        t3 = time.perf_counter()
        b = visual_bytes(current + extra, vt)
        t4 = time.perf_counter()
        out["tiles"][tile] = {
            "coarse_poles_read_warm_s": round(t1 - t0, 3),
            "props_visual_today_s": round(t3 - t2, 4),
            "props_visual_g1_s": round(t4 - t3, 4),
            "props_today": len(current),
            "props_g1": len(current) + len(extra),
            "props_glb_bytes_today": len(a),
            "props_glb_bytes_g1": len(b),
            "trees_added": len(f["new_trees"]),
        }
    # Cold loads: what a fresh build worker pays once per raw file before its first G1 tile.
    vt = C.VisualTile.parse("t0_0_0")
    cold = {}
    for name in (
        "GIS:Varusteet",
        "GIS:Polkupyoraparkki",
        "GIS:Follariasemat",
        "GIS:Pysakointiautomaatit",
        "GIS:Puut",
    ):
        path = sources.wfs_source(name, vt)[1]
        if path is None:
            continue
        spatial._area.cache_clear()
        t0 = time.perf_counter()
        spatial.frame(path, vt.bbox)
        cold[name] = round(time.perf_counter() - t0, 3)
    out["cold_layer_load_s"] = cold
    spatial._area.cache_clear()
    t0 = time.perf_counter()
    read_sources(vt.bbox, "work-3x3")
    t1 = time.perf_counter()
    read_sources(vt.bbox, "work-3x3")
    t2 = time.perf_counter()
    out["full_props_reader_t0_0_0"] = {"cold_s": round(t1 - t0, 3), "warm_s": round(t2 - t1, 3)}
    return out


def markdown(report: dict) -> str:
    lines = [
        "| Tile | Street m | Lamps: register / OSM / 30 m rule = total (tile-level rule total) "
        "| Furniture: bench / bin / bike / meter / other = total | Furniture within 15 m of a street "
        "| Trees today + new register trees | Props added | Per 100 m: lamps / furniture |",
        "|---|---:|---|---|---:|---|---:|---|",
    ]
    for tile, t in report["tiles"].items():
        g = t["g1"]
        lamp = g["lamps"]
        f = g["furniture"]
        today = t.get("today", {})
        add = t.get("added", {})
        per = t.get("per_100m") or {}
        bike = f["bike_rack"]["total"] + f["bike_station"]["total"]
        lines.append(
            f"| {tile} | {t['street_m']:.0f} "
            f"| {lamp['register']} / {lamp['osm']} / {lamp['lit_rule_station']} = {lamp['total_station_rule']} "
            f"({lamp['total_tile_rule']}) "
            f"| {f['bench']['total']} / {f['bin']['total']} / {bike} / {f['parking_meter']['total']} / "
            f"{f['other']['total']} = {g['furniture_total']} | {g['furniture_near_street']} "
            f"| {today.get('trees', '?')} + {g['trees']['new_register']} "
            f"| {add.get('props_total', '?')} | {per.get('lamps', '–')} / {per.get('furniture', '–')} |"
        )
    lines += [
        "",
        "| Camera | Lamps 50 m | Furniture 50 m | Lamps / 100 m (target) | Furniture / 100 m (target) | Missing layers |",
        "|---|---:|---:|---|---|---|",
    ]
    for cid, c in report["cameras"].items():
        if "radius" not in c:
            continue
        tg = c.get("targets", {})

        def cell(metric, c=c, tg=tg):
            value = c.get("per_100m", {}).get("per_100m", {}).get(metric)
            t = tg.get(f"per_100m.{metric}")
            return f"{value:g}" + (f" ({t['target']}, {t['status']})" if t else "")

        lines.append(
            f"| {cid} | {c['radius']['lamps']} | {c['radius']['furniture']} | {cell('lamps')} | {cell('furniture')} "
            f"| {', '.join(c.get('missing_layers', [])) or '–'} |"
        )
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--tile", action="append", help=f"tiles (default: {' '.join(CENTRE_3X3)} {KUPITTAA})"
    )
    ap.add_argument("--url", default=rs.DEFAULT_URL, help="published tiles for today's counts")
    ap.add_argument("--tiles-dir", type=Path, help="local tile directory instead of --url")
    ap.add_argument("--cache", type=Path, help="scorecard blob cache directory")
    ap.add_argument("--cameras", type=Path, default=rs.CAMERAS)
    ap.add_argument("--targets", type=Path, default=rs.TARGETS)
    ap.add_argument("--no-timing", action="store_true")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--markdown", type=Path)
    a = ap.parse_args(argv)
    import tomllib

    tiles = a.tile or [*CENTRE_3X3, KUPITTAA]
    cameras = rs.load_cameras(a.cameras)
    for cam in cameras:  # camera tiles for the projections
        tiles += [n for n in rs.needed_tiles(cam) if n not in tiles]
    sources = Sources()
    published = rs.Source(url=None if a.tiles_dir else a.url, tiles=a.tiles_dir, cache=a.cache)
    targets = tomllib.loads(a.targets.read_text())
    missing: dict[str, list] = {}
    data, today = {}, {}
    report_tiles = {}
    requested = a.tile or [*CENTRE_3X3, KUPITTAA]
    for tile in tiles:
        data[tile] = {"data": g1_items(tile, sources, missing)}
        today[tile] = published.load(tile)
        if tile in requested:
            report_tiles[tile] = summarise(tile, data[tile]["data"], today[tile])
    report = {
        "raw_root": str(C.raw_dir()),
        "dedup_m": DEDUP_M,
        "lamp_reach_m": LAMP_REACH,
        "lamp_spacing_m": LAMP_SPACING,
        "missing_layers": {k: sorted(set(v)) for k, v in missing.items()},
        "tiles": report_tiles,
        "cameras": {c["id"]: camera_projection(c, data, today, targets) for c in cameras},
    }
    if not a.no_timing:
        report["timing"] = timings(requested, data, today, sources)
    text = json.dumps(report, indent=1, default=str) + "\n"
    if a.json:
        a.json.write_text(text)
    else:
        sys.stdout.write(text)
    table = markdown(report)
    if a.markdown:
        a.markdown.write_text(table)
    else:
        sys.stderr.write(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
