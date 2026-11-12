"""Packs mesh parts into one self-contained GLB (docs/design/world-pipeline.md, section 3.3)."""

from __future__ import annotations

import json
import struct

import numpy as np

from .api import Material, Part

FLOAT, UINT = 5126, 5125
ARRAY, ELEMENTS = 34962, 34963


def flat_normals(positions: np.ndarray, triangles: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Unwelds the triangles so that each has its own face normal: (positions, normals, source vertex index)."""
    index = triangles.reshape(-1)
    corners = positions[index].reshape(-1, 3, 3)
    normal = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    length = np.linalg.norm(normal, axis=1, keepdims=True)
    normal = np.divide(normal, length, out=np.zeros_like(normal), where=length > 0)
    return corners.reshape(-1, 3), np.repeat(normal, 3, axis=0), index


def material_json(material: Material) -> dict:
    out = {
        "name": material.family,
        "pbrMetallicRoughness": {
            "baseColorFactor": [*(round(float(c), 4) for c in material.color), 1.0],
            "roughnessFactor": round(float(material.roughness), 4),
            "metallicFactor": round(float(material.metalness), 4),
        },
        "extras": {"turku": {"family": material.family, "params": material.params}},
    }
    if material.double_sided:
        out["doubleSided"] = True
    return out


def pack(parts: list[Part], entity: str) -> tuple[bytes, dict]:
    """GLB bytes and {triangles, vertices, families} for parts that share one origin. One node per part name, one
    primitive per part."""
    blob = bytearray()
    views: list[dict] = []
    accessors: list[dict] = []
    materials: list[dict] = []
    material_index: dict[str, int] = {}
    meshes: dict[str, list[dict]] = {}
    triangles = vertices = 0

    def accessor(array: np.ndarray, component: int, kind: str, target: int, bounds=False) -> int:
        data = np.ascontiguousarray(array, dtype="<f4" if component == FLOAT else "<u4")
        while len(blob) % 4:
            blob.append(0)
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": data.nbytes, "target": target})
        blob.extend(data.tobytes())
        entry = {"bufferView": len(views) - 1, "componentType": component, "count": int(data.shape[0]), "type": kind}
        if bounds:
            entry["min"], entry["max"] = data.min(axis=0).tolist(), data.max(axis=0).tolist()
        accessors.append(entry)
        return len(accessors) - 1

    for part in parts:
        positions = np.asarray(part.positions, dtype=np.float64).reshape(-1, 3)
        faces = np.asarray(part.triangles, dtype=np.int64).reshape(-1, 3)
        if not len(faces):
            continue
        normals, uvs, colors, extra = part.normals, part.uvs, part.colors, dict(part.attributes)
        if normals is None:
            positions, normals, source = flat_normals(positions, faces)
            faces = np.arange(len(positions)).reshape(-1, 3)
            uvs = None if uvs is None else np.asarray(uvs)[source]
            colors = None if colors is None else np.asarray(colors)[source]
            extra = {name: np.asarray(values)[source] for name, values in extra.items()}
        key = json.dumps(material_json(part.material), sort_keys=True)
        if key not in material_index:
            material_index[key] = len(materials)
            materials.append(json.loads(key))
        attributes = {"POSITION": accessor(positions, FLOAT, "VEC3", ARRAY, bounds=True),
                      "NORMAL": accessor(normals, FLOAT, "VEC3", ARRAY)}
        if uvs is not None:
            attributes["TEXCOORD_0"] = accessor(np.asarray(uvs).reshape(-1, 2), FLOAT, "VEC2", ARRAY)
        if colors is not None:
            colors = np.asarray(colors)
            attributes["COLOR_0"] = accessor(colors, FLOAT, "VEC3" if colors.shape[1] == 3 else "VEC4", ARRAY)
        for name, values in extra.items():
            values = np.asarray(values)
            width = 1 if values.ndim == 1 else values.shape[1]
            attributes[f"_{name.upper()}"] = accessor(values.reshape(-1, width) if width > 1 else values.reshape(-1),
                                                      FLOAT, ("SCALAR", "VEC2", "VEC3", "VEC4")[width - 1], ARRAY)
        primitive = {"attributes": attributes, "indices": accessor(faces.reshape(-1), UINT, "SCALAR", ELEMENTS),
                     "material": material_index[key], "mode": 4}
        meshes.setdefault(part.name, []).append(primitive)
        triangles += len(faces)
        vertices += len(positions)

    gltf = {
        "asset": {"version": "2.0", "generator": "turku3d.world"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(meshes)))}],
        "nodes": [{"name": name, "mesh": i, "extras": {"entity": entity, "part": name}} for i, name in enumerate(meshes)],
        "meshes": [{"name": name, "primitives": primitives} for name, primitives in meshes.items()],
        "materials": materials,
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(blob)}],
    }
    text = json.dumps(gltf, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    while len(blob) % 4:
        blob.append(0)
    total = 12 + 8 + len(text) + 8 + len(blob)
    out = struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(text), b"JSON") + text
    out += struct.pack("<I4s", len(blob), b"BIN\0") + bytes(blob)
    families = sorted({m["extras"]["turku"]["family"] for m in materials})
    return out, {"triangles": triangles, "vertices": vertices, "families": families}
