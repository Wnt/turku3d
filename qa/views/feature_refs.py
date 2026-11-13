#!/usr/bin/env python3
"""Feature views for comparing two world systems at the same cameras: a set stratified by kind of place, from data.

`select` reads the city's OSM table (pipeline/turku3d/world/sources/osm.py), the old system's landmark ranking
(turku3d.landmarks.ranking) and an optional list of extra spots (a JSON data file, e.g. review spots read from the
page), and writes a feature list (turku-feature-set/1): one row per target {id, feature, what, look_at [E, N],
look_h, camera?, near?, distance [lo, hi], fov}. Features: bridge, quay, bank, passage, landmark, pool, fountain,
square, crossing, street. Targets are kept inside the boxes given by --inside (a turku-region/1 index: its unit
squares) and spread out (at least --spread m apart within a feature).

`refs` turns a feature list into reference photos: for each row the newest street-level panorama that sees the
target (no OSM building footprint on the ray, except one that holds the target), lo..hi metres from it (near the
row's `camera` hint when given), straightened like every reference crop (qalib/upright.py) and cropped towards the
target (960x720). Rows go into the set's views.jsonl registry, crops into its refs/, and views.json is the
turku-capture-views/1 set for scene-capture.mjs (group = feature, aim = feature view). The panoramas stay outside Git.

Run from the repo root:
    bash pipeline/env/run.sh python qa/views/feature_refs.py select --inside centre.json --extra spots.json --out SET
    bash pipeline/env/run.sh python qa/views/feature_refs.py refs SET
"""

import argparse
import json
import math
import random
import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import shapely
from shapely import STRtree
from shapely.geometry import LineString, Point

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "qa"), str(ROOT / "textures"), str(ROOT / "qa/views"), str(ROOT / "pipeline")]
from pilot_refs import H, W, candidates  # noqa: E402
from PIL import Image  # noqa: E402
from qalib import geo, upright  # noqa: E402
from qalib.pano import perspective  # noqa: E402
from turku3d import config  # noqa: E402
from turkutex.streetview.panos import PanoCache, camera_height  # noqa: E402

Image.MAX_IMAGE_PIXELS = None
OSM = Path(config.data_root()) / "world/sources/osm"
RIVER_WAYS = {"river", "canal"}
STREETS = {"primary", "secondary", "tertiary", "residential", "unclassified", "living_street", "pedestrian"}


def osm_rows(box):
    """OSM rows (dicts with tags and shapely geometry) whose box meets (e0, n0, e1, n1)."""
    table = sorted(OSM.glob("*.parquet"))[-1]
    e0, n0, e1, n1 = box
    t = pq.read_table(table, filters=[("e1", ">=", e0), ("e0", "<=", e1), ("n1", ">=", n0), ("n0", "<=", n1)],
                      columns=["osm_type", "osm_id", "tags", "geometry"]).to_pandas()
    geoms = shapely.from_wkb(t["geometry"].values)
    return [dict(type=a, id=int(b), tags=json.loads(c) if isinstance(c, str) else (c or {}), geom=g)
            for a, b, c, g in zip(t["osm_type"], t["osm_id"], t["tags"], geoms)]


def region_squares(index):
    """Unit squares of a turku-region/1 index in survey coordinates (E0, N0, E1, N1)."""
    d = json.loads(Path(index).read_text())
    E0, N0 = d["origin"][0], d["origin"][1]
    # A square is engine x (E - E0) and z (-(N - N0)).
    return [(E0 + u["square"][0], N0 - u["square"][3], E0 + u["square"][2], N0 - u["square"][1]) for u in d["units"]]


def select(args):
    squares = region_squares(args.inside)
    inside = shapely.union_all([shapely.box(*s) for s in squares]).buffer(-args.margin)
    box = inside.bounds
    rows = osm_rows(box)
    within = [r for r in rows if r["geom"] is not None and inside.contains(r["geom"].representative_point())]
    buildings = [r["geom"] for r in rows if r["tags"].get("building") and r["geom"].geom_type in ("Polygon", "MultiPolygon")]
    btree = STRtree(buildings)
    water = shapely.union_all([r["geom"] for r in rows if r["geom"].geom_type in ("Polygon", "MultiPolygon") and (
        r["tags"].get("water") in RIVER_WAYS or r["tags"].get("waterway") == "riverbank")] or [Point(0, 0).buffer(0.1)])
    out = []

    def under_building(g):
        return any(buildings[j].contains(g.representative_point()) for j in btree.query(g.representative_point()))

    def pick(feature, cands, n, spread=None):
        """n candidates (geom, what, extra) spread at least `spread` m apart, in a seeded random order."""
        spread = args.spread if spread is None else spread
        rng = random.Random(f"{args.seed}-{feature}")  # one stream per feature: a change in one leaves the others
        rng.shuffle(cands)
        cands.sort(key=lambda c: -c[3] if len(c) > 3 else 0)  # a rank first when given, else the shuffled order
        took = []
        for c in cands:
            p = c[0]
            if all(p.distance(q[0]) >= spread for q in took):
                took.append(c)
            if len(took) == n:
                break
        for i, c in enumerate(took, 1):
            out.append(dict(id=f"{feature}-{i:02d}", feature=feature, what=c[1], look_at=[round(c[0].x, 2), round(c[0].y, 2)], **c[2]))

    # Bridges: carriageway, footway and rail bridges over the river (one per bridge name), other bridges apart.
    river = [r for r in within if r["tags"].get("bridge") not in (None, "no") and (r["tags"].get("highway") or r["tags"].get("railway"))
             and r["geom"].geom_type == "LineString" and r["geom"].length > 12]
    seen, over, other = set(), [], []
    for r in sorted(river, key=lambda r: -r["geom"].length):
        name = r["tags"].get("bridge:name") or r["tags"].get("name") or f"way {r['id']}"
        if name in seen:
            continue
        seen.add(name)
        mid = r["geom"].interpolate(0.5, normalized=True)
        what = f"bridge {name} ({r['tags'].get('highway') or r['tags'].get('railway')}, OSM way {r['id']})"
        if water.distance(mid) < 3:
            over.append((mid, what, dict(look_h=3.0, distance=[20, 90], fov=60), r["geom"].length))
        elif r["geom"].length > 20 and (r["tags"].get("railway") or r["tags"].get("highway") in STREETS):
            other.append((mid, what, dict(look_h=3.0, distance=[15, 70], fov=60), r["geom"].length))
    pick("bridge", over, 3, spread=120)
    pick("bridge-other", other, 1)
    # River edges: quays (a quay, wall or retaining wall at the edge) and natural banks, sampled every 25 m.
    hard = [r["geom"] for r in rows if r["tags"].get("man_made") == "quay" or r["tags"].get("barrier") in ("retaining_wall", "wall")
            or r["tags"].get("natural") == "coastline"]
    htree = STRtree(hard) if hard else None
    edge = water.boundary if water.area > 1 else None
    quays, banks = [], []
    if edge is not None:
        for line in getattr(edge, "geoms", [edge]):
            for s in np.arange(0, line.length, 25.0):
                p = line.interpolate(s)
                if not inside.contains(p):
                    continue
                near_hard = htree is not None and any(hard[j].distance(p) < 6 for j in htree.query(p.buffer(6)))
                # Seen along the river from the bank: the camera hint 35 m along the edge and 8 m inland of it.
                a, b = line.interpolate(max(0.0, s - 1)), line.interpolate(min(line.length, s + 1))
                tx, ty = (b.x - a.x), (b.y - a.y)
                norm = math.hypot(tx, ty) or 1.0
                nx, ny = -ty / norm, tx / norm
                if water.contains(Point(p.x + 3 * nx, p.y + 3 * ny)):
                    nx, ny = -nx, -ny
                ahead = line.interpolate(min(line.length, s + 35)) if s + 35 <= line.length else line.interpolate(max(0.0, s - 35))
                hint = [round(ahead.x + 8 * nx, 2), round(ahead.y + 8 * ny, 2)]
                (quays if near_hard else banks).append((p, f"river {'quay' if near_hard else 'bank'} at {p.x:.0f},{p.y:.0f}",
                                                        dict(look_h=0.5, distance=[15, 60], fov=60, camera=hint, near=18.0)))
    pick("quay", quays, 3, spread=150)
    pick("bank", banks, 2, spread=150)
    # Building passages and covered footways at ground level.
    pas = [r for r in within if r["geom"].geom_type == "LineString" and (r["tags"].get("tunnel") == "building_passage" or r["tags"].get("covered") in ("yes", "arcade"))
           and str(r["tags"].get("layer", "0")) in ("0", "") and r["tags"].get("indoor") in (None, "no")]
    pick("passage", [(r["geom"].interpolate(0.5, normalized=True), f"{r['tags'].get('tunnel') or 'covered'} {r['tags'].get('name', '')} (OSM way {r['id']})".replace("  ", " "),
                      dict(look_h=2.0, distance=[8, 45], fov=60, through=True)) for r in pas], 3)
    # Landmarks as the old system ranks them (turku3d.landmarks.ranking), buildings with a footprint.
    from turku3d.landmarks.ranking import rank
    lm = []
    for r in within:
        if r["tags"].get("building") and r["geom"].geom_type in ("Polygon", "MultiPolygon") and r["geom"].area > 150:
            res = rank(r["tags"])
            score = res["score"] if isinstance(res, dict) else res[0]
            if score >= args.landmark_min:
                h = float(str(r["tags"].get("height", "15")).split()[0].replace(",", ".") or 15) if str(r["tags"].get("height", "15")).split() else 15.0
                lm.append((r["geom"].representative_point(), f"landmark {r['tags'].get('name', '')} (OSM {r['type']} {r['id']}, rank {score:.0f})",
                           dict(look_h=min(12.0, h / 2), distance=[18, 110], fov=65, through=True), score))
    pick("landmark", lm, 3, spread=150)
    # Water features: outdoor pools (not under a roof) and fountains.
    pools = [r for r in within if r["tags"].get("leisure") == "swimming_pool" and r["tags"].get("indoor") not in ("yes",)
             and r["geom"].geom_type in ("Polygon", "MultiPolygon") and not under_building(r["geom"])]
    pick("pool", [(r["geom"].representative_point(), f"outdoor pool {r['tags'].get('name', '')} (OSM {r['type']} {r['id']})", dict(look_h=0.0, distance=[8, 60], fov=60)) for r in pools], 2)
    fountains = [r for r in within if r["tags"].get("amenity") == "fountain"]
    pick("fountain", [(r["geom"].representative_point(), f"fountain {r['tags'].get('name', '')} (OSM {r['type']} {r['id']})", dict(look_h=1.0, distance=[8, 45], fov=60)) for r in fountains], 3)
    # Squares: place=square and pedestrian areas of at least 1,500 m2.
    sq = [r for r in within if r["geom"].geom_type in ("Polygon", "MultiPolygon") and (r["tags"].get("place") == "square" or (
        r["tags"].get("highway") == "pedestrian" and r["tags"].get("area") == "yes")) and r["geom"].area >= 1500]
    pick("square", [(r["geom"].representative_point(), f"square {r['tags'].get('name', '')} (OSM {r['type']} {r['id']}, {r['geom'].area:.0f} m2)", dict(look_h=1.0, distance=[15, 80], fov=65), r["geom"].area) for r in sq], 3)
    # Signalised crossings.
    sig = [r for r in within if r["geom"].geom_type == "Point" and (r["tags"].get("highway") == "traffic_signals" or r["tags"].get("crossing") == "traffic_signals")]
    pick("crossing", [(r["geom"], f"signalised crossing (OSM node {r['id']})", dict(look_h=2.0, distance=[12, 45], fov=60)) for r in sig], 3)
    # Ordinary streets per area type, from the register (LoD2 year, storeys, class) within 50 m of a street point.
    if args.streets:
        types = street_types(rows, inside)
        for t, cands in types.items():
            pick(f"street-{t}", cands, args.streets, spread=150)
    # Extra spots (a data file: review spots read from the page, each {id, what, camera [E, N, H], heading, pitch}).
    for s in json.loads(Path(args.extra).read_text()) if args.extra else []:
        E, N, Hc = s["camera"]
        d = s.get("ahead", 25.0)
        t = math.radians(s["heading"])
        out.append(dict(id=s["id"], feature=s.get("feature", "review"), what=s["what"], look_at=[round(E + d * math.sin(t), 2), round(N + d * math.cos(t), 2)],
                        camera=[E, N], near=s.get("near", 30.0), look_h=s.get("look_h", 1.5), distance=[4, 80], fov=s.get("fov", 60),
                        through=True, review=s))
    if args.keep:
        out = [f for f in out if f["feature"] in args.keep.split(",")]
    Path(args.out).mkdir(parents=True, exist_ok=True)
    (Path(args.out) / "features.json").write_text(json.dumps(dict(schema="turku-feature-set/1", inside=str(args.inside), seed=args.seed, features=out), indent=1) + "\n")
    for f in out:
        print(f["id"], f["what"])


def lod2_register(box):
    """LoD2 buildings meeting box: (footprint, year, storeys, class) from the register parquet blocks (raw/wfs)."""
    import pandas as pd
    from shapely import force_2d
    e0, n0, e1, n1 = box
    out = {}
    for d in sorted((Path(config.data_root()) / "raw/wfs").glob("*")):
        try:
            be, bn = (float(x) for x in d.name.split("-", 1)[1].split("_"))
        except (ValueError, IndexError):
            continue
        f = d / "bldg_Building_LOD2.parquet"
        if not f.exists() or be > e1 or be + 500 < e0 or bn > n1 or bn + 500 < n0:
            continue
        df = pd.read_parquet(f, columns=["feature_key", "surface_type", "geometry", "yearOfConstruction", "storeysAboveGround", "class", "function"])
        for key, g in df[df["surface_type"] == "GroundSurface"].groupby("feature_key"):
            foot = shapely.union_all([force_2d(shapely.from_wkb(x)).buffer(0) for x in g["geometry"]])
            a = g.iloc[0]
            year = str(a["yearOfConstruction"])[:4]
            storeys = pd.to_numeric(a["storeysAboveGround"], errors="coerce")
            out[key] = (foot, int(year) if year.isdigit() else None, 0 if pd.isna(storeys) else float(storeys), str(a["class"]), str(a["function"]))
    return list(out.values())


def street_types(rows, inside):
    """Street points every 50 m, typed by the register floor area within 50 m: stone town (before 1940, 3+ storeys),
    wooden (before 1940, at most 2 storeys, residential), blocks-1950-79 (3+ storeys) and detached (from 1945, at most
    2 storeys, residential); a type that holds 60 % of the floor area names the point."""
    reg = lod2_register(inside.bounds)
    tree = STRtree([b[0] for b in reg])
    out = {"stone-town": [], "wooden": [], "blocks-1950-79": [], "detached": []}
    for r in rows:
        if r["geom"].geom_type != "LineString" or r["tags"].get("highway") not in STREETS or r["tags"].get("tunnel") or r["tags"].get("bridge"):
            continue
        line = r["geom"]
        for s in np.arange(25.0, line.length, 50.0):
            p = line.interpolate(s)
            if not inside.contains(p):
                continue
            floor = dict.fromkeys(out, 0.0)
            total = 0.0
            for j in tree.query(p.buffer(50)):
                foot, year, storeys, cls, fn = reg[j]
                if foot.distance(p) > 50 or foot.area < 40:
                    continue
                area = foot.area * max(1.0, storeys)
                total += area
                home = cls == "1000"
                if year and year < 1940 and storeys >= 3:
                    floor["stone-town"] += area
                elif year and year < 1940 and home:
                    floor["wooden"] += area
                elif year and 1950 <= year <= 1979 and storeys >= 3:
                    floor["blocks-1950-79"] += area
                elif year and year >= 1945 and storeys <= 2 and home:
                    floor["detached"] += area
            if total <= 0:
                continue
            t, a = max(floor.items(), key=lambda kv: kv[1])
            if a >= 0.6 * total:
                a0, b0 = line.interpolate(max(0.0, s - 20)), line.interpolate(min(line.length, s + 20))
                out[t].append((p, f"{t} street {r['tags'].get('name', '')} ({a / total:.0%} of the floor area, OSM way {r['id']})",
                               dict(look_h=1.5, distance=[0, 30], fov=60, along=[round(a0.x, 2), round(a0.y, 2), round(b0.x, 2), round(b0.y, 2)])))
    return out


def refs(args):
    s = Path(args.set)
    rows = json.loads((s / "features.json").read_text())["features"]
    if args.only:
        rows = [r for r in rows if r["id"] in args.only.split(",")]
    pts = [r["look_at"] for r in rows]
    box = (min(p[0] for p in pts) - 300, min(p[1] for p in pts) - 300, max(p[0] for p in pts) + 300, max(p[1] for p in pts) + 300)
    osm = osm_rows(box)
    polys = [r["geom"] for r in osm if r["tags"].get("building") and r["geom"].geom_type in ("Polygon", "MultiPolygon")
             and str(r["tags"].get("layer", "0")) in ("0", "") and r["tags"].get("building") not in ("roof", "bridge")]
    tree = STRtree(polys)
    cache, refreshed = PanoCache(), set()
    registry = s / "views.jsonl"
    old = {json.loads(line)["id"]: json.loads(line) for line in registry.read_text().splitlines() if line.strip()} if registry.exists() else {}
    done = dict(old)
    for r in rows:
        if r["id"].replace("-", "_") in old and not args.force:
            continue
        view, why = one(r, s / "refs", polys, tree, cache, refreshed, args.min_date)
        print(r["id"], why, flush=True)
        if view:
            done[view["id"]] = view
            registry.write_text("".join(json.dumps(v) + "\n" for v in done.values()))
    feats = {r["id"].replace("-", "_"): r for r in json.loads((s / "features.json").read_text())["features"]}
    views = [dict(id=vid.replace("_", "-"), reference=vid, group=feats[vid]["feature"] if vid in feats else "other", aim="feature")
             for vid in done]
    (s / "views.json").write_text(json.dumps(dict(
        schema="turku-capture-views/1", scene=None, note="w2-compare feature set: one camera per feature target, from its reference photo (qa/views/feature_refs.py)",
        references=str(registry.resolve()), history="judge.jsonl", quality="medium", time="2026-06-15T14:00:00+03:00", weather="clear",
        viewport=[960, 720], renderScale=1, views=views), indent=1) + "\n")


def one(r, out, polys, tree, cache, refreshed, min_date):
    if r.get("along"):  # an ordinary street: the along-street view of street_refs.py at the point
        from street_refs import street_view
        a, vid = r["along"], r["id"].replace("-", "_")
        view, why = street_view(LineString([(a[0], a[1]), (a[2], a[3])]), Point(*r["look_at"]), None, vid, r["feature"], cache, refreshed, out,
                                tags=["w2-compare", r["feature"]], min_date=min_date)
        if view:
            view["image"] = str((out / f"{vid}.jpg").resolve())
        return view, why
    tE, tN = r["look_at"]
    target = Point(tE, tN)
    lo, hi = r.get("distance", [10, 70])
    hint = Point(*r["camera"]) if r.get("camera") else None
    own = [j for j in tree.query(target) if polys[j].contains(target)] if r.get("through") else []
    if hint is not None:
        found = [m for m in candidates(cache, hint.x, hint.y, 0, r.get("near", 30.0), refreshed)]
    else:
        found = candidates(cache, tE, tN, lo, hi, refreshed)
    found = [m for m in found if (m.source == "lookaround" or len(m.id) == 22) and lo <= math.hypot(m.E - tE, m.N - tN) <= hi]
    ground = sorted(m.elevation - camera_height(m.source, m.date) for m in found)
    ground = ground[len(ground) // 2] if ground else 0.0

    def clear(m):
        cam = Point(m.E, m.N)
        if any(polys[j].contains(cam) for j in tree.query(cam)):
            return False
        ray = LineString([(m.E, m.N), (tE, tN)])
        ray = LineString([ray.coords[0], ray.interpolate(max(0.0, ray.length - 1.0)).coords[0]])
        return not any(j not in own and polys[j].intersects(ray) for j in tree.query(ray))

    pool = [m for m in found if abs(m.elevation - camera_height(m.source, m.date) - ground) < 3 and (min_date is None or (m.date or "") >= min_date) and clear(m)]
    if not pool:
        return None, f"no clear panorama {lo}-{hi} m from {r['what']}"
    if hint is not None:
        order = sorted(pool, key=lambda m: math.hypot(m.E - hint.x, m.N - hint.y))
    else:
        ideal = (lo + hi) / 2.5
        order = sorted(pool, key=lambda m: ((m.date or "")[:7], -abs(math.hypot(m.E - tE, m.N - tN) - ideal)), reverse=True)
    for m in order[:6]:
        if m.source == "lookaround":
            eq, heading = cache.lookaround_equirect(m, zoom=2), geo.true_to_grid_bearing(m.E, m.N, -math.degrees(m.heading_rad) + 180)
        else:
            eq, heading = cache.google_image(m.id, zoom=4), geo.true_to_grid_bearing(m.E, m.N, math.degrees(m.heading_rad))
        eq = Image.open(eq)
        straight = upright.estimate_up(eq, heading, upright.metadata_up(m.source, heading, m.pitch_rad, m.roll_rad))
        if not straight["straightened"]:
            continue
        d = math.hypot(m.E - tE, m.N - tN)
        cam = camera_height(m.source, m.date)
        pitch = max(-15.0, min(20.0, math.degrees(math.atan2(r.get("look_h", 1.5) - cam, d))))
        fov = r.get("fov", 60)
        crop = None
        for lift in (0.0, 2.0, 4.0, 6.0, 8.0) if m.source == "lookaround" else (0.0,):
            pose = dict(E=round(m.E, 3), N=round(m.N, 3), H=round(m.elevation, 3), yaw=round(geo.bearing(m.E, m.N, tE, tN) % 360, 3),
                        pitch=round(pitch + lift, 1), roll=0.0, fov=fov)
            crop = perspective(eq, heading, pose, W, H, up=straight["up"])
            if (np.asarray(crop).max(axis=2) < 6).mean() < 0.003:
                break
            crop = None
        if crop is not None:
            break
    else:
        return None, f"no level panorama fills the view of {r['what']}"
    if crop is None:
        return None, f"no level panorama fills the view of {r['what']}"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{r['id'].replace('-', '_')}.jpg"
    crop.save(path, quality=90)
    google = m.source == "google"
    vid = r["id"].replace("-", "_")
    return dict(id=vid, image=str(path.resolve()), width=W, height=H, pose=pose, time=None,
                source=dict(kind="google_streetview" if google else "apple_lookaround", pano_id=m.id, pano_heading=round(heading, 3),
                            note=f"w2-compare {r['feature']}: {r['what']}; {d:.0f} m away", straightening=straight),
                capture_date=m.date,
                pose_confidence=dict(level="medium" if google else "low", xy_m=1.5 if google else 2.0, h_m=1.0, angle_deg=3.0,
                                     note="Metadata pose, straightened by vertical lines; heading and position not lidar registered"),
                area=r["feature"], tags=["w2-compare", r["feature"]]), \
        f"{m.source} {m.date} {d:.0f} m, yaw {pose['yaw']:.0f} pitch {pose['pitch']:.0f} -> {r['what']}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("select")
    a.add_argument("--inside", required=True, help="turku-region/1 index whose unit squares bound the targets")
    a.add_argument("--extra", help="JSON list of extra spots {id, what, camera [E, N, H], heading, pitch}")
    a.add_argument("--out", required=True)
    a.add_argument("--seed", type=int, default=20261113)
    a.add_argument("--margin", type=float, default=40.0)
    a.add_argument("--spread", type=float, default=100.0)
    a.add_argument("--landmark-min", type=float, default=60.0)
    a.add_argument("--streets", type=int, default=0, help="street views per area type (0: none)")
    a.add_argument("--keep", help="comma-separated features to write (default: all)")
    b = sub.add_parser("refs")
    b.add_argument("set")
    b.add_argument("--only")
    b.add_argument("--force", action="store_true")
    b.add_argument("--min-date", default="2022")
    args = ap.parse_args()
    (select if args.cmd == "select" else refs)(args)


if __name__ == "__main__":
    main()
