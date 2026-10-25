"""Exact point-to-triangle distances with an AABB tree and bounded memory."""

import numpy as np
from scipy.spatial import cKDTree
from trimesh.triangles import closest_point


def surface_distances(positions, indices, points):
    """Euclidean distance to the shell, including vertical facades and roof steps."""
    triangles = positions[indices]
    if not len(triangles):
        return np.full(len(points), np.inf)
    centers = triangles.mean(axis=1)
    nearest = cKDTree(centers).query(points)[1]
    closest = closest_point(triangles[nearest], points)
    best = np.sum((points - closest) ** 2, axis=1)

    def visit(ids, point_ids):
        if not len(point_ids):
            return
        vertices = triangles[ids]
        low, high = vertices.min(axis=(0, 1)), vertices.max(axis=(0, 1))
        q = points[point_ids]
        delta = np.maximum(np.maximum(low - q, q - high), 0)
        point_ids = point_ids[np.sum(delta**2, axis=1) < best[point_ids]]
        if not len(point_ids):
            return
        if len(ids) <= 8:
            q = points[point_ids]
            for i in ids:
                tri = np.broadcast_to(triangles[i], (len(q), 3, 3))
                near = closest_point(tri, q)
                best[point_ids] = np.minimum(best[point_ids], np.sum((near - q) ** 2, axis=1))
        else:
            axis = np.argmax(np.ptp(centers[ids], axis=0))
            order = ids[np.argsort(centers[ids, axis], kind="stable")]
            middle = len(order) // 2
            visit(order[:middle], point_ids)
            visit(order[middle:], point_ids)

    visit(np.arange(len(triangles)), np.arange(len(points)))
    return np.sqrt(best)
