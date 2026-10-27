"""Fast acceptance for plain lidar ground; curb accuracy is deferred."""

import numpy as np
import shapely

from turku3d.col.materials import mat_id

from .mesh import seam_errors


def evaluate_ground(meshes, accepted, water, buildings, bounds, bridges=None):
    bridges = shapely.Polygon() if bridges is None else bridges
    forbidden = shapely.union_all(
        [g for g in (water, buildings.buffer(0.5), bridges) if not g.is_empty]
    )
    gx, gz = np.meshgrid(
        np.arange(bounds.xmin + 0.5, bounds.xmax), np.arange(bounds.zmin + 0.5, bounds.zmax)
    )
    cells = np.c_[gx.ravel(), gz.ravel()]
    open_cells = ~shapely.contains_xy(forbidden, *cells.T)
    origin = np.floor([bounds.xmin, bounds.zmin]).astype(np.int64)
    dimensions = np.ceil([bounds.xmax, bounds.zmax]).astype(np.int64) - origin
    occupied = np.floor(accepted[:, [0, 2]]).astype(np.int64) - origin
    occupied = occupied[np.all((occupied >= 0) & (occupied < dimensions), axis=1)]
    keys = np.unique(occupied[:, 1] * dimensions[0] + occupied[:, 0])
    q = np.floor(cells).astype(np.int64) - origin
    covered = np.isin(q[:, 1] * dimensions[0] + q[:, 0], keys)
    coverage = float(covered[open_cells].mean()) if open_cells.any() else 0.0
    positions, faces, offset = [], [], 0
    water_faces = []
    for mesh in meshes.values():
        positions.append(mesh.positions)
        solid = mesh.material != mat_id("water")
        faces.append(mesh.indices[solid] + offset)
        water_faces.append(mesh.positions[mesh.indices[~solid], 1])
        offset += len(mesh.positions)
    water_faces = np.concatenate(water_faces) if water_faces else np.empty((0, 3))
    p, inv = np.unique(np.concatenate(positions), axis=0, return_inverse=True)
    f = inv[np.concatenate(faces)]
    edges = np.sort(np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]), axis=1)
    unique, counts = np.unique(edges, axis=0, return_counts=True)
    exposed = unique[counts == 1]
    midpoints = p[exposed].mean(axis=1)[:, [0, 2]]
    domain = shapely.box(*bounds.as_tuple())
    unexpected = shapely.distance(shapely.points(midpoints), domain.boundary) > 1e-7
    if not bridges.is_empty:
        # At a bridge bank the river-bed wall ends inside the deck footprint.
        # Its exposed edge is intentional; all other interior holes still fail.
        unexpected &= ~shapely.covers(bridges.buffer(0.01), shapely.points(midpoints))
    internal_open_edges = int(unexpected.sum())
    seams = seam_errors(meshes)
    metrics = {
        "coverage": {"open_cells": int(open_cells.sum()), "occupied_fraction": coverage},
        "mesh": {
            "max_tile_triangles": max(len(m.indices) for m in meshes.values()),
            "seam_mismatches": seams,
            "interior_open_edges": internal_open_edges,
            "nonmanifold_edges": int((counts > 2).sum()),
            "all_triangles_material": all(
                len(m.material) == len(m.indices) and np.all(m.material > 0)
                for m in meshes.values()
            ),
        },
        "water": {
            "triangles": len(water_faces),
            "levels_m": sorted({float(h) for h in water_faces[:, 0]}),
            "max_face_tilt_m": float(np.ptp(water_faces, axis=1).max())
            if len(water_faces)
            else 0.0,
        },
    }
    checks = {
        "coverage": coverage >= 0.95,
        "triangle_budget": metrics["mesh"]["max_tile_triangles"] <= 150_000,
        "seams": not seams,
        "holes": internal_open_edges == 0 and not (counts > 2).any(),
        "materials": metrics["mesh"]["all_triangles_material"],
        # Every body is one horizontal plane at its own level.
        "water_plane": metrics["water"]["max_face_tilt_m"] == 0.0,
    }
    checks = {key: bool(value) for key, value in checks.items()}
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "metrics": metrics,
        "acceptance": "Plain lidar ground; curb accuracy deferred by user decision",
    }
