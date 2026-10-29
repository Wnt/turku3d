"""Structural collision assembly, independent of visual and texture packaging."""

import json

import numpy as np

from turku3d import config as C
from turku3d.col.format import MIN_TRI_AREA
from turku3d.col.materials import mat_id
from turku3d.ground.mesh import Mesh
from turku3d.ground.water import add_river_bed


def current(builder, tile):
    """Only current, atomically published visual/collision pairs are usable."""
    codes = {}
    for name in ("collision.json", "manifest.json"):
        try:
            value = json.loads((builder.root / tile / name).read_text())
            field = "collision_code" if "collision_code" in value else "code"
            if field not in codes:
                codes[field] = getattr(builder, field)()
            if value.get(field) != codes[field]:
                continue
            key_for = (
                (getattr(builder, "structural_input_key", None) or builder.input_key)
                if value.get("pass_kind") == "first"
                else builder.input_key
            )
            if value.get("pass_kind") != "first" and "building_keys" in value:
                from turku3d.facade_kit import parameter_hash

                if value.get("facade_params") != parameter_hash(value["building_keys"]):
                    continue
            key = key_for(tile)
            if key is not None and value.get("inputs") != key:
                continue
            cells = {c.id for c in C.VisualTile.parse(tile).collision_tiles()}
            if (
                value.get("tile") == tile
                and set(value.get("collision", {})) == cells
                and value.get("visual", {}).get(tile)
            ):
                return value
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def fragment(builder, tile):
    value = current(builder, tile)
    return (
        None
        if value is None
        else {
            k: value[k]
            for k in ("coarse", "tile", "collision", "visual", "pass_kind", "roads")
            if k in value
        }
    )


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


def compact_collision(col):
    """Weld exact stored position bits, then remove invalid faces and unused vertices.

    Clipped terrain and structure faces carry independent vertex arrays. Encoding
    that visual-style soup wastes collision bytes and Rapier memory, especially
    on stair railings. Work after tile-local float32 conversion so every retained
    triangle corner and seam keeps its exact bits; never round or simplify it.
    """
    corners = col.positions[col.indices].reshape(-1, 3)
    bits = np.ascontiguousarray(corners).view(np.uint32)
    # Keep coincident faces of different materials/classes independent, including
    # water sensors over solid surfaces. Only identical material faces may merge.
    keys = np.column_stack([bits, np.repeat(col.materials, 3)])
    _, first, inverse = np.unique(keys, axis=0, return_index=True, return_inverse=True)
    # Keep first-use order for the lossless meshopt encoder.
    order = np.argsort(first)
    rank = np.empty(len(order), np.uint32)
    rank[order] = np.arange(len(order), dtype=np.uint32)
    positions = corners[first[order]]
    indices = rank[inverse].reshape(-1, 3)
    indices, materials = solid_triangles(positions, indices, col.materials)
    used, remapped = np.unique(indices, return_inverse=True)
    col.positions = positions[used]
    col.indices = remapped.reshape(-1, 3).astype(np.uint32)
    col.materials = materials


def collision_tiles(
    tile,
    grid_pos,
    grid_idx,
    materials,
    level,
    shells,
    ground_keep=None,
    shore=None,
    *,
    facade_components=False,
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
            if facade_components:
                from turku3d.facade_component_geometry import collision_walls

                walls, wall_idx = collision_walls(b, walls)
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
