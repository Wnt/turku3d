"""Planar wall partitioning and visible-surface diagnostics."""

import numpy as np
import shapely

DISTANCE = 0.005
PARALLEL = 0.99999
MIN_AREA = 0.0001


def trapezoids(geometry):
    """Partition a polygon, including holes and sloping edges, into vertical slabs."""
    for polygon in shapely.get_parts(geometry):
        if polygon.geom_type != "Polygon" or polygon.area < 1e-9:
            continue
        cuts = sorted(set(shapely.get_coordinates(polygon)[:, 0]))
        for left, right in zip(cuts, cuts[1:], strict=False):
            if right - left < 1e-8:
                continue
            slab = polygon.intersection(shapely.box(left, -10000, right, 10000))
            for part in shapely.get_parts(slab):
                if part.geom_type != "Polygon" or part.area < 1e-9:
                    continue
                xy = np.asarray(part.exterior.coords)
                lo = xy[np.isclose(xy[:, 0], left, atol=1e-8, rtol=0), 1]
                hi = xy[np.isclose(xy[:, 0], right, atol=1e-8, rtol=0), 1]
                if len(lo) and len(hi):
                    yield np.array(
                        [[left, lo.min()], [right, hi.min()], [right, hi.max()], [left, lo.max()]]
                    )


def paint_partition(layers, origin, direction):
    """Resolve paint precedence in wall metres; emit no depth-offset decals."""
    covered = shapely.Polygon()
    result = []
    # Explicit paint priority replaces the old millimetre depth hierarchy.
    for _, (points, uv, style, _priority) in sorted(
        enumerate(layers), key=lambda item: (item[1][3], item[0]), reverse=True
    ):
        polygon = shapely.make_valid(shapely.Polygon(uv))
        if polygon.area < 1e-9:
            continue
        remainder = polygon.difference(covered)
        if remainder.equals(polygon):
            result.append((points, uv, style))
        else:
            for coords in trapezoids(remainder):
                vertices = origin + coords[:, :1] * direction
                vertices[:, 1] = coords[:, 1]
                result.append((vertices, coords, style))
        covered = covered.union(polygon)
    return reversed(result)


def overlaps(faces, ids, *, split=None):
    """Find positive-area overlap of simultaneously visible near-coplanar faces.

    Back-to-back solid boundaries cannot compete under backface culling. They
    are intentionally excluded; both orientations are still checked independently.
    Input triangles or quads are in one metric frame, including exported float32.
    """
    if not len(faces):
        return []
    points = [np.asarray(p, float) for p in faces]
    normals = np.array([np.cross(p[1] - p[0], p[-1] - p[0]) for p in points])
    lengths = np.linalg.norm(normals, axis=1)
    normals /= np.maximum(lengths[:, None], 1e-20)
    low = np.array([p.min(axis=0) for p in points])
    high = np.array([p.max(axis=0) for p in points])
    bounds = shapely.box(
        low[:, 0] - DISTANCE, low[:, 2] - DISTANCE, high[:, 0] + DISTANCE, high[:, 2] + DISTANCE
    )
    tree = shapely.STRtree(bounds)
    result = []
    for i, p in enumerate(points):
        if split is not None and i < split:
            continue
        # Only pairs with at least one vertical wall matter here (roof seams included).
        if lengths[i] < 1e-12:
            continue
        js = tree.query(bounds[i])
        if split is not None:
            js = js[js < split]
        js = js[(js < i) & (high[js, 1] > low[i, 1]) & (low[js, 1] < high[i, 1])]
        js = js[
            (normals[js] @ normals[i] > PARALLEL)
            & ((abs(normals[js, 1]) < 0.02) | (abs(normals[i, 1]) < 0.02))
        ]
        if not len(js):
            continue
        direction = np.array([-normals[i, 2], 0, normals[i, 0]])
        direction /= np.linalg.norm(direction)
        vertical = np.cross(normals[i], direction)
        basis = np.array([direction, vertical])
        a = shapely.Polygon((p - p[0]) @ basis.T)
        for j in js:
            from turku3d.wall_assembly import band_polygon

            q = points[j]
            b = shapely.Polygon((q - p[0]) @ basis.T)
            # Solve the plane-distance band over the actual shared polygon,
            # including sloping triangles and only partly coincident planes.
            common = a.buffer(-0.00005).intersection(b.buffer(-0.00005))
            common = (
                band_polygon(common, basis @ normals[j], (p[0] - q[0]) @ normals[j], DISTANCE)
                if not common.is_empty
                else common
            )
            if common.area > MIN_AREA:
                result.append(dict(first=ids[j], second=ids[i], area=float(common.area)))
    return result


def wall_faces(meshes):
    """Collect architectural wall triangles in the precision shipped to the GPU."""
    faces, ids = [], []
    for mesh in meshes:
        points = np.asarray(mesh.positions, np.float32).astype(float)
        triangles = points[np.asarray(mesh.indices)]
        normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        lengths = np.linalg.norm(normals, axis=1)
        selected = np.flatnonzero((lengths > 1e-12) & (abs(normals[:, 1]) < 0.02 * lengths))
        faces.extend(triangles[selected])
        owner = mesh.extras.get("building_id", mesh.name)
        ids.extend(f"{owner}/{mesh.name}/face:{i}" for i in selected)
    return faces, ids


def validate_meshes(meshes, tile):
    faces, ids = wall_faces(meshes)
    violations = overlaps(faces, ids)
    # Geometry is resolved at assembly. Surface evidence must never prevent the
    # structural first pass or an appearance upgrade from being served.
    return dict(faces=len(faces), violations=len(violations), overlaps=violations)


def validate_ownership(shells, tile):
    """Passage splits inherit their complete source building's half-open tile owner."""
    from turku3d import config as C

    for b, _ in shells:
        centre = b.footprint.centroid
        collision = getattr(b, "_collision_owner", None)
        point = collision.origin if collision is not None else (centre.x, 0, centre.y)
        owner = getattr(b, "visual_owner", C.VisualTile.at(point[0], point[2]).id)
        if owner != tile.id:
            raise ValueError(f"wall ownership {b.key}: {owner} emitted in {tile.id}")


def validate_neighbours(meshes, shells, tile, root, *, pass_kind="full", content_hash=None):
    """Resolve comparable neighbour evidence under one serialized admission.

    Records carry the visual pass and generator content hash. Already admitted
    geometry takes precedence over a new duplicate, never over another pass or
    version. Ownership contradictions alone are programming errors.
    """
    import fcntl
    import json
    from pathlib import Path

    from turku3d import config as C
    from turku3d.coarse.packaging import code_hash
    from turku3d.package.gltf import GlbMesh
    from turku3d.wall_assembly import assemble_structures

    validate_ownership(shells, tile)
    content_hash = content_hash or code_hash()
    owners = sorted({b.key for b, _ in shells})
    faces, ids = [], []
    box = tile.bbox
    local_faces, local_ids = wall_faces(meshes)
    for local, identity in zip(local_faces, local_ids, strict=True):
        face = local + tile.origin
        lo, hi = face.min(axis=0), face.max(axis=0)
        if (
            lo[0] > box.xmin + DISTANCE
            and hi[0] < box.xmax - DISTANCE
            and lo[2] > box.zmin + DISTANCE
            and hi[2] < box.zmax - DISTANCE
        ):
            continue
        faces.append(face.tolist())
        ids.append(f"{tile.id}/{identity}")
    directory = Path(root) / ".wall-surfaces"
    directory.mkdir(parents=True, exist_ok=True)
    records = directory / pass_kind
    records.mkdir(exist_ok=True)
    removed = 0
    with (directory / "admission.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                if di == dj == 0:
                    continue
                neighbour = C.VisualTile(0, tile.i + di, tile.j + dj).id
                path = records / f"{neighbour}.json"
                if not path.exists():
                    continue
                record = json.loads(path.read_text())
                # Legacy evidence has no comparable generation identity.
                if (record.get("pass_kind"), record.get("content_hash")) != (
                    pass_kind,
                    content_hash,
                ):
                    continue
                duplicate = set(owners).intersection(record["owners"])
                if duplicate:
                    raise ValueError(f"wall ownership {tile.id}/{neighbour}: {sorted(duplicate)}")
                violations = overlaps(
                    faces + record["faces"], ids + record["ids"], split=len(faces)
                )
                # Both records have already passed their own tile check.
                violations = [
                    v for v in violations if v["first"].split("/")[0] != v["second"].split("/")[0]
                ]
                if violations:
                    # The already admitted neighbour owns this surface. Subtract
                    # only the competing frontage, retaining UVs and materials.
                    points = np.asarray(record["faces"], float).reshape(-1, 3) - tile.origin
                    protected = GlbMesh(
                        "neighbour",
                        points,
                        np.arange(len(points), dtype=np.uint32).reshape(-1, 3),
                    )
                    meshes[:] = assemble_structures(meshes, [protected])
                    removed += len(violations)
                    local_faces, local_ids = wall_faces(meshes)
                    faces = [(face + tile.origin).tolist() for face in local_faces]
                    ids = [f"{tile.id}/{identity}" for identity in local_ids]
        path = records / f"{tile.id}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                dict(
                    owners=owners,
                    faces=faces,
                    ids=ids,
                    pass_kind=pass_kind,
                    content_hash=content_hash,
                )
            )
        )
        temporary.replace(path)
    return dict(resolved=removed, pass_kind=pass_kind, content_hash=content_hash)


def layer_meshes(paths):
    """Read generated wall-bearing layers before meshopt/atlas packing."""
    from turku3d.package.gltf import GlbMesh, accessor_array, read_glb

    meshes = []
    for path in paths:
        if path.name in {"vegetation.vis.glb", "props.vis.glb"}:
            continue  # Instanced foliage/props do not own architectural wall surfaces.
        document, binary = read_glb(path.read_bytes())
        for node in document["nodes"]:
            if "mesh" not in node:
                continue
            # Layer writers share the tile root translation; positions are tile-local.
            for primitive in document["meshes"][node["mesh"]]["primitives"]:
                attrs = primitive["attributes"]
                material = document["materials"][primitive["material"]]
                pbr = material["pbrMetallicRoughness"]
                image, repeat = None, False
                if "baseColorTexture" in pbr:
                    texture = document["textures"][pbr["baseColorTexture"]["index"]]
                    view = document["bufferViews"][
                        document["images"][texture["source"]]["bufferView"]
                    ]
                    image = binary[view["byteOffset"] : view["byteOffset"] + view["byteLength"]]
                    repeat = document["samplers"][texture["sampler"]]["wrapS"] == 10497
                meshes.append(
                    GlbMesh(
                        node.get("name", path.name),
                        accessor_array(document, binary, attrs["POSITION"]),
                        accessor_array(document, binary, primitive["indices"]).reshape(-1, 3),
                        normals=accessor_array(document, binary, attrs["NORMAL"])
                        if "NORMAL" in attrs
                        else None,
                        uvs=accessor_array(document, binary, attrs["TEXCOORD_0"])
                        if "TEXCOORD_0" in attrs
                        else None,
                        image=image,
                        repeat=repeat,
                        color=tuple(pbr["baseColorFactor"]),
                        roughness=pbr["roughnessFactor"],
                        emissive=tuple(material.get("emissiveFactor", [0, 0, 0])),
                        extras=node.get("extras", {}),
                    )
                )
    return meshes
