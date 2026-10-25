"""Validate every shared collision edge after decoding the shipped .col files."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from turku3d import config as C
from turku3d.col.format import read_col


def edge_equal(a, b, axis, coordinate):
    # Indexed objects may repeat a boundary vertex; compare coordinate sets, not multiplicities.
    def edge(p):
        p = np.unique(p[p[:, axis] == coordinate], axis=0)
        return p[np.lexsort(p.T)].tobytes()
    return edge(a) == edge(b)


def continuous_positions(tile):
    positions = tile.world_positions()
    if tile.meta is None or 'continuous_vertices' not in tile.meta:
        return positions  # Legacy tiles contained only ground.
    ranges = tile.meta['continuous_vertices']
    return np.concatenate([positions[start:start + count] for start, count in ranges]) if ranges else positions[:0]


def verify_seams(root: Path) -> list[str]:
    manifest = json.loads((root / 'manifest.json').read_text())
    tiles = {cid: read_col(root / e['url']) for cid, e in manifest['collision'].items()}
    return verify_tile_seams(tiles)


def verify_tile_seams(tiles) -> list[str]:
    bad = []
    for cid, tile in tiles.items():
        a = continuous_positions(tile)
        for dx, dz, axis in ((1, 0, 0), (0, 1, 2)):
            other = C.CollisionTile(tile.tile_i + dx, tile.tile_j + dz)
            if other.id not in tiles:
                continue
            b = continuous_positions(tiles[other.id])
            coordinate = other.origin[axis]
            if not edge_equal(a, b, axis, coordinate):
                bad.append(f'seam mismatch: {cid}/{other.id}')
    return bad
