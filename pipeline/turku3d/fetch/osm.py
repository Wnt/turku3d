"""OSM: Geofabrik Finland pbf (dated, md5-checked, resumable) -> osmium clip per area -> GeoParquet.

Files:
  raw/osm/geofabrik/finland-YYMMDD.osm.pbf           whole Finland extract (~0.77 GB), kept for re-clips
  raw/osm/<area>/<area>-YYMMDD.osm.pbf                 osmium extract --strategy smart of the area bbox
  raw/osm/<area>/<area>-YYMMDD.parquet                 osmium export: one row per point/line/(multi)polygon,
                                                       columns osm_type, osm_id, tags (JSON), selected tag
                                                       columns, geometry (EPSG:3877), geometry_local (x, z)
The clip bbox is the area core + margin + OSM_EXTRA_M (context for the road graph), converted to WGS84.
"""

from __future__ import annotations

import json
import hashlib
import subprocess
from pathlib import Path

from .. import config
from .common import Registry, atomic_write_text, curl_download, curl_head, http_get, kind_dir, sha256_file
from .sources import GEOFABRIK_FINLAND

OSM_EXTRA_M = 100.0
TAG_COLUMNS = ("highway", "building", "building:levels", "roof:shape", "height", "name", "surface", "barrier",
               "kerb", "amenity", "shop", "natural", "landuse", "leisure", "railway", "lanes", "oneway",
               "maxspeed", "crossing", "entrance", "addr:street", "addr:housenumber", "step_count", "layer")


def resolve_dated_url(url: str = GEOFABRIK_FINLAND) -> dict:
    h = curl_head(url)
    if h["status"] != 200:
        raise RuntimeError(f"HEAD {url} -> {h['status']}")
    return h


def wgs84_bbox(area: config.Area, extra: float = OSM_EXTRA_M) -> tuple[float, float, float, float]:
    """(lon_min, lat_min, lon_max, lat_max) enclosing the area's margin bbox + extra (all 4 corners)."""
    b = area.with_margin.buffered(extra).to_en()
    corners = [config.en_to_wgs84(e, n) for e in (b.emin, b.emax) for n in (b.nmin, b.nmax)]
    lats = [c[0] for c in corners]
    lons = [c[1] for c in corners]
    return (min(lons), min(lats), max(lons), max(lats))


def fetch_finland(log=print) -> Path:
    root = kind_dir("osm")
    reg = Registry(root)
    cached = sorted((root / "geofabrik").glob("finland-*.osm.pbf"))
    for path in reversed(cached):
        if reg.is_complete(path):
            log(f"osm  {path.name}: cached snapshot")
            return path
    h = resolve_dated_url()
    name = h["url"].rsplit("/", 1)[1]  # finland-261024.osm.pbf
    dest = root / "geofabrik" / name
    if reg.is_complete(dest):
        log(f"osm  {name}: cached")
        return dest
    md5_body, _ = http_get(h["url"] + ".md5")
    md5 = md5_body.decode().split()[0]
    rec, now = curl_download(h["url"], dest, reg, expected_bytes=h["content_length"],
                             last_modified=h["last_modified"], md5=md5)
    with dest.open("rb") as stream:
        got = hashlib.file_digest(stream, "md5").hexdigest()
    if got != md5:
        dest.unlink()
        raise RuntimeError(f"md5 mismatch for {name}: {got} != {md5}")
    log(f"osm  {name}: {'downloaded' if now else 'cached'} {rec['bytes'] / 1e6:.0f} MB, md5 ok")
    return dest


def clip(area: config.Area, src: Path, log=print) -> Path:
    root = kind_dir("osm")
    reg = Registry(root)
    date = src.name.split("-")[1].split(".")[0]
    out = root / area.name / f"{area.name}-{date}.osm.pbf"
    if reg.is_complete(out):
        log(f"osm  clip {out.name}: cached")
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    bb = wgs84_bbox(area)
    tmp = out.with_name(out.name + ".tmp.osm.pbf")
    subprocess.run(["osmium", "extract", "--strategy", "smart", "-b", ",".join(f"{v:.6f}" for v in bb),
                    "--overwrite", "-o", str(tmp), str(src)], check=True)
    tmp.replace(out)
    reg.record(out, url=None, source=src.name, bbox_wgs84=list(bb), source_sha256=reg.get(src)["sha256"])
    log(f"osm  clip {out.name}: {out.stat().st_size / 1e3:.0f} kB")
    return out


def to_parquet(clip_pbf: Path, log=print) -> tuple[Path, dict]:
    import geopandas as gpd
    from shapely.geometry import shape

    from .wfs import to_local

    root = kind_dir("osm")
    reg = Registry(root)
    out = clip_pbf.with_name(clip_pbf.name.replace(".osm.pbf", ".parquet"))
    summ_path = clip_pbf.with_name(clip_pbf.name.replace(".osm.pbf", ".summary.json"))
    if reg.is_complete(out) and summ_path.exists():
        log(f"osm  {out.name}: cached")
        return out, json.loads(summ_path.read_text())
    seq = subprocess.run(["osmium", "export", "-f", "geojsonseq", "-a", "type,id", "--overwrite",
                          "-o", "-", str(clip_pbf)], check=True, capture_output=True).stdout
    recs = []
    for line in seq.split(b"\n"):
        line = line.strip().lstrip(b"\x1e")
        if not line:
            continue
        f = json.loads(line)
        props = dict(f.get("properties") or {})
        typ, oid = props.pop("@type", ""), props.pop("@id", "")
        recs.append({"osm_type": typ, "osm_id": int(oid) if str(oid).isdigit() else oid,
                     "tags": json.dumps(props, ensure_ascii=False, sort_keys=True),
                     **{k: props.get(k) for k in TAG_COLUMNS}, "geometry": shape(f["geometry"])})
    gdf = gpd.GeoDataFrame(recs, geometry="geometry", crs=config.CRS_WGS84).to_crs(config.CRS_HORIZONTAL)
    gdf["geometry_local"] = gpd.GeoSeries(to_local(gdf.geometry.values), crs=None)
    tmp = out.with_name(out.name + ".tmp")
    gdf.to_parquet(tmp, index=False)
    tmp.replace(out)
    reg.record(out, url=None, source=clip_pbf.name)
    info = subprocess.run(["osmium", "fileinfo", "-e", "-j", str(clip_pbf)], check=True, capture_output=True,
                          text=True).stdout
    fi = json.loads(info)
    s = {
        "clip": clip_pbf.name, "rows": int(len(gdf)),
        "by_geom_type": {k: int(v) for k, v in gdf.geom_type.value_counts().items()},
        "objects": fi.get("data", {}).get("count", {}),
        "timestamp_last": fi.get("data", {}).get("timestamp", {}).get("last"),
        "counts": {
            "building": int(gdf["building"].notna().sum()),
            "highway": int(gdf["highway"].notna().sum()),
            "barrier=kerb": int((gdf["barrier"] == "kerb").sum()),
            "highway=steps": int((gdf["highway"] == "steps").sum()),
            "highway=crossing": int((gdf["highway"] == "crossing").sum()),
            "highway=street_lamp": int((gdf["highway"] == "street_lamp").sum()),
            "natural=tree": int((gdf["natural"] == "tree").sum()),
            "amenity/shop": int((gdf["amenity"].notna() | gdf["shop"].notna()).sum()),
        },
        "parquet_bytes": out.stat().st_size,
    }
    atomic_write_text(summ_path, json.dumps(s, indent=1))
    log(f"osm  {out.name}: {s['rows']} features {s['counts']}")
    return out, s


def fetch_area(area: config.Area, log=print) -> dict:
    src = fetch_finland(log=log)
    c = clip(area, src, log=log)
    _, s = to_parquet(c, log=log)
    return s
