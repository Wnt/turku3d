"""Custom per-building appearance entry points, independent of generic facade programs."""

import json

import numpy as np

from turku3d.package.gltf import GlbMesh

from .atlas import batch, merge
from .descriptions import resolve
from .faces import Face, architectural_faces
from .layout import accepted_openings
from .procedural import maps, material_maps, roof_surface, wall_maps
from .relief import apply as apply_relief


def visible_walls(building, walls):
    """Trim roof-part closure walls hidden inside the surveyed building volume.

    Collision keeps its closed solids. Three outside probes must all find a roof
    before raising a visual wall's base; this leaves uncertain edges untouched.
    """
    import shapely

    from turku3d.coarse.roofs import plane_height

    parts = getattr(building, "roof_parts", [])
    if not parts:
        return np.asarray(walls).reshape(-1, 4, 3)
    polygons = [p for p, _ in parts]
    tree = shapely.STRtree(polygons)
    result = []
    for original in np.asarray(walls).reshape(-1, 4, 3):
        q = original.copy()
        a, b = q[0, [0, 2]], q[1, [0, 2]]
        direction = b - a
        length = np.linalg.norm(direction)
        if length < 0.001:
            continue
        outward = np.array([direction[1], -direction[0]]) / length
        floors = []
        for t in (0.02, 0.5, 0.98):
            xz = a + (b - a) * t + outward * 0.03
            ids = tree.query(shapely.Point(xz), predicate="within")
            floors.append(
                max(
                    [float(plane_height(parts[i][1], xz[None, :])[0]) for i in ids],
                    default=building.base,
                )
            )
        floor = min(floors)
        if floor >= q[2:, 1].max() - 0.02:
            continue
        if floor < q[2:, 1].min() - 0.02:
            q[:2, 1] = np.maximum(q[:2, 1], floor)
        result.append(q)
    return np.asarray(result).reshape(-1, 4, 3)


def material_wall_parts(building, walls, description):
    """Plan whole connected facades before splitting narrow material bands."""
    import shapely

    d = description
    for face in architectural_faces(visible_walls(building, walls)):
        if face.width >= 3:
            yield face, None
            continue
        low, high = face.low, face.high
        tower = high - building.base > d["tower"]["minimum_height_m"]
        stone = building.base + (d["tower"]["stone_top_m"] if tower else 1.5)
        copper = building.base + d["tower"]["copper_start_m"]
        levels = sorted(set([low, high, *[v for v in (stone, copper) if low < v < high]]))
        for lo, hi in zip(levels[:-1], levels[1:], strict=True):
            kind = "roof" if lo >= copper - 0.001 else "stone" if hi <= stone + 0.001 else "wall"
            clipped = face.polygon.intersection(shapely.box(0, lo, face.width, hi))
            for polygon in shapely.get_parts(clipped):
                if polygon.geom_type == "Polygon" and polygon.area > 0.001:
                    yield (
                        Face(polygon, face.start, face.direction, face.identity + f"_{lo:.3f}"),
                        kind,
                    )


def wall_meshes(building, walls, origin, description=None):
    d = description or resolve(building)
    if d is None:
        return None
    result = []
    relief = []
    for index, (face, band_kind) in enumerate(material_wall_parts(building, walls, d)):
        vertices = face.rectangle()
        width = float(np.linalg.norm(vertices[1, [0, 2]] - vertices[0, [0, 2]]))
        low, high = float(vertices[:, 1].min()), float(vertices[:, 1].max())
        if width < 0.08 or high - low < 0.08:
            continue
        tower = (
            high - building.base > d["tower"]["minimum_height_m"]
            and width < d["tower"]["maximum_width_m"]
        )
        direction = vertices[1, [0, 2]] - vertices[0, [0, 2]]
        outward = np.array([direction[1], -direction[0]]) / width
        wall_description = dict(
            d, show_entrance=bool(d.get("entrance") and tower and outward[0] < -0.9)
        )
        plan = accepted_openings(
            width,
            high - low,
            low - building.base,
            wall_description,
            tower,
            face.polygon,
            building.base,
        )
        repeat = not plan and (width < 3 or high - low < 1.5)
        albedo, normal, orm = (
            (None, None, None)
            if repeat
            else wall_maps(
                round(width, 3),
                round(high - low, 3),
                round(low - building.base, 3),
                json.dumps(wall_description, sort_keys=True),
                tower,
                json.dumps(plan),
            )
        )
        uv = np.array(
            [
                [0, (high - vertices[0, 1]) / (high - low)],
                [1, (high - vertices[1, 1]) / (high - low)],
                [1, (high - vertices[2, 1]) / (high - low)],
                [0, (high - vertices[3, 1]) / (high - low)],
            ]
        )
        if repeat:
            kind = band_kind or (
                "roof"
                if high - building.base > d["tower"]["copper_start_m"]
                else "stone"
                if high - building.base < 1.5
                else "wall"
            )
            albedo, normal, orm = material_maps(json.dumps(d, sort_keys=True), kind)
        result.append(
            GlbMesh(
                f"landmark_{building.key}_{index}",
                vertices - origin,
                np.array([[0, 3, 2], [0, 2, 1]], np.uint32),
                uvs=uv,
                image=albedo,
                normal_image=normal,
                orm_image=orm,
                repeat=repeat,
                extras=dict(
                    layer="buildings",
                    object_id=f"landmark_{building.key}_{index}",
                    building_id=building.facade_key,
                    material_key="landmark_procedural",
                    surface="wall",
                    texture_uv="normalized",
                    landmark_description=d["content_hash"],
                    landmark_tower=tower,
                    landmark_face=face.identity,
                ),
            )
        )
        from shapely.affinity import translate

        result[-1], detail = apply_relief(
            result[-1],
            building.base - origin[1],
            wall_description,
            tower and not repeat,
            translate(face.polygon, yoff=-origin[1]),
            plan,
        )
        if repeat:
            result[-1].uvs[:, 0] *= width / 4
            result[-1].uvs[:, 1] = (high - result[-1].uvs[:, 1] * (high - low)) / 4
        if detail is not None:
            relief.append(detail)
    return batch(result) + ([merge(relief)] if relief else [])


def roof_mesh(building, roof, indices, origin, description=None):
    d = description or resolve(building)
    if d is None:
        return None
    albedo, normal, orm = maps(
        *roof_surface(4, 4, d["roof"], 11),
        4,
        4,
        metal=d["roof"]["material"] in ("copper", "metal", "zinc"),
    )
    # Gravity and the plane normal define a common frame across coplanar triangles.
    triangles = roof[np.asarray(indices)]
    vertices = triangles.reshape(-1, 3)
    uv = []
    for tri in triangles:
        normal_axis = np.cross(tri[1] - tri[0], tri[2] - tri[0])
        normal_axis /= max(np.linalg.norm(normal_axis), 1e-9)
        axis = np.cross([0, 1, 0], normal_axis)
        if np.linalg.norm(axis) < 1e-6:
            axis = np.array([1.0, 0, 0])
        axis /= np.linalg.norm(axis)
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
            material_key="landmark_roof_" + d["roof"]["material"],
            surface="roof",
            landmark_description=d["content_hash"],
        ),
    )
