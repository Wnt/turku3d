"""Tile clipping for generated structures, preserving interpolated heights and UVs."""

import numpy as np

from turku3d.structures.geometry import Faces


def clip_mesh(mesh, bbox):
    """Clip each triangle to the tile, so neighbours never draw a complete duplicate."""
    faces = Faces()
    for triangle in mesh.indices:
        vertices = np.c_[mesh.positions[triangle], mesh.uvs[triangle]]
        for axis, edge, sign in (
            (0, bbox.xmin, 1),
            (0, bbox.xmax, -1),
            (2, bbox.zmin, 1),
            (2, bbox.zmax, -1),
        ):
            if not len(vertices):
                break
            result = []
            for a, b in zip(vertices, np.roll(vertices, -1, axis=0), strict=True):
                inside_a, inside_b = sign * (a[axis] - edge) >= 0, sign * (b[axis] - edge) >= 0
                if inside_a:
                    result.append(a)
                if inside_a != inside_b:
                    # Sort endpoints to give both tiles bitwise-identical intersections.
                    lo, hi = (a, b) if a[axis] < b[axis] else (b, a)
                    p = lo + (hi - lo) * ((edge - lo[axis]) / (hi[axis] - lo[axis]))
                    p[axis] = edge
                    result.append(p)
            vertices = np.asarray(result)
        if len(vertices) >= 3:
            faces.face(vertices[:, :3], vertices[:, 3:])
    if not faces.indices:
        return None
    result = faces.mesh(mesh.object_id, mesh.material, visual=mesh.visual, metadata=mesh.metadata)
    triangles = result.positions[result.indices]
    area = (
        np.linalg.norm(
            np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1
        )
        / 2
    )
    result.indices = result.indices[area >= 1e-6]
    return result if len(result.indices) else None
