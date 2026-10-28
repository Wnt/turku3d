"""Structural collision assembly, independent of visual and texture packaging."""

import numpy as np

from turku3d import config as C
from turku3d.col.format import MIN_TRI_AREA
from turku3d.col.materials import mat_id
from turku3d.ground.mesh import Mesh
from turku3d.ground.water import add_river_bed

def solid_triangles(pos, idx, mat):
    """Drop the slivers and duplicates that successive bank, tunnel and kerb cuts can leave (measured on the stored
    tile-local float32 positions); the .col format rejects both, which would fail the whole tile upgrade."""
    idx = np.asarray(idx).reshape(-1, 3)
    mat = np.asarray(mat)
    p = np.asarray(pos, dtype=np.float64).reshape(-1, 3)
    a, b, c = p[idx[:, 0]], p[idx[:, 1]], p[idx[:, 2]]
    keep = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1) >= MIN_TRI_AREA
    _, first = np.unique(np.sort(idx, axis=1), axis=0, return_index=True)
    unique = np.zeros(len(idx), bool)
    unique[first] = True
    keep &= unique
    return idx[keep], mat[keep]


def collision_tiles(
    tile, grid_pos, grid_idx, materials, level, shells, ground_keep=None, shore=None
):
    """Split ground along the 62.5 m grid; carved building parts retain the original owner."""
    cells_per_side = round(np.sqrt(len(grid_pos))) - 1
    per = cells_per_side // C.COLLISION_PER_VISUAL
    out = {}
    for ct in tile.collision_tiles():
        ci, cj = ct.i - tile.i * C.COLLISION_PER_VISUAL, ct.j - tile.j * C.COLLISION_PER_VISUAL
        rows = np.arange(cj * per, (cj + 1) * per)
        cols = np.arange(ci * per, (ci + 1) * per)
        cells = (rows[:, None] * cells_per_side + cols[None, :]).ravel()
        tris = np.stack([2 * cells, 2 * cells + 1], axis=1).ravel()
        if ground_keep is not None:
            tris = tris[ground_keep[tris]]
        mesh = Mesh(grid_pos, grid_idx[tris], materials[tris]).compact()
        if shore is None:
            mesh = add_river_bed(mesh, level)
        else:
            centres = shore.positions[shore.indices].mean(axis=1)
            owners = np.floor(centres[:, [0, 2]] / C.COLLISION_TILE_SIZE).astype(int)
            mesh = Mesh(
                shore.positions,
                shore.indices[(owners[:, 0] == ct.i) & (owners[:, 1] == ct.j)],
                shore.material[(owners[:, 0] == ct.i) & (owners[:, 1] == ct.j)],
            ).compact()
        positions, indices, material = [mesh.positions], [mesh.indices], [mesh.material]
        count = len(mesh.positions)
        for b, (roof, roof_idx, floor, floor_idx, walls, _, wall_idx) in shells:
            c = b.footprint.centroid
            if getattr(b, "_collision_owner", C.CollisionTile.at(c.x, c.y)) != ct:
                continue
            for pos, idx, mid in (
                (roof, roof_idx, mat_id("roof")),
                (floor, floor_idx, mat_id("building")),
                (walls, wall_idx, mat_id("building")),
            ):
                positions.append(pos)
                indices.append(idx + count)
                material.append(np.full(len(idx), mid, np.uint8))
                count += len(pos)
        out[ct] = (np.concatenate(positions), np.concatenate(indices), np.concatenate(material))
    return out
