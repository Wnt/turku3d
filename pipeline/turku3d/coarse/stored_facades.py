"""Bind existing facade maps to surveyed draft walls without changing their geometry."""

import math

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


def row_sections(vertices, uv, params, eave):
    """Keep only complete storeys below the lower end of a sloped wall top.

    The atlas already leaves roughly a quarter-storey of wall above its windows.
    Finishing at a floor boundary preserves that frieze; any remaining roof/gable
    area uses the same material's blank module, with no partial window rows.
    """
    base = vertices[0, 1]
    height = min(eave, min(vertices[2:, 1])) - base
    ground, upper = params["ground"], params["upper"]
    rows = math.floor((height - ground + 1e-6) / upper)
    cutoff = base + ground + rows * upper if height + 1e-6 >= ground else base
    cutoff = min(cutoff, min(vertices[2:, 1]))
    if cutoff > base:
        lower, lower_uv = vertices.copy(), uv.copy()
        lower[2:, 1] = cutoff
        lower_uv[2:, 1] = cutoff
        yield False, lower, lower_uv
    if max(vertices[2:, 1]) > cutoff + 1e-6:
        cap, cap_uv = vertices.copy(), uv.copy()
        cap[:2, 1] = cutoff
        cap_uv[:2, 1] = cutoff
        yield True, cap, cap_uv


def wall_meshes(b, walls, wuv, widx, origin, params):
    stored = building(b.facade_key).get("facades", [])
    meshes, fallback = [], {False: [], True: []}
    # Each extrusion ring closes before the next roof part starts. Its lowest top
    # is the eave, including when a gable side has several collinear segments.
    eaves = {}
    ring_start = 0
    for end in range(0, len(walls), 4):
        if np.linalg.norm(walls[end + 1, [0, 2]] - walls[ring_start, [0, 2]]) < 1e-6:
            eave = float(walls[ring_start : end + 4].reshape(-1, 4, 3)[:, 2:, 1].min())
            eaves.update((i, eave) for i in range(ring_start, end + 4, 4))
            ring_start = end + 4
    # The extrusion contract emits four vertices and two triangles for each vertical wall.
    for start in range(0, len(walls), 4):
        vertices = walls[start : start + 4]
        match = match_wall(vertices, stored)
        if match is None:
            if params.get("plinth"):
                cap = min(b.base + 0.8, min(vertices[2:, 1]))
                lower = vertices.copy()
                lower[2:, 1] = cap
                meshes.append(
                    GlbMesh(
                        f"plinth_{b.key}_{start}",
                        lower - origin,
                        np.array([[0, 3, 2], [0, 2, 1]], np.uint32),
                        uvs=wuv[start : start + 4],
                        extras=dict(
                            layer="buildings",
                            object_id=f"plinth_{b.key}_{start}",
                            material_key="plinth_granite",
                            surface="wall",
                        ),
                    )
                )
                vertices = vertices.copy()
                vertices[:2, 1] = cap
            if params["module"] >= 42:
                fallback[True].append((vertices, wuv[start : start + 4]))
            else:
                eave = eaves.get(start, min(vertices[2:, 1]))
                for blank, points, uv in row_sections(
                    vertices, wuv[start : start + 4], params, eave
                ):
                    fallback[blank].append((points, uv))
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
    for blank, parts in fallback.items():
        if not parts:
            continue
        style = dict(params)
        if blank and style["module"] < 42:
            style["module"] = 42 + style["module"] // 6
        suffix = "_frieze" if blank else ""
        meshes.append(
            GlbMesh(
                f"walls_{b.key}{suffix}",
                np.concatenate([p for p, _ in parts]) - origin,
                np.concatenate(
                    [np.array([[0, 3, 2], [0, 2, 1]], np.uint32) + 4 * i for i in range(len(parts))]
                ),
                uvs=np.concatenate([uv for _, uv in parts]),
                color=(1, 1, 1, 1),
                extras=dict(
                    layer="buildings",
                    object_id=f"walls_{b.key}{suffix}",
                    building_id=b.facade_key,
                    material_key="facade_kit",
                    base_height=b.base,
                    top_height=b.top,
                    facade_kit=style,
                ),
            )
        )
    return meshes
