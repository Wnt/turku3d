"""Bind existing facade maps to surveyed draft walls without changing their geometry."""

import numpy as np

from turku3d import config as C
from turku3d.facade_assets import building
from turku3d.package.gltf import GlbMesh


def match_wall(points, facades):
    """Only near-collinear, overlapping walls of the same stable building may share a map."""
    return match_prepared(points, prepare_walls(facades))


def prepare_walls(facades):
    """Convert invariant surveyed segments once for all of a building's roof fragments."""
    rows = []
    for facade in facades:
        en = np.asarray(facade["coordinates"], float)[:, :2]
        segment = np.column_stack((en[:, 0] - C.ORIGIN_E, C.ORIGIN_N - en[:, 1]))
        width = float(np.linalg.norm(segment[-1] - segment[0]))
        if width >= 0.1:
            outward = np.asarray(facade["normal"])[[0, 2]] if facade.get("normal") is not None else [0, 0]
            rows.append((facade, segment[0], (segment[-1] - segment[0]) / width, width, outward))
    if not rows:
        return None
    return ([r[0] for r in rows], *[np.asarray([r[i] for r in rows]) for i in range(1, 5)])


def match_prepared(points, prepared, *, min_coverage=0.8):
    if prepared is None:
        return None
    facades, starts, tangents, widths, outward = prepared
    xz = np.asarray(points)[:, [0, 2]]
    a, b = xz[0], xz[1]
    length = float(np.linalg.norm(b - a))
    if length < 1e-6:
        return None
    direction = (b - a) / length
    delta = xz[None, :, :] - starts[:, None, :]
    normals = np.column_stack((-tangents[:, 1], tangents[:, 0]))
    distance = np.abs(np.einsum("nvi,ni->nv", delta, normals)).max(axis=1)
    along = np.einsum("nvi,ni->nv", delta, tangents)
    coverage = np.maximum(0, np.minimum(along.max(axis=1), widths) - np.maximum(along.min(axis=1), 0)) / length
    side = outward @ [direction[1], -direction[0]]
    valid = (np.abs(tangents @ direction) >= 0.995) & (distance <= 0.65) & (coverage >= min_coverage)
    valid &= (np.linalg.norm(outward, axis=1) == 0) | (side >= 0.95)
    matches = np.flatnonzero(valid)
    if not len(matches):
        return None
    index = matches[np.argmin((distance + 1 - coverage)[matches])]
    facade = facades[index]
    uv = np.column_stack((along[index], np.asarray(points)[:, 1] - facade["base_height"]))
    transform = facade["metadata"].get("uv")
    if transform:
        scale, offset = np.asarray(transform["scale"]), np.asarray(transform["offset"])
        # Unique maps originally clamp at the wall boundary. Preserve that behavior before
        # atlas packing, where sampling past an edge would instead leak into another wall.
        uv = (np.clip(uv * scale + offset, 0, 1) - offset) / scale
    return facade, uv



def exposed_wall_quads(walls):
    """Paint one surface per outward wall, including near-coincident LOD2 slivers.

    Roof-part solids remain closed for collision. Their independently simplified
    rings can put two outward faces within millimetres of each other, especially
    where a narrow fallback roof strip borders a surveyed roof. Subtract covered
    areas in wall metres before assigning facade programs, keeping the taller
    face and any exposed remainder. Opposite-facing solid boundaries are retained.
    """
    import shapely

    quads = np.asarray(walls).reshape(-1, 4, 3)
    if not len(quads):
        return walls
    starts = quads[:, 0][:, [0, 2]]
    directions = quads[:, 1][:, [0, 2]] - starts
    lengths = np.linalg.norm(directions, axis=1)
    directions = directions / lengths[:, None]
    priority = sorted(range(len(quads)), key=lambda i: (-quads[i, 2:, 1].max(), -lengths[i], i))
    rank = np.argsort(priority)
    result = []
    for i, vertices in enumerate(quads):
        direction = directions[i]
        normal = np.array([-direction[1], direction[0]])
        delta = quads[:, :, [0, 2]] - starts[i]
        along = delta @ direction
        # Compare planes only over their shared frontage. A long surveyed edge
        # may diverge beyond a short sliver without changing that overlap.
        across = delta[:, :2] @ normal
        slope = (across[:, 1] - across[:, 0]) / np.maximum(along[:, 1] - along[:, 0], 1e-9)
        shared_left = np.maximum(0, along[:, :2].min(axis=1))
        shared_right = np.minimum(lengths[i], along[:, :2].max(axis=1))
        distance = np.maximum(
            np.abs(across[:, 0] + (shared_left - along[:, 0]) * slope),
            np.abs(across[:, 0] + (shared_right - along[:, 0]) * slope),
        )
        candidates = np.flatnonzero(
            (rank < rank[i]) & (directions @ direction > 0.99999)
            & (distance <= 0.005) & (along.max(axis=1) > 0)
            & (along.min(axis=1) < lengths[i])
        )
        if not len(candidates):
            result.append(vertices)
            continue
        surface = shapely.Polygon(np.c_[along[i], vertices[:, 1]])
        occluders = [shapely.Polygon(np.c_[along[j], quads[j, :, 1]]) for j in candidates]
        remainder = surface.difference(shapely.union_all(occluders))
        if remainder.equals(surface):
            result.append(vertices)
            continue
        # Vertical slabs of the planar difference are trapezoids, preserving
        # sloping eaves and partial-height overlaps without extra paint layers.
        for polygon in shapely.get_parts(remainder):
            if polygon.geom_type != "Polygon" or polygon.area < 1e-6:
                continue
            cuts = sorted(set(np.asarray(polygon.exterior.coords)[:, 0]))
            for left, right in zip(cuts, cuts[1:]):
                if right - left < 1e-6:
                    continue
                slab = polygon.intersection(shapely.box(left, -10000, right, 10000))
                for part in shapely.get_parts(slab):
                    if part.geom_type != "Polygon" or part.area < 1e-6:
                        continue
                    xy = np.asarray(part.exterior.coords)
                    lo = xy[np.isclose(xy[:, 0], left, atol=1e-7, rtol=0), 1]
                    hi = xy[np.isclose(xy[:, 0], right, atol=1e-7, rtol=0), 1]
                    if not len(lo) or not len(hi):
                        continue
                    points = np.array([vertices[0]] * 4)
                    points[:, [0, 2]] = starts[i] + np.array([left, right, right, left])[:, None] * direction
                    points[:, 1] = [lo.min(), hi.min(), hi.max(), lo.max()]
                    result.append(points)
    return np.asarray(result, float).reshape(-1, 3)


def wall_meshes(b, walls, origin):
    from turku3d.facade_program_geometry import wall_parts
    from turku3d.facade_programs import compile_wall, resolve

    walls = exposed_wall_quads(walls)
    stored = prepare_walls(building(b.facade_key).get("facades", []))
    meshes = []
    # Each extrusion ring closes before the next roof part starts. Its lowest top
    # is the eave, including when a gable side has several collinear segments.
    eaves = {}
    ring_start = 0
    for end in range(0, len(walls), 4):
        if np.linalg.norm(walls[end + 1, [0, 2]] - walls[ring_start, [0, 2]]) < 1e-6:
            eave = float(walls[ring_start : end + 4].reshape(-1, 4, 3)[:, 2:, 1].min())
            eaves.update((i, eave) for i in range(ring_start, end + 4, 4))
            ring_start = end + 4
    program = resolve(b, walls, eaves)
    instructions, program_parts = [], []
    # The extrusion contract emits four vertices and two triangles for each vertical wall.
    for start in range(0, len(walls), 4):
        vertices = walls[start : start + 4]
        match = match_prepared(vertices, stored)
        wall = program["walls"][start // 4]
        if wall.get("measured_wall_id"):
            match = None  # Direct review owns this wall, including over older generated maps.
        wall["paint_source"] = (
            "reviewed-program" if wall.get("measured_wall_id") else
            "stored-facade-map" if match else
            "existing-description" if program.get("description_source") else "rule-sampled"
        )
        if match:
            wall["stored_facade_index"] = match[0]["metadata"]["facade_index"]
        if match is None:
            style = compile_wall(program["walls"][start // 4], program["storeys"]["value"])
            style["origin"][1] = b.base
            for points, uv, instruction in wall_parts(vertices, program["walls"][start // 4], style):
                instructions.append(dict(start=len(program_parts) * 4, count=4, **instruction))
                program_parts.append((points, uv))
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
    if program_parts:
        meshes.append(
            GlbMesh(
                f"walls_{b.key}",
                np.concatenate([p for p, _ in program_parts]) - origin,
                np.concatenate(
                    [
                        np.array([[0, 3, 2], [0, 2, 1]], np.uint32) + 4 * i
                        for i in range(len(program_parts))
                    ]
                ),
                uvs=np.concatenate([uv for _, uv in program_parts]),
                color=(1, 1, 1, 1),
                extras=dict(
                    layer="buildings",
                    object_id=f"walls_{b.key}",
                    building_id=b.facade_key,
                    material_key="facade_kit",
                    base_height=b.base,
                    top_height=b.top,
                    facade_kit=instructions[0],
                    facade_kit_walls=instructions,
                ),
            )
        )
    if meshes:
        # build() exports this diagnostic program separately before writing the layer GLB.
        meshes[0].extras["facade_program"] = program
    return meshes
