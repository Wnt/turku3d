#!/usr/bin/env python3
"""Reference photos of the pilot locations from the newest street panoramas.

For each target in pilot-targets.json (a building, by register coordinates and height), this:
- picks the newest Look Around or Street View panorama that sees the building's footprint (register LOD2 ground
  surfaces) unobstructed by other footprints, from a facade distance that fits the height. `from` (a compass bearing from the building) asks for
  a side.
- fetches it (textures/turkutex/streetview/panos.py) and straightens it (qalib/upright.py: vertical lines, else its
  own pitch and roll). One that cannot be straightened gives way to the next;
- crops it towards the building, pitched and widened so that the facade fits, from the ground to the roof line.

Rows go into views.jsonl with area = location id, and crops into QA_DATA/refs. The panoramas stay outside Git.
Run from the repo root:
    bash pipeline/env/run.sh python qa/views/pilot_refs.py [--only kauppatori,...] [--registry FILE]
"""

import argparse
import json
import math
import re
import sys
from pathlib import Path

import pandas as pd
from shapely import STRtree, wkb
from shapely.geometry import LineString, Point, Polygon

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "qa"), str(ROOT / "textures")]
from PIL import Image  # noqa: E402
from qalib import geo, registry, upright  # noqa: E402
from qalib.pano import perspective  # noqa: E402
from qalib.paths import data_dir, rel  # noqa: E402
from turku3d import config  # noqa: E402
from turkutex.streetview.panos import PanoCache, camera_height, z17_tile  # noqa: E402

Image.MAX_IMAGE_PIXELS = None
W, H = 960, 720


def footprints(points, margin=300.0):
    """Building footprints (EPSG:3877) from the register's LOD2 ground surfaces (raw/wfs), near the points."""
    import pyarrow.parquet as pq
    from shapely import force_2d, unary_union

    E0, E1 = min(p[0] for p in points) - margin, max(p[0] for p in points) + margin
    N0, N1 = min(p[1] for p in points) - margin, max(p[1] for p in points) + margin
    parts = {}
    for f in sorted((config.data_root() / "raw/wfs").glob("*/bldg_Building_LOD2.parquet")):
        if "surface_type" not in pq.read_schema(f).names:
            continue
        df = pd.read_parquet(f, columns=["feature_key", "surface_type", "geometry"])
        for key, g in df[df["surface_type"] == "GroundSurface"][["feature_key", "geometry"]].itertuples(index=False):
            g = force_2d(wkb.loads(g))
            x0, y0, x1, y1 = g.bounds
            if x1 >= E0 and x0 <= E1 and y1 >= N0 and y0 <= N1:
                parts.setdefault(key, []).append(g.buffer(0))
    return [unary_union(gs) for gs in parts.values()]


def candidates(cache, E, N, lo, hi, refreshed):
    """Panoramas lo..hi metres from (E, N), from the coverage tiles around it (each refreshed once per run)."""
    found = {}
    for dE, dN in [(0, 0), (-45, 0), (45, 0), (0, -45), (0, 45)]:
        lat, lon = config.en_to_wgs84(E + dE, N + dN)
        tile = z17_tile(lat, lon)
        refresh = tile not in refreshed
        refreshed.add(tile)
        for m in cache.lookaround_coverage(lat, lon, refresh=refresh):
            found[m.id] = m
        for p in cache.google_coverage(lat, lon, refresh=refresh):
            pe, pn = config.wgs84_to_en(p["lat"], p["lon"])
            if lo <= math.hypot(pe - E, pn - N) <= hi and (m := cache.google_newest(p["id"])) is not None:
                found[m.id] = m
    return [m for m in found.values() if lo <= math.hypot(m.E - E, m.N - N) <= hi]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--targets", type=Path, default=Path(__file__).with_name("pilot-targets.json"))
    ap.add_argument("--registry", type=Path, default=registry.default_registry())
    ap.add_argument("--only", help="comma-separated location ids")
    a = ap.parse_args()
    only = set(a.only.split(",")) if a.only else None
    cache, refreshed, out = PanoCache(), set(), data_dir() / "refs"
    out.mkdir(parents=True, exist_ok=True)
    locations = json.loads(a.targets.read_text())["locations"]
    polys = footprints([t["look_at"] for loc in locations for t in loc.get("targets", [])])
    tree = STRtree(polys)
    for loc in locations:
        if only and loc["id"] not in only:
            continue
        for i, t in enumerate(loc.get("targets", []), 1):
            tE, tN = t["look_at"]
            here = Point(tE, tN)
            near = [polys[j] for j in tree.query(here.buffer(10))]
            if not near:
                print(f"{loc['id']}_{i:02}: no footprint at {tE},{tN}")
                continue
            foot = min(near, key=lambda g: g.distance(here))
            ideal = t.get("ideal", min(35.0, max(14.0, 1.1 * t["height"])))

            def seen(m):
                """Facade distance when the footprint is in the requested direction and unobstructed, else None."""
                cam = Point(m.E, m.N)
                if foot.contains(cam):
                    return None
                if "from" in t:
                    off = (geo.bearing(tE, tN, m.E, m.N) - t["from"] + 180) % 360 - 180
                    if abs(off) > 35:
                        return None
                ray = LineString([cam, foot.exterior.interpolate(foot.exterior.project(cam)) if foot.geom_type == "Polygon"
                                  else foot.boundary.interpolate(foot.boundary.project(cam))])
                ray = LineString([ray.coords[0], ray.interpolate(ray.length - 0.5).coords[0]])
                if any(polys[j] is not foot and polys[j].intersects(ray) for j in tree.query(ray)):
                    return None
                return foot.distance(cam)

            lo, hi = t.get("distance", (8.0, max(40.0, 1.8 * t["height"])))
            found = [m for m in candidates(cache, tE, tN, 0, hi + 30, refreshed) if m.source == "lookaround" or len(m.id) == 22]
            ground = sorted(m.elevation - camera_height(m.source, m.date) for m in found)
            ground = ground[len(ground) // 2] if ground else 0.0
            # Street-level only: no user photospheres (indoor, rooftops) and nothing below the street (car parks).
            pool = [(m, fd) for m in found
                    if abs(m.elevation - camera_height(m.source, m.date) - ground) < 3 and (fd := seen(m)) is not None and lo <= fd <= hi]
            if not pool:
                print(f"{loc['id']}_{i:02}: no panorama sees {t['what']}")
                continue
            # Newest first, then the facade distance; a panorama is used only once it is straightened.
            for m, fd in sorted(pool, key=lambda p: (p[0].date or "", -abs(p[1] - ideal)), reverse=True)[:5]:
                if m.source == "lookaround":
                    eq, heading = cache.lookaround_equirect(m, zoom=2), geo.true_to_grid_bearing(m.E, m.N, -math.degrees(m.heading_rad) + 180)
                else:
                    eq, heading = cache.google_image(m.id, zoom=4), geo.true_to_grid_bearing(m.E, m.N, math.degrees(m.heading_rad))
                eq = Image.open(eq)
                straight = upright.estimate_up(eq, heading, upright.metadata_up(m.source, heading, m.pitch_rad, m.roll_rad))
                if straight["straightened"]:
                    break
            else:
                print(f"{loc['id']}_{i:02}: no level panorama sees {t['what']}")
                continue
            d = fd
            cam = camera_height(m.source, m.date)
            top = math.degrees(math.atan2(t["height"] + 1 - cam, d))  # roof line, 1 m above the register height
            base = math.degrees(math.atan2(-cam, d))  # foot of the facade
            pitch = max(0.0, min(25.0, (top + base) / 2))
            fov = max(55.0, min(85.0, top - base + 8))
            pose = dict(E=round(m.E, 3), N=round(m.N, 3), H=round(m.elevation, 3), yaw=round(geo.bearing(m.E, m.N, tE, tN) % 360, 3),
                        pitch=round(pitch, 1), roll=0.0, fov=round(fov, 1))
            vid = f"{loc['id']}_{i:02}"
            target = out / f"{vid}.jpg"
            perspective(eq, heading, pose, W, H, up=straight["up"]).save(target, quality=93)
            google = m.source == "google"
            view = dict(
                id=vid, image=rel(target), width=W, height=H, pose=pose, time=None,
                source=dict(kind="google_streetview" if google else "apple_lookaround", pano_id=m.id, pano_heading=round(heading, 3),
                            note=f"pilot target: {t['what']}; {d:.0f} m away", straightening=straight),
                capture_date=m.date,
                pose_confidence=dict(level="medium" if google else "low", xy_m=1.5 if google else 2.0, h_m=1.0, angle_deg=3.0,
                                     note="Metadata pose, straightened by vertical lines; heading and position not lidar registered"),
                area=loc["id"], tags=["pilot", loc["id"]],
            )
            registry.upsert(view, a.registry)
            print(f"{vid}: {m.source} {m.date} {d:.0f} m, pitch {pitch:.0f} fov {fov:.0f}, up from {straight['method']} -> {t['what']}")


if __name__ == "__main__":
    main()
