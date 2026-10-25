"""Post-process one generated image into a game material set.

  turkutex post IN.png --out DIR [--name N] [--tile xy|x|none] [--flatten xy|x|none]
                [--size WxH] [--material facade_mixed] [--no-ktx2] [--sobel]

Writes DIR/: albedo.png, normal.png, height.png, orm.png, tile2x2.jpg (QA preview),
albedo.ktx2 (ETC1S sRGB), orm.ktx2 (ETC1S linear), normal.ktx2 (UASTC normal-map, zstd),
post.json (parameters, QA before/after, map provenance, KTX2 header info, sha256 of every file).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import time
from pathlib import Path

import numpy as np
from PIL import Image

from ..paths import tools_dir
from . import maps, qa
from .tileable import crop_to_period, feather_wrap_x, flatten_lowfreq, make_tileable

POST_VERSION = "3"  # 3: native KTX encoder support, explicit RGB normal channels


def native_encoder() -> Path | None:
    configured = os.environ.get("TURKUTEX_TOKTX")
    path = Path(configured) if configured else tools_dir() / "ktx/bin/toktx"
    if path.is_file():
        return path
    found = shutil.which("toktx") if not configured else None
    return Path(found) if found else None


def pow2_floor(n: int) -> int:
    return 1 << max(4, int(np.floor(np.log2(max(16, n)))))


def target_size(w: int, h: int, spec: str | None) -> tuple[int, int]:
    if spec:
        a, b = spec.lower().split("x")
        return int(a), int(b)
    return pow2_floor(w), pow2_floor(h)


def ktx2_header(path: Path) -> dict:
    b = Path(path).read_bytes()[:80]
    if len(b) < 80 or b[:12] != b"\xabKTX 20\xbb\r\n\x1a\n":
        return {"valid": False}
    vk, ts, w, h, d, layers, faces, levels, sc = struct.unpack("<9I", b[12:48])
    return {
        "valid": True,
        "vkFormat": vk,
        "width": w,
        "height": h,
        "levels": levels,
        "supercompression": {0: "none", 1: "BasisLZ(ETC1S)", 2: "zstd", 3: "zlib"}.get(sc, sc),
    }


def encode_ktx2(png: Path, out: Path, mode: str, linear: bool) -> dict:
    native = native_encoder()
    if native is None:
        raise RuntimeError("KTX-Software is required; run make textures-ktx or put toktx on PATH")
    start = time.monotonic()
    cmd = [
        str(native),
        "--t2",
        "--genmipmap",
        "--assign_oetf",
        "linear" if linear else "srgb",
        "--assign_primaries",
        "none" if linear else "bt709",
        "--threads",
        "2",
        "--encode",
        "etc1s" if mode == "etc1s" else "uastc",
    ]
    if mode == "etc1s":
        cmd += ["--qlevel", "128", "--clevel", "1"]
    else:
        cmd += ["--zcmp", "6", "--uastc_rdo_l", "0.5", "--uastc_rdo_m"]
    if mode == "uastc-normal":
        # --normal_mode repacks XY into RGB/A and breaks standard glTF RGB normal sampling.
        cmd += ["--normalize"]
    subprocess.run([*cmd, str(out), str(png)], check=True, capture_output=True, text=True)
    validator = native.with_name("ktx")
    if not validator.is_file():
        raise RuntimeError(f"Missing KTX validator beside {native}")
    subprocess.run(
        [str(validator), "validate", "--gltf-basisu", str(out)],
        check=True,
        capture_output=True,
        text=True,
    )
    return {
        "encoder": "toktx",
        "mode": mode,
        "srgb": not linear,
        "bytes": out.stat().st_size,
        "ms": round((time.monotonic() - start) * 1000),
        "header": ktx2_header(out),
        "gltf_basisu_valid": True,
    }


def sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def process(
    inp: Path,
    out: Path,
    name: str | None = None,
    tile: str = "xy",
    flatten: str = "xy",
    size: str | None = None,
    material: str = "facade_mixed",
    ktx2: bool = True,
    sobel: bool = False,
    band_frac: float | None = None,
    period_crop: bool = True,
) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    src = Image.open(inp).convert("RGB")
    W, H = target_size(src.width, src.height, size)
    period = {"period_px": None}
    if period_crop and tile == "x":
        # strips: keep a whole number of bays so the wrap does not squeeze a bay at the seam
        cropped, period = crop_to_period(np.asarray(src))
        src = Image.fromarray(cropped)
    img = np.asarray(src.resize((W, H), Image.LANCZOS))
    axes = "" if tile == "none" else tile
    qa_before = qa.metrics(img, axes)
    if flatten != "none":
        img = flatten_lowfreq(img, flatten)
    if axes:
        if axes == "x" and period.get("period_px"):
            # whole-bay crop: the edges already match; quilting would paste a window fragment into
            # the seam, so only feather the residual step
            img = feather_wrap_x(img, max(4, img.shape[1] // 128))
        else:
            img = make_tileable(img, axes, band_frac=band_frac or (0.12 if axes == "x" else 0.2))
    Image.fromarray(img).save(out / "albedo.png")
    if not sobel and maps.deepbump_available():
        nrm = maps.normals_deepbump(img)
        if axes:
            nrm = maps.periodic_normals(nrm, axes)
        hgt = maps.height_deepbump(nrm, seamless=bool(axes))
        how = "DeepBump color_to_normals (LARGE overlap) + normals_to_height"
    else:
        nrm = maps.normals_sobel(img, wrap=bool(axes))
        if axes:
            nrm = maps.periodic_normals(nrm, axes)
        hgt = maps.height_from_luminance(img)
        how = "Sobel on luminance (DeepBump not installed or --sobel)"
    if axes:
        how += "; periodic normal-edge blend and unit renormalization"
    Image.fromarray(nrm).save(out / "normal.png")
    Image.fromarray(hgt).save(out / "height.png")
    o = maps.orm(img, hgt, material)
    Image.fromarray(o).save(out / "orm.png")
    Image.fromarray(qa.tile_preview(img, axes) if axes else img).save(
        out / "tile2x2.jpg", quality=88
    )
    qa_after = qa.metrics(img, axes)
    map_qa = (
        {
            k: qa.metrics(np.asarray(Image.open(out / f"{k}.png").convert("RGB")), axes)
            for k in ("normal", "height", "orm")
        }
        if axes
        else {}
    )
    rec = {
        "name": name or inp.stem,
        "input": str(inp),
        "input_sha256": sha(inp),
        "post_version": POST_VERSION,
        "size": [W, H],
        "source_size": [src.width, src.height],
        "tile": tile,
        "flatten": flatten,
        "period_crop": period,
        "material": material,
        "maps": how,
        "qa_before": qa_before,
        "qa_after": qa_after,
        "map_seam_ratio": {k: {a: v[f"seam_{a}"] for a in axes} for k, v in map_qa.items()},
        "map_qa": map_qa,
        "ktx2": {},
    }
    if ktx2:
        rec["ktx2"]["albedo"] = encode_ktx2(out / "albedo.png", out / "albedo.ktx2", "etc1s", False)
        rec["ktx2"]["orm"] = encode_ktx2(out / "orm.png", out / "orm.ktx2", "etc1s", True)
        rec["ktx2"]["normal"] = encode_ktx2(
            out / "normal.png", out / "normal.ktx2", "uastc-normal", True
        )
    rec["files"] = {
        p.name: {"bytes": p.stat().st_size, "sha256": sha(p)}
        for p in sorted(out.iterdir())
        if p.suffix in (".png", ".ktx2", ".jpg")
    }
    rec["seconds"] = round(time.time() - t0, 1)
    (out / "post.json").write_text(json.dumps(rec, indent=1))
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="turkutex post",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("input", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--name")
    ap.add_argument("--tile", default="xy", choices=["xy", "x", "none"])
    ap.add_argument(
        "--flatten", default=None, choices=["xy", "x", "none"], help="default: same as --tile"
    )
    ap.add_argument("--size", help="WxH output size (default: power-of-two floor of the input)")
    ap.add_argument("--material", default="facade_mixed", choices=sorted(maps.ORM_PRESETS))
    ap.add_argument("--no-ktx2", action="store_true")
    ap.add_argument("--sobel", action="store_true", help="skip DeepBump")
    ap.add_argument(
        "--no-period-crop", action="store_true", help="x strips: do not crop to whole bays"
    )
    a = ap.parse_args(argv)
    r = process(
        a.input,
        a.out,
        a.name,
        a.tile,
        a.flatten or a.tile,
        a.size,
        a.material,
        not a.no_ktx2,
        a.sobel,
        period_crop=not a.no_period_crop,
    )
    print(
        json.dumps(
            {
                "out": str(a.out),
                "size": r["size"],
                "qa_before": r["qa_before"],
                "qa_after": r["qa_after"],
                "ktx2_bytes": {k: v["bytes"] for k, v in r["ktx2"].items()},
                "seconds": r["seconds"],
            }
        )
    )
    return 0 if r["qa_after"]["pass_seam"] else 1
