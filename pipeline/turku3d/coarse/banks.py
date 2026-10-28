"""Exact water-outline terrain cuts and shared visible/solid river banks."""

import numpy as np
import shapely

from turku3d.col.materials import mat_id
from turku3d.ground.mesh import Mesh

from . import surfaces


def join(*meshes):
    offsets = np.cumsum([0, *[len(m.positions) for m in meshes[:-1]]])
    return Mesh(
        np.concatenate([m.positions for m in meshes]),
        np.concatenate([m.indices + offset for m, offset in zip(meshes, offsets, strict=True)]),
        np.concatenate([m.material for m in meshes]),
    )


def produce(positions, indices, materials, keep, water, level, regions=(), barriers=()):
    """Cut both sides on the same outline, keeping grid and collision-tile constraints.

    Mapped quays/walls retain the surveyed rim and descend vertically. Other banks
    blend over three metres to 40 cm below water. The synthetic bed is collision
    only; the bank faces are identical in the visual and solid meshes.
    """
    empty = Mesh(np.empty((0, 3)), np.empty((0, 3), np.uint32), np.empty(0, np.uint8))
    if not water:
        dry = Mesh(positions, indices[keep], materials[keep]).compact()
        return dry, empty, empty, dry
    body = shapely.union_all(water)
    mapped = [g for g, m in regions if m == mat_id("quay")]
    mapped.extend(
        g
        for g, tags, *_ in barriers
        if tags.get("barrier") in ("wall", "retaining_wall", "city_wall")
        or tags.get("man_made") in ("quay", "embankment")
        or tags.get("embankment") == "yes"
    )
    quay = shapely.union_all(mapped).buffer(2.0)
    cuts = [(body.buffer(3), mat_id("soil")), (body, mat_id("water"))]
    materials = materials.copy()
    centres = positions[indices].mean(axis=1)
    for polygon, material in cuts:
        materials[shapely.contains_xy(polygon, centres[:, 0], centres[:, 2])] = material
    p, ix, ids, _ = surfaces.split_boundaries(positions, indices, materials, keep, cuts)
    wet = ids == mat_id("water")
    if not wet.any():
        dry = Mesh(p, ix, ids).compact()
        return dry, empty, empty, dry
    wet_mesh = Mesh(p, ix[wet], ids[wet]).compact()
    dry = Mesh(p, ix[~wet], ids[~wet]).compact()

    def rim(points):
        distance = shapely.distance(shapely.points(points[:, [0, 2]]), body)
        # A continuous transition at the end of each mapped retaining edge.
        masonry = (
            np.zeros(len(points))
            if quay.is_empty
            else np.clip(1 - shapely.distance(shapely.points(points[:, [0, 2]]), quay), 0, 1)
        )
        blend = np.clip(distance / 3, 0, 1)
        target = (level - 0.4) * (1 - blend) + points[:, 1] * blend
        return np.minimum(points[:, 1], target * (1 - masonry) + points[:, 1] * masonry)

    dry.positions[:, 1] = rim(dry.positions)
    # Cancel internal wet edges by coordinates: boundary cutting duplicates vertices.
    edges = np.concatenate([wet_mesh.indices[:, pair] for pair in ([0, 1], [1, 2], [2, 0])])
    xz = np.round(wet_mesh.positions[:, [0, 2]], 9)
    _, vertex = np.unique(xz, axis=0, return_inverse=True)
    _, first, counts = np.unique(
        np.sort(vertex[edges], axis=1), axis=0, return_index=True, return_counts=True
    )
    edge = edges[first[counts == 1]]
    ends = wet_mesh.positions[edge]
    # Window and tunnel cuts are not shorelines. Test both ends and the midpoint.
    probe = shapely.points(
        np.concatenate([ends[:, 0, [0, 2]], ends[:, 1, [0, 2]], ends.mean(axis=1)[:, [0, 2]]])
    )
    shore = (shapely.distance(probe, body.boundary).reshape(3, -1) < 1e-7).all(axis=0)
    ends = ends[shore]
    top = ends.reshape(-1, 3).copy()
    top[:, 1] = rim(top)
    top = top.reshape(-1, 2, 3)
    bottom = top.copy()
    bottom[:, :, 1] = level - 3
    bank_pos = np.stack([top[:, 0], top[:, 1], bottom[:, 0], bottom[:, 1]], axis=1).reshape(-1, 3)
    # Wet triangles face up; their boundary direction makes the bank face into water.
    bank_ix = (np.arange(len(top))[:, None, None] * 4 + np.array([[0, 1, 2], [1, 3, 2]])).reshape(
        -1, 3
    )
    stone = shapely.distance(shapely.points(ends.mean(axis=1)[:, [0, 2]]), quay) < 1e-7
    bank_ids = np.repeat(np.where(stone, mat_id("quay"), mat_id("soil")), 2).astype(np.uint8)
    bank = Mesh(bank_pos, bank_ix.astype(np.uint32), bank_ids)
    bed = Mesh(
        wet_mesh.positions.copy(),
        wet_mesh.indices,
        np.full(len(wet_mesh.indices), mat_id("soil"), np.uint8),
    )
    bed.positions[:, 1] = level - 3
    wet_mesh.positions[:, 1] = level
    return dry, wet_mesh, bank, join(dry, wet_mesh, bank, bed)


def stage(cache, tile, grid_pos, grid_idx, inputs, keep, level, separate_decks):
    from pathlib import Path

    from turku3d import config as C
    from turku3d.stage_cache import source_key

    centres = grid_pos[grid_idx].mean(axis=1)
    ids = surfaces.classify(centres, inputs.ortho, tile, inputs.streets, inputs.surfaces)
    water = inputs.water
    # Legacy decks stay solid; separate bridge slabs span the exact water cut.
    if not separate_decks and np.any(ids == mat_id("bridge_deck")):
        decks = shapely.union_all(
            shapely.polygons(grid_pos[grid_idx[ids == mat_id("bridge_deck")]][:, :, [0, 2]])
        )
        water = [g.difference(decks) for g in water]
    return cache.run(
        "river_banks",
        source_key(
            Path(__file__),
            surfaces.split_boundaries,
            Path(C.__file__),
            Path(__file__).parents[1] / "col/materials_v1.json",
        ),
        [grid_pos, grid_idx, ids, keep, water, level, inputs.surfaces, inputs.barriers],
        lambda: produce(
            grid_pos, grid_idx, ids, keep, water, level, inputs.surfaces, inputs.barriers
        ),
    )
