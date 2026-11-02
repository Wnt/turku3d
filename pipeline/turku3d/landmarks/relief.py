"""Measured elevation profiles extruded into shared masonry/trim batches.

The same metre-space parameters work for cornices, pediments and pilasters on
any described building. Anchors assign each component to one surveyed wall,
including buildings assembled from several roof/footprint fragments.
"""

import json

import numpy as np
import shapely

from turku3d.package.gltf import GlbMesh

from .elevations import coordinates, select, texture
from .procedural import material_maps


def profile(part):
    if "path_m" in part:
        return shapely.LineString(part["path_m"]).buffer(
            part["width_m"] / 2, cap_style="flat", join_style="mitre"
        )
    return shapely.Polygon(part["polygon_m"], part.get("holes_m"))


def extrude(polygon, back, front):
    """Independent triangles keep hard coping edges; holes retain inward sides."""
    polygon = shapely.orient_polygons(polygon)
    faces, sides = [], []
    for triangle in shapely.get_parts(shapely.constrained_delaunay_triangles(polygon)):
        xy = np.asarray(shapely.orient_polygons(triangle).exterior.coords)[:3]
        faces.append(np.c_[xy[::-1], np.full(3, front)])
        sides.append(np.c_[xy, np.full(3, back)])
    for poly in shapely.get_parts(polygon):
        for ring in [poly.exterior, *poly.interiors]:
            for a, b in zip(ring.coords[:-1], ring.coords[1:], strict=True):
                quad = np.array([[*a, back], [*b, back], [*b, front], [*a, front]])
                sides.extend([quad[[0, 2, 1]], quad[[0, 3, 2]]])
    return np.asarray(faces).reshape(-1, 3), np.asarray(sides).reshape(-1, 3)


def meshes(building, walls, origin, description):
    d = description
    result = []
    for number, row in enumerate(d["elevations"]):
        if not row.get("relief"):
            continue
        owned = []
        for wall in walls:
            if select(wall, d["elevations"]) is not row:
                continue
            uv = coordinates(wall, building.base, row)
            outline = shapely.Polygon(uv * [row["width_m"], -row["height_m"]]
                                      + [0, row["height_m"]])
            plane = float(np.median((wall[:, [0, 2]] - row["origin_xz"]) @ row["normal_xz"]))
            owned.append((outline, plane))
        if not owned:
            continue
        for index, part in enumerate(row["relief"]):
            polygon = profile(part)
            if polygon.is_empty or not polygon.is_valid:
                raise ValueError(f"invalid elevation relief {number}/{index}")
            anchor = shapely.Point(part["anchor_m"])
            planes = [plane for outline, plane in owned if outline.covers(anchor)]
            if not planes:
                continue
            # Elevation origins locate the texture, not necessarily the actual
            # surveyed wall plane (setbacks and multipart fronts are common).
            plane = max(planes)
            back = part.get("offset_m", .025)
            front = back + part["depth_m"]
            if not 0 < part["depth_m"] <= 2:
                raise ValueError("relief depth must be in (0, 2] metres")
            face, edges = extrude(polygon, back, front)
            kind = part.get("material", "trim")
            for surface, local in ((kind, face), ("brick" if kind == "elevation" else kind, edges)):
                axis, normal = np.asarray(row["axis_xz"]), np.asarray(row["normal_xz"])
                xz = row["origin_xz"] + local[:, :1] * axis + (local[:, 2:] + plane) * normal
                world = np.c_[xz[:, 0], building.base + row.get("base_offset_m", 0) + local[:, 1], xz[:, 1]]
                if surface == "elevation":
                    images = texture(json.dumps(d, sort_keys=True), json.dumps(row, sort_keys=True))
                    uv = coordinates(world, building.base, row)
                else:
                    material = d if surface == "brick" else {
                        **d, "wall_material": "render", "brick": {**d["brick"], "colour": d["trim_colour"]}
                    }
                    images = material_maps(json.dumps(material, sort_keys=True), "wall")
                    triangles = world.reshape(-1, 3, 3)
                    face_normal = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
                    projection = np.argmax(np.abs(face_normal), axis=1)
                    uv = np.concatenate([t[:, [2, 1] if p == 0 else [0, 2] if p == 1 else [0, 1]] / 4
                                         for t, p in zip(triangles, projection, strict=True)])
                result.append(GlbMesh(
                    f"relief_{building.key}_{number}_{index}_{surface}", world - origin,
                    np.arange(len(world), dtype=np.uint32).reshape(-1, 3), uvs=uv,
                    image=images[0], normal_image=images[1], orm_image=images[2],
                    repeat=surface != "elevation",
                    extras=dict(layer="buildings", building_id=building.facade_key,
                                material_key="landmark_procedural", surface="wall", facade_relief=True,
                                landmark_description=d["content_hash"], texture_uv="normalized"),
                ))
    return result
