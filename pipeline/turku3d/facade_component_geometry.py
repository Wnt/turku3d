"""Wall-local low-poly components, paint-preserving cuts and simple solid collision.

The expanded source meshes are checked by wall_surfaces before packaging converts
repeated boxes into GPU instances. All coordinates are metres in the surveyed wall
frame; no shell, generated map or photographic description is overwritten.
"""

import numpy as np
import shapely

from turku3d import config as C
from turku3d.package.gltf import GlbMesh
from turku3d.wall_surfaces import trapezoids

TRI = np.array([[0, 3, 2], [0, 2, 1]], np.uint32)
COLOURS = dict(
    slab=(0.55, 0.53, 0.49, 1),
    rail=(0.16, 0.18, 0.18, 1),
    glass=(0.12, 0.23, 0.28, 1),
    wall=(0.55, 0.53, 0.49, 1),
)


def frame(vertices):
    tangent = vertices[1] - vertices[0]
    tangent[1] = 0
    tangent /= np.linalg.norm(tangent)
    return tangent, np.array([tangent[2], 0, -tangent[0]])


def plan(vertices, wall):
    """Admit only components contained by this surviving roof/passage wall fragment.

    This prevents decoration bridging a mapped through-passage or duplicated LOD2
    part. Partial/occluded fragments stay unresolved rather than recreating a wall.
    """
    descriptors = wall.get("geometry_components", [])
    if not descriptors:
        return []
    g = wall.get("measured_geometry", wall.get("geometry", {}))
    coordinates = g.get("coordinates") or [g["p0"], g["p1"]]
    a = np.array(
        [
            coordinates[0][0] - C.ORIGIN_E,
            g.get("base_h", vertices[0, 1]),
            C.ORIGIN_N - coordinates[0][1],
        ]
    )
    b = np.array([coordinates[-1][0] - C.ORIGIN_E, a[1], C.ORIGIN_N - coordinates[-1][1]])
    width = np.linalg.norm(b - a)
    _, height = wall.get("measured_size", [wall["width_m"], wall["height_m"]])
    tangent, normal = frame(vertices)
    sign = 1 if (b - a) @ tangent > 0 else -1
    offset = (a - vertices[0]) @ tangent
    length = (vertices[1] - vertices[0]) @ tangent
    boundary = shapely.Polygon(np.c_[[0, length, length, 0], vertices[:, 1]])
    admitted, occupied = [], shapely.Polygon()
    for desc in descriptors:
        if desc["kind"] in {"passage", "artwork"}:
            continue  # Mapped passages are already cut by tunnels; artwork is only a slot.
        x, y, w, h = desc["bounds"]
        left, right = sorted([offset + sign * x * width, offset + sign * (x + w) * width])
        bottom, top = a[1] + y * height, a[1] + (y + h) * height
        # Insets keep corner returns/slabs clear of neighbouring wall and roof faces.
        left, right = left + 0.04, right - 0.04
        bottom, top = bottom + 0.04, top - 0.04
        rect = shapely.box(left, bottom, right, top)
        if (
            right - left < 0.3
            or top - bottom < 0.3
            or not boundary.buffer(1e-6).covers(rect)
            or rect.intersection(occupied).area > 1e-6
        ):
            continue
        if desc["kind"] == "stairs" and (
            float(desc["rise_m"]) > top - bottom or float(desc["run_m"]) > right - left
        ):
            continue
        occupied = occupied.union(rect)
        admitted.append(
            {
                **desc,
                "rect": rect,
                "metric": [left, bottom, right, top],
                "tangent": tangent,
                "normal": normal,
            }
        )
    return admitted


def surface_parts(points, uv, plans, *, collision=False):
    """Replace the occupied patch, reusing its UV frame on the displaced face."""
    if not plans:
        yield points, uv
        return
    tangent, normal = frame(points)
    # Plans use the original wall frame, whereas paint patches may start inside it.
    reference = plans[0]["origin"]
    local = np.c_[(points - reference) @ tangent, points[:, 1]]
    surface = shapely.Polygon(local)
    cuts = [
        p
        for p in plans
        if p["kind"] in {"bay", "recess", "loggia", "arcade"}
        and (not collision or p["kind"] == "arcade")
    ]
    regions = [(surface.difference(shapely.union_all([p["rect"] for p in cuts])), 0)]
    for p in cuts:
        depth = p["depth_m"] * (1 if p["kind"] == "bay" else -1)
        regions.append((surface.intersection(p["rect"]), depth))
    # UV is affine on each existing wall paint quad, including stored-map clamping.
    matrix = np.c_[local, np.ones(len(local))]
    transform = np.linalg.lstsq(matrix, uv, rcond=None)[0]
    for region, depth in regions:
        for xy in trapezoids(region):
            out = reference + xy[:, :1] * tangent + depth * normal
            out[:, 1] = xy[:, 1]
            yield out, np.c_[xy, np.ones(len(xy))] @ transform


def quad_mesh(points, name, material, building, origin, **extras):
    return GlbMesh(
        name,
        np.asarray(points) - origin,
        TRI.copy(),
        color=COLOURS[material],
        roughness=0.28 if material == "glass" else 0.85,
        extras=dict(
            layer="buildings",
            object_id=name,
            surface="facade-component",
            building_id=building,
            material_key=f"component_{material}",
            **extras,
        ),
    )


def geometry(vertices, plans, building, origin):
    """Return visible meshes and hit boxes; no decorative collider fills an opening."""
    import hashlib

    # A building can emit the same component index on several surveyed walls.
    wall_id = hashlib.sha256(np.asarray(vertices, dtype="<f8").tobytes()).hexdigest()[:16]
    identity = f"component_{building}_{wall_id}"
    meshes, boxes = [], []
    if not plans:
        return meshes, boxes
    tangent, normal = frame(vertices)
    reference = vertices[0].copy()
    reference[1] = 0

    def world(xyz):
        xyz = np.asarray(xyz, float)
        out = reference + xyz[:, :1] * tangent + xyz[:, 2:3] * normal
        out[:, 1] = xyz[:, 1]
        return out

    def face(coords, material="wall"):
        meshes.append(
            quad_mesh(
                world(coords), f"{identity}_{len(meshes)}", material, building, origin
            )
        )

    def box(x0, y0, z0, x1, y1, z1, material="slab", solid=True):
        if min(x1 - x0, y1 - y0, z1 - z0) <= 0.001:
            return
        # Each face has flat normals. Winding faces outside in wall-local x/y/outward.
        v = np.array(
            [
                [x0, y0, z0],
                [x1, y0, z0],
                [x1, y1, z0],
                [x0, y1, z0],
                [x0, y0, z1],
                [x1, y0, z1],
                [x1, y1, z1],
                [x0, y1, z1],
            ]
        )
        faces = [[0, 1, 2, 3], [5, 4, 7, 6], [4, 0, 3, 7], [1, 5, 6, 2], [3, 2, 6, 7], [4, 5, 1, 0]]
        # Our frame is left-handed: the default TRI winding yields exterior normals.
        points = world(v[np.array(faces)[:, ::-1].ravel()])
        size = [x1 - x0, y1 - y0, z1 - z0]
        center = world([[(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2]])[0]
        mesh = GlbMesh(
            f"{identity}_{len(meshes)}",
            points - origin,
            np.concatenate([TRI + i * 4 for i in range(6)]),
            color=COLOURS[material],
            roughness=0.28 if material == "glass" else 0.85,
            extras=dict(
                layer="buildings",
                object_id=f"{identity}_{len(meshes)}",
                surface="facade-component",
                building_id=building,
                material_key=f"component_{material}",
                component_box=dict(
                    center=(center - origin).tolist(), size=size, tangent=tangent.tolist()
                ),
            ),
        )
        meshes.append(mesh)
        if solid:
            boxes.append((points, mesh.indices))

    def rail(left, r, y, d):
        # Posts meet horizontal bars at boundaries, never overlap their front surfaces.
        box(left, y, d - 0.06, r, y + 0.07, d, "rail")
        box(left, y + 0.93, d - 0.06, r, y + 1, d, "rail")
        count = max(1, int((r - left) / 0.3))
        for x in np.linspace(left + 0.03, r - 0.03, count + 1):
            box(x - 0.025, y + 0.07, d - 0.055, x + 0.025, y + 0.93, d - 0.005, "rail", False)

    for p in plans:
        p["origin"] = reference
        left, y, r, t = p["metric"]
        d, kind = p["depth_m"], p["kind"]
        if kind in {"bay", "recess", "loggia", "arcade"}:
            z = d if kind == "bay" else -d
            # The original wall patch is removed; only its returns and moved face exist.
            for coords in (
                [[left, y, 0], [left, y, z], [left, t, z], [left, t, 0]],
                [[r, y, z], [r, y, 0], [r, t, 0], [r, t, z]],
                [[left, y, z], [left, y, 0], [r, y, 0], [r, y, z]],
                [[left, t, 0], [left, t, z], [r, t, z], [r, t, 0]],
            ):
                face(coords)
            if kind == "bay":
                # Collision uses a box, but its expanded mesh is not emitted twice.
                before = len(meshes)
                box(left, y, 0.02, r, t, d)
                del meshes[before:]
            if kind == "arcade":
                # Back and returns are supplied to collision below. Column rhythm is
                # observed when counted, otherwise a conservative <=4 m spacing prior.
                count = max(2, p["count"] if p["count"] > 1 else int((r - left) / 4) + 1)
                for x in np.linspace(left + 0.18, r - 0.18, count):
                    box(x - 0.12, y, 0.03, x + 0.12, t - 0.2, 0.31)
                if p["head"] != "flat":
                    # Faceted arch spandrels occupy only the head above the opening.
                    for a, b in zip(
                        np.linspace(left + 0.3, r - 0.3, count)[:-1],
                        np.linspace(left + 0.3, r - 0.3, count)[1:],
                        strict=True,
                    ):
                        radius = (b - a) / 2
                        xs = np.linspace(a, b, 9)
                        for x0, x1 in zip(xs[:-1], xs[1:], strict=True):
                            h0 = (
                                t
                                - 0.2
                                - radius
                                + np.sqrt(max(0, radius**2 - (x0 - (a + b) / 2) ** 2))
                            )
                            h1 = (
                                t
                                - 0.2
                                - radius
                                + np.sqrt(max(0, radius**2 - (x1 - (a + b) / 2) ** 2))
                            )
                            face(
                                [
                                    [x0, h0, 0.34],
                                    [x1, h1, 0.34],
                                    [x1, t - 0.1, 0.34],
                                    [x0, t - 0.1, 0.34],
                                ]
                            )
        if kind in {"balcony", "glazed_balcony", "loggia", "veranda"}:
            rows, count = p["rows"], p["count"]
            for row in range(rows):
                bottom = y + (t - y) * row / rows
                top = y + (t - y) * (row + 1) / rows
                for col in range(count):
                    a = left + (r - left) * col / count + 0.03
                    b = left + (r - left) * (col + 1) / count - 0.03
                    outward = 0.12 if kind == "loggia" else d
                    back = -d + 0.02 if kind == "loggia" else 0.03
                    box(a, bottom, back, b, bottom + 0.12, outward)
                    if top - bottom < 1.25:
                        continue
                    if kind in {"glazed_balcony", "veranda"}:
                        # Low-cost opaque tinted glass shares one material; avoids alpha sorting.
                        box(a, bottom + 0.12, outward - 0.04, b, top - 0.1, outward, "glass")
                        box(a, bottom + 0.12, back, a + 0.06, top - 0.1, outward - 0.06, "rail")
                        box(b - 0.06, bottom + 0.12, back, b, top - 0.1, outward - 0.06, "rail")
                    else:
                        rail(a, b, bottom + 0.12, outward + 0.08)
        if kind == "stairs":
            rise, run = float(p["rise_m"]), float(p["run_m"])
            if run > r - left or rise > t - y:
                continue
            count = max(1, int(np.ceil(rise / 0.18)))
            for step in range(count):
                a = (
                    left + run * step / count
                    if p["direction"] > 0
                    else r - run * (step + 1) / count
                )
                box(a, y, 0.03, a + run / count, y + rise * (step + 1) / count, d)
    return meshes, boxes


def collision_walls(building, walls):
    """Open collision only for walkable arcades; passages remain tunnel-owned."""
    from turku3d.facade_programs import matching_wall, measured

    observation = measured(building.facade_key)
    if not observation or not any(w.get("geometry_components") for w in observation["walls"]):
        idx = (
            np.concatenate([TRI + i * 4 for i in range(len(walls) // 4)])
            if len(walls)
            else np.empty((0, 3), np.uint32)
        )
        return walls, idx
    positions, indices = [], []
    count = 0

    def append(points, idx):
        nonlocal count
        positions.append(points)
        indices.append(idx + count)
        count += len(points)

    for vertices in walls.reshape(-1, 4, 3):
        own = matching_wall(vertices, observation)
        plans = plan(vertices, own) if own else []
        _, boxes = geometry(vertices, plans, building.facade_key, np.zeros(3))
        uv = np.zeros((4, 2))
        for points, _ in surface_parts(vertices, uv, plans, collision=True):
            append(points, TRI)
        for points, idx in boxes:
            append(points, idx)
        # Arcade returns are collision surfaces; upper loggias stay nonwalkable.
        for p in plans:
            if p["kind"] != "arcade":
                continue
            only, _ = geometry(vertices, [p], building.facade_key, np.zeros(3))
            for mesh in only[:4]:
                append(mesh.positions, mesh.indices)
    return (
        (np.concatenate(positions), np.concatenate(indices))
        if positions
        else (walls, np.empty((0, 3), np.uint32))
    )


def slots(walls, observation, passage=None):
    """Located artwork slots and existing passage apertures, without invented tunnels.

    Passage slots are bound only to a missing part of a surveyed facade within
    the building's surviving height range. The tunnel producer owns their depth,
    floor and collision. Artwork slots are inert until a licensed asset is bound.
    """
    from turku3d.facade_programs import matching_wall

    result = []
    if not observation:
        return result
    for wall in observation.get("walls", []):
        descriptors = [
            d for d in wall.get("geometry_components", []) if d["kind"] in {"passage", "artwork"}
        ]
        if not descriptors:
            continue
        g = wall["geometry"]
        coords = g.get("coordinates") or [g["p0"], g["p1"]]
        a = np.array([coords[0][0] - C.ORIGIN_E, g.get("base_h", 0), C.ORIGIN_N - coords[0][1]])
        b = np.array([coords[-1][0] - C.ORIGIN_E, a[1], C.ORIGIN_N - coords[-1][1]])
        width = np.linalg.norm(b - a)
        tangent = (b - a) / width
        cover = []
        for quad in walls.reshape(-1, 4, 3):
            match = matching_wall(quad, {**observation, "walls": [wall]})
            if match is not None:
                cover.append(shapely.Polygon(np.c_[(quad - a) @ tangent, quad[:, 1] - a[1]]))
        if not cover:
            continue
        occupied = shapely.union_all(cover)
        for descriptor in descriptors:
            x, y, w, h = descriptor["bounds"]
            rectangle = shapely.box(
                x * width, y * wall["height_m"], (x + w) * width, (y + h) * wall["height_m"]
            )
            if descriptor["kind"] == "passage":
                # A carved part carries a raised base; do not infer a passage
                # from crop loss above the roof or outside the surveyed frontage.
                if passage is None:
                    continue
                aperture = rectangle.difference(occupied).intersection(
                    shapely.box(
                        occupied.bounds[0],
                        passage["floor"] - a[1],
                        occupied.bounds[2],
                        passage["ceiling"] - a[1],
                    )
                )
                if aperture.area < 0.5 or occupied.bounds[3] < rectangle.bounds[3]:
                    continue
            else:
                aperture = rectangle.intersection(occupied)
            for quad in trapezoids(aperture):
                points = a + quad[:, :1] * tangent
                points[:, 1] = a[1] + quad[:, 1]
                result.append(
                    dict(
                        kind=descriptor["kind"],
                        wall_id=wall["wall_id"],
                        corners=points.tolist(),
                        outward_offset_m=0.04,
                        owner="mapped-passage"
                        if descriptor["kind"] == "passage"
                        else "artwork-asset-slot",
                    )
                )
    return result
