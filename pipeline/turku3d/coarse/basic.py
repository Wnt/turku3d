"""Cheap visible geometry paired with structural collision, without texture work."""

import numpy as np
import hashlib
import json
import time

from turku3d.col.materials import table
from turku3d.package.gltf import GlbMesh, build_glb

# sRGB colours distinguish navigable ground, building shells and water immediately.
COLOURS = {
    "road": (89, 91, 94),
    "sidewalk": (171, 167, 156),
    "paving_slab": (164, 159, 147),
    "gravel": (152, 140, 117),
    "soil": (117, 107, 80),
    "grass": (97, 126, 71),
    "water": (66, 116, 137),
    "building": (192, 177, 153),
    "roof": (118, 107, 98),
    "wall": (153, 151, 143),
    "railing": (107, 112, 112),
    "bridge_deck": (119, 119, 113),
    "quay": (151, 145, 132),
    "curb": (183, 179, 165),
    "stair": (171, 166, 151),
}


def visual(tile, cells):
    """Draw precisely the collision triangles, including roofs, tunnels and water surfaces."""
    positions, indices, materials = [], [], []
    count = 0
    for cell, col in cells:
        positions.append(col.positions.astype(np.float64) + np.asarray(cell.origin) - tile.origin)
        indices.append(col.indices + count)
        materials.append(col.materials)
        count += len(col.positions)
    pos, idx, ids = np.concatenate(positions), np.concatenate(indices), np.concatenate(materials)
    names = {m.id: m.name for m in table()}
    meshes = []
    for material in np.unique(ids):
        selected = idx[ids == material]
        used, inverse = np.unique(selected, return_inverse=True)
        name = names[int(material)]
        rgb = np.asarray(COLOURS.get(name, (164, 160, 145))) / 255
        linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
        meshes.append(
            GlbMesh(
                f"basic_{name}",
                pos[used],
                inverse.reshape(-1, 3),
                color=(*map(float, linear), 1.0),
                extras={"layer": "basic", "surface": name},
            )
        )
    data = build_glb(
        meshes, root_translation=tile.origin, root_extras={"basic": True, "tile": tile.id}
    )
    return data, len(idx), [float(pos[:, 1].min()), float(pos[:, 1].max())]


def publish(tile, out, cells, collision, roads, code, input_key, buildings, seconds, started):
    from .build import _entry, _write
    from .collision_key import code_hash

    t = time.perf_counter()
    data, triangles, height = visual(tile, cells)
    url = f"vis.{hashlib.sha256(data).hexdigest()}.glb"
    _write(out / url, data)
    roads_entry = None
    if roads is not None:
        road_url = f"roads.{hashlib.sha256(roads['json']).hexdigest()}.json"
        _write(out / road_url, roads["json"])
        roads_entry = _entry(road_url, roads["json"], nodes=roads["nodes"], edges=roads["edges"])
    identity = code_hash()
    seconds["basic_visual"] = time.perf_counter() - t
    seconds["drivable_ready"] = time.perf_counter() - started
    record = dict(
        coarse=True,
        tile=tile.id,
        pass_kind="first",
        code=code,
        collision_code=identity,
        inputs=input_key,
        buildings=buildings,
        collision=collision,
        roads=roads_entry,
        visual={tile.id: _entry(url, data, level=0, tris=triangles, h=height, coarse=True)},
        seconds={**seconds, "total": seconds["drivable_ready"]},
    )
    # The only public pointer moves after every immutable payload exists.
    _write(out / "collision.json", json.dumps(record).encode())
    return record
