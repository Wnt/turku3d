#!/usr/bin/env python3
"""
Enumerate and download Google Maps *user-contributed photo spheres* (third-party
360 panoramas, ids "CIHM0og...") around a point, without an API key, using the
`streetlevel` package's Street View metadata RPC with search_third_party=True.

`find_panorama` returns only the single nearest pano per query, so we sample a grid
of query points (radius ~= step) and de-duplicate.

Setup (streetlevel 0.12.13 hard-depends on pyfrpc (Mapy.cz) which does not build on
Python 3.14, and on pyexiv2 whose wheel needs a missing Homebrew lib; neither is
needed for Google):
  uv venv -p 3.12 .venv-sl
  uv pip install -p .venv-sl/bin/python --no-deps streetlevel
  uv pip install -p .venv-sl/bin/python aiohttp numpy pillow 'protobuf>4' pycryptodome \
      requests scipy pyproj bd09convertor coordinatesconverter pyequilib
  # stub pyexiv2 (only used when writing EXIF):
  SP=$(.venv-sl/bin/python -c "import site;print(site.getsitepackages()[0])")
  mkdir -p $SP/pyexiv2 && printf 'class Image: pass\nclass ImageData: pass\n' > $SP/pyexiv2/__init__.py

Usage:
  .venv-sl/bin/python tools/refs/gmaps_photospheres.py --center 60.44839,22.26726 \
      --half 300 --step 60 --out data/refs/gmaps_spheres --download 3 --zoom 4
"""
import argparse, json, math, os, sys
from streetlevel import streetview as sv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--center", required=True)
    ap.add_argument("--half", type=float, default=300, help="half-size of square search area, m")
    ap.add_argument("--step", type=float, default=60, help="grid step, m (query radius = step)")
    ap.add_argument("--out", default="data/refs/gmaps_spheres")
    ap.add_argument("--download", type=int, default=0, help="download N nearest third-party panos")
    ap.add_argument("--zoom", type=int, default=4, help="streetlevel zoom (max varies per pano)")
    ap.add_argument("--official", action="store_true", help="also keep Google's own Street View panos")
    a = ap.parse_args()
    lat0, lon0 = map(float, a.center.split(","))
    mlat, mlon = 111320.0, 111320.0 * math.cos(math.radians(lat0))
    n = int(a.half // a.step)
    found = {}
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            lat, lon = lat0 + i * a.step / mlat, lon0 + j * a.step / mlon
            try:
                p = sv.find_panorama(lat, lon, radius=int(a.step), search_third_party=True)
            except Exception as e:
                print("query failed", lat, lon, e, file=sys.stderr); continue
            if not p or p.id in found:
                continue
            third = sv.is_third_party_panoid(p.id)
            if not third and not a.official:
                continue
            d = math.hypot((p.lat - lat0) * mlat, (p.lon - lon0) * mlon)
            found[p.id] = dict(id=p.id, third_party=third, lat=p.lat, lon=p.lon, dist_m=round(d),
                               date=str(p.date), heading_deg=round(math.degrees(p.heading), 1) if p.heading is not None else None,
                               size=[p.image_sizes[-1].x, p.image_sizes[-1].y] if p.image_sizes else None,
                               source=getattr(p, "source", None),
                               uploader=getattr(p, "uploader", None),
                               places=[str(pl.name) for pl in (getattr(p, "places", None) or [])],
                               address=[str(x) for x in (getattr(p, "address", None) or [])],
                               permalink=sv.build_permalink(id=p.id, lat=p.lat, lon=p.lon))
            print("%-26s %5dm %-10s %-12s %-20s %s" % (p.id, d, p.date, found[p.id]["size"], found[p.id]["uploader"], found[p.id]["places"]))
    os.makedirs(a.out, exist_ok=True)
    res = sorted(found.values(), key=lambda r: r["dist_m"])
    json.dump(res, open(os.path.join(a.out, "photospheres.json"), "w"), indent=1, ensure_ascii=False)
    print("%d unique panos -> %s/photospheres.json" % (len(res), a.out))
    for r in [r for r in res if r["third_party"]][: a.download]:
        p = sv.find_panorama_by_id(r["id"])
        img = sv.get_panorama(p, zoom=a.zoom)
        fn = os.path.join(a.out, "%s_%s.jpg" % (r["id"], img.width))
        img.save(fn, quality=92)
        print("saved", fn, img.size)


if __name__ == "__main__":
    main()
