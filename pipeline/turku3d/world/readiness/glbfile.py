"""Reads a GLB back into primitives: the check measures what the store holds, not what a generator meant."""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

DTYPES = {5120: "i1", 5121: "u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4"}
WIDTHS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


@dataclass
class Surface:
    """What a glTF material says about its surface, in the terms of the readiness rules."""

    family: str
    params: dict
    surface: str  # opaque | masked | blend | decal
    double_sided: bool
    color: tuple[float, float, float]
    roughness: float
    metalness: float
    emissive: tuple[float, float, float]
    key: str  # identity of the material row: family and parameters


@dataclass
class Primitive:
    part: str
    positions: np.ndarray  # (n, 3) float64, in the file's frame (relative to the entity origin)
    triangles: np.ndarray  # (m, 3) int64
    material: Surface
    normals: np.ndarray | None = None
    uvs: np.ndarray | None = None
    attributes: list[str] = field(default_factory=list)


def surface_of(material: dict | None) -> Surface:
    material = material or {}
    pbr = material.get("pbrMetallicRoughness", {})
    turku = (material.get("extras") or {}).get("turku") or {}
    family = turku.get("family") or material.get("name") or "unnamed"
    params = turku.get("params") or {}
    alpha = {"MASK": "masked", "BLEND": "blend"}.get(material.get("alphaMode", "OPAQUE"), "opaque")
    surface = turku.get("surface") or params.get("surface") or alpha
    color = tuple(float(c) for c in pbr.get("baseColorFactor", [1, 1, 1, 1])[:3])
    return Surface(
        family=family,
        params=params,
        surface=surface,
        double_sided=bool(material.get("doubleSided", False)),
        color=color,
        roughness=float(pbr.get("roughnessFactor", 1.0)),
        metalness=float(pbr.get("metallicFactor", 1.0)),
        emissive=tuple(float(c) for c in material.get("emissiveFactor", [0, 0, 0])),
        key=json.dumps([family, params], sort_keys=True),
    )


def node_matrix(node: dict) -> np.ndarray:
    if "matrix" in node:
        return np.array(node["matrix"], dtype=float).reshape(4, 4).T
    out = np.eye(4)
    if "scale" in node:
        out = np.diag([*node["scale"], 1.0])
    if "rotation" in node:
        x, y, z, w = node["rotation"]
        rotation = np.eye(4)
        rotation[:3, :3] = [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
        out = rotation @ out
    if "translation" in node:
        translation = np.eye(4)
        translation[:3, 3] = node["translation"]
        out = translation @ out
    return out


def read(path: Path | str) -> list[Primitive]:
    data = Path(path).read_bytes()
    magic, _, _ = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF":
        raise ValueError(f"{path}: not a GLB")
    length, _ = struct.unpack_from("<I4s", data, 12)
    gltf = json.loads(data[20 : 20 + length])
    offset = 20 + length
    binary = b""
    if offset + 8 <= len(data):
        size, _ = struct.unpack_from("<I4s", data, offset)
        binary = data[offset + 8 : offset + 8 + size]
    views, accessors = gltf.get("bufferViews", []), gltf.get("accessors", [])

    def accessor(index: int) -> np.ndarray:
        entry = accessors[index]
        view = views[entry["bufferView"]]
        dtype = np.dtype(DTYPES[entry["componentType"]])
        width = WIDTHS[entry["type"]]
        start = view.get("byteOffset", 0) + entry.get("byteOffset", 0)
        stride = view.get("byteStride") or dtype.itemsize * width
        count = entry["count"]
        if stride == dtype.itemsize * width:
            array = np.frombuffer(binary, dtype=dtype, count=count * width, offset=start).reshape(
                count, width
            )
        else:
            raw = np.frombuffer(
                binary,
                dtype=np.uint8,
                count=(count - 1) * stride + dtype.itemsize * width,
                offset=start,
            )
            rows = np.lib.stride_tricks.as_strided(
                raw, shape=(count, dtype.itemsize * width), strides=(stride, 1)
            )
            array = np.ascontiguousarray(rows).view(dtype).reshape(count, width)
        return array

    out: list[Primitive] = []
    nodes = gltf.get("nodes", [])
    scenes = gltf.get("scenes") or [{"nodes": list(range(len(nodes)))}]
    stack = [(index, np.eye(4)) for index in scenes[gltf.get("scene", 0)].get("nodes", [])]
    while stack:
        index, parent = stack.pop()
        node = nodes[index]
        world = parent @ node_matrix(node)
        stack += [(child, world) for child in node.get("children", [])]
        if "mesh" not in node:
            continue
        mesh = gltf["meshes"][node["mesh"]]
        part = (
            (node.get("extras") or {}).get("part")
            or node.get("name")
            or mesh.get("name")
            or f"mesh{node['mesh']}"
        )
        identity = np.allclose(world, np.eye(4))
        for primitive in mesh["primitives"]:
            if primitive.get("mode", 4) != 4:
                continue
            attributes = primitive["attributes"]
            positions = accessor(attributes["POSITION"]).astype(np.float64)
            normals = (
                accessor(attributes["NORMAL"]).astype(np.float64)
                if "NORMAL" in attributes
                else None
            )
            if not identity:
                positions = positions @ world[:3, :3].T + world[:3, 3]
                if normals is not None:
                    normals = normals @ np.linalg.inv(world[:3, :3])
            if "indices" in primitive:
                triangles = accessor(primitive["indices"]).astype(np.int64).reshape(-1, 3)
            else:
                triangles = np.arange(len(positions), dtype=np.int64).reshape(-1, 3)
            material = gltf["materials"][primitive["material"]] if "material" in primitive else None
            out.append(
                Primitive(
                    part=part,
                    positions=positions,
                    triangles=triangles,
                    material=surface_of(material),
                    normals=normals,
                    uvs=accessor(attributes["TEXCOORD_0"]).astype(np.float64)
                    if "TEXCOORD_0" in attributes
                    else None,
                    attributes=sorted(attributes),
                )
            )
    return out
