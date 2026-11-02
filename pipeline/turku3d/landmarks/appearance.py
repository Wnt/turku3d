"""Custom per-building appearance entry points, independent of generic facade programs."""

import json

import numpy as np

from turku3d.package.gltf import GlbMesh

from .descriptions import resolve
from .procedural import copper, maps, wall_maps


def wall_meshes(building, walls, origin, description=None):
    d = description or resolve(building)
    if d is None:
        return None
    result = []
    for index, vertices in enumerate(np.asarray(walls).reshape(-1, 4, 3)):
        width = float(np.linalg.norm(vertices[1, [0, 2]] - vertices[0, [0, 2]]))
        low, high = float(vertices[:, 1].min()), float(vertices[:, 1].max())
        if width < 0.08 or high - low < 0.08:
            continue
        tower = (
            high - building.base > d["tower"]["minimum_height_m"]
            and width < d["tower"]["maximum_width_m"]
        )
        albedo, normal, orm = wall_maps(
            round(width, 3),
            round(high - low, 3),
            round(low - building.base, 3),
            json.dumps(d, sort_keys=True),
            tower,
        )
        uv = np.array(
            [
                [0, (high - vertices[0, 1]) / (high - low)],
                [1, (high - vertices[1, 1]) / (high - low)],
                [1, (high - vertices[2, 1]) / (high - low)],
                [0, (high - vertices[3, 1]) / (high - low)],
            ]
        )
        result.append(
            GlbMesh(
                f"landmark_{building.key}_{index}",
                vertices - origin,
                np.array([[0, 3, 2], [0, 2, 1]], np.uint32),
                uvs=uv,
                image=albedo,
                normal_image=normal,
                orm_image=orm,
                extras=dict(
                    layer="buildings",
                    object_id=f"landmark_{building.key}_{index}",
                    building_id=building.facade_key,
                    material_key="landmark_procedural",
                    surface="wall",
                    texture_uv="normalized",
                    landmark_description=d["content_hash"],
                    landmark_tower=tower,
                ),
            )
        )
    return result


def roof_mesh(building, roof, indices, origin, description=None):
    d = description or resolve(building)
    if d is None:
        return None
    albedo, normal, orm = maps(*copper(4, 4, d["roof"], 11), 4, 4, metal=True)
    # Per-triangle planar physical UVs keep seams on steep spires as well as roofs.
    triangles = roof[np.asarray(indices)]
    vertices = triangles.reshape(-1, 3)
    uv = []
    for tri in triangles:
        axis = tri[1] - tri[0]
        axis /= max(np.linalg.norm(axis), 1e-9)
        normal_axis = np.cross(axis, tri[2] - tri[0])
        normal_axis /= max(np.linalg.norm(normal_axis), 1e-9)
        vertical = np.cross(normal_axis, axis)
        uv.extend(np.column_stack((tri @ axis, tri @ vertical)) / 4)
    return GlbMesh(
        f"landmark_roof_{building.key}",
        vertices - origin,
        np.arange(len(vertices), dtype=np.uint32).reshape(-1, 3),
        uvs=np.asarray(uv),
        image=albedo,
        normal_image=normal,
        orm_image=orm,
        repeat=True,
        extras=dict(
            layer="buildings",
            object_id=f"landmark_roof_{building.key}",
            building_id=building.facade_key,
            material_key="landmark_copper",
            surface="roof",
            landmark_description=d["content_hash"],
        ),
    )
