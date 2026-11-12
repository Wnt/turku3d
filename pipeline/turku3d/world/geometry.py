"""Small mesh helpers shared by the kinds."""

from __future__ import annotations

import hashlib

import mapbox_earcut
import numpy as np

UP = np.array([0.0, 1.0, 0.0])


def seed(text: str) -> int:
    """A stable 32-bit seed from an entity id or any other text."""
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "little")


def newell(ring: np.ndarray) -> np.ndarray:
    """Area-weighted normal of a 3D ring; its direction follows the ring's winding (counter-clockwise seen from it)."""
    nxt = np.roll(ring, -1, axis=0)
    return np.array([
        np.sum((ring[:, 1] - nxt[:, 1]) * (ring[:, 2] + nxt[:, 2])),
        np.sum((ring[:, 2] - nxt[:, 2]) * (ring[:, 0] + nxt[:, 0])),
        np.sum((ring[:, 0] - nxt[:, 0]) * (ring[:, 1] + nxt[:, 1])),
    ])


def plane_frame(normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Tangent (horizontal) and bitangent of a plane, right-handed with its unit normal: t x b = n."""
    t = np.cross(UP, normal)
    length = np.linalg.norm(t)
    t = t / length if length > 1e-6 else np.array([1.0, 0.0, 0.0])
    return t, np.cross(normal, t)


def earcut(rings2d: list[np.ndarray]) -> np.ndarray:
    """Triangles (m, 3) of a 2D polygon given as an exterior ring and holes, counter-clockwise."""
    points = np.concatenate(rings2d).astype(np.float64)
    ends = np.cumsum([len(r) for r in rings2d]).astype(np.uint32)
    faces = mapbox_earcut.triangulate_float64(points, ends).reshape(-1, 3).astype(np.int64)
    a, b, c = points[faces[:, 0]], points[faces[:, 1]], points[faces[:, 2]]
    area = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    faces[area < 0] = faces[area < 0][:, ::-1]
    return faces[np.abs(area) > 1e-9]


def triangulate_planar(rings: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
    """A planar 3D polygon (exterior ring first) -> (positions, triangles, unit normal, wall uv: along, up)."""
    rings = [r for r in rings if len(r) >= 3]
    if not rings:
        return None
    normal = newell(rings[0])
    length = np.linalg.norm(normal)
    if length < 1e-9:
        return None
    normal = normal / length
    t, b = plane_frame(normal)
    points = np.concatenate(rings)
    flat = [np.column_stack([r @ t, r @ b]) for r in rings]
    faces = earcut(flat)
    if not len(faces):
        return None
    along = points @ t
    return points, faces, normal, np.column_stack([along - along.min(), points[:, 1]])


def merge(meshes: list[tuple[np.ndarray, ...]]) -> tuple[np.ndarray, ...]:
    """Concatenates (positions, triangles, *vertex arrays) meshes, offsetting the indices."""
    offset, columns = 0, [[] for _ in meshes[0]]
    for mesh in meshes:
        for k, array in enumerate(mesh):
            columns[k].append(array + offset if k == 1 else array)
        offset += len(mesh[0])
    return tuple(np.concatenate(c) for c in columns)
