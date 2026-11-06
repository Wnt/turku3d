"""Custom per-building appearance entry points, independent of generic facade programs."""

import json

import numpy as np

from turku3d.package.gltf import GlbMesh, vertex_normals

from .atlas import batch, merge
from .descriptions import resolve
from .procedural import copper_start, maps, material_maps, roof_surface, wall_maps, window_rows


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
    """Narrow fragments share maps, but never stretch spire metal below its stage."""
    import shapely

    from turku3d.wall_surfaces import trapezoids

    d = description
    for q in visible_walls(building, walls):
        start = q[0, [0, 2]]
        direction = q[1, [0, 2]] - start
        width = np.linalg.norm(direction)
        if width >= 3:
            yield q, None
            continue
        if width < 0.001:
            continue
        direction /= width
        low, high = q[:, 1].min(), q[:, 1].max()
        tower = high - building.base > d["tower"]["minimum_height_m"]
        stone = building.base + (d["tower"]["stone_top_m"] if tower else 1.5)
        copper = building.base + copper_start(d)
        levels = sorted(set([low, high, *[v for v in (stone, copper) if low < v < high]]))
        face = shapely.Polygon(np.c_[[0, width, width, 0], q[:, 1]])
        for lo, hi in zip(levels[:-1], levels[1:], strict=True):
            kind = "roof" if lo >= copper - 0.001 else "stone" if hi <= stone + 0.001 else "wall"
            for xy in trapezoids(face.intersection(shapely.box(0, lo, width, hi))):
                points = np.empty((4, 3))
                points[:, [0, 2]] = start + xy[:, :1] * direction
                points[:, 1] = xy[:, 1]
                yield points, kind


def facade_lines(building, tolerance=0.5):
    """The footprint rings simplified to their facade lines: (start, unit axis, length) in x/z."""
    footprint = getattr(building, "footprint", None)
    lines = []
    if footprint is None:
        return lines
    for polygon in getattr(footprint, "geoms", [footprint]):
        for ring in [polygon.exterior, *polygon.interiors]:
            points = np.asarray(ring.simplify(tolerance).coords)[:, :2]
            for a, b in zip(points[:-1], points[1:], strict=True):
                length = float(np.linalg.norm(b - a))
                if length > 1e-3:
                    lines.append((a, (b - a) / length, length))
    return lines


def facade_line(vertices, lines, tolerance=0.6):
    """Index of the facade line a wall piece lies on (parallel, within ``tolerance`` m), or None."""
    a, b = vertices[0, [0, 2]], vertices[1, [0, 2]]
    width = np.linalg.norm(b - a)
    best = None
    for index, (start, axis, length) in enumerate(lines):
        if abs((b - a) @ axis) < 0.94 * width:
            continue
        normal = np.array([-axis[1], axis[0]])
        offset = max(abs((a - start) @ normal), abs((b - start) @ normal))
        along = sorted([(a - start) @ axis, (b - start) @ axis])
        if offset < tolerance and along[0] > -tolerance and along[1] < length + tolerance:
            if best is None or offset < best[0]:
                best = (offset, index)
    return None if best is None else best[1]


def body_height(building, walls):
    """Width-weighted median top of the visible walls above the base: the main body's height."""
    walls = np.asarray(walls).reshape(-1, 4, 3)
    if not len(walls):
        return 0.0
    widths = np.linalg.norm(walls[:, 1, [0, 2]] - walls[:, 0, [0, 2]], axis=1)
    tops = walls[:, :, 1].max(axis=1) - building.base
    order = np.argsort(tops)
    cumulative = np.cumsum(widths[order])
    return float(tops[order][np.searchsorted(cumulative, cumulative[-1] / 2)])


def wall_meshes(building, walls, origin, description=None):
    d = description or resolve(building)
    if d is None or "wall" not in d.get("surfaces", ["wall", "roof"]):
        return None
    if d.get("elevations"):
        from .elevations import coordinates, select, texture
        from .relief import meshes as relief_meshes

        result = []
        visible = visible_walls(building, walls)
        for index, vertices in enumerate(visible):
            row = select(vertices, d["elevations"])
            if row is None:
                albedo, normal, orm = material_maps(json.dumps(d, sort_keys=True), "wall")
                direction = vertices[1, [0, 2]] - vertices[0, [0, 2]]
                direction /= np.linalg.norm(direction)
                uv = np.c_[vertices[:, [0, 2]] @ direction / 4, -vertices[:, 1] / 4]
            else:
                albedo, normal, orm = texture(json.dumps(d, sort_keys=True), json.dumps(row, sort_keys=True))
                uv = coordinates(vertices, building.base, row)
            result.append(GlbMesh(
                f"landmark_{building.key}_{index}", vertices - origin,
                np.array([[0, 3, 2], [0, 2, 1]], np.uint32),
                uvs=uv, image=albedo,
                normal_image=normal, orm_image=orm, repeat=row is None,
                extras=dict(layer="buildings", building_id=building.facade_key,
                            object_id=f"landmark_{building.key}_{index}",
                            material_key="landmark_procedural", surface="wall",
                            texture_uv="normalized", landmark_description=d["content_hash"]),
            ))
        result.extend(relief_meshes(building, visible, origin, d))
        groups = {}
        for mesh in result:
            groups.setdefault((mesh.image, mesh.repeat), []).append(mesh)
        return batch([merge(group) for group in groups.values()])
    parts = list(material_wall_parts(building, walls, d))
    windows = dict(d["windows"], rows_m=window_rows(
        d["windows"], body_height(building, [q for q, _ in parts]),
        getattr(building, "attributes", {}).get("levels")))
    d = dict(d, windows=windows)
    lines = facade_lines(building)
    result, shared = [], {}
    for index, (vertices, band_kind) in enumerate(parts):
        width = float(np.linalg.norm(vertices[1, [0, 2]] - vertices[0, [0, 2]]))
        low, high = float(vertices[:, 1].min()), float(vertices[:, 1].max())
        if width < 0.08 or high - low < 0.08:
            continue
        tower = (
            high - building.base > d["tower"]["minimum_height_m"]
            and width < d["tower"]["maximum_width_m"]
        )
        extras = dict(
            layer="buildings",
            object_id=f"landmark_{building.key}_{index}",
            building_id=building.facade_key,
            material_key="landmark_procedural",
            surface="wall",
            texture_uv="normalized",
            landmark_description=d["content_hash"],
            landmark_tower=tower,
        )
        # Survey walls break into pieces at every roof part; the pieces of one facade line share
        # that line's openings instead of each narrow piece falling back to plain material.
        if not tower and band_kind in (None, "wall"):
            line = facade_line(vertices, lines)
            if line is not None and lines[line][2] >= 3:
                shared.setdefault(line, []).append((vertices, extras))
                continue
        repeat = width < 3 or high - low < 1.5
        direction = vertices[1, [0, 2]] - vertices[0, [0, 2]]
        outward = np.array([direction[1], -direction[0]]) / width
        wall_description = dict(
            d, show_entrance=bool(d.get("entrance") and tower and outward[0] < -0.9)
        )
        albedo, normal, orm = (
            (None, None, None)
            if repeat
            else wall_maps(
                round(width, 3),
                round(high - low, 3),
                round(low - building.base, 3),
                json.dumps(wall_description, sort_keys=True),
                tower,
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
                if high - building.base > copper_start(d)
                else "stone"
                if high - building.base < 1.5
                else "wall"
            )
            albedo, normal, orm = material_maps(json.dumps(d, sort_keys=True), kind)
            uv[:, 0] *= width / 4
            uv[:, 1] = (high - uv[:, 1] * (high - low)) / 4
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
                extras=extras,
            )
        )
    # Shared lines span whole facades: one texel budget per building bounds their atlas pages.
    extent = sum(lines[line][2] * (max(v[:, 1].max() for v, _ in members) - min(v[:, 1].min() for v, _ in members))
                 for line, members in shared.items())
    density = int(np.clip(np.sqrt(LINE_TEXELS / max(extent, 1e-6)), 16, 40))
    for line, members in sorted(shared.items()):
        result.append(line_mesh(building, lines[line], members, origin, d, density))
    return night_wash(batch(result), building, origin, d)


LINE_TEXELS = 3 * 2048 * 2048  # three atlas pages of facade lines per landmark


def line_mesh(building, line, members, origin, description, density=40):
    """One facade line's pieces as one mesh sampling one texture in line metres."""
    start, axis, length = line
    # Outward-wound pieces: texture x runs along their own direction, so it reads from outside.
    if sum((v[1, [0, 2]] - v[0, [0, 2]]) @ axis for v, _ in members) < 0:
        start, axis = start + axis * length, -axis
    low = min(float(v[:, 1].min()) for v, _ in members)
    high = max(float(v[:, 1].max()) for v, _ in members)
    albedo, normal, orm = wall_maps(
        round(length, 3),
        round(high - low, 3),
        round(low - building.base, 3),
        json.dumps(dict(description, show_entrance=False), sort_keys=True),
        False,
        density=density,
    )
    meshes = []
    for vertices, extras in members:
        x = (vertices[:, [0, 2]] - start) @ axis / length
        uv = np.c_[np.clip(x, 0, 1), (high - vertices[:, 1]) / (high - low)]
        meshes.append(GlbMesh(
            extras["object_id"], vertices - origin, np.array([[0, 3, 2], [0, 2, 1]], np.uint32),
            uvs=uv, image=albedo, normal_image=normal, orm_image=orm, repeat=False, extras=extras,
        ))
    return merge(meshes)


def roof_mesh(building, roof, indices, origin, description=None):
    d = description or resolve(building)
    if d is None or "roof" not in d.get("surfaces", ["wall", "roof"]):
        return None
    albedo, normal, orm = maps(
        *roof_surface(4, 4, d["roof"], 11),
        4,
        4,
        metal=d["roof"]["material"] in ("copper", "metal", "zinc"),
    )
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
            material_key="landmark_roof_" + d["roof"]["material"],
            surface="roof",
            landmark_description=d["content_hash"],
        ),
    )


def split_night_levels(mesh, levels):
    """Split only triangles crossing lighting boundaries, interpolating authored attributes."""
    fields = ['positions', 'normals', 'uvs', 'colors', 'baked_visibility']
    fields = [name for name in fields if getattr(mesh, name) is not None]
    output = {name: list(getattr(mesh, name)) for name in fields}
    triangles = []
    for indices in mesh.indices:
        points = mesh.positions[indices]
        cuts = [y for y in levels if points[:, 1].min() < y < points[:, 1].max()]
        if not cuts:
            triangles.append(indices)
            continue
        polygons = [list(np.eye(3))]
        for y in cuts:
            next_polygons = []
            for polygon in polygons:
                for side in (-1, 1):
                    clipped = []
                    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
                        da, db = a @ points[:, 1] - y, b @ points[:, 1] - y
                        if side * da >= 0:
                            clipped.append(a)
                        if da * db < 0:
                            clipped.append(a + (b-a) * da/(da-db))
                    if len(clipped) >= 3:
                        next_polygons.append(clipped)
            polygons = next_polygons
        for polygon in polygons:
            offset = len(output['positions'])
            weights = np.asarray(polygon)
            for name in fields:
                output[name].extend(weights @ getattr(mesh, name)[indices])
            triangles.extend([offset, offset+i, offset+i+1] for i in range(1, len(polygon)-1))
    used, remap = np.unique(np.asarray(triangles).ravel(), return_inverse=True)
    for name in fields:
        setattr(mesh, name, np.asarray(output[name])[used])
    mesh.indices = remap.reshape(-1, 3).astype(np.uint32)


def night_wash(meshes, building, origin, description):
    """Bake a reviewed landmark's diffuse wall contribution; copper and roofs stay unlit."""
    profile = description.get('night_lighting')
    if not profile:
        return meshes
    start, end = profile.get('tower_ramp_m', [12, 28])
    copper = copper_start(description)
    fade = profile.get('copper_fade_m', 3)
    for mesh in meshes:
        split_night_levels(mesh, [h + building.base - origin[1]
                                 for h in (start, end, copper-fade, copper) if np.isfinite(h)])
        height = mesh.positions[:, 1] + origin[1] - building.base
        normals = vertex_normals(mesh.positions, mesh.indices)
        facing = np.maximum.reduce([np.maximum(0, normals[:, [0, 2]] @ np.asarray(d))
                                    for d in profile['directions_xz']])
        # Survey geometry carries height; the description owns the masonry/copper transition.
        upper = np.clip((height - start) / (end-start), 0, 1)
        energy = profile['body_intensity'] * (1-upper) + profile['tower_intensity'] * upper
        energy *= np.clip((copper-height)/fade, 0, 1) * (.2+.8*facing)
        mesh.night_irradiance = energy[:, None] * np.asarray(profile['color_linear'])
        mesh.extras['material_key'] = 'night_landmark_wall'
    return meshes
