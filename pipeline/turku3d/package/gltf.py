"""Minimal glTF 2.0 binary (GLB) writer for visual tiles (docs/specs/tiles.md §3.1).

numpy only, no third-party glTF library. One mesh per node, each node gets its own primitive, positions
are tile-local float32, the tile origin goes in the root node translation (doubles in JSON).
Layer source textures are embedded as image/jpeg or image/png buffer views. The assembler
binds library maps, encodes KTX2 and applies meshopt when producing shipped GLBs.

    mesh = GlbMesh("ground_t0_0_0", positions, indices, normals=n, uvs=uv, image=jpeg_bytes,
                   extras={"kind": "ground", "id": "ground_t0_0_0", "tile": "t0_0_0"})
    write_glb([mesh], "t0_0_0.glb", root_translation=(0, 0, 0), root_extras={"tile": "t0_0_0", "objects": [...]})
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

FLOAT, UINT32 = 5126, 5125
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963


@dataclass
class GlbMesh:
    name: str
    positions: np.ndarray  # (N,3) tile-local
    indices: np.ndarray  # (M,3)
    normals: np.ndarray | None = None
    uvs: np.ndarray | None = None
    image: bytes | None = None  # JPEG or PNG bytes (baseColorTexture)
    color: tuple[float, float, float, float] = (0.8, 0.8, 0.8, 1.0)
    roughness: float = 0.95
    repeat: bool = False  # Texture wraps (REPEAT) instead of clamping to its edge
    extras: dict = field(default_factory=dict)


def vertex_normals(pos: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """Area-weighted vertex normals (float32, unit length)."""
    p = pos.astype(np.float64)
    t = idx.astype(np.int64)
    fn = np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]])
    n = np.zeros_like(p)
    for k in range(3):
        np.add.at(n, t[:, k], fn)
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    ln[ln == 0] = 1.0
    return (n / ln).astype(np.float32)


def _mime(img: bytes) -> str:
    if img[:2] == b"\xff\xd8":
        return "image/jpeg"
    if img[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    raise ValueError("image must be JPEG or PNG")


def build_glb(
    meshes: list[GlbMesh],
    *,
    root_translation=(0.0, 0.0, 0.0),
    root_name: str = "tile",
    root_extras: dict | None = None,
    generator: str = "turku3d.package.gltf",
) -> bytes:
    gltf: dict = {
        "asset": {"version": "2.0", "generator": generator},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [],
        "meshes": [],
        "materials": [],
        "accessors": [],
        "bufferViews": [],
        "buffers": [],
    }
    blob = bytearray()

    def view(data: bytes, target: int | None) -> int:
        while len(blob) % 4:
            blob.append(0)
        bv = {"buffer": 0, "byteOffset": len(blob), "byteLength": len(data)}
        if target:
            bv["target"] = target
        blob.extend(data)
        gltf["bufferViews"].append(bv)
        return len(gltf["bufferViews"]) - 1

    def accessor(arr: np.ndarray, ctype: int, typ: str, target: int, minmax: bool = False) -> int:
        acc = {
            "bufferView": view(arr.tobytes(), target),
            "componentType": ctype,
            "count": int(arr.shape[0]),
            "type": typ,
        }
        if minmax:
            acc["min"] = [float(v) for v in arr.min(0)]
            acc["max"] = [float(v) for v in arr.max(0)]
        gltf["accessors"].append(acc)
        return len(gltf["accessors"]) - 1

    root = {"name": root_name, "translation": [float(v) for v in root_translation], "children": []}
    if root_extras:
        root["extras"] = root_extras
    gltf["nodes"].append(root)
    for m in meshes:
        pos = np.ascontiguousarray(m.positions, np.float32).reshape(-1, 3)
        idx = np.ascontiguousarray(m.indices, np.uint32).reshape(-1)
        if idx.size and int(idx.max()) >= len(pos):
            raise ValueError(f"{m.name}: index out of range")
        nrm = m.normals if m.normals is not None else vertex_normals(pos, idx.reshape(-1, 3))
        attrs = {
            "POSITION": accessor(pos, FLOAT, "VEC3", ARRAY_BUFFER, minmax=True),
            "NORMAL": accessor(np.ascontiguousarray(nrm, np.float32), FLOAT, "VEC3", ARRAY_BUFFER),
        }
        pbr: dict = {
            "baseColorFactor": list(m.color),
            "metallicFactor": 0.0,
            "roughnessFactor": m.roughness,
        }
        if m.uvs is not None:
            attrs["TEXCOORD_0"] = accessor(
                np.ascontiguousarray(m.uvs, np.float32), FLOAT, "VEC2", ARRAY_BUFFER
            )
        if m.image is not None:
            if m.uvs is None:
                raise ValueError(f"{m.name}: texture without uvs")
            gltf.setdefault("images", []).append(
                {"bufferView": view(m.image, None), "mimeType": _mime(m.image)}
            )
            wrap = 10497 if m.repeat else 33071
            samplers = gltf.setdefault("samplers", [])
            sampler = {"magFilter": 9729, "minFilter": 9987, "wrapS": wrap, "wrapT": wrap}
            if sampler not in samplers:
                samplers.append(sampler)
            gltf.setdefault("textures", []).append(
                {"sampler": samplers.index(sampler), "source": len(gltf["images"]) - 1}
            )
            pbr["baseColorTexture"] = {"index": len(gltf["textures"]) - 1}
            pbr["baseColorFactor"] = [1.0, 1.0, 1.0, 1.0]
        gltf["materials"].append({"name": f"{m.name}_mat", "pbrMetallicRoughness": pbr})
        prim = {
            "attributes": attrs,
            "indices": accessor(idx, UINT32, "SCALAR", ELEMENT_ARRAY_BUFFER),
            "material": len(gltf["materials"]) - 1,
            "mode": 4,
        }
        gltf["meshes"].append({"name": m.name, "primitives": [prim]})
        node = {"name": m.name, "mesh": len(gltf["meshes"]) - 1}
        if m.extras:
            node["extras"] = m.extras
        gltf["nodes"].append(node)
        root["children"].append(len(gltf["nodes"]) - 1)
    while len(blob) % 4:
        blob.append(0)
    gltf["buffers"].append({"byteLength": len(blob)})
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    out = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(blob))
    out += struct.pack("<II", len(js), 0x4E4F534A) + js
    out += struct.pack("<II", len(blob), 0x004E4942) + bytes(blob)
    return out


def write_glb(meshes: list[GlbMesh], path: str | Path, **kw) -> bytes:
    data = build_glb(meshes, **kw)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(p)
    return data


def read_glb(data: bytes) -> tuple[dict, bytes]:
    """Parse a GLB into (json, bin chunk). For tests."""
    magic, version, length = struct.unpack_from("<III", data, 0)
    if magic != 0x46546C67 or version != 2 or length != len(data):
        raise ValueError("not a GLB v2")
    jl, jt = struct.unpack_from("<II", data, 12)
    js = json.loads(data[20 : 20 + jl])
    off = 20 + jl
    bl, bt = struct.unpack_from("<II", data, off)
    return js, data[off + 8 : off + 8 + bl]


def accessor_array(js: dict, binc: bytes, i: int) -> np.ndarray:
    acc = js["accessors"][i]
    bv = js["bufferViews"][acc["bufferView"]]
    dt = {FLOAT: np.float32, UINT32: np.uint32}[acc["componentType"]]
    ncomp = {"SCALAR": 1, "VEC2": 2, "VEC3": 3}[acc["type"]]
    a = np.frombuffer(binc, dt, acc["count"] * ncomp, bv["byteOffset"])
    return a.reshape(-1, ncomp) if ncomp > 1 else a
