#!/usr/bin/env python3
"""Adapter: turku.360tour.fi (3DVista) downloads -> posed QA reference views.

Reads the output of the 360tour downloader (tools/refs/tdv360.py, owned by another worker) if it
is present; it never downloads anything itself. Expected layout (as written by tdv360.py today):
  <root>/<pano_id>/meta.json        {id, label, lat, lon, gps_alt_m, date "YYYY:MM:DD HH:MM:SS", kind, ...}
  <root>/<pano_id>/equirect_*.jpg   equirect whose centre column is TOUR yaw 0 (yaw clockwise, pitch up)
Optional keys the downloader may add later and that this adapter honours:
  heading_offset  compass bearing (deg) of tour yaw 0          (default: --heading-offset, required)
  camera_E, camera_N, camera_H   solved camera position (EPSG:3877 / N2000), overrides lat/lon/gps_alt
  pose_confidence {level, ...}
Tour yaw is NOT north: the offset must be solved per pano (docs/research/reference-photos.md; for
"View over the River Aura" it is ~0 deg). Drone GPS altitudes are unreliable, so poses from this
adapter default to confidence "low" unless meta.json says otherwise.

  qa/.venv/bin/python qa/views/tour360_adapter.py list
  qa/.venv/bin/python qa/views/tour360_adapter.py make --pano B5D39CE1 --heading-offset 0 \
      --tour-yaw -12 --pitch -25 --hfov 60 --size 1600x1200 --id tour_river_aura_pilot
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from qalib import camera, geo, registry  # noqa: E402
from qalib.pano import perspective  # noqa: E402
from qalib.paths import REPO, data_dir, rel  # noqa: E402

Image.MAX_IMAGE_PIXELS = None
DEFAULT_ROOT = REPO / "data" / "refs" / "360tour"


def find_panos(root: Path) -> list[dict]:
    out = []
    if not root.exists():
        return out
    for m in sorted(root.glob("*/meta.json")):
        meta = json.loads(m.read_text())
        eqs = sorted(m.parent.glob("equirect_[0-9]*.jpg"), key=lambda p: int(p.stem.split("_")[-1]))
        meta["_dir"] = str(m.parent)
        meta["_equirect"] = str(eqs[-1]) if eqs else None
        out.append(meta)
    return out


def _date(meta) -> tuple[str | None, str | None]:
    d = meta.get("date")
    if not d:
        return None, None
    ymd, _, hms = d.partition(" ")
    ymd = ymd.replace(":", "-")
    # EXIF DateTimeOriginal is local camera time; Finland is +03:00 in summer, +02:00 in winter.
    month = int(ymd[5:7]) if len(ymd) >= 7 else 1
    off = "+03:00" if 4 <= month <= 10 else "+02:00"
    return ymd, (f"{ymd}T{hms}{off}" if hms else None)


def make(pano_sel: str, tour_yaw: float, pitch: float, hfov: float, size, view_id: str | None,
         heading_offset: float | None, cam_h: float | None, root: Path = DEFAULT_ROOT, registry_path=None):
    cand = [p for p in find_panos(root) if pano_sel.lower() in p["id"].lower() or pano_sel.lower() in p.get("label", "").lower()]
    if len(cand) != 1:
        raise SystemExit(f"--pano matched {len(cand)} downloaded panoramas under {root}")
    meta = cand[0]
    if not meta["_equirect"]:
        raise SystemExit(f"{meta['id']}: no equirect_*.jpg; run tdv360.py fetch --equirect first")
    off = meta.get("heading_offset", heading_offset)
    if off is None:
        raise SystemExit("heading offset unknown: pass --heading-offset (compass bearing of tour yaw 0)")
    if "camera_E" in meta:
        e, n = meta["camera_E"], meta["camera_N"]
    elif meta.get("lat") is not None:
        e, n = geo.wgs84_to_en(meta["lat"], meta["lon"])
    else:
        raise SystemExit(f"{meta['id']}: no position in meta.json (no EXIF GPS); solve it first")
    h = meta.get("camera_H", cam_h if cam_h is not None else meta.get("gps_alt_m"))
    if h is None:
        raise SystemExit("camera height unknown: pass --h")
    w, hh = size
    pose = {"E": round(e, 3), "N": round(n, 3), "H": round(float(h), 3), "yaw": round((off + tour_yaw) % 360, 3),
            "pitch": pitch, "roll": 0.0, "fov": round(camera.vfov(hfov, w, hh), 4)}
    eq = Image.open(meta["_equirect"])
    view_id = view_id or f"tour_{meta['id'].split('_')[1]}_y{int(round(pose['yaw'])) % 360:03d}"
    out = data_dir() / "refs" / f"{view_id}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    perspective(eq, off, pose, w, hh).save(out, quality=93)
    date, t = _date(meta)
    conf = meta.get("pose_confidence") or {"level": "low", "xy_m": 10.0, "h_m": 20.0, "angle_deg": 3.0,
                                           "note": "EXIF GPS of the thumbnail; drone altitude unreliable; heading offset hand-solved"}
    view = {"id": view_id, "image": rel(out), "width": w, "height": hh, "pose": pose, "time": t,
            "source": {"kind": "360tour", "pano_id": meta["id"], "pano_heading": off,
                       "url": "https://turku.360tour.fi/", "note": f"{meta.get('label')} ({meta.get('kind')}); (c) A1 Media Oy, internal QA only"},
            "capture_date": date, "pose_confidence": conf, "area": "office-block", "tags": ["360tour", meta.get("kind", "")]}
    registry.upsert(view, registry_path)
    return view


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--registry", default=None)
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("list")
    m = sp.add_parser("make")
    m.add_argument("--pano", required=True)
    m.add_argument("--tour-yaw", type=float, required=True, help="yaw in the tour's own frame (deg)")
    m.add_argument("--pitch", type=float, default=0.0)
    m.add_argument("--hfov", type=float, default=60.0, help="horizontal fov (tdv360 --view convention)")
    m.add_argument("--size", default="1600x1200")
    m.add_argument("--heading-offset", type=float, help="compass bearing of tour yaw 0")
    m.add_argument("--h", type=float, help="camera height N2000 (default: meta gps_alt_m)")
    m.add_argument("--id")
    a = ap.parse_args(argv)
    root = Path(a.root)
    if a.cmd == "list":
        panos = find_panos(root)
        if not panos:
            print(f"no 360tour downloads under {root} (run tools/refs/tdv360.py fetch ... --equirect)")
        for p in panos:
            print(f"{p['id']}  {p.get('label')!r}  {p.get('kind')}  lat/lon={p.get('lat')},{p.get('lon')}  "
                  f"equirect={'yes' if p['_equirect'] else 'no'}  heading_offset={p.get('heading_offset', '?')}")
        return 0
    w, h = map(int, a.size.lower().split("x"))
    v = make(a.pano, a.tour_yaw, a.pitch, a.hfov, (w, h), a.id, a.heading_offset, a.h, root, a.registry)
    print(f"{v['id']}: {v['image']} pose={v['pose']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
