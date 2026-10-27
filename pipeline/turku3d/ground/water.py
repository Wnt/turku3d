"""Explicit synthetic collision bed when measured bathymetry is unavailable."""

import numpy as np

from turku3d.col.materials import mat_id

from .mesh import Mesh


def add_river_bed(mesh, water_level, depth=3.0):
    """Close solid banks to a flat bed; leave the independent water sensor intact.

    This is a collision fallback, never a claim of measured bathymetry. The caller
    retains the original mesh for visuals, so the synthetic bed/banks are not rendered.
    """
    wet = mesh.material == mat_id("water")
    if not wet.any():
        return mesh
    wf = mesh.indices[wet]
    ids, inverse = np.unique(wf, return_inverse=True)
    bed = mesh.positions[ids].copy()
    bed[:, 1] = water_level - depth
    bed_faces = inverse.reshape(-1, 3) + len(mesh.positions)
    bed_index = np.zeros(len(mesh.positions), dtype=np.uint32)
    bed_index[ids] = np.arange(len(ids)) + len(mesh.positions)
    edges = np.concatenate([wf[:, [0, 1]], wf[:, [1, 2]], wf[:, [2, 0]]])
    _, first, counts = np.unique(
        np.sort(edges, axis=1), axis=0, return_index=True, return_counts=True
    )
    boundary = edges[first[counts == 1]]
    dry_ids = np.unique(mesh.indices[~wet])
    banks = np.empty((0, 3), dtype=np.uint32)
    if len(dry_ids):
        from scipy.spatial import cKDTree  # imported on use: dry tiles never need it

        tree = cKDTree(mesh.positions[dry_ids][:, [0, 2]])
        distances, nearest = tree.query(mesh.positions[boundary][:, :, [0, 2]])
        shore = (distances < 1e-7).all(axis=1)
        a, b = dry_ids[nearest[shore]].T
        ba, bb = bed_index[boundary[shore]].T
        banks = np.r_[np.c_[a, b, ba], np.c_[b, bb, ba]].astype(np.uint32)
    added = np.r_[bed_faces, banks].astype(np.uint32)
    return Mesh(
        np.r_[mesh.positions, bed],
        np.r_[mesh.indices, added],
        np.r_[mesh.material, np.full(len(added), mat_id("soil"), np.uint8)],
    ).compact()
