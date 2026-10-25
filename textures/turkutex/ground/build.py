"""Build ground texture inputs for the assembler; never writes shipped GLB/.col/tilesets."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import map_coordinates
from turku3d import config as C
from turku3d.col.materials import by_name

from ..paths import TEXTURES_DIR
from ..post.pipeline import sha
from .encode import encode
from .ortho import colour_calibration, fuse, gpu_bytes
from .splat import MATERIAL_KEYS, pack_weights, rasterize_triangles, weights

PIXEL_M = 0.04
CORE_PX = round(C.visual_tile_size(0) / PIXEL_M)
HALO_PX = 65  # (6250 + 130) is block-aligned; > all sequential filter support.
SPLAT_PX = 1024
SPLAT_HALO = 8


def read_ortho(path: Path, x0, z0, pixel_m, width, height):
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import from_origin
    from rasterio.vrt import WarpedVRT

    transform = from_origin(C.ORIGIN_E + x0, C.ORIGIN_N - z0, pixel_m, pixel_m)
    with rasterio.open(path) as src:
        if str(src.crs) != C.CRS_HORIZONTAL:
            raise ValueError(f"{path}: expected {C.CRS_HORIZONTAL}, got {src.crs}")
        with WarpedVRT(
            src,
            crs=src.crs,
            transform=transform,
            width=width,
            height=height,
            resampling=Resampling.bilinear,
            add_alpha=True,
        ) as vrt:
            rgb = np.moveaxis(vrt.read([1, 2, 3]), 0, -1)
            valid = vrt.read(vrt.count) > 0
    return rgb, valid


def semantic_raster(layer: Path | None, tile, size, halo):
    step = tile.size / size
    shape = (size + halo * 2,) * 2
    ids = np.zeros(shape, np.uint8)
    sources = []
    if layer is None:
        return ids, sources
    if not layer.is_dir():
        raise FileNotFoundError(layer)
    bounds = tile.bbox.buffered(step * halo)
    for i, j in C.grid_range(bounds, C.COLLISION_TILE_SIZE):
        path = layer / f"c_{i}_{j}.col.npz"
        if not path.exists():
            continue
        with np.load(path, allow_pickle=False) as a:
            raster = rasterize_triangles(
                a["positions"],
                a["indices"][a["material"] != by_name()["water"].id],
                a["material"][a["material"] != by_name()["water"].id],
                shape=shape,
                x0=bounds.xmin,
                z0=bounds.zmin,
                pixel_m=step,
            )
        ids[raster != 0] = raster[raster != 0]
        sources.append({"path": str(path), "sha256": sha(path)})
    if not sources:
        raise ValueError(f"{tile.id}: no ground collision layers intersect texture tile")
    return ids, sources


def build_tile(
    tile, primary, recent, calibration, out: Path, layer: Path | None, splats_only=False
):
    start = time.monotonic()
    out.mkdir(parents=True, exist_ok=True)
    n = CORE_PX + 2 * HALO_PX
    x0, z0 = tile.bbox.xmin - HALO_PX * PIXEL_M, tile.bbox.zmin - HALO_PX * PIXEL_M
    if splats_only:
        previous = json.loads((out / "material.json").read_text())
        combined = np.asarray(Image.open(out / "ortho.png").convert("RGB"))
        uncertain = np.asarray(Image.open(out / "uncertainty.png"))
        outputs = {k: v for k, v in previous["textures"].items() if k.startswith("ortho")}
    else:
        p, pv = read_ortho(primary, x0, z0, PIXEL_M, n, n)
        r, rv = read_ortho(recent, x0, z0, PIXEL_M, n, n)
        combined, matched, uncertain = fuse(p, r, pv, rv, calibration, pixel_m=PIXEL_M)
        outputs = {}
        for name, rgb in [("ortho2022", p), ("ortho2025", matched), ("ortho", combined)]:
            png = out / f"{name}.png"
            Image.fromarray(rgb).save(png, compress_level=2)
            outputs[name] = encode(
                [png], out / f"{name}.ktx2", quality=16 if name == "ortho" else None
            )
            print(f"{tile.id} {name}: {outputs[name]['bytes']:,} bytes", flush=True)
        Image.fromarray(uncertain).save(out / "uncertainty.png")
    ids, sources = semantic_raster(layer, tile, SPLAT_PX, SPLAT_HALO)
    s = SPLAT_PX + 2 * SPLAT_HALO
    coordinates = (
        (np.arange(s) - SPLAT_HALO + 0.5) * (tile.size / SPLAT_PX) / PIXEL_M + HALO_PX - 0.5
    )
    yy, xx = np.meshgrid(coordinates, coordinates, indexing="ij")
    rgb = np.stack(
        [map_coordinates(combined[..., c], [yy, xx], order=1, mode="nearest") for c in range(3)], -1
    )
    lo, hi = pack_weights(weights(ids, rgb))
    for name, value in [("splat0", lo), ("splat1", hi)]:
        png = out / f"{name}.png"
        Image.fromarray(value).save(png)
        outputs[name] = encode([png], out / f"{name}.ktx2", linear=True, uastc=True)
    core = ids[SPLAT_HALO:-SPLAT_HALO, SPLAT_HALO:-SPLAT_HALO]
    per_tile = gpu_bytes(n, n) + 2 * gpu_bytes(s, s)
    result = {
        "version": 1,
        "tile": tile.id,
        "material_keys": MATERIAL_KEYS,
        "pixel_m": PIXEL_M,
        "source_native_pixel_m": {"2022": 0.055, "2025": 0.052},
        "pixel_note": "4 cm output sampling; native ortho detail is not increased by resampling",
        "bounds_xz": list(tile.bbox.as_tuple()),
        "image_origin": "north-west; row increases south",
        "ortho_uv": {"offset": HALO_PX / n, "scale": CORE_PX / n},
        "splat_uv": {"offset": SPLAT_HALO / s, "scale": SPLAT_PX / s},
        "textures": {name: {"uri": f"{name}.ktx2", **meta} for name, meta in outputs.items()},
        "runtime_textures": ["ortho", "splat0", "splat1"],
        "semantic_source": "ground triangles + ortho fallback"
        if sources
        else "ortho-only preview; ground layer absent",
        "triangle_coverage": float(np.mean(core != 0)),
        "ground_inputs": sources,
        "uncertain_fraction": float(np.mean(uncertain[HALO_PX:-HALO_PX, HALO_PX:-HALO_PX] != 0)),
        "gpu_bytes_8bpp": per_tile,
        "seconds": round(time.monotonic() - start, 2),
    }
    (out / "material.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tiles", nargs="+", default=["t0_0_0", "t0_0_1", "t0_1_0", "t0_1_1"])
    ap.add_argument("--area", default="work-3x3")
    ap.add_argument(
        "--splats-only",
        action="store_true",
        help="Reuse existing fused orthos; rebuild triangle-derived splats",
    )
    ap.add_argument(
        "--ground-layer",
        type=Path,
        help="ground layer directory with .col.npz inputs; absent = ortho-only preview",
    )
    ap.add_argument("--out", type=Path, default=C.work_root() / "textures/ground")
    a = ap.parse_args(argv)
    tiles = [C.VisualTile.parse(t) for t in a.tiles]
    if any(t.level != 0 for t in tiles):
        ap.error("ground textures require L0 tiles")
    primary = C.raw_dir("ortho") / "ortho2022to" / f"{a.area}.vrt"
    recent = C.raw_dir("ortho") / "ortho2025" / f"{a.area}.vrt"
    previous = json.loads((a.out / "MANIFEST.json").read_text()) if a.splats_only else None
    if previous:
        calibration = previous["calibration"]
    else:
        # Stable calibration footprint is the named raw area, independent of tile order/subsets.
        area = C.AREAS[a.area].core
        step = 1.0
        width, height = round((area.xmax - area.xmin) / step), round((area.zmax - area.zmin) / step)
        p, pv = read_ortho(primary, area.xmin, area.zmin, step, width, height)
        r, rv = read_ortho(recent, area.xmin, area.zmin, step, width, height)
        calibration = colour_calibration(p, r, pv & rv)
    a.out.mkdir(parents=True, exist_ok=True)
    products = [
        build_tile(t, primary, recent, calibration, a.out / t.id, a.ground_layer, a.splats_only)
        for t in tiles
    ]
    if previous:
        inputs = previous["inputs"]
    else:
        inputs = []
        import rasterio

        for vrt in [primary, recent]:
            with rasterio.open(vrt) as ds:
                inputs.extend({"path": f, "sha256": sha(Path(f))} for f in sorted(set(ds.files)))
    code = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        code.update(path.name.encode() + path.read_bytes())
    shared = 8 * 3 * gpu_bytes(1024, 1024)
    resident = 4 * max(p["gpu_bytes_8bpp"] for p in products) + shared
    manifest = {
        "version": 1,
        "inputs": inputs,
        "params": {"area": a.area, "pixel_m": PIXEL_M, "halo_px": HALO_PX, "splat_px": SPLAT_PX},
        "calibration": calibration,
        "code_version": code.hexdigest(),
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "tiles_written": [t.id for t in tiles],
        "tiles": products,
        "gpu_budget": {
            "worst_supported_block_bytes": 16,
            "shared_library_bytes": shared,
            "four_resident_tiles_bytes": resident,
            "D4_limit_bytes": 300_000_000,
            "within_D4": resident <= 300_000_000,
            "scope": "ground textures only; 2022/2025 archive textures are not resident",
            "uncompressed_fallback": "not budget-compliant; requires block-compression support",
        },
    }
    manifest["source_licenses"] = json.loads(
        (TEXTURES_DIR / "library/ground-sources.json").read_text()
    )
    (a.out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest["gpu_budget"]))
    return 0 if resident <= 300_000_000 else 1


if __name__ == "__main__":
    raise SystemExit(main())
