"""Distribute structure faces across producer-owned L0 boundaries at assembly time."""

from collections import defaultdict

import numpy as np

from turku3d import config as C
from turku3d.layers import PRIMITIVE_DTYPE, CollisionLayer


def collision_faces(tiles, available):
    """Keep whole triangles in every touched cell, deduplicating the producer's copies.

    Structure producers own only their L0 cells. A clipped survey endpoint can put a
    cheek face exactly on that ownership boundary, so its neighbour needs the same
    face even when that neighbour's producer emitted no structure there. Assembly
    can distribute across owners without changing or writing any layer product.
    """
    faces = {}
    for layer in tiles.values():
        for indices, material in zip(layer.indices, layer.material, strict=True):
            points = layer.positions[indices]
            # Cyclic permutations preserve winding; reversed faces remain distinct.
            start = min(range(3), key=lambda i: tuple(points[i]))
            points = np.roll(points, -start, axis=0)
            faces[(int(material), points.tobytes())] = points
    cells = defaultdict(list)
    for (material, _), points in faces.items():
        xz = points[:, [0, 2]]
        bounds = C.BBoxLocal(*xz.min(axis=0), *xz.max(axis=0)).buffered(1e-6)
        for cell in C.collision_tiles_for_bbox(bounds):
            if cell.id in available:
                cells[cell.id].append((points, material))
    # Primitives already carry their producer's complete-object ownership; do not
    # drop or move them while redistributing mesh faces.
    for cid, layer in tiles.items():
        if len(layer.primitives):
            cells.setdefault(cid, [])
    result = {}
    for cid, triangles in cells.items():
        positions = (
            np.concatenate([p for p, _ in triangles]).astype(np.float64)
            if triangles
            else np.empty((0, 3), dtype=np.float64)
        )
        result[cid] = CollisionLayer(
            positions,
            np.arange(len(positions), dtype=np.uint32).reshape(-1, 3),
            np.array([m for _, m in triangles], dtype=np.uint8),
            tiles[cid].primitives if cid in tiles else np.empty(0, dtype=PRIMITIVE_DTYPE),
        )
    return result


def cell_hash(layer):
    """Effective geometry identity includes faces contributed by neighbouring owners."""
    import hashlib

    h = hashlib.sha256()
    for data in (layer.positions, layer.indices, layer.material, layer.primitives):
        h.update(data.tobytes())
    return h.hexdigest()
