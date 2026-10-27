#!/usr/bin/env python3
"""
3DVista virtual tour (e.g. https://turku.360tour.fi/) reference-image tool.

Subcommands
  list      inventory every panorama: label, cube face size per level, EXIF GPS /
            capture date / camera / Photoshop slice name (from the *_t.jpg thumbnail),
            distance to a pilot point.  Writes <out>/inventory.json
  fetch     download all tiles of one level for one panorama, assemble the 6 cube
            faces, and optionally stitch an equirectangular image and/or
            rectilinear "camera" views (yaw/pitch/hfov) for game-vs-photo QA.

Examples
  python3 -I tools/refs/tdv360.py list  --out data/refs/360tour --near 60.44839,22.26726
  python3 -I tools/refs/tdv360.py fetch --out data/refs/360tour --pano B5D39CE1 --equirect
  python3 -I tools/refs/tdv360.py fetch --out data/refs/360tour --pano "Old Beauties" \
          --equirect --eq-width 16384 --view 0,0,90,1920x1080 --view -137,5,60,1600x1200

Conventions (verified on turku.360tour.fi by seam continuity, see --check-seams):
  yaw 0 = centre of face "f", yaw grows clockwise (to the right, "r"), pitch + = up.
  These are the same yaw/pitch numbers used by the tour's hotspots/adjacentPanoramas.
  Tour yaw is NOT geographic north; a per-panorama heading offset has to be solved
  (e.g. from hotspot bearings to known landmarks).

Only numpy + Pillow are needed.  Downloads go into <out>/<pano_id>/ .
"""
import argparse, concurrent.futures as cf, io, json, math, os, re, sys, time, urllib.request
import numpy as np
from PIL import Image, ExifTags

Image.MAX_IMAGE_PIXELS = None
UA = "Mozilla/5.0 (reference-photo research; contact via project)"
FACES = "fbrlud"


def http_get(url, retries=4):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as e:  # noqa
            if i == retries - 1:
                raise
            time.sleep(1.5 * (i + 1))


def cached(base, rel, outdir):
    p = os.path.join(outdir, "raw", rel)
    if not os.path.exists(p):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(http_get(base.rstrip("/") + "/" + rel))
    return p


LEVEL_RE = re.compile(r'\{"url":"(media/panorama_[^"]+?_0/([fbrlud])/(\d+)/\{row\}_\{column\}\.jpg)"([^}]*)\}')


def parse_tour(base, outdir):
    sg = open(cached(base, "script_general.js", outdir), encoding="utf-8").read()
    try:
        loc = open(cached(base, "locale/fi.txt", outdir), encoding="utf-8").read()
    except Exception:
        loc = ""
    labels = dict(re.findall(r"^(panorama_[A-F0-9_]+)\.label\s*=\s*(.*?)\s*$", loc, re.M))
    panos = {}
    for url, face, lvl, rest in LEVEL_RE.findall(sg):
        pid = re.search(r"(panorama_[A-F0-9_]+?)_0/", url).group(1)
        g = lambda k: int(re.search(r'"%s":(\d+)' % k, rest).group(1))
        lv = panos.setdefault(pid, {}).setdefault(int(lvl), {})
        lv[face] = dict(url=url, w=g("width"), h=g("height"), rows=g("rowCount"), cols=g("colCount"))
    out = []
    for pid, levels in panos.items():
        out.append(dict(id=pid, label=labels.get(pid, ""), thumbnail="media/%s_t.jpg" % pid,
                        levels={k: levels[k]["f"] for k in sorted(levels)}))
    return out


def exif_info(path):
    im = Image.open(path)
    ex = im.getexif()
    sub = ex.get_ifd(0x8769)
    gps = ex.get_ifd(0x8825)
    T = lambda d: {ExifTags.TAGS.get(k, k): v for k, v in d.items()}
    d, s = T(ex), T(sub)
    info = dict(camera=d.get("Model"), date=s.get("DateTimeOriginal"), artist=d.get("Artist"))
    if gps and 2 in gps and 4 in gps:
        dms = lambda v: float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
        lat, lon = dms(gps[2]), dms(gps[4])
        if gps.get(1) == "S": lat = -lat
        if gps.get(3) == "W": lon = -lon
        info.update(lat=round(lat, 7), lon=round(lon, 7))
        if 6 in gps:
            info["gps_alt_m"] = float(gps[6]) * (-1 if gps.get(5) in (b"\x01", 1) else 1)
    # Photoshop IRB 'Slices' resource carries the original file/slice name (e.g. "Jokiranta_VanhaSuurtori_360")
    raw = open(path, "rb").read()
    m = re.search(rb"8BIM\x04\x1a.{6}", raw, re.S)
    if m:
        seg = raw[m.end(): m.end() + 600]
        try:
            n = int.from_bytes(seg[16 + 4: 16 + 8], "big")  # bounds(16) then unicode string length
            name = seg[24: 24 + 2 * n].decode("utf-16-be", "ignore")
            if name.isprintable():
                info["source_name"] = name
        except Exception:
            pass
    return info


def haversine(a, b):
    R = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def cmd_list(a):
    panos = parse_tour(a.base, a.out)
    near = tuple(map(float, a.near.split(","))) if a.near else None
    for p in panos:
        tp = cached(a.base, p["thumbnail"], a.out)
        p.update(exif_info(tp))
        p["kind"] = "aerial(drone)" if (p.get("camera") or "").startswith("FC") else "ground/unknown"
        if near and "lat" in p:
            p["dist_m"] = round(haversine(near, (p["lat"], p["lon"])))
    panos.sort(key=lambda p: p.get("dist_m", 1e9))
    json.dump(panos, open(os.path.join(a.out, "inventory.json"), "w"), indent=1, ensure_ascii=False)
    for p in panos:
        L = p["levels"][0] if 0 in p["levels"] else p["levels"][min(p["levels"])]
        print("%-36s %-34s face=%5d tiles=%2dx%-2d %-22s %-20s %s %s" % (
            p["id"], p["label"][:34], L["w"], L["rows"], L["cols"], p.get("camera"), p.get("date"),
            ("%.5f,%.5f" % (p["lat"], p["lon"])) if "lat" in p else "no-GPS",
            ("%dm" % p["dist_m"]) if "dist_m" in p else ""))


# ---------------------------------------------------------------- stitching
def face_dirs(face, u, v):
    """u,v in [-1,1] (u right, v down) on a face -> unit-cube direction (x right, y up, z forward)."""
    one = np.ones_like(u)
    if face == "f": return u, -v, one
    if face == "b": return -u, -v, -one
    if face == "r": return one, -v, -u
    if face == "l": return -one, -v, u
    if face == "u": return u, one, v
    if face == "d": return u, -one, -v
    raise ValueError(face)


def dir_to_face(x, y, z):
    """Vectorised inverse of face_dirs: returns face index (into FACES), u, v."""
    ax, ay, az = np.abs(x), np.abs(y), np.abs(z)
    fi = np.zeros(x.shape, np.int8); u = np.zeros_like(x); v = np.zeros_like(x)
    m = (az >= ax) & (az >= ay) & (z > 0); fi[m] = 0; u[m] = x[m] / az[m]; v[m] = -y[m] / az[m]
    m2 = (az >= ax) & (az >= ay) & (z <= 0); fi[m2] = 1; u[m2] = -x[m2] / az[m2]; v[m2] = -y[m2] / az[m2]
    rest = ~(m | m2)
    m = rest & (ax >= ay) & (x > 0); fi[m] = 2; u[m] = -z[m] / ax[m]; v[m] = -y[m] / ax[m]
    m = rest & (ax >= ay) & (x <= 0); fi[m] = 3; u[m] = z[m] / ax[m]; v[m] = -y[m] / ax[m]
    m = rest & (ay > ax) & (y > 0); fi[m] = 4; u[m] = x[m] / ay[m]; v[m] = z[m] / ay[m]
    m = rest & (ay > ax) & (y <= 0); fi[m] = 5; u[m] = x[m] / ay[m]; v[m] = -z[m] / ay[m]
    return fi, u, v


def sample(faces, fi, u, v):
    """Bilinear sample from list of 6 HxWx3 uint8 arrays."""
    N = faces[0].shape[0]
    out = np.zeros(fi.shape + (3,), np.float32)
    for k in range(6):
        m = fi == k
        if not m.any(): continue
        F = faces[k]
        px = np.clip((u[m] + 1) * 0.5 * N - 0.5, 0, N - 1.001)
        py = np.clip((v[m] + 1) * 0.5 * N - 0.5, 0, N - 1.001)
        x0, y0 = px.astype(np.int32), py.astype(np.int32)
        fx, fy = (px - x0)[:, None], (py - y0)[:, None]
        c = (F[y0, x0] * (1 - fx) + F[y0, x0 + 1] * fx) * (1 - fy) + (F[y0 + 1, x0] * (1 - fx) + F[y0 + 1, x0 + 1] * fx) * fy
        out[m] = c
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def yawpitch_to_dir(yaw, pitch):
    return np.cos(pitch) * np.sin(yaw), np.sin(pitch), np.cos(pitch) * np.cos(yaw)


def equirect(faces, W):
    H = W // 2
    out = np.zeros((H, W, 3), np.uint8)
    lon = ((np.arange(W) + 0.5) / W * 2 - 1) * math.pi  # -180..180, yaw 0 = centre column
    step = max(1, 4_000_000 // W)
    for r0 in range(0, H, step):
        r = np.arange(r0, min(H, r0 + step))
        lat = (0.5 - (r + 0.5) / H) * math.pi
        LON, LAT = np.meshgrid(lon, lat)
        out[r0:r0 + len(r)] = sample(faces, *dir_to_face(*yawpitch_to_dir(LON, LAT)))
    return out


def view(faces, yaw_deg, pitch_deg, hfov_deg, W, H):
    """Rectilinear pinhole view (what a game camera at the pano position would see)."""
    f = (W / 2) / math.tan(math.radians(hfov_deg) / 2)
    xs, ys = np.meshgrid(np.arange(W) + 0.5 - W / 2, np.arange(H) + 0.5 - H / 2)
    d = np.stack([xs, -ys, np.full_like(xs, f)], -1)
    d /= np.linalg.norm(d, axis=-1, keepdims=True)
    p, y = math.radians(pitch_deg), math.radians(yaw_deg)
    Rx = np.array([[1, 0, 0], [0, math.cos(p), math.sin(p)], [0, -math.sin(p), math.cos(p)]])
    Ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    d = d @ (Ry @ Rx).T
    return sample(faces, *dir_to_face(d[..., 0], d[..., 1], d[..., 2]))


def seam_report(faces):
    """Mean abs difference across the 12 cube edges (low = conventions correct)."""
    N = faces[0].shape[0]; e = lambda a: a.astype(np.float32)
    F = dict(zip(FACES, faces))
    pairs = {
        "f|r": (F["f"][:, -1], F["r"][:, 0]), "r|b": (F["r"][:, -1], F["b"][:, 0]),
        "b|l": (F["b"][:, -1], F["l"][:, 0]), "l|f": (F["l"][:, -1], F["f"][:, 0]),
        "u|f": (F["u"][-1, :], F["f"][0, :]), "d|f": (F["d"][0, :], F["f"][-1, :]),
        "u|b": (F["u"][0, ::-1], F["b"][0, :]), "d|b": (F["d"][-1, ::-1], F["b"][-1, :]),
        "u|r": (F["u"][::-1, -1], F["r"][0, :]), "u|l": (F["u"][:, 0], F["l"][0, :]),
        "d|r": (F["d"][:, -1], F["r"][-1, :]), "d|l": (F["d"][::-1, 0], F["l"][-1, :]),
    }
    return {k: round(float(np.abs(e(a) - e(b)).mean()), 1) for k, (a, b) in pairs.items()}


def cmd_fetch(a):
    inv_p = os.path.join(a.out, "inventory.json")
    panos = json.load(open(inv_p)) if os.path.exists(inv_p) else parse_tour(a.base, a.out)
    cand = [p for p in panos if a.pano.lower() in p["id"].lower() or a.pano.lower() in p["label"].lower()]
    if len(cand) != 1:
        sys.exit("pano selector matched %d panoramas: %s" % (len(cand), [p["label"] for p in cand]))
    p = cand[0]
    lv = p["levels"][str(a.level)] if str(a.level) in p["levels"] else p["levels"][a.level]
    pdir = os.path.join(a.out, p["id"]); os.makedirs(pdir, exist_ok=True)
    print("%s '%s' level %d: face %dpx, %dx%d tiles/face" % (p["id"], p["label"], a.level, lv["w"], lv["rows"], lv["cols"]))
    jobs = []
    for face in FACES:
        tmpl = lv["url"].replace("/f/%d/" % a.level, "/%s/%d/" % (face, a.level))
        for r in range(lv["rows"]):
            for c in range(lv["cols"]):
                rel = tmpl.replace("{row}", str(r)).replace("{column}", str(c))
                jobs.append((face, r, c, rel))
    tdir = os.path.join(pdir, "tiles_L%d" % a.level)

    def get(job):
        face, r, c, rel = job
        fp = os.path.join(tdir, face, "%d_%d.jpg" % (r, c))
        if not os.path.exists(fp):
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            data = http_get(a.base.rstrip("/") + "/" + rel)
            open(fp + ".part", "wb").write(data); os.replace(fp + ".part", fp)
        return fp

    t0 = time.time()
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for i, _ in enumerate(ex.map(get, jobs)):
            if (i + 1) % 200 == 0: print("  %d/%d tiles" % (i + 1, len(jobs)))
    print("  tiles done in %.0fs" % (time.time() - t0))
    faces = []
    for face in FACES:
        img = Image.new("RGB", (lv["w"], lv["h"]))
        x = 0; y = 0
        for r in range(lv["rows"]):
            x = 0; th = 0
            for c in range(lv["cols"]):
                t = Image.open(os.path.join(tdir, face, "%d_%d.jpg" % (r, c))).convert("RGB")
                img.paste(t, (x, y)); x += t.width; th = t.height
            y += th
        img.save(os.path.join(pdir, "face_%s.jpg" % face), quality=92)
        faces.append(np.asarray(img))
    print("  seams (mean |diff| per edge, 8-bit):", seam_report(faces))
    meta = dict(p); meta.update(level=a.level, face_px=lv["w"], conventions="yaw0=face f centre, yaw+ clockwise, pitch+ up")
    if a.equirect:
        W = a.eq_width or int(round(math.pi * lv["w"] / 2) * 2)
        print("  equirect %dx%d ..." % (W, W // 2))
        eq = equirect(faces, W)
        Image.fromarray(eq).save(os.path.join(pdir, "equirect_%d.jpg" % W), quality=92)
        Image.fromarray(eq).resize((4096, 2048), Image.LANCZOS).save(os.path.join(pdir, "equirect_preview_4096.jpg"), quality=88)
        meta["equirect"] = "equirect_%d.jpg" % W
    for vs in a.view or []:
        yv, pv, fv, wh = vs.split(","); Wv, Hv = map(int, wh.split("x"))
        im = view(faces, float(yv), float(pv), float(fv), Wv, Hv)
        name = "view_y%s_p%s_f%s_%s.jpg" % (yv, pv, fv, wh)
        Image.fromarray(im).save(os.path.join(pdir, name), quality=92)
        meta.setdefault("views", []).append(name)
    json.dump(meta, open(os.path.join(pdir, "meta.json"), "w"), indent=1, ensure_ascii=False)
    print("  wrote", pdir)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="https://turku.360tour.fi")
    ap.add_argument("--out", default="data/refs/360tour")
    sp = ap.add_subparsers(dest="cmd", required=True)
    l = sp.add_parser("list"); l.add_argument("--near", help="lat,lon to rank by distance")
    f = sp.add_parser("fetch")
    f.add_argument("--pano", required=True, help="id fragment or label substring")
    f.add_argument("--level", type=int, default=0, help="0 = highest resolution")
    f.add_argument("--workers", type=int, default=8)
    f.add_argument("--equirect", action="store_true")
    f.add_argument("--eq-width", type=int, default=0, help="default pi*face (≈ source pano width)")
    f.add_argument("--view", action="append", help="yaw,pitch,hfov,WxH  rectilinear render (repeatable)")
    for s in (l, f):  # allow --out/--base after the subcommand too
        s.add_argument("--base", default=argparse.SUPPRESS); s.add_argument("--out", default=argparse.SUPPRESS)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    {"list": cmd_list, "fetch": cmd_fetch}[a.cmd](a)


if __name__ == "__main__":
    main()
