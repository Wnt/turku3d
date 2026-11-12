"""Mesh builder of the street-object kinds: closed solids with real thickness, grouped by part and material.

An asset is authored in its own frame: metres, +Y up, the base on y = 0 at the origin, and its front towards +Z
(an instance yaw of 0 turns +Z to the south; see docs/design/world-pipeline.md, section 3.3). Smooth surfaces
(tubes, lathes) are welded with averaged normals; flat faces get their own vertices and the face normal.
"""

from __future__ import annotations

import math

import numpy as np

from ..api import Material, Part
from ..geometry import earcut


def srgb(r: float, g: float, b: float) -> tuple[float, float, float]:
    """Linear RGB of an sRGB colour given as 0..255 values."""
    def lin(c: float) -> float:
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return (round(lin(r), 4), round(lin(g), 4), round(lin(b), 4))


def rgb_hex(text: str) -> tuple[float, float, float]:
    text = text.lstrip("#")
    return srgb(int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


class Mesh:
    """Collects triangles per (part name, material); `parts()` returns them as contract parts."""

    def __init__(self) -> None:
        self.groups: dict[tuple[str, str], dict] = {}

    def _group(self, part: str, material: Material) -> dict:
        key = (part, repr(material))
        if key not in self.groups:
            self.groups[key] = {"part": part, "material": material, "p": [], "n": [], "t": [], "c": [], "uv": [], "count": 0}
        return self.groups[key]

    def add(self, part: str, material: Material, positions, normals, triangles, colors=None, uvs=None) -> None:
        positions = np.asarray(positions, dtype=np.float64).reshape(-1, 3)
        triangles = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
        if not len(triangles):
            return
        group = self._group(part, material)
        group["p"].append(positions)
        group["n"].append(np.asarray(normals, dtype=np.float64).reshape(-1, 3))
        group["t"].append(triangles + group["count"])
        group["c"].append(None if colors is None else np.asarray(colors, dtype=np.float64).reshape(-1, 3))
        group["uv"].append(None if uvs is None else np.asarray(uvs, dtype=np.float64).reshape(-1, 2))
        group["count"] += len(positions)

    def flat(self, part: str, material: Material, positions, triangles, colors=None) -> None:
        """Triangles with face normals: every triangle gets its own three vertices."""
        positions = np.asarray(positions, dtype=np.float64).reshape(-1, 3)
        triangles = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
        corners = positions[triangles.reshape(-1)].reshape(-1, 3, 3)
        normal = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
        length = np.linalg.norm(normal, axis=1, keepdims=True)
        keep = length[:, 0] > 1e-12
        corners, normal = corners[keep], normal[keep] / length[keep]
        tint = None if colors is None else np.asarray(colors, dtype=np.float64)[triangles.reshape(-1)].reshape(-1, 3, 3)[keep].reshape(-1, 3)
        self.add(part, material, corners.reshape(-1, 3), np.repeat(normal, 3, axis=0),
                 np.arange(len(corners) * 3).reshape(-1, 3), tint)

    # -- solids ------------------------------------------------------------------------------------------------

    def tube(self, part: str, material: Material, path, radii, sides: int = 8, caps: tuple[bool, bool] = (True, True)) -> None:
        """A round bar along a polyline (points (n, 3)), with a radius per point or one for all. Rings are mitred
        at the bends, the side is smooth and the ends are capped: a closed solid."""
        path = np.asarray(path, dtype=np.float64).reshape(-1, 3)
        count = len(path)
        radii = np.full(count, float(radii)) if np.isscalar(radii) else np.asarray(radii, dtype=np.float64)
        segment = np.diff(path, axis=0)
        segment /= np.maximum(np.linalg.norm(segment, axis=1, keepdims=True), 1e-12)
        tangent = np.vstack([segment[:1], segment[:-1] + segment[1:], segment[-1:]])
        tangent /= np.maximum(np.linalg.norm(tangent, axis=1, keepdims=True), 1e-12)
        # A frame carried along the path without twist.
        first = tangent[0]
        helper = np.array([1.0, 0.0, 0.0]) if abs(first[0]) < 0.9 else np.array([0.0, 0.0, 1.0])
        u = np.cross(first, helper)
        u /= np.linalg.norm(u)
        angles = np.arange(sides) * 2 * math.pi / sides
        rings, normals = [], []
        for k in range(count):
            t = tangent[k]
            u = u - t * (u @ t)
            u /= max(np.linalg.norm(u), 1e-12)
            v = np.cross(t, u)
            radial = np.outer(np.cos(angles), u) + np.outer(np.sin(angles), v)
            # A mitred ring is stretched by 1 / cos(half the bend angle).
            stretch = 1.0
            if 0 < k < count - 1:
                stretch = 1.0 / max(float(segment[k - 1] @ t), 0.5)
            rings.append(path[k] + radial * radii[k] * stretch)
            normals.append(radial)
        positions, normal = np.concatenate(rings), np.concatenate(normals)
        faces = []
        for k in range(count - 1):
            a, b = k * sides, (k + 1) * sides
            for s in range(sides):
                s1 = (s + 1) % sides
                faces += [[a + s, a + s1, b + s1], [a + s, b + s1, b + s]]
        self.add(part, material, positions, normal, faces)
        for end, wanted in ((0, caps[0]), (count - 1, caps[1])):
            if wanted and radii[end] > 1e-6:
                ring = rings[end]
                centre = path[end]
                out = -tangent[end] if end == 0 else tangent[end]
                fan = np.vstack([centre, ring])
                tris = [[0, 1 + (s + 1) % sides, 1 + s] if end == 0 else [0, 1 + s, 1 + (s + 1) % sides] for s in range(sides)]
                self.add(part, material, fan, np.tile(out, (len(fan), 1)), tris)

    def lathe(self, part: str, material: Material, profile, sides: int = 12, centre=(0.0, 0.0)) -> None:
        """A solid of revolution about the vertical axis through `centre` (x, z): profile is (radius, y) pairs from
        the bottom up; the ends are capped where their radius is not 0."""
        profile = np.asarray(profile, dtype=np.float64).reshape(-1, 2)
        angles = np.arange(sides) * 2 * math.pi / sides
        cos, sin = np.cos(angles), np.sin(angles)
        slope = np.gradient(profile[:, 0]), np.gradient(profile[:, 1])
        positions, normals = [], []
        for (radius, y), dr, dy in zip(profile, *slope):
            positions.append(np.column_stack([centre[0] + radius * cos, np.full(sides, y), centre[1] + radius * sin]))
            length = math.hypot(dr, dy) or 1.0
            normals.append(np.column_stack([cos * dy / length, np.full(sides, -dr / length), sin * dy / length]))
        faces = []
        for k in range(len(profile) - 1):
            a, b = k * sides, (k + 1) * sides
            for s in range(sides):
                s1 = (s + 1) % sides
                faces += [[a + s, b + s1, a + s1], [a + s, b + s, b + s1]]
        self.add(part, material, np.concatenate(positions), np.concatenate(normals), faces)
        for end, down in ((0, True), (len(profile) - 1, False)):
            radius, y = profile[end]
            if radius > 1e-6:
                fan = np.vstack([[centre[0], y, centre[1]], positions[end]])
                tris = [[0, 1 + s, 1 + (s + 1) % sides] if down else [0, 1 + (s + 1) % sides, 1 + s] for s in range(sides)]
                self.add(part, material, fan, np.tile([0.0, -1.0 if down else 1.0, 0.0], (len(fan), 1)), tris)

    def box(self, part: str, material: Material, centre, size, yaw: float = 0.0, pitch: float = 0.0) -> None:
        """A box of size (x, y, z) about its centre, turned by yaw about +Y (after a pitch about its own X)."""
        half = np.asarray(size, dtype=np.float64) / 2
        corners = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]) * half
        self.hull8(part, material, _turn(corners, yaw, pitch) + np.asarray(centre, dtype=np.float64))

    def hull8(self, part: str, material: Material, corners) -> None:
        """A closed hexahedron from 8 corners ordered as (x, y, z) sign bits: index = 4 * x + 2 * y + z."""
        corners = np.asarray(corners, dtype=np.float64).reshape(8, 3)
        quads = [[0, 1, 3, 2], [4, 6, 7, 5], [0, 4, 5, 1], [2, 3, 7, 6], [0, 2, 6, 4], [1, 5, 7, 3]]
        tris = [t for a, b, c, d in quads for t in ([a, b, c], [a, c, d])]
        self.flat(part, material, corners, tris)

    def beam(self, part: str, material: Material, start, end, width: float, depth: float, up=(0.0, 1.0, 0.0)) -> None:
        """A rectangular bar from start to end: `width` across (horizontal for a level beam), `depth` along `up`."""
        start, end = np.asarray(start, dtype=np.float64), np.asarray(end, dtype=np.float64)
        axis = end - start
        length = np.linalg.norm(axis)
        if length < 1e-9:
            return
        axis /= length
        up = np.asarray(up, dtype=np.float64)
        side = np.cross(up, axis)
        if np.linalg.norm(side) < 1e-6:
            side = np.cross(np.array([1.0, 0.0, 0.0]), axis)
        side /= np.linalg.norm(side)
        top = np.cross(axis, side)
        corners = [start + axis * (length if sx > 0 else 0) + top * sy * depth / 2 + side * sz * width / 2
                   for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
        self.hull8(part, material, corners)

    def prism(self, part: str, material: Material, outline, z0: float, z1: float, colors=None,
              frame=None, front: bool = True, back: bool = True) -> None:
        """A flat solid: the 2D outline (x, y), counter-clockwise seen from +Z, between z0 (back) and z1 (front).
        `frame` (origin, x axis, y axis, z axis) places it; default is the asset frame."""
        outline = np.asarray(outline, dtype=np.float64).reshape(-1, 2)
        faces = earcut([outline])
        count = len(outline)
        positions, tris = [], []
        if front:
            positions.append(np.column_stack([outline, np.full(count, z1)]))
            tris.append(faces)
        if back:
            offset = sum(len(p) for p in positions)
            positions.append(np.column_stack([outline, np.full(count, z0)]))
            tris.append(faces[:, ::-1] + offset)
        offset = sum(len(p) for p in positions)
        rim = np.vstack([np.column_stack([outline, np.full(count, z1)]), np.column_stack([outline, np.full(count, z0)])])
        positions.append(rim)
        tris.append(np.array([t for k in range(count) for t in (
            [offset + k, offset + count + k, offset + count + (k + 1) % count],
            [offset + k, offset + count + (k + 1) % count, offset + (k + 1) % count])]))
        points = np.concatenate(positions)
        if frame is not None:
            origin, ax, ay, az = (np.asarray(v, dtype=np.float64) for v in frame)
            points = origin + np.outer(points[:, 0], ax) + np.outer(points[:, 1], ay) + np.outer(points[:, 2], az)
        self.flat(part, material, points, np.concatenate(tris), colors)

    def parts(self) -> list[Part]:
        out = []
        for group in self.groups.values():
            colors = None
            if any(c is not None for c in group["c"]):
                colors = np.concatenate([c if c is not None else np.ones((len(p), 3)) for c, p in zip(group["c"], group["p"])])
            uvs = None
            if any(u is not None for u in group["uv"]):
                uvs = np.concatenate([u if u is not None else np.zeros((len(p), 2)) for u, p in zip(group["uv"], group["p"])])
            out.append(Part(group["part"], np.round(np.concatenate(group["p"]), 5), np.concatenate(group["t"]),
                            group["material"], normals=np.round(np.concatenate(group["n"]), 4), colors=colors, uvs=uvs))
        return out


def _turn(points: np.ndarray, yaw: float, pitch: float = 0.0) -> np.ndarray:
    points = np.asarray(points, dtype=np.float64)
    if pitch:
        c, s = math.cos(pitch), math.sin(pitch)
        points = points @ np.array([[1, 0, 0], [0, c, -s], [0, s, c]]).T
    if yaw:
        c, s = math.cos(yaw), math.sin(yaw)
        points = points @ np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]).T
    return points


def bounds(parts: list[Part]) -> tuple[np.ndarray, np.ndarray]:
    points = np.concatenate([p.positions for p in parts])
    return points.min(axis=0), points.max(axis=0)


def open_edges(parts: list[Part]) -> tuple[int, int, int]:
    """(edges not shared by exactly two triangles, triangles whose winding disagrees with their normals, triangles)
    of an asset, with vertices welded by position: all zero but the last for closed, outward-facing solids."""
    weld: dict[tuple, int] = {}
    edges: dict[tuple[int, int], int] = {}
    flipped = total = 0
    for part in parts:
        positions = np.round(np.asarray(part.positions, dtype=np.float64), 4)
        index = np.array([weld.setdefault(tuple(p), len(weld)) for p in positions])
        for tri in np.asarray(part.triangles):
            a, b, c = positions[tri]
            normal = np.cross(b - a, c - a)
            if np.linalg.norm(normal) < 1e-12:
                continue
            total += 1
            if part.normals is not None and float(normal @ np.asarray(part.normals)[tri].sum(axis=0)) < 0:
                flipped += 1
            for i, j in ((0, 1), (1, 2), (2, 0)):
                key = (int(index[tri[i]]), int(index[tri[j]]))
                edges[key] = edges.get(key, 0) + 1
    bad = sum(1 for (i, j), n in edges.items() if n != 1 or edges.get((j, i), 0) != 1)
    return bad, flipped, total
