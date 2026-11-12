"""Compile mapped context tree placements into the prepared vegetation contract.

These records retain source transforms and stable identities. They use the same
CC0 prototypes and runtime species library as the playable trees, never skyline
cones. Context trees add visual context only, not unsupported collision geometry.
"""
from __future__ import annotations

import copy
import hashlib
import json
import struct
from pathlib import Path

import numpy as np
import shapely

from turku3d.package.gltf import build_glb, read_glb
from turku3d.vegetation.visual import prototypes


def compile_context_trees(records, output, *, occupied_ids, bounds, water):
    meshes, batches, occupied = [], [], set(occupied_ids)
    points, identities = [], []
    for data in records:
        if data.get("version") != 1 or not isinstance(data.get("nodes"), list):
            raise ValueError("Context tree placements are not ready")
        for node in data["nodes"]:
            count = len(node["ids"])
            if any(len(node[key]) != count for key in ("positions", "scales", "rotations", "species")):
                raise ValueError("Context tree metadata and transforms disagree")
            matrix = np.asarray(node["matrix"], float).reshape(4, 4).T
            if not np.isfinite(matrix).all() or not np.allclose(matrix[3], [0, 0, 0, 1]):
                raise ValueError("Invalid context tree parent transform")
            world = np.c_[np.asarray(node["positions"], float).reshape(-1, 3), np.ones(count)] @ matrix.T
            keep = []
            for i, identity in enumerate(node["ids"]):
                x, y, z = world[i, :3]
                if identity in occupied or bounds[0] <= x < bounds[2] and bounds[1] <= z < bounds[3]:
                    continue
                if not np.isfinite([x, y, z, *node["scales"][i], *node["rotations"][i]]).all():
                    raise ValueError("Non-finite context tree transform")
                occupied.add(identity)
                keep.append(i)
                points.append(world[i, :3])
                identities.append(identity)
            if not keep:
                continue
            selected = {key: [node[key][i] for i in keep] for key in ("positions", "scales", "rotations", "ids", "species")}
            selected["matrix"] = node["matrix"]
            for prototype in prototypes(node["variant"]):
                mesh = copy.deepcopy(prototype)
                mesh.extras.update(variant=node["variant"], instance_object_ids=selected["ids"],
                    instance_species=selected["species"],
                    instance_near_water=[shapely.Point(world[i, 0], world[i, 2]).distance(water) < 25 for i in keep],
                    vegetation_instances={"version": 1, "cell_size": 50})
                meshes.append(mesh)
                batches.append(selected)
    if not meshes:
        return None, []
    document, binary = read_glb(build_glb(meshes, root_name="context-vegetation"))
    blob = bytearray(binary)
    for node, batch in zip(document["nodes"][1:], batches, strict=True):
        attrs = {}
        for semantic, key, kind in (("TRANSLATION", "positions", "VEC3"), ("SCALE", "scales", "VEC3"), ("ROTATION", "rotations", "VEC4")):
            values = np.asarray(batch[key], dtype="<f4")
            while len(blob) % 4:
                blob.append(0)
            document["bufferViews"].append(dict(buffer=0, byteOffset=len(blob), byteLength=values.nbytes))
            blob.extend(values.tobytes())
            document["accessors"].append(dict(bufferView=len(document["bufferViews"])-1,
                componentType=5126, count=len(values), type=kind))
            attrs[semantic] = len(document["accessors"])-1
        node["matrix"] = batch["matrix"]
        node["extensions"] = {"EXT_mesh_gpu_instancing": {"attributes": attrs}}
    document["extensionsUsed"] = document["extensionsRequired"] = ["EXT_mesh_gpu_instancing"]
    document["buffers"][0]["byteLength"] = len(blob)
    encoded = json.dumps(document, separators=(",", ":")).encode()
    encoded += b" " * (-len(encoded) % 4)
    raw = struct.pack("<III", 0x46546C67, 2, 28 + len(encoded) + len(blob))
    raw += struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
    raw += struct.pack("<II", len(blob), 0x004E4942) + blob
    sha = hashlib.sha256(raw).hexdigest()
    shared = Path(output) / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / (sha + ".glb")).write_bytes(raw)
    positions = np.asarray(points)
    # Conservative context envelope includes complete crowns, not only bases.
    radius = max(float(max(s)) for batch in batches for s in batch["scales"])
    asset = dict(id="context-vegetation", entityId="context-vegetation", url="shared/" + sha + ".glb",
        sha256=sha, position=[0, 0, 0], recipe="vegetation",
        bounds=dict(min=(positions.min(axis=0)-radius).tolist(), max=(positions.max(axis=0)+radius).tolist()))
    return asset, identities
