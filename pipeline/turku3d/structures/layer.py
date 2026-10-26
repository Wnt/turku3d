"""Complete-object publication through the shared layer v1 helpers."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from shapely.geometry import MultiPoint

from turku3d import config as C
from turku3d.col.materials import mat_id
from turku3d.fetch.common import atomic_write_bytes
from turku3d.layers import (
    CollisionLayer,
    read_manifest,
    write_collision,
    write_manifest,
    write_visual,
)
from turku3d.package.gltf import GlbMesh

MATERIAL_KEYS = {
    "stair_tread_helper": "stone_stairs",
    "wall": "concrete_wall",
    "fence": "metal_fence",
    "railing": "metal_railing",
    "quay": "granite_quay",
    "ramp": "concrete_ramp",
}


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def code_hash():
    paths = sorted(Path(__file__).parent.glob("*.py"))
    paths += [
        Path(C.__file__),
        Path(C.__file__).parent / "col/materials_v1.json",
        Path(C.__file__).parent / "package/gltf.py",
        Path(C.__file__).parent / "layers.py",
    ]
    paths += [
        C.REPO_ROOT / p
        for p in (
            "tools/validator/src/structures.ts",
            "tools/validator/src/structures-cli.ts",
            "tools/validator/src/kcc.ts",
            "tools/validator/package-lock.json",
            "game/src/shared/col/rapierBuild.ts",
            "game/src/shared/col/colFormat.ts",
            "game/src/shared/col/materials.ts",
        )
    ]
    return hashlib.sha256("".join(sha256(p) for p in paths).encode()).hexdigest()


def cached_manifest(out, inputs, params):
    out = Path(out)
    try:
        previous = read_manifest(out)
        expected = {
            **params,
            "seams": "objects",
            "acceptance_passed": json.loads((out / "metrics.json").read_text()).get("status")
            == "PASS",
        }
        stored = dict(previous["params"])
        metrics_hash = stored.pop("metrics_sha256")
        if (
            previous["inputs"] == inputs
            and previous["code"] == code_hash()
            and stored == expected
            and sha256(out / "metrics.json") == metrics_hash
            and (out / "report.md").is_file()
        ):
            return previous
    except (ValueError, OSError, KeyError):
        pass
    return None


def write_layer(meshes, out, *, inputs, params, metrics):
    """Publish complete objects in every collision cell they touch."""
    out = Path(out)
    previous = cached_manifest(out, inputs, params)
    if previous is not None:
        return previous, True
    collision, visuals = defaultdict(list), defaultdict(list)
    for mesh in meshes:
        centre = MultiPoint(mesh.positions[:, [0, 2]]).convex_hull.centroid
        xz = mesh.positions[:, [0, 2]]
        bounds = C.BBoxLocal(*xz.min(axis=0), *xz.max(axis=0)).buffered(1e-6)
        for tile in C.collision_tiles_for_bbox(bounds):
            collision[tile.id].append(mesh)
        if mesh.visual:
            visuals[C.VisualTile.at(centre.x, centre.y).id].append(mesh)
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for tid, parts in sorted(collision.items()):
        positions, indices, materials = [], [], []
        count = 0
        for mesh in parts:
            positions.append(mesh.positions)
            indices.append(mesh.indices + count)
            materials.append(np.full(len(mesh.indices), mat_id(mesh.material), np.uint8))
            count += len(mesh.positions)
        filename = f"{tid}.col.npz"
        write_collision(
            out / filename,
            CollisionLayer(
                np.concatenate(positions).astype(np.float64),
                np.concatenate(indices).astype(np.uint32),
                np.concatenate(materials),
            ),
        )
        files.append(filename)
    for tid, parts in sorted(visuals.items()):
        filename = f"{tid}.vis.glb"
        write_visual(
            out / filename,
            [
                GlbMesh(
                    mesh.object_id,
                    mesh.positions,
                    mesh.indices,
                    uvs=mesh.uvs,
                    extras={
                        "layer": "structures",
                        "object_id": mesh.object_id,
                        "material_key": MATERIAL_KEYS[mesh.material],
                        **mesh.metadata,
                    },
                )
                for mesh in parts
            ],
            root_extras={"layer": "structures"},
        )
        files.append(filename)
    metrics = {
        **metrics,
        "collision_tiles": len(collision),
        "visual_tiles": len(visuals),
        "triangles": sum(len(m.indices) for m in meshes),
    }
    atomic_write_bytes(
        out / "metrics.json", (json.dumps(metrics, indent=2, allow_nan=False) + "\n").encode()
    )
    atomic_write_bytes(
        out / "report.md",
        (
            "# Structures layer report\n\n```json\n"
            + json.dumps(metrics, indent=2, allow_nan=False)
            + "\n```\n"
        ).encode(),
    )
    manifest = write_manifest(
        out,
        inputs=inputs,
        params={
            **params,
            "seams": "objects",
            "acceptance_passed": metrics.get("status") == "PASS",
            "metrics_sha256": sha256(out / "metrics.json"),
        },
        code=code_hash(),
        tiles=files,
    )
    for pattern in ("*.col.npz", "*.vis.glb"):
        for path in out.glob(pattern):
            if path.name not in files:
                path.unlink()
    return manifest, False
