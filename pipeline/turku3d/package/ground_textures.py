"""Publish ground resources only when their declared source geometry is current."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

from PIL import Image

from turku3d import config as C

from .manifest import _atomic_write

RUNTIME_ORTHO_CORE_PX = 5600


def runtime_ortho(root: Path, tile: str, spec: dict) -> tuple[Path, dict]:
    """Encode the shipped ortho near its native detail while retaining the 4 cm source."""
    source = root / tile / "ortho.png"
    meta = spec["textures"]["ortho"]
    if not source.is_file() or spec.get("pixel_m") != 0.04:
        return root / tile / meta["uri"], spec
    width = meta["width"]
    core = round(C.visual_tile_size(0) / spec["pixel_m"])
    target = round(width * RUNTIME_ORTHO_CORE_PX / core / 4) * 4
    key = hashlib.sha256(source.read_bytes() + f"runtime-ortho-v1:{target}:q4".encode()).hexdigest()
    directory = C.work_root() / "cache/package-ground" / key
    output = directory / "ortho.ktx2"
    if not output.is_file():
        directory.mkdir(parents=True, exist_ok=True)
        png = directory / "ortho.png"
        with Image.open(source) as image:
            image.resize((target, target), Image.Resampling.LANCZOS).save(png)
        temporary = directory / "ortho.tmp.ktx2"
        try:
            subprocess.run(
                [
                    "toktx",
                    "--t2",
                    "--genmipmap",
                    "--threads",
                    str(max(1, int(os.environ.get("TURKU3D_JOBS", "4")))),
                    "--assign_oetf",
                    "srgb",
                    "--encode",
                    "etc1s",
                    "--qlevel",
                    "4",
                    "--clevel",
                    "0",
                    str(temporary),
                    str(png),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)
    spec = json.loads(json.dumps(spec))
    spec["textures"]["ortho"] = {
        "uri": "ortho.ktx2",
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "bytes": output.stat().st_size,
        "width": target,
        "height": target,
        "levels": meta["levels"],
        "valid": True,
        "supercompression": meta["supercompression"],
    }
    spec["pixel_m"] = spec["pixel_m"] * width / target
    spec["pixel_note"] = "4 cm source resampled for the shipped wire budget"
    return output, spec


def ground_texture_code_hash():
    h = hashlib.sha256()
    for path in sorted((C.REPO_ROOT / "textures/turkutex/ground").glob("*.py")):
        h.update(path.name.encode() + path.read_bytes())
    return h.hexdigest()


def publish_ground(root: Path, out: Path, tile_ids, ground_layer: Path | None = None):
    bindings = {}
    manifest = root / "MANIFEST.json"
    if (
        manifest.is_file()
        and json.loads(manifest.read_text()).get("code_version") != ground_texture_code_hash()
    ):
        raise ValueError(f"{manifest}: ground texture code changed; run make refresh")
    library = root.parent / "library"
    arrays = [library / f"{name}.array.ktx2" for name in ("albedo", "normal", "orm")]
    if not all(p.is_file() for p in arrays):
        if any(
            source.is_file() and json.loads(source.read_text()).get("ground_inputs")
            for tile in tile_ids
            if (source := root / tile / "material.json")
        ):
            missing = ", ".join(str(p) for p in arrays if not p.is_file())
            raise FileNotFoundError(
                f"Ground texture library is incomplete: {missing}; run make refresh"
            )
        return bindings
    library_id = hashlib.sha256(b"".join(p.read_bytes() for p in arrays)).hexdigest()[:16]
    library_url = f"ground/library/{library_id}/"
    for tile in tile_ids:
        source = root / tile / "material.json"
        if not source.is_file():
            if ground_layer is not None and (ground_layer / f"{tile}.vis.glb").exists():
                raise ValueError(f"{tile}: ground binding missing; run make refresh")
            continue
        spec = json.loads(source.read_text())
        if not spec.get("ground_inputs"):
            continue  # An ortho-only preview is not a production ground binding.
        if ground_layer is not None:
            stale = [
                item["path"]
                for item in spec["ground_inputs"]
                if not (ground_layer / Path(item["path"]).name).is_file()
                or hashlib.sha256((ground_layer / Path(item["path"]).name).read_bytes()).hexdigest()
                != item["sha256"]
            ]
            if stale:
                raise ValueError(
                    f"{tile}: stale ground binding ({len(stale)} changed collision inputs, "
                    f"first {Path(stale[0]).name}); run make refresh"
                )
        maps = [
            root / tile / spec["textures"][name]["uri"] for name in ("ortho", "splat0", "splat1")
        ]
        if not all(p.is_file() for p in maps):
            raise FileNotFoundError(f"{tile}: ground maps missing; run make refresh")
        for name, path in zip(("ortho", "splat0", "splat1"), maps, strict=True):
            if hashlib.sha256(path.read_bytes()).hexdigest() != spec["textures"][name]["sha256"]:
                raise ValueError(f"Ground texture hash mismatch: {path}")
        ortho, shipped_spec = runtime_ortho(root, tile, spec)
        maps[0] = ortho
        spec_bytes = (
            source.read_bytes()
            if shipped_spec is spec
            else (json.dumps(shipped_spec, indent=2) + "\n").encode()
        )
        product_id = hashlib.sha256(spec_bytes).hexdigest()[:16]
        tile_url = f"ground/{tile}/{product_id}/"
        for path in maps:
            _atomic_write(out / tile_url / path.name, path.read_bytes())
        _atomic_write(out / tile_url / source.name, spec_bytes)
        bindings[tile] = {
            "version": 1,
            "texture_bytes": sum(p.stat().st_size for p in maps),
            "spec": f"{tile_url}material.json",
            "library": library_url,
        }
    if bindings:
        for path in arrays:
            _atomic_write(out / library_url / path.name, path.read_bytes())
    return bindings
