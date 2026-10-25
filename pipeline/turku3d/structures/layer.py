"""Deterministic clipping and the COMMON §4 layer writer."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path
import tempfile
import numpy as np
from turku3d import config as C
from turku3d.col.materials import mat_id
from turku3d.package.gltf import GlbMesh, write_glb
from .geometry import Mesh


MATERIAL_KEYS = {"stair_tread_helper": "stone_stairs", "wall": "concrete_wall",
                 "fence": "metal_fence", "railing": "metal_railing", "quay": "granite_quay", "ramp": "concrete_ramp"}


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def code_hash():
    paths = sorted(Path(__file__).parent.glob("*.py"))
    paths += [Path(C.__file__), Path(C.__file__).parent / "col/materials_v1.json",
              Path(C.__file__).parent / "package/gltf.py"]
    return hashlib.sha256("".join(sha256(p) for p in paths).encode()).hexdigest()


def _split(poly, axis, plane):
    """Split once, then share the exact same intersection values on both sides.

    Positions and UVs travel together. Canonical endpoint order prevents opposite
    triangle winding from changing the intersection bits. All grid planes are
    applied globally (x then z), before any tile selects its triangles.
    """
    low, high = [], []
    for a, b in zip(poly, np.roll(poly, -1, axis=0)):
        if a[axis] <= plane:
            low.append(a)
        if a[axis] >= plane:
            high.append(a)
        if (a[axis] < plane < b[axis]) or (b[axis] < plane < a[axis]):
            first, second = sorted((a, b), key=lambda v: tuple(v[:3]))
            t = (plane - first[axis]) / (second[axis] - first[axis])
            v = first + t * (second - first)
            v[axis] = plane
            low.append(v)
            high.append(v)
    # A coplanar polygon is owned once, with seam-only vertices added below.
    if np.all(poly[:, axis] == plane):
        return [poly]
    return [np.asarray(p) for p in (low, high) if len(p) >= 3]


def tile_mesh(mesh: Mesh, size=C.COLLISION_TILE_SIZE):
    data = np.column_stack((mesh.positions, mesh.uvs))
    pieces = [data[t] for t in mesh.indices]
    for axis in (0, 2):
        lo = int(np.floor(mesh.positions[:, axis].min() / size)) + 1
        hi = int(np.ceil(mesh.positions[:, axis].max() / size))
        for k in range(lo, hi):
            pieces = [out for p in pieces for out in _split(p, axis, k * size)]
    groups = defaultdict(list)
    for poly in pieces:
        for k in range(1, len(poly) - 1):
            tri = poly[[0, k, k + 1]]
            if np.linalg.norm(np.cross(tri[1, :3] - tri[0, :3], tri[2, :3] - tri[0, :3])) < 1e-12:
                continue
            x, _, z = tri[:, :3].mean(axis=0)
            groups[C.grid_index(x, z, size)].append(tri)
    return {key: np.concatenate(tris) for key, tris in groups.items()}


def _arrays(parts):
    p = np.concatenate([part[:, :3] for _, part in parts])
    # Lexicographic deduplication preserves float64 seam values and strips hard-normal duplication.
    p, inverse = np.unique(p, axis=0, return_inverse=True)
    indices = inverse.astype(np.uint32).reshape(-1, 3)
    mats = np.concatenate([np.full(len(part) // 3, mat_id(mesh.material), dtype=np.uint8)
                           for mesh, part in parts])
    return p, indices, mats


def _complete_seams(tiles):
    """Include the common boundary vertex union, including faces on a tile edge.

    A structure can touch an edge without occupying the neighbouring tile. Its
    contact vertices are retained there without adding duplicate collision faces.
    Intersections of crossing triangles already match before this completion.
    """
    for i, j in sorted(tiles):
        for axis, other in ((0, (i + 1, j)), (2, (i, j + 1))):
            if other not in tiles:
                continue
            coordinate = other[0 if axis == 0 else 1] * C.COLLISION_TILE_SIZE
            a, b = tiles[i, j][0], tiles[other][0]
            edge = np.unique(np.concatenate((a[a[:, axis] == coordinate], b[b[:, axis] == coordinate])), axis=0)
            for key in ((i, j), other):
                p, idx, mat = tiles[key]
                merged, inv = np.unique(np.concatenate((p, edge)), axis=0, return_inverse=True)
                tiles[key] = merged, inv[:len(p)][idx].astype(np.uint32), mat
    # A corner can be introduced by the final edge: propagate once more to all four neighbours.
    corners = defaultdict(list)
    for p, _, _ in tiles.values():
        for v in p[(p[:, 0] % C.COLLISION_TILE_SIZE == 0) & (p[:, 2] % C.COLLISION_TILE_SIZE == 0)]:
            corners[v[0], v[2]].append(v)
    for (x, z), points in corners.items():
        i, j = C.grid_index(x, z, C.COLLISION_TILE_SIZE)
        for key in ((i, j), (i - 1, j), (i, j - 1), (i - 1, j - 1)):
            if key in tiles:
                p, idx, mat = tiles[key]
                merged, inv = np.unique(np.concatenate((p, points)), axis=0, return_inverse=True)
                tiles[key] = merged, inv[:len(p)][idx].astype(np.uint32), mat


def layer_identity(inputs, params):
    return dict(layer="structures", schema_version=1, inputs=inputs, params=params, code_hash=code_hash())


def cached_manifest(out, inputs, params):
    out = Path(out)
    manifest_path = out / "MANIFEST.json"
    if not manifest_path.is_file():
        return None
    try:
        previous = json.loads(manifest_path.read_text())
    except (ValueError, OSError):
        return None
    files = previous.get("files", {})
    if not files or not {"metrics.json", "report.md"}.issubset(files):
        return None
    if all(previous.get(k) == v for k, v in layer_identity(inputs, params).items()) and all(
            Path(name).name == name and (out / name).is_file() and sha256(out / name) == digest
            for name, digest in files.items()):
        return previous
    return None


def write_layer(meshes, out, *, inputs, params, metrics):
    out = Path(out)
    identity = layer_identity(inputs, params)
    manifest_path = out / "MANIFEST.json"
    previous = cached_manifest(out, inputs, params)
    if previous is not None:
        return previous, True
    collision, visuals = defaultdict(list), defaultdict(list)
    for mesh in meshes:
        for key, part in tile_mesh(mesh).items():
            collision[key].append((mesh, part))
        if mesh.visual:
            for key, part in tile_mesh(mesh, C.VISUAL_TILE_SIZE).items():
                visuals[key].append((mesh, part))
    arrays = {key: _arrays(parts) for key, parts in collision.items()}
    _complete_seams(arrays)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".structures-", dir=out.parent) as temp:
        stage = Path(temp)
        for (i, j), (p, idx, mat) in sorted(arrays.items()):
            np.savez(stage / f"c_{i}_{j}.col.npz", positions=p, indices=idx, material=mat)
        for (i, j), parts in sorted(visuals.items()):
            glb_meshes = []
            for mesh, part in parts:
                glb_meshes.append(GlbMesh(mesh.object_id, part[:, :3],
                    np.arange(len(part), dtype=np.uint32).reshape(-1, 3), uvs=part[:, 3:],
                    extras={"layer": "structures", "object_id": mesh.object_id,
                            "material_key": MATERIAL_KEYS[mesh.material], **mesh.metadata}))
            write_glb(glb_meshes, stage / f"t0_{i}_{j}.vis.glb", root_extras={"layer": "structures"})
        metrics = {**metrics, "collision_tiles": len(arrays), "visual_tiles": len(visuals),
                   "triangles": sum(len(v[1]) for v in arrays.values())}
        (stage / "metrics.json").write_text(json.dumps(metrics, indent=2, allow_nan=False) + "\n")
        lines = ["# Structures layer report", "", f"Status: {metrics.get('status', 'synthetic')}", "",
                 f"Collision tiles: {len(arrays)}; visual tiles: {len(visuals)}; triangles: {metrics['triangles']}.", "",
                 "## Measurements", "", "```json", json.dumps(metrics, indent=2, allow_nan=False), "```", ""]
        (stage / "report.md").write_text("\n".join(lines))
        files = {p.name: sha256(p) for p in sorted(stage.iterdir())}
        manifest = {**identity, "status": metrics.get("status", "SYNTHETIC"), "tiles": sorted(n for n in files if n.endswith((".col.npz", ".vis.glb"))), "files": files}
        (stage / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
        if manifest_path.exists():
            old = json.loads(manifest_path.read_text())
            if old.get("layer") != "structures":
                raise ValueError("refusing to overwrite a different layer")
            for name in old.get("files", {}):
                if Path(name).name != name:
                    raise ValueError("invalid manifest file name")
                if name not in files:
                    (out / name).unlink(missing_ok=True)
        for name in files:
            (stage / name).replace(out / name)
        (stage / "MANIFEST.json").replace(manifest_path)
    return manifest, False
