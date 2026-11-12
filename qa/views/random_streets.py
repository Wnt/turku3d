#!/usr/bin/env python3
"""A fixed set of random on-street locations in built-up Turku, each with one reference view of its nearest building.

The set tests streets nobody tuned by hand. Candidate points are drawn along the street network (OSM streets inside
the municipality, weighted by length) from a recorded seed, and taken in that order. A candidate joins the set when:
- it is more than 300 m from every pilot target (pilot-targets.json) and 400 m from every point already taken
  (and from the points of the sets named with --avoid);
- the city's LoD2 register has a building of at least 50 m2 within 30 m of it: the nearest one is the target;
- its area type (below) is not yet half of the set, and every type can still get one point;
- a street panorama from --min-date on sees the target: the view is made exactly like a pilot view
  (pilot_refs.reference_view).
A point whose render in the frozen city turns out unusable is listed with --reject INDEX=REASON (kept in the set's
draw.json), and the same command then replaces it with the next candidates of the same sequence.

Area type, from the LoD2 buildings within 150 m (floor area by use):
- centre: floor-area ratio of at least 0.8, at least 15 % of it housing and under half industry, storage, schools
  or hospitals (closed blocks of housing, shops and offices);
- work: mostly industry, storage, trade, offices, schools, hospitals (industrial and campus areas);
- apartments: mostly housing of three storeys or more;
- houses: mostly housing of one or two storeys (detached and row houses).

Everything goes into --out: draw.json (seed, parameters, points, rejections), targets.json (as pilot-targets.json),
views.jsonl (the reference registry), refs/*.jpg (the photo crops), views.json (the capture view set for
scripts/dev/scene-capture.mjs and scene-judge.mjs, whose history is judge.jsonl beside it) and cache/ (LoD2 pages and
results per candidate, OSM extracts). None of it belongs in Git: the photos are private, and a hidden set stays hidden.

Run from the repo root (about 20 s per candidate that reaches the panorama step):
    bash pipeline/env/run.sh python qa/views/random_streets.py --set dev --count 12 --seed 7 --out DIR [--avoid DIR2/draw.json]
"""

import argparse
import json
import math
import random
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import shapely
from shapely import STRtree, force_2d, unary_union
from shapely.geometry import Point, shape

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "qa"), str(ROOT / "textures"), str(ROOT / "qa/views")]
from pilot_refs import reference_view  # noqa: E402
from qalib import registry  # noqa: E402
from turku3d import config  # noqa: E402
from turku3d.fetch import wfs  # noqa: E402
from turku3d.fetch.common import Registry  # noqa: E402
from turkutex.streetview.panos import PanoCache  # noqa: E402

STREETS = {"residential", "living_street", "unclassified", "tertiary", "secondary", "primary", "pedestrian"}
TYPES = ("centre", "apartments", "houses", "work")
PILOT_CLEAR_M, SPREAD_M, BUILDING_M, AREA_M, MIN_FOOTPRINT_M2, LOD2_HALF_M = 300.0, 400.0, 30.0, 150.0, 50.0, 200.0
WORK_CLASSES_HEAVY = {"691", "692", "699", "613", "1160", "1150", "1100", "1120"}  # industry, storage, education, health


def osm_layers(work: Path):
    """Streets (EPSG:3877 lines with names), building polygons and the municipality polygon from the OSM snapshot."""
    work.mkdir(parents=True, exist_ok=True)
    seq, adm = work / "turku-sel.geojsonseq", work / "adm8.geojsonseq"
    pbf = sorted((config.raw_dir("osm") / "geofabrik").glob("finland-*.osm.pbf"))[-1]
    if not seq.exists() or not adm.exists():
        run = lambda *a: subprocess.run(a, check=True)  # noqa: E731
        run("osmium", "extract", "-b", "22.08,60.36,22.46,60.56", "--strategy", "smart", str(pbf), "-o", str(work / "turku.osm.pbf"), "--overwrite")
        run("osmium", "tags-filter", str(work / "turku.osm.pbf"), "w/highway", "w/building", "-o", str(work / "turku-sel.osm.pbf"), "--overwrite")
        run("osmium", "export", str(work / "turku-sel.osm.pbf"), "-f", "geojsonseq", "-o", str(seq), "--overwrite", "--geometry-types=linestring,polygon")
        run("osmium", "tags-filter", str(pbf), "r/admin_level=8", "-o", str(work / "adm8.osm.pbf"), "--overwrite")
        run("osmium", "export", str(work / "adm8.osm.pbf"), "-f", "geojsonseq", "-o", str(adm), "--overwrite", "--geometry-types=polygon")
    from pyproj import Transformer

    project = Transformer.from_crs("EPSG:4326", config.CRS_HORIZONTAL, always_xy=True).transform
    to_en = lambda g: shapely.transform(g, lambda xy: np.column_stack(project(xy[:, 0], xy[:, 1])))  # noqa: E731
    rows = lambda f: (json.loads(line.strip("\x1e\n")) for line in open(f) if line.strip("\x1e\n"))  # noqa: E731
    city = next(to_en(shape(r["geometry"])) for r in rows(adm) if r["properties"].get("name") == "Turku")
    streets, buildings = [], []
    for r in rows(seq):
        p, kind = r["properties"], r["geometry"]["type"]
        if kind == "LineString" and p.get("highway") in STREETS and "tunnel" not in p and p.get("access") not in ("private", "no"):
            streets.append((to_en(shape(r["geometry"])), p.get("name")))
        elif kind != "LineString" and "building" in p:
            buildings.append(to_en(shape(r["geometry"])))
    return streets, buildings, city, pbf.name


def lod2_buildings(E, N, cache_dir: Path):
    """LoD2 buildings whose bbox meets the 400 m square around (E, N): footprint and register attributes, from the WFS."""
    layer = next(layer for layer in wfs.WFS_LAYERS if layer.name == "bldg:Building_LOD2")
    box = (E - LOD2_HALF_M, N - LOD2_HALF_M, E + LOD2_HALF_M, N + LOD2_HALF_M)
    pages = wfs.fetch_pages(layer, box, cache_dir, Registry(cache_dir), log=lambda s: None)
    gdf, _ = wfs.pages_to_frame(pages, layer.name)
    out = {}
    if "surface_type" not in gdf.columns:
        return []
    for key, rows in gdf[gdf["surface_type"] == "GroundSurface"].groupby("feature_key"):
        foot = unary_union([force_2d(g).buffer(0) for g in rows.geometry])
        if foot.is_empty:
            continue
        a = rows.iloc[0]
        num = lambda k: float(a[k]) if k in a.index and a[k] not in (None, "") and str(a[k]) != "<NA>" else 0.0  # noqa: E731
        txt = lambda k: str(a[k]) if k in a.index and str(a[k]) != "<NA>" else ""  # noqa: E731
        out[key] = dict(key=key, foot=foot, cls=txt("class"), fn=txt("function"), height=num("measuredHeight"), storeys=num("storeysAboveGround"),
                        floor_area=num("kerrosala"), year=txt("yearOfConstruction"), address=txt("taydellinen_osoite_fi"))
    return list(out.values())


def building_use(b):
    """houses, apartments or work; None for sheds, garages and unregistered small buildings."""
    if b["cls"] == "1000":
        return "houses" if b["fn"] == "1000" or b["storeys"] <= 2 else "apartments"
    if b["cls"] in ("0", "999", "") or (b["cls"] == "1150" and b["foot"].area < 200):
        return None
    return "work"


def area_type(E, N, buildings):
    disc = Point(E, N).buffer(AREA_M)
    floor = dict.fromkeys(("houses", "apartments", "work", "heavy"), 0.0)
    for b in buildings:
        use = building_use(b)
        if use is None or not b["foot"].intersects(disc):
            continue
        area = b["floor_area"] or b["foot"].area * max(1.0, b["storeys"])
        floor[use] += area
        if b["cls"] in WORK_CLASSES_HEAVY:
            floor["heavy"] += area
    total = floor["houses"] + floor["apartments"] + floor["work"]
    if total <= 0:
        return None, floor
    far = total / disc.area
    if far >= 0.8 and floor["heavy"] < 0.5 * total and floor["houses"] + floor["apartments"] >= 0.15 * total:
        return "centre", {**floor, "far": far}
    return max(("houses", "apartments", "work"), key=lambda k: floor[k]), {**floor, "far": far}


def pilot_points():
    """Where the pilot views look: the targets of pilot-targets.json and the cameras of its older rows."""
    spec = json.loads(Path(__file__).with_name("pilot-targets.json").read_text())
    rows = {r["id"]: r for r in registry.load()}
    pts = []
    for loc in spec["locations"]:
        pts += [tuple(t["look_at"]) for t in loc.get("targets", [])]
        pts += [(rows[i]["pose"]["E"], rows[i]["pose"]["N"]) for i in loc.get("existing", []) if i in rows]
    return pts


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", required=True, help="set name: view ids are <set>_NN in the registry and <set>-NN in views.json")
    ap.add_argument("--count", type=int, required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--avoid", type=Path, action="append", default=[], help="draw.json of another set: keep 400 m from its points")
    ap.add_argument("--reject", action="append", default=[], help="INDEX=REASON: a candidate whose render is unusable")
    ap.add_argument("--min-date", default="2024")
    ap.add_argument("--max-candidates", type=int, default=20000)
    a = ap.parse_args()
    out = a.out.expanduser().resolve()
    cache_dir = out / "cache"
    draw_file = out / "draw.json"
    old = json.loads(draw_file.read_text()) if draw_file.exists() else {}
    if old and (old["seed"], old["set"]) != (a.seed, a.set):
        sys.exit(f"{draw_file} holds set {old['set']} with another seed: use a new --out")
    rejected = dict(old.get("rejected", {}))
    for r in a.reject:
        i, _, why = r.partition("=")
        rejected[str(int(i))] = why or "unusable render"
    known_file = cache_dir / "candidates.json"  # index -> what the LoD2 and panorama steps found (they are not repeated)
    known = json.loads(known_file.read_text()) if known_file.exists() else {}

    t0 = time.time()
    streets, osm_buildings, city, snapshot = osm_layers(cache_dir / "osm")
    lengths = np.array([s.length for s, _ in streets])
    cum = np.cumsum(lengths)
    osm_tree = STRtree(osm_buildings)
    keep_off = [(p, PILOT_CLEAR_M) for p in pilot_points()]
    for f in a.avoid:
        keep_off += [((p["E"], p["N"]), SPREAD_M) for p in json.loads(f.expanduser().read_text())["points"]]
    print(f"{len(streets)} streets, {cum[-1] / 1000:.0f} km, {len(osm_buildings)} OSM buildings ({time.time() - t0:.0f} s)", flush=True)

    rng = random.Random(a.seed)
    panos, refreshed = PanoCache(), set()
    cap = a.count // 2
    points, counts, tally = [], dict.fromkeys(TYPES, 0), {}

    def no(why):
        tally[why] = tally.get(why, 0) + 1

    for index in range(a.max_candidates):
        if len(points) == a.count:
            break
        u = rng.random() * cum[-1]
        k = int(np.searchsorted(cum, u, side="right"))
        line, street = streets[k]
        at = line.interpolate(u - (cum[k] - lengths[k]))
        E, N = round(at.x, 1), round(at.y, 1)
        if not city.contains(at):
            no("outside the municipality")
            continue
        if not any(osm_buildings[j].distance(at) <= BUILDING_M for j in osm_tree.query(at.buffer(BUILDING_M))):
            no("no building within 30 m (OSM)")
            continue
        if any(math.hypot(E - p[0], N - p[1]) < d for p, d in keep_off):
            no("near a pilot target or another set")
            continue
        if any(math.hypot(E - p["E"], N - p["N"]) < SPREAD_M for p in points):
            no("within 400 m of a point of the set")
            continue
        if str(index) in rejected:
            no(f"rejected: {rejected[str(index)]}")
            continue
        c = known.setdefault(str(index), {})
        buildings = None
        if "target" not in c:
            buildings = lod2_buildings(E, N, cache_dir / "lod2" / f"{index:05}")
            near = [b for b in buildings if b["foot"].area >= MIN_FOOTPRINT_M2 and b["height"] > 0 and b["foot"].distance(at) <= BUILDING_M]
            kind, floor = area_type(E, N, buildings)
            if not near or kind is None:
                c["target"] = None
            else:
                b = min(near, key=lambda b: b["foot"].distance(at))
                inside = b["foot"].representative_point()
                c["target"] = dict(key=b["key"], look_at=[round(inside.x, 1), round(inside.y, 1)], height=b["height"],
                                   what=f"{b['address'] or 'building'} ({b['year'] or 'year unknown'}, {b['storeys']:.0f} storeys, {building_use(b) or 'other'})",
                                   distance_m=round(b["foot"].distance(at), 1))
                c["type"], c["floor"] = kind, {k: round(v, 2) for k, v in floor.items()}
        if c["target"] is None:
            no("no LoD2 building within 30 m")
            continue
        kind = c["type"]
        missing = [t for t in TYPES if counts[t] == 0]
        if counts[kind] >= cap:
            no(f"type cap: {kind}")
            continue
        if kind not in missing and a.count - len(points) <= len(missing):
            no("slot kept for a type not yet in the set")
            continue
        vid = f"{a.set}_{len(points) + 1:02}"
        if "view" not in c:
            if buildings is None:
                buildings = lod2_buildings(E, N, cache_dir / "lod2" / f"{index:05}")
            polys = [b["foot"] for b in buildings]
            view, said = reference_view(dict(c["target"]), f"cand_{index:05}", a.set, polys, STRtree(polys), panos, refreshed,
                                        cache_dir / "crops", tags=["random", a.set], min_date=a.min_date, label="random street")
            c["view"], c["said"] = view, said
            known_file.write_text(json.dumps(known) + "\n")
        if c["view"] is None:
            no("no panorama sees the target")
            print(f"  candidate {index}: {c['said']}", flush=True)
            continue
        counts[kind] += 1
        points.append(dict(id=vid, index=index, E=E, N=N, street=street, type=kind, target=c["target"], floor=c["floor"]))
        print(f"{vid}: candidate {index}, {kind}, {c['said']} ({time.time() - t0:.0f} s)", flush=True)
    else:
        print(f"only {len(points)} of {a.count} points after {a.max_candidates} candidates", flush=True)

    # The set's files, rewritten whole so that a redraw leaves nothing stale.
    (out / "refs").mkdir(parents=True, exist_ok=True)
    reg = out / "views.jsonl"
    reg.unlink(missing_ok=True)
    for p in points:
        view = dict(known[str(p["index"])]["view"])
        crop = out / "refs" / f"{p['id']}.jpg"
        crop.write_bytes((cache_dir / "crops" / f"cand_{p['index']:05}.jpg").read_bytes())
        view.update(id=p["id"], image=str(crop))
        registry.upsert(view, reg)
    (out / "targets.json").write_text(json.dumps({"schema": "turku-pilot-targets/1", "note": f"Random street set {a.set}: the building nearest each drawn point (qa/views/random_streets.py).",
                                                  "locations": [{"id": a.set, "label": f"Random streets ({a.set})",
                                                                 "targets": [dict(what=p["target"]["what"], look_at=p["target"]["look_at"], height=p["target"]["height"]) for p in points]}]}, indent=1) + "\n")
    (out / "views.json").write_text(json.dumps({
        "schema": "turku-capture-views/1", "scene": None,
        "note": f"Random street set {a.set}: one view per drawn point, from the camera of its reference photo (qa/views/random_streets.py). Settings as scripts/dev/views/pilot-locations.json.",
        "references": str(reg), "history": "judge.jsonl", "quality": "medium", "time": "2026-06-15T14:00:00+03:00", "weather": "clear", "viewport": [960, 720], "renderScale": 1,
        "views": [{"id": p["id"].replace("_", "-"), "reference": p["id"], "group": p["type"]} for p in points]}, indent=1) + "\n")
    draw_file.write_text(json.dumps(dict(
        schema="turku-random-streets/1", set=a.set, seed=a.seed, count=a.count, min_date=a.min_date, osm=snapshot,
        rules=dict(streets=sorted(STREETS), pilot_clear_m=PILOT_CLEAR_M, spread_m=SPREAD_M, building_m=BUILDING_M, min_footprint_m2=MIN_FOOTPRINT_M2,
                   area_m=AREA_M, type_cap=cap, avoid=[str(f) for f in a.avoid]),
        street_km=round(float(sum(s.length for s, _ in streets if city.contains(s.interpolate(0.5, normalized=True)))) / 1000, 1), skipped=tally, types=counts, points=points, rejected=rejected), indent=1) + "\n")
    known_file.write_text(json.dumps(known) + "\n")
    print(json.dumps(dict(points=len(points), types=counts, skipped=tally, seconds=round(time.time() - t0))))


if __name__ == "__main__":
    main()
