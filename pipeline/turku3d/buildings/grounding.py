"""Extend a shell's surveyed footing to nearby terrain without moving its roof."""

from dataclasses import replace

import numpy as np

from .shells import Surface

# The facade rebind accepts base corrections through 0.75 m. A wall needs nearby
# ground support before a deeper local dip can request this bounded extension.
MAX_EXTENSION_M = 0.75


def ground_contact(shell, sample):
    """Lower shared GroundSurface vertices, preserving closure and facade identities.

    Only walls touching the shell's ground surface supply samples. Roof step walls
    and elevated openings must never be extended to terrain. `sample(xz)` returns
    the measured terrain height/material, or None outside the ground layer.
    """
    ground_ids = [i for i, surface in enumerate(shell.surfaces) if surface.kind == "GroundSurface"]
    footing = np.unique(shell.indices[np.isin(shell.surface_ids, ground_ids)])
    if not len(footing):
        return shell, 0.0
    drop = 0.0
    for sid, surface in enumerate(shell.surfaces):
        if surface.kind != "WallSurface":
            continue
        faces = shell.indices[shell.surface_ids == sid]
        bottom = np.intersect1d(np.unique(faces), footing)
        if len(bottom) < 2:
            continue
        triangles = shell.positions[faces]
        normal = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]).sum(
            0
        )
        normal /= np.linalg.norm(normal)
        p = shell.positions[bottom]
        # The longest footing edge spans this planar wall, including intermediate vertices.
        a, b = np.unravel_index(
            np.linalg.norm(p[:, None] - p[None, :], axis=2).argmax(), (len(p), len(p))
        )
        # Include the midpoint even when the regular spacing would skip it.
        count = max(3, int(np.ceil(np.linalg.norm(p[a] - p[b]) / 2)) + 1) | 1
        residuals = []
        for t in np.linspace(0, 1, count):
            point = p[a] * (1 - t) + p[b] * t
            found = sample(point[[0, 2]] + normal[[0, 2]] * 0.4)
            if found is None:
                continue
            residuals.append(float(point[1] - found[0]))
        # A local depression must not be discarded just because full contact
        # exceeds the correction range. Extend as far as allowed, provided this
        # wall also has a sample near its surveyed footing. Uniformly elevated
        # walls still require model review and stay untouched.
        if residuals and min(map(abs, residuals)) <= MAX_EXTENSION_M:
            drop = max(drop, min(MAX_EXTENSION_M, max(residuals)))
    if drop <= 0.05:
        return shell, 0.0
    positions = shell.positions.copy()
    positions[footing, 1] -= drop
    moved = {tuple(shell.positions[i]): positions[i] for i in footing}
    surfaces = [
        Surface([np.array([moved.get(tuple(v), v) for v in ring]) for ring in s.rings], s.kind)
        for s in shell.surfaces
    ]
    return replace(shell, positions=positions, surfaces=surfaces), drop
