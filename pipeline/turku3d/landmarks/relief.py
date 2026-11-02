"""Cut physical arch recesses and sweep cornices while retaining separate collision."""

from dataclasses import replace

import numpy as np
import shapely

from turku3d.package.gltf import GlbMesh
from turku3d.wall_surfaces import trapezoids

from .layout import arch_polygon


def apply(mesh, base, description, tower, polygon, plan):
    """The wall and its inset use the original PBR atlas; all stone returns share one material."""
    d = description
    v = mesh.positions
    width = float(np.linalg.norm((v[1] - v[0])[[0, 2]]))
    low, high = float(v[:, 1].min()), float(v[:, 1].max())
    height = high - low
    direction = v[1] - v[0]
    direction[1] = 0
    direction /= width
    normal = np.array([direction[2], 0, -direction[0]])
    origin = v[0].copy()
    face = polygon
    holes = [arch_polygon(cx, base + sill, w, h, pointed) for cx, sill, w, h, pointed in plan]
    painted, stone = [], []

    def points(xy, depth=0):
        p = origin + xy[:, :1] * direction + normal * depth
        p[:, 1] = xy[:, 1]
        return p

    def painted_polygon(polygon, depth=0):
        for xy in trapezoids(polygon):
            painted.append((points(xy, depth), np.c_[xy[:, 0] / width, (high - xy[:, 1]) / height]))

    painted_polygon(face.difference(shapely.union_all(holes)) if holes else face)
    for polygon in holes:
        depth = -min(0.42, (polygon.bounds[2] - polygon.bounds[0]) * 0.2)
        painted_polygon(polygon, depth)
        ring = np.asarray(polygon.exterior.coords)
        front, back = points(ring), points(ring, depth)
        for i in range(len(ring) - 1):
            stone.append(np.array([front[i], back[i], back[i + 1], front[i + 1]]))
    if tower:
        for level in d["tower"]["cornices_m"]:
            y = base + level
            band = shapely.box(0, y, width, y + 0.24)
            if not face.covers(band):
                continue
            xy = np.array([[0, y], [width, y], [width, y + 0.24], [0, y + 0.24]])
            back, front = points(xy), points(xy, 0.28)
            stone.append(front)
            for i in range(4):
                j = (i + 1) % 4
                stone.append(np.array([back[i], back[j], front[j], front[i]]))

    def indices(count):
        return (np.arange(count)[:, None, None] * 4 + np.array([[0, 3, 2], [0, 2, 1]])).reshape(
            -1, 3
        )

    wall = replace(
        mesh,
        positions=np.concatenate([p for p, _ in painted]),
        uvs=np.concatenate([uv for _, uv in painted]),
        indices=indices(len(painted)),
    )
    details = None
    if stone:
        colour = np.asarray(d["trim_colour"]) / 255
        colour = np.where(colour <= 0.04045, colour / 12.92, ((colour + 0.055) / 1.055) ** 2.4)
        details = GlbMesh(
            mesh.name + "_stone_relief",
            np.concatenate(stone),
            indices(len(stone)),
            color=(*colour, 1),
            extras={**mesh.extras, "material_key": "landmark_stone_relief"},
        )
    return wall, details
