"""Validate every shared collision edge after decoding the shipped .col files."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from turku3d import config as C
from turku3d.col.format import read_col


def verify_seams(root: Path) -> list[str]:
    manifest = json.loads((root / "manifest.json").read_text())
    tiles = {cid: read_col(root / e["url"]) for cid, e in manifest["collision"].items()}
    bad = []
    for cid, tile in tiles.items():
        a = tile.world_positions()
        for dx, dz, axis in ((1, 0, 0), (0, 1, 2)):
            other = C.CollisionTile(tile.tile_i + dx, tile.tile_j + dz)
            if other.id not in tiles:
                continue
            b = tiles[other.id].world_positions()
            coordinate = other.origin[axis]
            ae, be = a[a[:, axis] == coordinate], b[b[:, axis] == coordinate]
            ae = ae[np.lexsort(ae.T)]
            be = be[np.lexsort(be.T)]
            if not len(ae) or ae.shape != be.shape or ae.tobytes() != be.tobytes():
                bad.append(f"seam mismatch: {cid}/{other.id}")
    return bad
