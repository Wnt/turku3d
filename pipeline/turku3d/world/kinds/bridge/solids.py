"""Closed solids with metre UVs for bridge structure, in an entity's engine frame (x east, y up, z south).

Vertical faces carry (distance along the face, height) as UVs and horizontal faces their plan position, both in
absolute metres, so the building families' courses and joints run on across pieces (abutment, pier, parapet).
"""

from __future__ import annotations

import numpy as np

from ...api import Material, Part
from ...geometry import earcut


class Solids:
    """Flat-shaded faces grouped per (part, material)."""

    def __init__(self, base: float, origin_xz: tuple[float, float] = (0.0, 0.0)):
        self.base = base             # y of the entity origin: heights are given absolute and stored relative
        self.origin = np.asarray(origin_xz, float)   # absolute engine x, z of the entity origin (for UVs)
        self.groups: dict[tuple, dict] = {}

    def _group(self, part: str, material: Material) -> dict:
        key = (part, repr(material))
        if key not in self.groups:
            self.groups[key] = {'part': part, 'material': material, 'p': [], 'n': [], 'uv': [], 't': [], 'count': 0}
        return self.groups[key]

    def face(self, part: str, material: Material, points, uvs, outward=None):
        """One planar polygon (k, 3) with its UVs, wound to face `outward` when given."""
        points = np.asarray(points, float)
        if len(points) < 3:
            return
        normal = np.cross(points[1] - points[0], points[2] - points[0])
        if len(points) > 3:
            normal = np.sum([np.cross(points[k] - points[0], points[k + 1] - points[0]) for k in range(1, len(points) - 1)], axis=0)
        length = np.linalg.norm(normal)
        if length < 1e-10:
            return
        normal /= length
        uvs = np.asarray(uvs, float)
        if outward is not None and normal @ np.asarray(outward, float) < 0:
            points, uvs, normal = points[::-1], uvs[::-1], -normal
        group = self._group(part, material)
        k = len(points)
        triangles = np.column_stack([np.zeros(k - 2, int), np.arange(1, k - 1), np.arange(2, k)]) + group['count']
        shifted = points.copy()
        shifted[:, 1] -= self.base
        group['p'].append(shifted)
        group['n'].append(np.tile(normal, (k, 1)))
        group['uv'].append(uvs)
        group['t'].append(triangles)
        group['count'] += k

    def polygon(self, part: str, material: Material, ring_xz, heights, up: bool):
        """A horizontal (or sloping) polygon over a plan ring (k, 2) at heights (k,), facing up or down."""
        ring_xz = np.asarray(ring_xz, float)
        heights = np.asarray(heights, float)
        triangles = earcut([ring_xz])
        if not len(triangles):
            return
        points = np.column_stack([ring_xz[:, 0], heights, ring_xz[:, 1]])
        uvs = ring_xz + self.origin
        group = self._group(part, material)
        # earcut gives counter-clockwise triangles in (x, z), which seen from above (+y, with z south) face down.
        if up:
            triangles = triangles[:, ::-1]
        shifted = points.copy()
        shifted[:, 1] -= self.base
        group['p'].append(shifted)
        group['n'].append(np.tile([0.0, 1.0 if up else -1.0, 0.0], (len(points), 1)))
        group['uv'].append(uvs)
        group['t'].append(triangles + group['count'])
        group['count'] += len(points)

    def wall(self, part: str, material: Material, line_xz, top, bottom, outward_xz, u0: float = 0.0):
        """Vertical quads along a plan polyline between bottom and top (per point), facing `outward_xz` (per segment
        or one vector)."""
        line_xz = np.asarray(line_xz, float)
        top, bottom = np.broadcast_to(np.asarray(top, float), len(line_xz)), np.broadcast_to(np.asarray(bottom, float), len(line_xz))
        out = np.asarray(outward_xz, float)
        along = u0 + np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(line_xz, axis=0), axis=1))])
        for k in range(len(line_xz) - 1):
            o = out[k] if out.ndim == 2 else out
            a, b = line_xz[k], line_xz[k + 1]
            quad = [[a[0], bottom[k], a[1]], [b[0], bottom[k + 1], b[1]], [b[0], top[k + 1], b[1]], [a[0], top[k], a[1]]]
            if top[k] - bottom[k] < 1e-4 and top[k + 1] - bottom[k + 1] < 1e-4:
                continue
            self.face(part, material, quad, [[along[k], bottom[k]], [along[k + 1], bottom[k + 1]], [along[k + 1], top[k + 1]], [along[k], top[k]]],
                      [o[0], 0.0, o[1]])

    def band(self, part: str, material: Material, left, right, top, bottom, top_right=None, bottom_right=None, caps=(True, True)):
        """A closed solid between two plan polylines of equal length (left and right, matched point by point), from
        `bottom` to `top` per point (the right side may have heights of its own)."""
        left, right = np.asarray(left, float), np.asarray(right, float)
        n = len(left)
        if n < 2:
            return
        top = np.broadcast_to(np.asarray(top, float), n).copy()
        bottom = np.broadcast_to(np.asarray(bottom, float), n).copy()
        top_r = top if top_right is None else np.broadcast_to(np.asarray(top_right, float), n)
        bottom_r = bottom if bottom_right is None else np.broadcast_to(np.asarray(bottom_right, float), n)
        across = right - left
        run = np.diff((left + right) / 2, axis=0)
        # Side faces face away from the other side.
        self.wall(part, material, left, top, bottom, -across[:-1] / np.maximum(np.linalg.norm(across[:-1], axis=1, keepdims=True), 1e-9))
        self.wall(part, material, right, top_r, bottom_r, across[:-1] / np.maximum(np.linalg.norm(across[:-1], axis=1, keepdims=True), 1e-9))
        for k in range(n - 1):
            ring = np.array([left[k], left[k + 1], right[k + 1], right[k]])
            for heights, up in (([top[k], top[k + 1], top_r[k + 1], top_r[k]], True), ([bottom[k], bottom[k + 1], bottom_r[k + 1], bottom_r[k]], False)):
                points = np.column_stack([ring[:, 0], heights, ring[:, 1]])
                self.face(part, material, points, ring + self.origin, [0.0, 1.0 if up else -1.0, 0.0])
        for k, sign, wanted in ((0, -1.0, caps[0]), (n - 1, 1.0, caps[1])):
            if not wanted:
                continue
            direction = run[min(k, n - 2)] * sign
            quad = [[left[k][0], bottom[k], left[k][1]], [right[k][0], bottom_r[k], right[k][1]],
                    [right[k][0], top_r[k], right[k][1]], [left[k][0], top[k], left[k][1]]]
            width = float(np.linalg.norm(across[k]))
            self.face(part, material, quad, [[0.0, bottom[k]], [width, bottom_r[k]], [width, top_r[k]], [0.0, top[k]]],
                      [direction[0], 0.0, direction[1]])

    def prism(self, part: str, material: Material, ring_xz, top, bottom):
        """A closed upright solid over a plan ring (k, 2) from `bottom` to `top` (per point or one value)."""
        ring_xz = np.asarray(ring_xz, float)
        if len(ring_xz) > 1 and np.allclose(ring_xz[0], ring_xz[-1]):
            ring_xz = ring_xz[:-1]
        k = len(ring_xz)
        top = np.broadcast_to(np.asarray(top, float), k)
        bottom = np.broadcast_to(np.asarray(bottom, float), k)
        signed = np.sum(ring_xz[:, 0] * np.roll(ring_xz[:, 1], -1) - np.roll(ring_xz[:, 0], -1) * ring_xz[:, 1])
        closed = np.vstack([ring_xz, ring_xz[:1]])
        edges = np.diff(closed, axis=0)
        # Outward in plan: for a ring counter-clockwise in (x, z) the outside is to the right of each edge.
        normal = np.column_stack([edges[:, 1], -edges[:, 0]]) * (1.0 if signed > 0 else -1.0)
        self.wall(part, material, closed, np.append(top, top[0]), np.append(bottom, bottom[0]), normal)
        self.polygon(part, material, ring_xz, top, up=True)
        self.polygon(part, material, ring_xz, bottom, up=False)

    def parts(self) -> list[Part]:
        out = []
        for group in self.groups.values():
            if not group['p']:
                continue
            out.append(Part(group['part'], np.round(np.concatenate(group['p']), 4).astype(np.float32),
                            np.concatenate(group['t']).astype(np.uint32), group['material'],
                            normals=np.round(np.concatenate(group['n']), 4).astype(np.float32),
                            uvs=np.round(np.concatenate(group['uv']), 4).astype(np.float32)))
        return out
