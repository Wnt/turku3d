"""Independent draft stage functions and explicit dependencies for compiler-cache reuse."""

import copy

import numpy as np

from turku3d import config as C
from turku3d.facade_kit import parameters
from turku3d.package.gltf import GlbMesh
from turku3d.tile_layer import identities

from .stored_facades import wall_meshes


def building_visual(tile, shells, ortho, first_pass):
    from .build import jpeg, ortho_uv

    if not shells:
        return []
    origin = np.asarray(tile.origin)
    roof_pos, roof_idx = [], []
    rc = 0
    for _, (roof, ridx, _, _, _walls, _wuv, _widx) in shells:
        roof_pos.append(roof)
        roof_idx.append(ridx + rc)
        rc += len(roof)
    roof_pos = np.concatenate(roof_pos)
    meshes = [
        GlbMesh(
            f"roofs_{tile.id}",
            roof_pos - origin,
            np.concatenate(roof_idx),
            uvs=ortho_uv(tile, roof_pos),
            image=None if first_pass else jpeg(ortho),
            extras=dict(
                layer="buildings",
                object_id=f"roofs_{tile.id}",
                material_key="roof_ortho",
                surface="roof",
                texture_uv="normalized",
            ),
        ),
    ]
    for b, (_, _, _, _, walls, wuv, widx) in shells:
        width = max(float(wuv[:, 0].max()), 1.0)
        params = parameters(b.facade_key, b.top - b.base, width, getattr(b, "attributes", {}))
        meshes.extend(wall_meshes(b, walls, wuv, widx, origin, params))
    return meshes


def props_visual(tile, poles, grid_pos):
    from turku3d.props import visual_bytes

    from .build import grid_height

    poles = copy.deepcopy(poles)
    for pole in poles:
        pole.y = grid_height(tile, grid_pos, pole.x, pole.z)
    return visual_bytes(poles, tile)


def kerb_inputs(tile, sources):
    """The neighbouring ground grids a seam-crossing kerb's height sampler may read."""
    from .sources import NoCoverage

    paths = {}
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            try:
                found, _ = sources.lidar_paths(C.VisualTile(0, tile.i + di, tile.j + dj))
            except NoCoverage:
                continue
            paths.update({p.name: p for p in found})
    return identities(paths)
