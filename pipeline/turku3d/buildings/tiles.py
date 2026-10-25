"""Split triangles once on the global collision grid; reuse identical cut coordinates."""

from collections import defaultdict

import numpy as np

from turku3d import config as C


def split_polygon(poly, axis, value, cuts):
    lo, hi = [], []
    for a, b in zip(poly, np.roll(poly, -1, axis=0), strict=True):
        da, db = a[axis] - value, b[axis] - value
        if da <= 0:
            lo.append(a)
        if da >= 0:
            hi.append(a)
        if da * db < 0:
            aa, bb = sorted((tuple(a), tuple(b)))
            key = (aa, bb, axis, value)
            if key not in cuts:
                aa, bb = np.array(aa), np.array(bb)
                q = aa + (bb - aa) * ((value - aa[axis]) / (bb[axis] - aa[axis]))
                q[axis] = value
                cuts[key] = np.round(q, 9)
            lo.append(cuts[key])
            hi.append(cuts[key])
    return [np.asarray(p) for p in (lo, hi) if len(p) >= 3]


def collision_tiles(shells, provenance=False):
    buckets, cuts = defaultdict(list), {}
    for owner, shell in enumerate(shells):
        for tri, material in zip(shell.positions[shell.indices], shell.material, strict=True):
            pieces = [tri]
            for axis in (0, 2):
                start = int(np.floor(tri[:, axis].min() / C.COLLISION_TILE_SIZE)) + 1
                end = int(np.ceil(tri[:, axis].max() / C.COLLISION_TILE_SIZE))
                for k in range(start, end):
                    value = k * C.COLLISION_TILE_SIZE
                    pieces = [q for p in pieces for q in split_polygon(p, axis, value, cuts)]
            for p in pieces:
                # Every piece is convex because it is a clipped triangle.
                center = p.mean(axis=0)
                tile = C.CollisionTile.at(center[0], center[2])
                for k in range(1, len(p) - 1):
                    t = p[[0, k, k + 1]]
                    if np.linalg.norm(np.cross(t[1] - t[0], t[2] - t[0])) > 1e-10:
                        buckets[tile.id].append((t, material, owner))
    result = {}
    for tid, items in sorted(buckets.items()):
        p, inverse = np.unique(
            np.array([t for t, _, _ in items]).reshape(-1, 3), axis=0, return_inverse=True
        )
        result[tid] = {
            "positions": p.astype(np.float64),
            "indices": inverse.reshape(-1, 3).astype(np.uint32),
            "material": np.array([m for _, m, _ in items], np.uint8),
        }
        if provenance:
            result[tid]["building_index"] = np.array([owner for _, _, owner in items], np.uint32)
    return result
