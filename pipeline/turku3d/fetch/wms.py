"""City WMS orthos -> raw/ortho/<slug>/<area>/<chunk>.tif (tiled GeoTIFF, EPSG:3877) + <area>.vrt mosaic.

The WMS (TeklaOGCWeb, 1.1.1) serves PNG up to 4096 px per side and returns errors as HTTP 200 XML.
We request a fixed chunk grid aligned to the world origin O (chunk = CHUNK_PX * px_m metres) at a
resolution at or finer than the native pixel (5 cm for the 5.2-5.5 cm orthos, 1.25 cm for the ~1.2 cm
drone layer). Requests use TRANSPARENT=TRUE; pixels outside the layer's coverage come back transparent
(or pure white when the server ignores it) and become the GeoTIFF mask. Chunks with no data at all
are recorded as empty in ``<area>.chunks.json`` and not stored, so re-runs skip them.
"""

from __future__ import annotations

import io
import json
import math
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import config
from .common import Registry, atomic_write_text, http_get, kind_dir
from .sources import WMS_LAYERS, WMS_MAX_PX, WMS_URL, WmsLayer

CHUNK_PX = 2000
REFERER = {"Referer": "https://turku.asiointi.fi/IMS/fi"}


def chunk_grid(bbox_en: tuple[float, float, float, float], px_m: float, chunk_px: int = CHUNK_PX):
    """Chunks (key, (emin, nmin, emax, nmax)) of size chunk_px*px_m covering bbox, aligned to O."""
    assert chunk_px <= WMS_MAX_PX
    s = chunk_px * px_m
    e0, n0, e1, n1 = bbox_en
    i0 = math.floor(round((e0 - config.ORIGIN_E) / s, 9))
    i1 = math.ceil(round((e1 - config.ORIGIN_E) / s, 9))
    j0 = math.floor(round((config.ORIGIN_N - n1) / s, 9))  # j grows south like the engine grid
    j1 = math.ceil(round((config.ORIGIN_N - n0) / s, 9))
    out = []
    for j in range(j0, j1):
        for i in range(i0, i1):
            emin = config.ORIGIN_E + i * s
            nmax = config.ORIGIN_N - j * s
            out.append((f"{i}_{j}", (emin, nmax - s, emin + s, nmax)))
    return out


def getmap_params(layer: str, bb, w: int, h: int) -> dict:
    return dict(SERVICE="WMS", VERSION="1.1.1", REQUEST="GetMap", LAYERS=layer, STYLES="", SRS="EPSG:3877",
                BBOX=",".join(f"{v:.4f}" for v in bb), WIDTH=w, HEIGHT=h, FORMAT="image/png", TRANSPARENT="TRUE")


def decode_png(body: bytes):
    """PNG bytes -> (rgb uint8 HxWx3, valid mask bool HxW)."""
    import numpy as np
    from PIL import Image

    im = Image.open(io.BytesIO(body))
    im.load()
    if im.mode in ("RGBA", "LA") or "transparency" in im.info:
        a = np.asarray(im.convert("RGBA"))
        rgb, valid = a[..., :3], a[..., 3] > 0
    else:
        rgb = np.asarray(im.convert("RGB"))
        valid = ~(rgb.min(axis=2) >= 254)  # pure white = no coverage
    return np.ascontiguousarray(rgb), valid


def write_geotiff(path: Path, rgb, valid, bb, px_m: float, compress: str) -> None:
    import rasterio
    from rasterio.transform import from_origin

    h, w = valid.shape
    prof = dict(driver="GTiff", width=w, height=h, count=3, dtype="uint8", crs=config.CRS_HORIZONTAL,
                transform=from_origin(bb[0], bb[3], px_m, px_m), tiled=True, blockxsize=512, blockysize=512,
                compress=compress, BIGTIFF="IF_SAFER")
    if compress == "JPEG":
        prof.update(photometric="YCBCR", jpeg_quality=92)
    elif compress == "DEFLATE":
        prof.update(predictor=2, zlevel=6)
    tmp = path.with_name(path.name + ".tmp.tif")
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True):
        with rasterio.open(tmp, "w", **prof) as dst:
            dst.write(rgb.transpose(2, 0, 1))
            if not valid.all():
                dst.write_mask((valid * 255).astype("uint8"))
    tmp.replace(path)


def native_resolution_cm(rgb, valid, px_m: float) -> float | None:
    """Native pixel estimate (verification method): px / (1 - P(horizontal neighbour identical))."""
    import numpy as np

    v = valid[:, 1:] & valid[:, :-1]
    if v.sum() < 1000:
        return None
    eq = (np.abs(np.diff(rgb.astype(np.int16), axis=1)).sum(axis=2) == 0)[v]
    p = float(eq.mean())
    return px_m * 100 / max(1e-6, 1 - p)


def fetch_layer(layer: WmsLayer, area: config.Area, workers: int = 4, log=print, get=http_get) -> dict:
    root = kind_dir("ortho")
    reg = Registry(root)
    adir = root / layer.slug / area.name
    adir.mkdir(parents=True, exist_ok=True)
    state_path = adir.parent / f"{area.name}.chunks.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    bbox = area.with_margin.to_en().as_tuple()
    chunks = chunk_grid(bbox, layer.px_m, CHUNK_PX)

    def one(item):
        key, bb = item
        dest = adir / f"{key}.tif"
        st = state.get(key)
        if st and st.get("status") == "empty":
            return key, st
        if st and st.get("status") == "ok" and reg.is_complete(dest):
            return key, st
        body, hdr = get(WMS_URL, getmap_params(layer.name, bb, CHUNK_PX, CHUNK_PX), headers=REFERER)
        if "image" not in hdr.get("content-type", ""):
            raise RuntimeError(f"WMS {layer.name} {key}: {hdr.get('content-type')} {body[:300]!r}")
        rgb, valid = decode_png(body)
        cov = float(valid.mean())
        if cov == 0.0:
            return key, {"status": "empty", "bbox": list(bb)}
        write_geotiff(dest, rgb, valid, bb, layer.px_m, layer.compress)
        reg.record(dest, url=WMS_URL, layer=layer.name, bbox=list(bb), px_m=layer.px_m, coverage=round(cov, 4))
        return key, {"status": "ok", "bbox": list(bb), "coverage": round(cov, 4),
                     "native_cm": native_resolution_cm(rgb, valid, layer.px_m)}

    errors = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(one, c): c[0] for c in chunks}
        for fut in futs:
            key = futs[fut]
            try:
                k, st = fut.result()
                state[k] = st
            except Exception as e:
                errors.append(f"{key}: {e}")
                log(f"wms  {layer.slug} {key}: ERROR {e}")
            atomic_write_text(state_path, json.dumps(state, indent=0, sort_keys=True))

    ok = sorted(k for k, s in state.items() if s["status"] == "ok" and k in dict(chunks))
    vrt = adir.parent / f"{area.name}.vrt"
    if ok:
        files = [str(adir / f"{k}.tif") for k in ok]
        subprocess.run(["gdalbuildvrt", "-q", "-overwrite", str(vrt), *files], check=True)
    covs = [state[k]["coverage"] for k in ok]
    natives = [state[k].get("native_cm") for k in ok if state[k].get("native_cm")]
    area_px = (CHUNK_PX * CHUNK_PX) * len(chunks)
    s = {
        "layer": layer.name, "slug": layer.slug, "area": area.name, "px_m": layer.px_m,
        "chunk_m": CHUNK_PX * layer.px_m, "chunks": len(chunks), "chunks_ok": len(ok),
        "chunks_empty": sum(1 for k, _ in chunks if state.get(k, {}).get("status") == "empty"),
        "errors": errors,
        "coverage_of_bbox": round(sum(c * CHUNK_PX * CHUNK_PX for c in covs) / area_px, 4) if area_px else 0,
        "native_cm_median": round(sorted(natives)[len(natives) // 2], 2) if natives else None,
        "bytes": sum((adir / f"{k}.tif").stat().st_size for k in ok),
        "vrt": str(vrt) if ok else None,
    }
    atomic_write_text(adir.parent / f"{area.name}.summary.json", json.dumps(s, indent=1, ensure_ascii=False))
    log(f"wms  {layer.slug}: {s['chunks_ok']}/{s['chunks']} chunks ok, {s['chunks_empty']} empty, "
        f"coverage {s['coverage_of_bbox']:.1%}, native ~{s['native_cm_median']} cm, {len(errors)} errors")
    return s


def fetch_area(area: config.Area, layers=None, workers: int = 4, log=print) -> list[dict]:
    out = []
    for L in WMS_LAYERS:
        if layers and L.slug not in layers and L.name not in layers:
            continue
        out.append(fetch_layer(L, area, workers=workers, log=log))
    return out
