#!/usr/bin/env python3
"""Create posed perspective reference views from Google Street View (and, experimentally, Apple
Look Around) panoramas, and register them in qa/views/views.jsonl.

  search   list panos around a point (id, date, distance, bearing to a target)
  make     download one pano (cached in data/qa/panos/), cut a perspective view, register it
  refetch  re-create missing reference images from the registry rows (images are not committed)

Pose: E/N from the pano lat/lon (EPSG:3877), H = Google `elevation` (orthometric camera height,
evidence from one sample in docs/research/streetview.md; we record it as medium confidence), yaw /
pitch / fov as requested. The pano's own pitch/roll tilt (< 2 deg) is ignored.

Examples (from the repo root):
  qa/.venv/bin/python qa/views/sv_views.py search --near 23459668,6704254 --half 60
  qa/.venv/bin/python qa/views/sv_views.py make --pano hOH16IE5o9B6ar0x7Cm-9w \
      --look-at 23459668,6704254 --pitch 10 --fov 60 --size 1280x960 --id office_front
  qa/.venv/bin/python qa/views/sv_views.py refetch
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from qalib import geo, registry  # noqa: E402
from qalib.pano import perspective  # noqa: E402
from qalib.paths import data_dir, rel, resolve  # noqa: E402


LA_CENTRE_OFFSET = -90.0  # see fetch_lookaround(); one-sample visual estimate


def _sv():
    from streetlevel import streetview
    return streetview


def _sdeg(rad) -> float:
    return (math.degrees(rad or 0.0) + 180.0) % 360.0 - 180.0


def pano_cache(pano_id: str, zoom: int) -> Path:
    return data_dir() / "panos" / f"gsv_{pano_id}_z{zoom}.jpg"


def fetch_google(pano_id: str, zoom: int = 4):
    """Return (metadata, PIL equirect). The equirect is cached on disk."""
    sv = _sv()
    meta = sv.find_panorama_by_id(pano_id)
    if meta is None:
        raise SystemExit(f"pano {pano_id} not found")
    path = pano_cache(pano_id, zoom)
    if path.exists():
        img = Image.open(path)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        img = sv.get_panorama(meta, zoom=zoom)
        img.save(path, quality=92)
    return meta, img


def fetch_lookaround(lat: float, lon: float, zoom: int = 2):
    """Experimental: nearest Look Around pano -> equirect via streetlevel.lookaround.reproject
    (needs torch + torchvision installed in qa/.venv). Returns (meta, equirect, heading_of_centre).
    UNVERIFIED: the compass yaw of the equirect centre. streetlevel documents it as 'the inverse
    direction of travel', but on one Turku pano (2021-07-09, id 12202934725221942688) heading+180
    was visibly wrong and heading-90 (+-15 deg, judged by eye from the river bank) fitted. We use
    heading + LA_CENTRE_OFFSET and mark the pose low-confidence; solve it properly before use."""
    from streetlevel import lookaround
    from streetlevel.lookaround.reproject import to_equirectangular  # imports torch
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError as e:  # pragma: no cover
        raise SystemExit("pip install pillow-heif for Look Around faces") from e
    import io
    tile = lookaround.get_coverage_tile_by_latlon(lat, lon)
    best = min(tile.panos, key=lambda p: (p.lat - lat) ** 2 + ((p.lon - lon) * math.cos(math.radians(lat))) ** 2)
    auth = lookaround.Authenticator()
    faces = [Image.open(io.BytesIO(lookaround.get_panorama_face(best, f, zoom, auth))) for f in range(6)]
    eq = to_equirectangular(faces, best.camera_metadata)
    return best, eq, (math.degrees(best.heading) + LA_CENTRE_OFFSET) % 360.0


def cmd_search(a):
    e0, n0 = map(float, a.near.split(","))
    te, tn = map(float, (a.target or a.near).split(","))
    sv = _sv()
    seen = {}
    steps = range(-int(a.half), int(a.half) + 1, int(a.step))
    for de in steps:
        for dn in steps:
            lat, lon = geo.en_to_wgs84(e0 + de, n0 + dn)
            p = sv.find_panorama(lat, lon, radius=a.step)
            if p and p.id not in seen:
                seen[p.id] = p
    rows = []
    for p in seen.values():
        e, n = geo.wgs84_to_en(p.lat, p.lon)
        rows.append((geo.distance(e, n, te, tn), p, e, n))
    for d, p, e, n in sorted(rows, key=lambda r: r[0]):
        print(f"{p.id}  {p.date}  dist={d:6.1f} m  bearing_to_target={geo.bearing(e, n, te, tn):6.1f}  "
              f"E={e:.1f} N={n:.1f} elev={p.elevation:.2f}")


def make_view(*, pano_id=None, near=None, provider="google", look_at=None, yaw=None, pitch=0.0, roll=0.0,
              fov=60.0, size=(1280, 960), view_id=None, zoom=4, time=None, tags=(), registry_path=None,
              out_dir=None, area="office-block"):
    w, h = size
    if provider == "google":
        if pano_id is None:
            lat, lon = geo.en_to_wgs84(*near)
            p0 = _sv().find_panorama(lat, lon, radius=50)
            if p0 is None:
                raise SystemExit("no Street View pano near the point")
            pano_id = p0.id
        meta, eq = fetch_google(pano_id, zoom)
        heading = math.degrees(meta.heading)
        e, n = geo.wgs84_to_en(meta.lat, meta.lon)
        cam_h = float(meta.elevation)
        date = str(meta.date) if meta.date else None
        src = {"kind": "google_streetview", "pano_id": meta.id, "pano_heading": round(heading, 3),
               "url": f"https://www.google.com/maps/@?api=1&map_action=pano&pano={meta.id}",
               "note": f"pano tilt pitch={_sdeg(meta.pitch):.2f} roll={_sdeg(meta.roll):.2f} deg ignored; zoom {zoom}"}
        conf = {"level": "medium", "xy_m": 1.5, "h_m": 0.7, "angle_deg": 2.0,
                "note": "Google lat/lon/heading; H = Google elevation (camera N2000, 1-sample evidence); not lidar-registered"}
    elif provider == "lookaround":
        lat, lon = geo.en_to_wgs84(*near)
        meta, eq, heading = fetch_lookaround(lat, lon)
        pano_id = f"{meta.id}"
        e, n = geo.wgs84_to_en(meta.lat, meta.lon)
        cam_h = float(meta.elevation)
        date = meta.date.strftime("%Y-%m-%d") if meta.date else None
        src = {"kind": "apple_lookaround", "pano_id": str(meta.id), "pano_heading": round(heading, 3),
               "note": f"EXPERIMENTAL: equirect centre yaw assumed heading{LA_CENTRE_OFFSET:+.0f} (1-sample visual estimate)"}
        conf = {"level": "low", "xy_m": 2.0, "h_m": 1.0, "angle_deg": 15.0,
                "note": "Look Around reprojection orientation unverified"}
    else:
        raise SystemExit(f"unknown provider {provider}")

    if look_at is not None:
        yaw = geo.bearing(e, n, look_at[0], look_at[1])
        if len(look_at) > 2 and look_at[2] is not None:
            pitch = math.degrees(math.atan2(look_at[2] - cam_h, geo.distance(e, n, look_at[0], look_at[1])))
    if yaw is None:
        raise SystemExit("give --yaw or --look-at")
    pose = {"E": round(e, 3), "N": round(n, 3), "H": round(cam_h, 3), "yaw": round(yaw % 360.0, 3),
            "pitch": round(pitch, 3), "roll": round(roll, 3), "fov": round(fov, 3)}
    view_id = view_id or f"{'gsv' if provider == 'google' else 'la'}_{pano_id[:10]}_y{int(round(yaw)) % 360:03d}"
    out_dir = Path(out_dir or data_dir() / "refs")
    out_dir.mkdir(parents=True, exist_ok=True)
    img_path = out_dir / f"{view_id}.jpg"
    perspective(eq, heading, pose, w, h).save(img_path, quality=93)
    view = {"id": view_id, "image": rel(img_path), "width": w, "height": h, "pose": pose, "time": time,
            "source": src, "capture_date": date, "pose_confidence": conf, "area": area, "tags": list(tags)}
    if registry_path is not False:
        registry.upsert(view, registry_path)
    return view


def cmd_make(a):
    look = None
    if a.look_at:
        parts = [float(x) for x in a.look_at.split(",")]
        look = (parts[0], parts[1], parts[2] if len(parts) > 2 else None)
    w, h = map(int, a.size.lower().split("x"))
    near = tuple(map(float, a.near.split(","))) if a.near else None
    v = make_view(pano_id=a.pano, near=near, provider=a.provider, look_at=look, yaw=a.yaw, pitch=a.pitch,
                  roll=a.roll, fov=a.fov, size=(w, h), view_id=a.id, zoom=a.zoom, time=a.time,
                  tags=a.tag or [], registry_path=a.registry)
    print(f"{v['id']}: {v['image']}  pose={v['pose']}  date={v['capture_date']}")


def cmd_refetch(a):
    for v in registry.load(a.registry):
        if resolve(v["image"]).exists() and not a.force:
            continue
        s = v["source"]
        if s["kind"] != "google_streetview":
            print(f"skip {v['id']}: cannot refetch kind {s['kind']} here")
            continue
        meta, eq = fetch_google(s["pano_id"], a.zoom)
        out = resolve(v["image"])
        out.parent.mkdir(parents=True, exist_ok=True)
        perspective(eq, s.get("pano_heading", math.degrees(meta.heading)), v["pose"], v["width"], v["height"]).save(out, quality=93)
        print(f"refetched {v['id']} -> {v['image']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--registry", default=None, help="views.jsonl (default qa/views/views.jsonl)")
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("search")
    s.add_argument("--near", required=True, help="E,N centre of the search grid")
    s.add_argument("--target", help="E,N to report bearings to (default = --near)")
    s.add_argument("--half", type=float, default=60)
    s.add_argument("--step", type=float, default=20)
    m = sp.add_parser("make")
    m.add_argument("--pano", help="Street View pano id")
    m.add_argument("--near", help="E,N: use the nearest pano instead of --pano")
    m.add_argument("--provider", default="google", choices=["google", "lookaround"])
    m.add_argument("--look-at", help="E,N[,H] target; sets yaw (and pitch if H is given)")
    m.add_argument("--yaw", type=float)
    m.add_argument("--pitch", type=float, default=0.0)
    m.add_argument("--roll", type=float, default=0.0)
    m.add_argument("--fov", type=float, default=60.0, help="vertical fov, deg")
    m.add_argument("--size", default="1280x960")
    m.add_argument("--zoom", type=int, default=4, help="4 = 8192x4096 equirect")
    m.add_argument("--time", help="ISO time with offset for the render sun (default: none)")
    m.add_argument("--id")
    m.add_argument("--tag", action="append")
    r = sp.add_parser("refetch")
    r.add_argument("--zoom", type=int, default=4)
    r.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    {"search": cmd_search, "make": cmd_make, "refetch": cmd_refetch}[a.cmd](a)


if __name__ == "__main__":
    main()
