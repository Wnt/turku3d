"""Complete-object publication through the shared layer v1 helpers."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path

import numpy as np

from turku3d import config as C
from turku3d.col.materials import mat_id
from turku3d.layers import CollisionLayer, write_collision, write_visual
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
        Path(C.__file__).parent / "tile_layer.py",
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


def tile_cells(mesh, tile):
    """The collision tiles of `tile` that the whole mesh touches (seams: objects)."""
    xz = mesh.positions[:, [0, 2]]
    bounds = C.BBoxLocal(*xz.min(axis=0), *xz.max(axis=0)).buffered(1e-6)
    return [c for c in C.collision_tiles_for_bbox(bounds) if c.visual_parent() == tile]


def write_tile(meshes, directory, tile):
    """One L0 tile's structures: each complete mesh in every collision tile of this tile it
    touches, and the visual meshes in the tile's GLB. A mesh poking past the tile edge stays
    whole in this tile's cells, so a neighbour's build never writes it."""
    directory = Path(directory)
    collision = defaultdict(list)
    for mesh in meshes:
        for cell in tile_cells(mesh, tile):
            collision[cell.id].append(mesh)
    for tid, parts in sorted(collision.items()):
        positions, indices, materials = [], [], []
        count = 0
        for mesh in parts:
            position = mesh.positions.copy()
            for axis in (0, 2):
                boundary = (
                    np.rint(position[:, axis] / C.COLLISION_TILE_SIZE) * C.COLLISION_TILE_SIZE
                )
                near = np.abs(position[:, axis] - boundary) < 1e-8
                position[near, axis] = boundary[near]
            positions.append(position)
            indices.append(mesh.indices + count)
            materials.append(np.full(len(mesh.indices), mat_id(mesh.material), np.uint8))
            count += len(mesh.positions)
        write_collision(
            directory / f"{tid}.col.npz",
            CollisionLayer(
                np.concatenate(positions).astype(np.float64),
                np.concatenate(indices).astype(np.uint32),
                np.concatenate(materials),
            ),
        )
    visual = [mesh for mesh in meshes if mesh.visual]
    if visual:
        write_visual(
            directory / f"{tile.id}.vis.glb",
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
                for mesh in visual
            ],
            root_extras={"layer": "structures"},
        )
    return {"collision_tiles": len(collision), "triangles": sum(len(m.indices) for m in meshes)}


def omit_failed(meshes, issues):
    """Drop the meshes of objects that failed a geometric or physics check."""
    failed = {issue["object_id"].rsplit(":route:", 1)[0] for issue in issues}
    failed |= {source for issue in issues for source in issue.get("sources", [])}
    kept = [
        m
        for m in meshes
        if not any(m.object_id == f or m.object_id.startswith(f + ":") for f in failed)
    ]
    return kept, len(meshes) - len(kept)
