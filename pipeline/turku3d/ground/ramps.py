"""Surveyed ramp coverage on the existing measured terrain mesh."""

import numpy as np
import shapely

from turku3d.col.materials import mat_id


def classify_ramps(mesh, surveys):
    """Record measured ground coverage and label paved surveyed ramps.

    The survey locates a ramp, while the CDT already supplies its measured
    elevation and slope. Only walkable, non-water triangles can be relabelled.
    """
    if not surveys:
        return {"survey_lines": 0, "covered_lines": 0, "triangles": 0}, []
    lines = [line for _, line in surveys]
    triangles = mesh.positions[mesh.indices]
    footprints = shapely.polygons(triangles[:, :, [0, 2]])
    tree = shapely.STRtree(footprints)
    pairs = tree.query(lines, predicate="intersects")
    original = mesh.material.copy()
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    slope = np.degrees(
        np.arctan2(np.linalg.norm(normals[:, [0, 2]], axis=1), np.abs(normals[:, 1]))
    )
    high_face = (slope > 40) & (np.ptp(triangles[:, :, 1], axis=1) > 0.35)
    walkable = np.isin(
        original,
        [
            mat_id(name)
            for name in (
                "road",
                "sidewalk",
                "crossing",
                "cycleway",
                "cobblestone",
                "paving_slab",
                "gravel",
                "grass",
                "soil",
            )
        ],
    )
    selected = pairs[:, walkable[pairs[1]] & ~high_face[pairs[1]]]
    paved = np.isin(
        original,
        [
            mat_id(name)
            for name in ("road", "sidewalk", "crossing", "cycleway", "cobblestone", "paving_slab")
        ],
    )
    mesh.material[selected[1][paved[selected[1]] & (slope[selected[1]] <= 40)]] = mat_id("ramp")
    mesh.material[pairs[1][walkable[pairs[1]] & high_face[pairs[1]]]] = mat_id("wall")
    by_line = [[] for _ in surveys]
    all_solid = [[] for _ in surveys]
    wall_face = [[] for _ in surveys]
    for line_index, triangle_index in selected.T:
        by_line[line_index].append(int(triangle_index))
    for line_index, triangle_index in pairs.T:
        if original[triangle_index] != mat_id("water"):
            all_solid[line_index].append(int(triangle_index))
        if original[triangle_index] == mat_id("wall") or high_face[triangle_index]:
            wall_face[line_index].append(int(triangle_index))
    records = []
    for (object_id, line), indices, solid, walls in zip(
        surveys, by_line, all_solid, wall_face, strict=True
    ):
        footprint = (
            shapely.union_all(footprints[np.unique(indices)]) if indices else shapely.Polygon()
        )
        uncovered = line.difference(footprint.buffer(1e-6)).length
        solid_footprint = (
            shapely.union_all(footprints[np.unique(solid)]) if solid else shapely.Polygon()
        )
        solid_uncovered = line.difference(solid_footprint.buffer(1e-6)).length
        classification = (
            "ramp"
            if uncovered <= 0.01
            else "wall"
            if walls and solid_uncovered <= 0.01
            else "missing"
        )
        records.append(
            {
                "object_id": object_id,
                "line_xz": np.asarray(line.coords)[:, :2].tolist(),
                "length_m": float(line.length),
                "covered_length_m": float(max(0, line.length - uncovered)),
                "solid_covered_length_m": float(max(0, line.length - solid_uncovered)),
                "classification": classification,
                "wall_triangles": len(set(walls)),
            }
        )
    return {
        "survey_lines": len(lines),
        "covered_lines": sum(r["classification"] != "missing" for r in records),
        "triangles": len(np.unique(selected[1])),
    }, records
