"""Sanity report for an area's raw data -> raw/REPORT-<area>.json (+ a printed summary)."""

from __future__ import annotations

import json
from pathlib import Path

from .. import config
from .common import Registry, atomic_write_text, human, kind_dir
from .sources import WFS_LAYERS, WMS_LAYERS

CLASS_NAMES = {1: "unclassified", 2: "ground", 3: "veg_low", 4: "veg_med", 5: "veg_high", 6: "building",
               7: "low_noise", 8: "key_points", 9: "water", 12: "overlap"}


def laz_stats(path: Path, area: config.Area) -> dict:
    import laspy
    import numpy as np

    las = laspy.read(path)
    hdr = las.header
    e, n = np.asarray(las.x), np.asarray(las.y)
    cls = np.asarray(las.classification)
    out = {
        "file": path.name, "bytes": path.stat().st_size, "points_total": int(hdr.point_count),
        "las_version": str(hdr.version), "point_format": int(hdr.point_format.id),
        "has_crs_vlr": hdr.parse_crs() is not None,
        "bounds_en": [float(hdr.mins[0]), float(hdr.mins[1]), float(hdr.maxs[0]), float(hdr.maxs[1])],
        "z_range": [float(hdr.mins[2]), float(hdr.maxs[2])],
    }
    for label, bb in (("core", area.core.to_en()), ("with_margin", area.with_margin.to_en())):
        m = (e >= bb.emin) & (e < bb.emax) & (n >= bb.nmin) & (n < bb.nmax)
        a = (bb.emax - bb.emin) * (bb.nmax - bb.nmin)
        c = cls[m]
        vals, cnts = np.unique(c, return_counts=True)
        d = {
            "bbox_en": [bb.emin, bb.nmin, bb.emax, bb.nmax], "points": int(m.sum()),
            "pts_per_m2": round(float(m.sum()) / a, 2),
            "classes": {f"{int(v)}:{CLASS_NAMES.get(int(v), '?')}": int(k) for v, k in zip(vals, cnts)},
        }
        if label == "core":
            # 1 m cells with >= 1 class-2 point (research: class 2 alone leaves many cells empty)
            ix = np.floor(e[m] - bb.emin).astype(np.int64)
            iy = np.floor(n[m] - bb.nmin).astype(np.int64)
            w = int(bb.emax - bb.emin)
            cells = int(a)
            g2 = np.zeros(cells, bool)
            g2[(iy * w + ix)[c == 2]] = True
            bld = np.zeros(cells, bool)
            bld[(iy * w + ix)[c == 6]] = True
            d["cells_1m_with_class2"] = round(float(g2.mean()), 4)
            d["cells_1m_with_class2_excl_building_cells"] = round(float(g2[~bld].mean()), 4) if (~bld).any() else None
            zg = np.asarray(las.z)[m][c == 2]
            d["ground_z_range"] = [round(float(zg.min()), 3), round(float(zg.max()), 3)] if len(zg) else None
            gps = np.asarray(las.gps_time)[m] if "gps_time" in las.point_format.dimension_names else None
            if gps is not None and len(gps):
                d["gps_time_range"] = [float(gps.min()), float(gps.max())]
        out[label] = d
    return out


def dir_stats(d: Path) -> dict:
    files = [p for p in d.rglob("*") if p.is_file() and not p.name.startswith(".")]
    return {"files": len(files), "bytes": sum(p.stat().st_size for p in files),
            "human": human(sum(p.stat().st_size for p in files))}


def build(area: config.Area, log=print) -> dict:
    rep: dict = {"area": area.name, "core_en": list(area.core.to_en().as_tuple()),
                 "with_margin_en": list(area.with_margin.to_en().as_tuple()), "laz": [], "wfs": {}, "wms": {},
                 "osm": None, "dirs": {}, "checksums": {}}
    lroot = kind_dir("laz")
    for t in area.laz_tiles:
        p = lroot / "pistepilvi" / f"{t.name}.laz"
        if p.exists():
            rep["laz"].append(laz_stats(p, area))
        mp = lroot / "maanpinta" / f"{t.name}.laz"
        if mp.exists():
            import laspy
            with laspy.open(mp) as f:
                rep.setdefault("maanpinta", []).append({"file": mp.name, "points": int(f.header.point_count),
                                                        "bytes": mp.stat().st_size})
    wroot = kind_dir("wfs") / area.name
    for L in WFS_LAYERS:
        sp = wroot / f"{L.slug}.summary.json"
        if sp.exists():
            s = json.loads(sp.read_text())
            rep["wfs"][L.name] = {k: s.get(k) for k in ("status", "features", "rows", "requests", "leaf_pages",
                                                        "geom_types", "has_z", "by_kplkoodi", "by_Katuosalaji",
                                                        "by_Pintamateriaali") if s.get(k) is not None}
        else:
            rep["wfs"][L.name] = {"status": "missing"}
    oroot = kind_dir("ortho")
    for W in WMS_LAYERS:
        sp = oroot / W.slug / f"{area.name}.summary.json"
        if not sp.exists():
            rep["wms"][W.slug] = {"status": "missing"}
            continue
        s = json.loads(sp.read_text())
        if s.get("vrt"):
            import rasterio
            with rasterio.open(s["vrt"]) as ds:
                s["mosaic_px"] = [ds.width, ds.height]
                s["mosaic_res_m"] = [abs(ds.res[0]), abs(ds.res[1])]
                s["mosaic_crs"] = str(ds.crs)
        rep["wms"][W.slug] = s
    for p in sorted((kind_dir("osm") / area.name).glob("*.summary.json")):
        rep["osm"] = json.loads(p.read_text())
    for k in ("laz", "wfs", "ortho", "osm"):
        d = kind_dir(k)
        rep["dirs"][k] = dir_stats(d)
        rep["checksums"][k] = {"registered": len(Registry(d).records())}
    out = config.raw_dir() / f"REPORT-{area.name}.json"
    atomic_write_text(out, json.dumps(rep, indent=1, ensure_ascii=False))
    log(render(rep))
    log(f"report -> {out}")
    return rep


def render(rep: dict) -> str:
    L = [f"== raw data report: {rep['area']}  core {rep['core_en']}  +margin {rep['with_margin_en']}"]
    for k, d in rep["dirs"].items():
        L.append(f"  raw/{k:<6} {d['files']:>6} files  {d['human']:>10}   (sha256 registered: {rep['checksums'][k]['registered']})")
    for t in rep["laz"]:
        c = t["core"]
        L.append(f"  LAZ {t['file']}: {t['points_total']:,} pts, LAS {t['las_version']} fmt {t['point_format']}, "
                 f"CRS VLR {t['has_crs_vlr']}, {human(t['bytes'])}")
        L.append(f"    core: {c['points']:,} pts = {c['pts_per_m2']} pts/m2; 1 m cells w/ class 2: "
                 f"{c['cells_1m_with_class2']:.1%} (non-building cells {c['cells_1m_with_class2_excl_building_cells']:.1%}); "
                 f"ground z {c['ground_z_range']}")
        L.append("    classes: " + ", ".join(f"{k}={v:,}" for k, v in c["classes"].items()))
    for m in rep.get("maanpinta", []):
        L.append(f"  maanpinta {m['file']}: {m['points']:,} key points")
    L.append("  WFS (features / rows / requests):")
    for name, s in rep["wfs"].items():
        if s.get("status") == "ok":
            L.append(f"    {name:<32} {s['features']:>6} {s['rows']:>7} {s['requests']:>4}  {s.get('geom_types')}")
        else:
            L.append(f"    {name:<32} {s.get('status')}")
    for slug, s in rep["wms"].items():
        if s.get("status") == "missing":
            L.append(f"  WMS {slug}: missing")
            continue
        L.append(f"  WMS {slug}: {s['chunks_ok']}/{s['chunks']} chunks, mosaic {s.get('mosaic_px')} px at "
                 f"{s.get('mosaic_res_m')} m, coverage {s['coverage_of_bbox']:.1%}, est. native {s['native_cm_median']} cm, "
                 f"{human(s['bytes'])}")
    if rep["osm"]:
        o = rep["osm"]
        L.append(f"  OSM {o['clip']}: {o['rows']} features {o['by_geom_type']}  {o['counts']}  last edit {o['timestamp_last']}")
    return "\n".join(L)
