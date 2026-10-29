"""Cheap visible geometry paired with structural collision, without texture work."""

import hashlib
import json
import time

import numpy as np

from turku3d.col.materials import mat_id, table
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


def visual(tile, cells, shells=(), wall_context=(), *, root=None, cache=None):
    """Reuse structural surfaces, assembling building walls once for both passes."""
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
        if shells and material in (mat_id("building"), mat_id("roof")):
            continue
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
    from turku3d.wall_surfaces import validate_meshes

    from .stored_facades import assemble_walls

    building_meshes = []
    for (b, shell), walls in zip(shells, assemble_walls(shells, wall_context), strict=True):
        roof, ridx, floor, fidx = shell[:4]
        widx = (
            np.array([[0, 3, 2], [0, 2, 1]])[None, :, :]
            + np.arange(len(walls) // 4)[:, None, None] * 4
        ).reshape(-1, 3)
        for surface, vertices, triangles in (
            ("roof", roof, ridx),
            ("building", walls, widx),
            ("building", floor, fidx),
        ):
            if not len(triangles):
                continue
            rgb = np.asarray(COLOURS[surface]) / 255
            linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
            building_meshes.append(
                GlbMesh(
                    f"basic_{b.key}_{surface}",
                    vertices - tile.origin,
                    triangles,
                    color=(*map(float, linear), 1.0),
                    extras=dict(layer="buildings", surface=surface, building_id=b.facade_key),
                )
            )
    from turku3d.wall_assembly import assemble_structures

    raw = building_meshes + meshes
    def assemble():
        result = assemble_structures(raw)
        validate_meshes(result, tile.id)
        return result
    if cache is None:
        meshes = assemble()
    else:
        from pathlib import Path
        from turku3d.stage_cache import source_key
        from turku3d import wall_assembly, wall_surfaces

        meshes = cache.run("basic_walls", source_key(Path(wall_assembly.__file__),
                           Path(wall_surfaces.__file__)), raw, assemble)
    if root is not None:
        from turku3d.wall_surfaces import validate_neighbours

        from contextlib import nullcontext
        with cache.measure("basic_neighbours") if cache else nullcontext():
            validate_neighbours(meshes, shells, tile, root, pass_kind="first")
    data = build_glb(
        meshes, root_translation=tile.origin, root_extras={"basic": True, "tile": tile.id}
    )
    return (
        data,
        sum(len(m.indices) for m in meshes),
        [float(pos[:, 1].min()), float(pos[:, 1].max())],
    )


def publish(
    tile,
    out,
    cells,
    collision,
    roads,
    code,
    input_key,
    buildings,
    seconds,
    started,
    shells=(),
    wall_context=(),
    cache=None,
):
    from .build import _entry, _write
    from .collision_key import code_hash

    t = time.perf_counter()
    data, triangles, height = visual(tile, cells, shells, wall_context, root=out.parent, cache=cache)
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
