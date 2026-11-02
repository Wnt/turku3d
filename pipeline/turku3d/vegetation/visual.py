"""CC0 prototypes, normalized to unit height/radius and GPU-instanced by variant."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import numpy as np

from turku3d.layers import read_visual, write_visual
from turku3d.package.gltf import GlbMesh, read_glb
from turku3d.package.manifest import _atomic_write

ASSETS = Path(__file__).with_name("assets")


def array(js, blob, index):
    a = js["accessors"][index]
    v = js["bufferViews"][a["bufferView"]]
    dtype = np.dtype({5126: "<f4", 5125: "<u4", 5123: "<u2", 5121: "u1"}[a["componentType"]])
    count = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[a["type"]]
    return np.ndarray(
        (a["count"], count),
        dtype=dtype,
        buffer=blob,
        offset=v.get("byteOffset", 0) + a.get("byteOffset", 0),
        strides=(v.get("byteStride", count * dtype.itemsize), dtype.itemsize),
    ).copy()


def prototypes(name):
    js, blob = read_glb((ASSETS / f"{name}.glb").read_bytes())
    primitives = js["meshes"][0]["primitives"]
    all_pos = np.concatenate([array(js, blob, p["attributes"]["POSITION"]) for p in primitives])
    bottom, top = all_pos[:, 1].min(), all_pos[:, 1].max()
    center = (all_pos[:, [0, 2]].min(0) + all_pos[:, [0, 2]].max(0)) / 2
    radius = np.linalg.norm(all_pos[:, [0, 2]] - center, axis=1).max()
    result = []
    for i, p in enumerate(primitives):
        pos = array(js, blob, p["attributes"]["POSITION"])
        pos[:, [0, 2]] = (pos[:, [0, 2]] - center) / radius
        pos[:, 1] = (pos[:, 1] - bottom) / (top - bottom)
        material = js["materials"][p["material"]]["name"]
        leaf = "leaf" in material
        # Linear RGB autumn palette; evergreens retain subdued green needles.
        color = (
            (
                (0.055, 0.18, 0.07, 1)
                if "pine" in name
                else {
                    "tree_default_fall": (0.60, 0.30, 0.045, 1),
                    "tree_detailed_fall": (0.52, 0.18, 0.035, 1),
                    "tree_oak_fall": (0.38, 0.105, 0.025, 1),
                }[name]
            )
            if leaf
            else (0.19, 0.12, 0.075, 1)
        )
        result.append(
            GlbMesh(
                f"{name}:{i}",
                pos,
                array(js, blob, p["indices"]).reshape(-1, 3),
                color=color,
                extras={
                    "layer": "vegetation",
                    "object_id": f"{name}:{i}",
                    "material_key": "tree_foliage" if leaf else "tree_bark",
                },
            )
        )
    return result


def write_instances(path, tile, trees):
    meshes, batches = [], []
    triangles = 0
    for name in sorted({t.variant for t in trees}):
        batch = [t for t in trees if t.variant == name]
        for mesh in prototypes(name):
            mesh.extras.update(
                instance_object_ids=[t.object_id for t in batch],
                instance_species=[t.species for t in batch],
                variant=name,
                vegetation_instances={"version": 1, "cell_size": 50},
            )
            meshes.append(mesh)
            batches.append(batch)
            triangles += len(mesh.indices) * len(batch)
    write_visual(path, meshes, root_translation=tile.origin)
    js, data = read_glb(Path(path).read_bytes())
    blob = bytearray(data)
    for node, batch in zip(js["nodes"][1:], batches, strict=True):

        def append(values, typ):
            a = np.asarray(values, dtype="<f4")
            while len(blob) % 4:
                blob.append(0)
            js["bufferViews"].append({"buffer": 0, "byteOffset": len(blob), "byteLength": a.nbytes})
            blob.extend(a.tobytes())
            js["accessors"].append(
                {
                    "bufferView": len(js["bufferViews"]) - 1,
                    "componentType": 5126,
                    "count": len(a),
                    "type": typ,
                }
            )
            return len(js["accessors"]) - 1

        translations = [[t.x - tile.origin[0], t.y, t.z - tile.origin[2]] for t in batch]
        scales = [[t.crown_radius, t.height, t.crown_radius] for t in batch]
        angles = [
            int(hashlib.sha256(t.object_id.encode()).hexdigest()[:8], 16) / 2**32 * np.pi
            for t in batch
        ]
        attrs = {
            "TRANSLATION": append(translations, "VEC3"),
            "SCALE": append(scales, "VEC3"),
            "ROTATION": append([[0, np.sin(a), 0, np.cos(a)] for a in angles], "VEC4"),
        }
        node["extensions"] = {"EXT_mesh_gpu_instancing": {"attributes": attrs}}
    js["extensionsUsed"] = js["extensionsRequired"] = ["EXT_mesh_gpu_instancing"]
    js["buffers"][0]["byteLength"] = len(blob)
    text = json.dumps(js, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    glb = struct.pack("<III", 0x46546C67, 2, 28 + len(text) + len(blob))
    glb += (
        struct.pack("<II", len(text), 0x4E4F534A)
        + text
        + struct.pack("<II", len(blob), 0x004E4942)
        + blob
    )
    _atomic_write(Path(path), glb)
    read_visual(path)
    return {
        "trees": len(trees),
        "draws_per_color_pass": len(meshes),
        "triangles_per_color_pass": triangles,
        "prototype_triangles": sum(len(m.indices) for m in meshes),
        "bytes": len(glb),
    }
