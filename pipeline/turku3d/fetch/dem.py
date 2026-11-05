"""MML 2 m DEM (keyless Funet mirror) -> raw/laz/maanpinta/<block>.laz ground key points.

For a city without open city lidar (``ground = "mml-dem2m"``) the ground of one 500 m block is the
National Land Survey 2 m elevation model sampled at the centres of a 2 m grid in the city's CRS,
written as the same LAZ key-point file (class 8, 1 mm scale) that Turku's ``maanpinta`` blocks
are, so every ground, water and collision producer reads one format. The DEM is EPSG:3067 + N2000;
points are reprojected horizontally only (heights stay N2000). A window is read through GDAL's
``/vsicurl/`` from the mirror's VRT, so only the needed sheet bytes are transferred.
"""

from __future__ import annotations

import numpy as np

from .. import config
from ..cities import MML_FUNET
from .common import Registry, human, kind_dir

SPACING = 2.0  # m, the DEM's own resolution
DEM_CRS = "EPSG:3067"
DEM_VRT = f"{MML_FUNET}/dem2m/dem2m_direct.vrt"


def block_points(block: config.LazTile):
    """(E, N, H) of the block's 2 m grid centres in the city CRS; NaN-free (nodata dropped)."""
    import rasterio
    from pyproj import Transformer
    from rasterio.windows import from_bounds

    b = block.bbox_en
    e = np.arange(b.emin + SPACING / 2, b.emax, SPACING)
    n = np.arange(b.nmax - SPACING / 2, b.nmin, -SPACING)
    ee, nn = np.meshgrid(e, n)
    ee, nn = ee.ravel(), nn.ravel()
    x, y = Transformer.from_crs(config.CRS_HORIZONTAL, DEM_CRS, always_xy=True).transform(ee, nn)
    pad = 6.0
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".vrt,.tif"):
        with rasterio.open(f"/vsicurl/{DEM_VRT}") as src:
            win = from_bounds(x.min() - pad, y.min() - pad, x.max() + pad, y.max() + pad, src.transform)
            win = win.round_offsets().round_lengths()
            z = src.read(1, window=win, boundless=True, fill_value=src.nodata).astype(np.float64)
            t = src.window_transform(win)
            nodata = src.nodata
    # Bilinear sample at pixel centres.
    col = (x - t.c) / t.a - 0.5
    row = (y - t.f) / t.e - 0.5
    c0, r0 = np.floor(col).astype(int), np.floor(row).astype(int)
    fc, fr = col - c0, row - r0
    c0 = np.clip(c0, 0, z.shape[1] - 2)
    r0 = np.clip(r0, 0, z.shape[0] - 2)
    q = [z[r0, c0], z[r0, c0 + 1], z[r0 + 1, c0], z[r0 + 1, c0 + 1]]
    bad = np.zeros(len(x), bool)
    for v in q:
        bad |= v == nodata
    h = (q[0] * (1 - fc) + q[1] * fc) * (1 - fr) + (q[2] * (1 - fc) + q[3] * fc) * fr
    keep = ~bad & np.isfinite(h)
    return ee[keep], nn[keep], h[keep]


def write_laz(path, e, n, h) -> None:
    import laspy

    header = laspy.LasHeader(point_format=3, version="1.2")
    header.scales = np.array([0.001, 0.001, 0.001])
    header.offsets = np.array([float(int(config.ORIGIN_E // 1e6) * 1e6), 6000000.0, 0.0])
    las = laspy.LasData(header)
    las.x, las.y, las.z = e, n, h
    las.classification = np.full(len(e), 8, np.uint8)  # model key points, as the city maanpinta
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp.laz")
    las.write(tmp)
    tmp.replace(path)


def fetch_blocks(blocks, log=print) -> list[dict]:
    root = kind_dir("laz")
    reg = Registry(root)
    out = []
    for block in blocks:
        dest = root / "maanpinta" / f"{block.name}.laz"
        if reg.is_complete(dest):
            out.append(reg.get(dest))
            log(f"dem  maanpinta/{block.name}: cached")
            continue
        e, n, h = block_points(block)
        if not len(e):
            raise RuntimeError(f"dem {block.name}: no DEM coverage")
        write_laz(dest, e, n, h)
        rec = reg.record(
            dest,
            url=DEM_VRT,
            dataset="maanpinta",
            tile=block.name,
            source="mml-dem2m",
            spacing_m=SPACING,
            points=int(len(e)),
            crs=config.CRS_HORIZONTAL,
        )
        log(f"dem  maanpinta/{block.name}: {len(e)} points, {human(rec['bytes'])}")
        out.append(rec)
    return out
