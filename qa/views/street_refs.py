#!/usr/bin/env python3
"""Along-street reference photos: the street itself, from the newest street panorama near a point.

A building-targeted view (pilot_refs.py) barely shows the ground, kerbs, markings, planting, fences, signs, lamps and
furniture. This makes the companion view of any point:
- the street is the OSM street nearest the point (or the one given);
- the panorama is the newest street-level Look Around or Street View one on that street (within 10 m of its centre
  line) and within 30 m of the point, the nearest one of that month; `min_date` keeps only newer ones;
- it is straightened like every reference crop (qalib/upright.py), and cropped looking along the street axis with a
  level horizon: pitch 0, roll 0, vertical field of view 60 degrees, 960x720. Of the two directions it takes the one
  in which `ahead` (a building, usually) lies in front of the camera. Where a Look Around panorama has no pixels at
  the bottom of that frame, the camera looks up by 2 to 8 degrees, as little as clears the gap.

Pilot locations: for each location of pilot-targets.json it makes --per-location views (default 2), each on the street
nearest one of the location's targets, from 25 m before the target and looking towards it. Rows go into views.jsonl as
<location>_street_NN (tag pilot-street), crops into QA_DATA/refs; the judged set is scripts/dev/views/pilot-streets.json.
Any other point: --at E,N --id NAME [--ahead E,N].

Run from the repo root (about a minute for all locations, after 15 s of reading the OSM streets):
    bash pipeline/env/run.sh python qa/views/street_refs.py [--only kauppatori,...] [--registry FILE]
    bash pipeline/env/run.sh python qa/views/street_refs.py --at 23459140,6704730 --id somewhere_street_01 --registry FILE
"""

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import shapely
from shapely import STRtree
from shapely.geometry import Point, shape

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "qa"), str(ROOT / "textures"), str(ROOT / "qa/views")]
from pilot_refs import H, W, candidates  # noqa: E402
from PIL import Image  # noqa: E402
from qalib import geo, registry, upright  # noqa: E402
from qalib.pano import perspective  # noqa: E402
from qalib.paths import data_dir, rel  # noqa: E402
from turku3d import config  # noqa: E402
from turkutex.streetview.panos import PanoCache, camera_height  # noqa: E402

STREETS = {"residential", "living_street", "unclassified", "tertiary", "secondary", "primary", "pedestrian"}
NEAR_M, ON_STREET_M, FOV, BEFORE_M, PITCHES = 30.0, 10.0, 60.0, 25.0, (0.0, 2.0, 4.0, 6.0, 8.0)


def osm_layers(work: Path | None = None):
    """Streets (EPSG:3877 lines with names), building polygons and the municipality polygon from the OSM snapshot
    (raw/osm/geofabrik), extracted once into the sandbox cache."""
    work = work or config.cache_dir("osm-streets")
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


def street_view(line, at, ahead, vid, area, cache, refreshed, out, tags=(), min_date=None, what="the street", skip=()):
    """The along-street view row at `at` on the street `line` (its crop is written to out/<vid>.jpg) and a one-line
    summary, or (None, why not). `ahead` (E, N) picks the direction; `skip` holds panorama ids not to use again."""
    found = [m for m in candidates(cache, at.x, at.y, 0, NEAR_M, refreshed) if m.source == "lookaround" or len(m.id) == 22]
    ground = sorted(m.elevation - camera_height(m.source, m.date) for m in found)
    ground = ground[len(ground) // 2] if ground else 0.0
    pool = [m for m in found if abs(m.elevation - camera_height(m.source, m.date) - ground) < 3 and m.id not in skip
            and line.distance(Point(m.E, m.N)) <= ON_STREET_M and (min_date is None or (m.date or "") >= min_date)]
    if not pool:
        return None, f"no panorama{f' since {min_date}' if min_date else ''} on the street within {NEAR_M:.0f} m of {what}"
    # The newest month first, then the nearest; a panorama is used only once it is straightened and fills the frame.
    crop = None
    for m in sorted(pool, key=lambda m: ((m.date or "")[:7], -math.hypot(m.E - at.x, m.N - at.y)), reverse=True)[:5]:
        if m.source == "lookaround":
            eq, heading = cache.lookaround_equirect(m, zoom=2), geo.true_to_grid_bearing(m.E, m.N, -math.degrees(m.heading_rad) + 180)
        else:
            eq, heading = cache.google_image(m.id, zoom=4), geo.true_to_grid_bearing(m.E, m.N, math.degrees(m.heading_rad))
        eq = Image.open(eq)
        straight = upright.estimate_up(eq, heading, upright.metadata_up(m.source, heading, m.pitch_rad, m.roll_rad))
        if not straight["straightened"]:
            continue
        s = line.project(Point(m.E, m.N))
        a, b = line.interpolate(max(0.0, s - 6)), line.interpolate(min(line.length, s + 6))
        yaw = geo.bearing(a.x, a.y, b.x, b.y)
        if ahead is not None and math.cos(math.radians(geo.bearing(m.E, m.N, ahead[0], ahead[1]) - yaw)) < 0:
            yaw = (yaw + 180) % 360
        # Look Around equirects have no pixels below about 25 degrees under the horizon: the camera then looks up a
        # little, by as much as clears the gap, and a panorama that needs more than 8 degrees gives way to the next.
        for pitch in PITCHES:
            pose = dict(E=round(m.E, 3), N=round(m.N, 3), H=round(m.elevation, 3), yaw=round(yaw, 3), pitch=pitch, roll=0.0, fov=FOV)
            crop = perspective(eq, heading, pose, W, H, up=straight["up"])
            if (np.asarray(crop).max(axis=2) < 6).mean() < 0.003:
                break
            crop = None
        if crop is not None:
            break
    if crop is None:
        return None, f"no level panorama fills the view on the street near {what}"
    target = out / f"{vid}.jpg"
    out.mkdir(parents=True, exist_ok=True)
    crop.save(target, quality=93)
    google = m.source == "google"
    away = math.hypot(m.E - at.x, m.N - at.y)
    view = dict(
        id=vid, image=rel(target), width=W, height=H, pose=pose, time=None,
        source=dict(kind="google_streetview" if google else "apple_lookaround", pano_id=m.id, pano_heading=round(heading, 3),
                    note=f"along the street at {what}; {away:.0f} m from the point", straightening=straight),
        capture_date=m.date,
        pose_confidence=dict(level="medium" if google else "low", xy_m=1.5 if google else 2.0, h_m=1.0, angle_deg=3.0,
                             note="Metadata pose, straightened by vertical lines; heading and position not lidar registered"),
        area=area, tags=list(tags),
    )
    return view, f"{m.source} {m.date} {away:.0f} m from the point, yaw {yaw:.0f} pitch {pitch:.0f}, up from {straight['method']} -> along the street at {what}"


def nearest_street(streets, tree, point, within):
    """(line, name) of the street nearest the point, or None beyond `within` metres."""
    near = [streets[j] for j in tree.query(point.buffer(within))]
    near = [s for s in near if s[0].distance(point) <= within]
    return min(near, key=lambda s: s[0].distance(point)) if near else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--targets", type=Path, default=Path(__file__).with_name("pilot-targets.json"))
    ap.add_argument("--registry", type=Path, default=registry.default_registry())
    ap.add_argument("--only", help="comma-separated location ids")
    ap.add_argument("--per-location", type=int, default=2)
    ap.add_argument("--min-date", default="2024")
    ap.add_argument("--at", help="E,N: one view at any point instead of the pilot locations")
    ap.add_argument("--ahead", help="E,N: with --at, what should lie in front of the camera")
    ap.add_argument("--id", help="with --at, the view id")
    a = ap.parse_args()
    cache, refreshed, out = PanoCache(), set(), data_dir() / "refs"
    streets, _, _, _ = osm_layers()
    tree = STRtree([s for s, _ in streets])
    if a.at:
        at = Point(*map(float, a.at.split(",")))
        street = nearest_street(streets, tree, at, 60.0)
        if not street or not a.id:
            sys.exit("--at needs --id and a street within 60 m")
        ahead = tuple(map(float, a.ahead.split(","))) if a.ahead else None
        view, said = street_view(street[0], street[0].interpolate(street[0].project(at)), ahead, a.id, a.id, cache, refreshed, out,
                                 tags=["street"], min_date=a.min_date, what=street[1] or "an unnamed street")
        if view:
            registry.upsert(view, a.registry)
        print(f"{a.id}: {said}")
        return
    rows = {r["id"]: r for r in registry.load()}
    only = set(a.only.split(",")) if a.only else None
    for loc in json.loads(a.targets.read_text())["locations"]:
        if only and loc["id"] not in only:
            continue
        # What each view looks towards: a target building, or the spot 20 m in front of an older row's camera.
        anchors = [(t["look_at"], t["what"]) for t in loc.get("targets", [])]
        for i in loc.get("existing", []):
            p, r = rows[i]["pose"], math.radians(rows[i]["pose"]["yaw"])
            anchors.append(([p["E"] + 20 * math.sin(r), p["N"] + 20 * math.cos(r)], f"the view {i}"))
        made = []
        for (E, N), what in anchors:
            if len(made) == a.per_location:
                break
            street = nearest_street(streets, tree, Point(E, N), 60.0)
            if not street:
                print(f"{loc['id']}: no street within 60 m of {what}")
                continue
            line, name = street
            s = line.project(Point(E, N))
            vid = f"{loc['id']}_street_{len(made) + 1:02}"
            for back in (-BEFORE_M, BEFORE_M):
                at = line.interpolate(min(line.length, max(0.0, s + back)))
                view, said = street_view(line, at, (E, N), vid, loc["id"], cache, refreshed, out, tags=["pilot-street", loc["id"]], min_date=a.min_date,
                                         what=f"{name or 'an unnamed street'}, towards {what}", skip={v["source"]["pano_id"] for v in made})
                # Two views of a location are two places: at least 30 m apart.
                if view and all(math.hypot(view["pose"]["E"] - v["pose"]["E"], view["pose"]["N"] - v["pose"]["N"]) >= 30 for v in made):
                    registry.upsert(view, a.registry)
                    made.append(view)
                    print(f"{vid}: {said}")
                    break
            else:
                print(f"{loc['id']}: {said if not view else 'the panorama is within 30 m of the previous view'} ({what})")
        if len(made) < a.per_location:
            print(f"{loc['id']}: only {len(made)} of {a.per_location} along-street views")


if __name__ == "__main__":
    main()
