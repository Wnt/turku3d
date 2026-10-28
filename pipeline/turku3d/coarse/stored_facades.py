"""Bind existing facade maps to surveyed draft walls without changing their geometry."""

import numpy as np

from turku3d import config as C
from turku3d.facade_assets import building
from turku3d.package.gltf import GlbMesh


def match_wall(points, facades):
    """Only near-collinear, overlapping walls of the same stable building may share a map."""
    xz = np.asarray(points)[:, [0, 2]]
    a, b = xz[0], xz[1]
    length = float(np.linalg.norm(b - a))
    if length < 1e-6:
        return None
    direction = (b - a) / length
    matches = []
    for f in facades:
        outward = np.array([direction[1], -direction[0]])
        if f.get("normal") is not None and np.dot(outward, np.asarray(f["normal"])[[0, 2]]) < 0.95:
            continue
        en = np.asarray(f["coordinates"], float)[:, :2]
        segment = np.c_[en[:, 0] - C.ORIGIN_E, C.ORIGIN_N - en[:, 1]]
        p, q = segment[0], segment[-1]
        width = np.linalg.norm(q - p)
        if width < 0.1:
            continue
        tangent = (q - p) / width
        if abs(np.dot(tangent, direction)) < 0.995:
            continue
        normal = np.array([-tangent[1], tangent[0]])
        distance = np.max(np.abs((xz - p) @ normal))
        along = (xz - p) @ tangent
        coverage = max(0, min(along.max(), width) - max(along.min(), 0)) / length
        if distance > 0.65 or coverage < 0.8:
            continue
        matches.append((distance + (1 - coverage), f, along))
    if not matches:
        return None
    _, facade, along = min(matches, key=lambda row: row[0])
    uv = np.c_[along, np.asarray(points)[:, 1] - facade["base_height"]]
    transform = facade["metadata"].get("uv")
    if transform:
        scale, offset = np.asarray(transform["scale"]), np.asarray(transform["offset"])
        # Unique maps originally clamp at the wall boundary. Preserve that behavior before
        # atlas packing, where sampling past an edge would instead leak into another wall.
        uv = (np.clip(uv * scale + offset, 0, 1) - offset) / scale
    return facade, uv


def wall_meshes(b, walls, wuv, widx, origin, params):
    stored = building(b.facade_key).get("facades", [])
    meshes, fallback = [], []
    # The extrusion contract emits four vertices and two triangles for each vertical wall.
    for start in range(0, len(walls), 4):
        vertices = walls[start : start + 4]
        match = match_wall(vertices, stored)
        if match is None:
            fallback.extend(widx[np.all((widx >= start) & (widx < start + 4), axis=1)])
            continue
        f, uv = match
        meta = f["metadata"]
        meshes.append(
            GlbMesh(
                f"stored_{b.facade_key}_{meta['facade_index']}_{start}",
                vertices - origin,
                np.array([[0, 3, 2], [0, 2, 1]], np.uint32),
                uvs=uv,
                color=(1, 1, 1, 1),
                extras=dict(
                    layer="buildings",
                    object_id=f"walls_{b.key}_{start}",
                    building_id=meta.get("building_id", b.facade_key),
                    facade_index=meta["facade_index"],
                    material_key="facade_stored",
                    surface="wall",
                    base_height=b.base,
                    top_height=b.top,
                ),
            )
        )
    if fallback:
        meshes.append(
            GlbMesh(
                f"walls_{b.key}",
                walls - origin,
                np.asarray(fallback, np.uint32),
                uvs=wuv,
                color=(1, 1, 1, 1),
                extras=dict(
                    layer="buildings",
                    object_id=f"walls_{b.key}",
                    building_id=b.facade_key,
                    material_key="facade_kit",
                    base_height=b.base,
                    top_height=b.top,
                    facade_kit=params,
                ),
            )
        )
    return meshes
