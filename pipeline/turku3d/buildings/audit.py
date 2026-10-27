"""Audit exported layer seams, per-building collision closure and visual metadata."""

import json
from collections import defaultdict

import numpy as np
import trimesh

from turku3d import config as C
from turku3d.layers import read_manifest
from turku3d.package.gltf import accessor_array, read_glb


def audit(layer):
    metrics = json.loads((layer / "metrics.json").read_text())
    owners = metrics["collision_building_ids"]
    triangles, borders, errors = defaultdict(list), {}, []
    paths = sorted(layer.glob("*.col.npz"))
    for path in paths:
        with np.load(path) as tile:
            p, idx, material = tile["positions"], tile["indices"], tile["material"]
            if p.dtype != np.float64 or idx.dtype != np.uint32 or material.dtype != np.uint8:
                errors.append(f"{path.name}: invalid collision dtypes")
            for owner in np.unique(tile["building_index"]):
                triangles[owners[owner]].append(p[idx[tile["building_index"] == owner]])
            tid = C.CollisionTile.parse(path.name.removesuffix(".col.npz"))
            borders[(tid.i, tid.j)] = p
    seams = 0
    continuous = read_manifest(layer)["params"].get("seams") == "continuous"
    for (i, j), p in borders.items() if continuous else []:
        for axis, neighbour, value in [
            (0, (i + 1, j), (i + 1) * C.COLLISION_TILE_SIZE),
            (2, (i, j + 1), (j + 1) * C.COLLISION_TILE_SIZE),
        ]:
            if neighbour not in borders:
                continue
            q = borders[neighbour]
            seams += 1
            if set(map(bytes, p[p[:, axis] == value])) != set(map(bytes, q[q[:, axis] == value])):
                errors.append(f"seam mismatch {(i, j)} / {neighbour}")
    counts = {}
    for owner, chunks in triangles.items():
        p, inverse = np.unique(np.concatenate(chunks).reshape(-1, 3), axis=0, return_inverse=True)
        mesh = trimesh.Trimesh(p, inverse.reshape(-1, 3), process=False)
        counts[owner] = len(mesh.faces)
        if len(mesh.faces) > 2000:
            errors.append(f"{owner}: exported collision exceeds 2000 triangles")
        if not mesh.is_watertight or not mesh.is_winding_consistent or mesh.volume <= 0:
            errors.append(f"{owner}: exported collision shell is not closed and oriented")
    meshes = 0
    for path in sorted(layer.glob("*.vis.glb")):
        js, blob = read_glb(path.read_bytes())
        for node in js["nodes"]:
            if "mesh" not in node:
                continue
            extras = node.get("extras", {})
            for key in ("layer", "object_id", "building_id", "material_key"):
                if key not in extras:
                    errors.append(f"{path.name}: missing {key}")
            for primitive in js["meshes"][node["mesh"]]["primitives"]:
                meshes += 1
                for name in ("POSITION", "NORMAL", "TEXCOORD_0"):
                    if not np.isfinite(
                        accessor_array(js, blob, primitive["attributes"][name])
                    ).all():
                        errors.append(f"{path.name}: nonfinite {name}")
    missing = set(owners) - set(triangles)
    errors.extend(f"{owner}: no exported collision triangles" for owner in sorted(missing))
    return {
        "pass": not errors and len(owners) == metrics["building_count"],
        "errors": errors,
        "collision_tiles": len(paths),
        "seam_pairs": seams,
        "shells": len(triangles),
        "max_clipped_triangles": max(counts.values(), default=0),
        "visual_tiles": len(list(layer.glob("*.vis.glb"))),
        "meshes": meshes,
        "facades": metrics["facade_count"],
        "passing_footprints": sum(r["pass"] for r in metrics["buildings"]),
    }


def main():
    result = audit(C.work_root() / "build/layers/buildings")
    print(json.dumps(result, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
