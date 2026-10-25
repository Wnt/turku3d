"""Publish assembler-declared ground resources, leaving absent products on library fallbacks."""

import hashlib
import json
from pathlib import Path

from .manifest import _atomic_write


def publish_ground(root: Path, out: Path, tile_ids, ground_layer: Path | None = None):
    bindings = {}
    library = root.parent / "library"
    arrays = [library / f"{name}.array.ktx2" for name in ("albedo", "normal", "orm")]
    if not all(p.is_file() for p in arrays):
        return bindings
    library_id = hashlib.sha256(b"".join(p.read_bytes() for p in arrays)).hexdigest()[:16]
    library_url = f"ground/library/{library_id}/"
    for tile in tile_ids:
        source = root / tile / "material.json"
        if not source.is_file():
            continue
        spec = json.loads(source.read_text())
        if not spec.get("ground_inputs"):
            continue  # An ortho-only preview is not a production ground binding.
        if ground_layer is not None and any(
            not (ground_layer / Path(item["path"]).name).is_file()
            or hashlib.sha256((ground_layer / Path(item["path"]).name).read_bytes()).hexdigest()
            != item["sha256"]
            for item in spec["ground_inputs"]
        ):
            continue  # Stale splats must not bind to changed geometry or synthetic fixtures.
        maps = [
            root / tile / spec["textures"][name]["uri"] for name in ("ortho", "splat0", "splat1")
        ]
        if not all(p.is_file() for p in maps):
            continue
        for name, path in zip(("ortho", "splat0", "splat1"), maps, strict=True):
            if hashlib.sha256(path.read_bytes()).hexdigest() != spec["textures"][name]["sha256"]:
                raise ValueError(f"Ground texture hash mismatch: {path}")
        product_id = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
        tile_url = f"ground/{tile}/{product_id}/"
        for path in maps + [source]:
            _atomic_write(out / tile_url / path.name, path.read_bytes())
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
