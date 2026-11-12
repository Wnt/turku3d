"""A minimal GLB writer for the workbench, following the world-pipeline mesh contract: one node per part name, one
primitive per material, `material.extras.turku = {family, params}` and a plain PBR fallback. The location driver
packs entities with the shared packer; this one serves the one-building preview.
"""

import json
import struct

import numpy as np

FLOAT, UINT32 = 5126, 5125
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963


def write(output, path, entity: str = "building/preview") -> dict:
    chunks, views, accessors, materials, meshes, nodes = [], [], [], [], {}, []
    material_index = {}
    offset = 0

    def push(array: np.ndarray, target: int, kind: str, bounds: bool = False) -> int:
        nonlocal offset
        data = np.ascontiguousarray(array).tobytes()
        pad = (-len(data)) % 4
        chunks.append(data + b"\x00" * pad)
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(data), "target": target})
        offset += len(data) + pad
        accessor = {
            "bufferView": len(views) - 1,
            "componentType": UINT32 if array.dtype == np.uint32 else FLOAT,
            "count": int(array.shape[0]) if kind != "SCALAR" else int(array.size),
            "type": kind,
        }
        if bounds:
            accessor["min"] = array.min(axis=0).tolist()
            accessor["max"] = array.max(axis=0).tolist()
        accessors.append(accessor)
        return len(accessors) - 1

    triangles = 0
    for part in output.parts:
        key = json.dumps([part.material.family, part.material.params], sort_keys=True)
        if key not in material_index:
            material_index[key] = len(materials)
            r, g, b = part.material.color
            materials.append(
                {
                    "name": part.material.family,
                    "pbrMetallicRoughness": {
                        "baseColorFactor": [r, g, b, 1.0],
                        "roughnessFactor": part.material.roughness,
                        "metallicFactor": part.material.metalness,
                    },
                    "extras": {
                        "turku": {"family": part.material.family, "params": part.material.params}
                    },
                }
            )
        attributes = {
            "POSITION": push(part.positions.astype(np.float32), ARRAY_BUFFER, "VEC3", bounds=True),
            "NORMAL": push(part.normals.astype(np.float32), ARRAY_BUFFER, "VEC3"),
        }
        if part.uvs is not None:
            attributes["TEXCOORD_0"] = push(part.uvs.astype(np.float32), ARRAY_BUFFER, "VEC2")
        for name, values in part.attributes.items():
            attributes[f"_{name}"] = push(
                values.astype(np.float32), ARRAY_BUFFER, f"VEC{values.shape[1]}"
            )
        indices = push(part.triangles.astype(np.uint32).reshape(-1), ELEMENT_ARRAY_BUFFER, "SCALAR")
        triangles += len(part.triangles)
        meshes.setdefault(part.name, []).append(
            {"attributes": attributes, "indices": indices, "material": material_index[key]}
        )

    gltf_meshes = []
    for name, primitives in meshes.items():
        gltf_meshes.append({"name": name, "primitives": primitives})
        nodes.append(
            {"name": name, "mesh": len(gltf_meshes) - 1, "extras": {"entity": entity, "part": name}}
        )
    binary = b"".join(chunks)
    gltf = {
        "asset": {"version": "2.0", "generator": "turku3d.world.kinds.building"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": gltf_meshes,
        "materials": materials,
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(binary)}],
    }
    text = json.dumps(gltf, separators=(",", ":")).encode()
    text += b" " * ((-len(text)) % 4)
    with open(path, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(text) + 8 + len(binary)))
        f.write(struct.pack("<I4s", len(text), b"JSON"))
        f.write(text)
        f.write(struct.pack("<I4s", len(binary), b"BIN\x00"))
        f.write(binary)
    return {
        "triangles": triangles,
        "bytes": 12 + 8 + len(text) + 8 + len(binary),
        "materials": len(materials),
        "parts": len(nodes),
    }
