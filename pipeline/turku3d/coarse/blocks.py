"""City blocks of nearby whole buildings: they batch, cull and swap detail together (GTA-style LOD parents).

A block joins buildings whose footprints lie within CONNECT metres (courtyards, alleys, party walls) and splits
blocks wider than MAX_EXTENT on a SPLIT grid of building centroids, so frustum and occlusion culling skip real
blocks. Each block's LOD parent is its buildings' LoD2 shells (roofs and walls, no floors), 7-19x fewer triangles
than the HD models, coloured with each building's own facade-program wall colour. Walls of buildings whose facade
program has windows are marked glazed so the client can light the same procedural windows the distant city uses
at night; blank walls and roofs never get invented windows. The runtime shows the HD children within
`hdDistance` of the block and the LOD parent beyond it.
"""
from __future__ import annotations

import hashlib
import math

import numpy as np
import shapely

from turku3d.package.gltf import GlbMesh

CONNECT = 15.0
MAX_EXTENT = 96.0
SPLIT = 64.0
# A 1.2 m window spans about 4 px at 1080p / 60 degrees at this distance (measured GTA HD band 112-299 m).
HD_DISTANCE = 280.0
ROOF = (0.20, 0.23, 0.22)
WALL = (0.60, 0.58, 0.54)


def assign(tile, models):
    """Block id per model (same order). `models` are archetypes.partition's (key, origin, meshes, parts)."""
    footprints = [shapely.union_all([b.footprint for b, _ in parts]) for _, _, _, parts in models]
    if not footprints:
        return []
    parent = list(range(len(footprints)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    tree = shapely.STRtree(footprints)
    for i, footprint in enumerate(footprints):
        for j in tree.query(footprint.buffer(CONNECT)):
            if j != i and footprint.distance(footprints[j]) <= CONNECT:
                parent[find(i)] = find(int(j))
    groups = {}
    for i in range(len(footprints)):
        groups.setdefault(find(i), []).append(i)
    blocks = [None] * len(footprints)
    for members in groups.values():
        lo = np.min([footprints[i].bounds[:2] for i in members], axis=0)
        hi = np.max([footprints[i].bounds[2:] for i in members], axis=0)
        split = {}
        for i in members:
            c = footprints[i].centroid
            cell = ((math.floor((c.x - tile.origin[0]) / SPLIT), math.floor((c.y - tile.origin[2]) / SPLIT))
                    if max(hi - lo) > MAX_EXTENT else (0, 0))
            split.setdefault(cell, []).append(i)
        for part in split.values():
            identity = "b" + hashlib.sha256("\n".join(sorted(models[i][0] for i in part)).encode()).hexdigest()[:16]
            for i in part:
                blocks[i] = identity
    return blocks


def _linear(colour, fallback):
    if not (isinstance(colour, str) and len(colour) == 7 and colour.startswith("#")):
        return fallback
    srgb = np.array([int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)])
    return tuple(np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4))


def _appearance(meshes):
    """(linear wall colour, glazed) from the HD model's own facade program."""
    for mesh in meshes:
        kit = mesh.extras.get("facade_kit")
        walls = mesh.extras.get("facade_kit_walls") or ([kit] if isinstance(kit, dict) else [])
        for program in walls:
            if isinstance(program, dict) and program.get("wall"):
                scope = program.get("glazing_scope")
                return _linear(program["wall"], WALL), scope not in (None, "none", "")
    return WALL, False


def lod_meshes(identity, members, origin):
    """LoD2 shell surfaces of a block in its local frame. `members` are (meshes, parts) of each building."""
    # One draw per block: roofs ride with their building's walls (the client's window term vanishes on
    # horizontal faces); only windowless buildings need a second, plain surface set.
    groups = {"walls-glazed": ([], [], []), "walls": ([], [], [])}
    for meshes, parts in members:
        wall, glazed = _appearance(meshes)
        name = "walls-glazed" if glazed else "walls"
        for _, shell in parts:
            if not isinstance(shell, tuple) or len(shell) != 7:
                continue
            for points, faces, colour in ((shell[4], shell[6], wall), (shell[0], shell[1], ROOF)):
                if points is None or faces is None:
                    continue
                points, faces = np.asarray(points, float), np.asarray(faces, np.int64)
                if not len(faces) or not np.isfinite(points).all():
                    continue
                positions, indices, colours = groups[name]
                offset = sum(len(p) for p in positions)
                positions.append(points - origin)
                indices.append(faces + offset)
                colours.append(np.tile(colour, (len(points), 1)))
    result = []
    for name, (positions, indices, colours) in groups.items():
        if not positions:
            continue
        result.append(GlbMesh(f"{identity}:{name}", np.concatenate(positions), np.concatenate(indices).astype(np.uint32),
                              colors=np.concatenate(colours), color=(1.0, 1.0, 1.0, 1.0), roughness=1.0,
                              extras=dict(layer="building_lod", lod=name, block=identity, object_id=f"{identity}:{name}",
                                          material_key="building_lod")))
    return result
