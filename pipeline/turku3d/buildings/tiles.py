"""Store each closed building in its footprint-centroid collision tile."""

from collections import defaultdict

import numpy as np

from turku3d import config as C


def collision_tiles(shells, footprints, provenance=False):
    buckets = defaultdict(list)
    for owner, (shell, footprint) in enumerate(zip(shells, footprints, strict=True)):
        center = footprint.centroid
        tile = C.CollisionTile.at(center.x, center.y)
        buckets[tile.id].append((owner, shell))
    result = {}
    for tid, items in sorted(buckets.items()):
        positions, indices, material, owners = [], [], [], []
        count = 0
        for owner, shell in items:
            positions.append(shell.positions)
            indices.append(shell.indices + count)
            material.append(shell.material)
            owners.append(np.full(len(shell.indices), owner, np.uint32))
            count += len(shell.positions)
        result[tid] = {
            "positions": np.concatenate(positions).astype(np.float64),
            "indices": np.concatenate(indices).astype(np.uint32),
            "material": np.concatenate(material).astype(np.uint8),
        }
        if provenance:
            result[tid]["building_index"] = np.concatenate(owners)
    return result
