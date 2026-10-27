"""Store each closed building in its footprint-centroid collision tile."""

from collections import defaultdict

import numpy as np
from shapely.geometry import box

from turku3d import config as C


def ownership_point(footprint, visual_tiles=None):
    """Own a cross-boundary building in the requested region it visibly occupies."""
    tiles = visual_tiles or C.OFFICE_BLOCK.visual_tiles
    overlaps = []
    for tile in tiles:
        bounds = tile.bbox
        region = box(bounds.xmin, bounds.zmin, bounds.xmax, bounds.zmax)
        area = footprint.intersection(region).area
        if area > 0:
            overlaps.append((area, tile.id, region))
    if overlaps:
        _, _, region = max(overlaps)
        return footprint.intersection(region).representative_point()
    return footprint.representative_point()


def collision_tiles(shells, footprints, ids=None, visual_tiles=None):
    """Whole shells per footprint-centroid collision tile.

    With `ids`, each tile also lists its own buildings (`building_ids`) and indexes them per
    triangle (`building_index`), so a building added elsewhere leaves the tile's bytes alone."""
    buckets = defaultdict(list)
    for owner, (shell, footprint) in enumerate(zip(shells, footprints, strict=True)):
        center = ownership_point(footprint, visual_tiles)
        tile = C.CollisionTile.at(center.x, center.y)
        buckets[tile.id].append((owner, shell))
    result = {}
    for tid, items in sorted(buckets.items()):
        positions, indices, material, owners = [], [], [], []
        count = 0
        for local, (_, shell) in enumerate(items):
            positions.append(shell.positions)
            indices.append(shell.indices + count)
            material.append(shell.material)
            owners.append(np.full(len(shell.indices), local, np.uint32))
            count += len(shell.positions)
        result[tid] = {
            "positions": np.concatenate(positions).astype(np.float64),
            "indices": np.concatenate(indices).astype(np.uint32),
            "material": np.concatenate(material).astype(np.uint8),
        }
        if ids is not None:
            result[tid]["building_ids"] = np.array([ids[owner] for owner, _ in items], dtype=str)
            result[tid]["building_index"] = np.concatenate(owners)
    return result
