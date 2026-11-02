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
        # Between successive vertex x coordinates every boundary is linear.
        # Intersect those edges analytically: a far-away clipping rectangle can
        # leak its height through GEOS overlay on almost-collinear remnants.
        rings = [np.asarray(r.coords) for r in [polygon.exterior, *polygon.interiors]]
        starts = np.concatenate([r[:-1] for r in rings])
        ends = np.concatenate([r[1:] for r in rings])
        cuts = np.unique(starts[:, 0])
        for left, right in zip(cuts, cuts[1:], strict=False):
            if right - left < 1e-8:
                continue
            middle = left + (right - left) * .5
            crossing = ((np.minimum(starts[:, 0], ends[:, 0]) < middle)
                        & (np.maximum(starts[:, 0], ends[:, 0]) > middle))
            a, b = starts[crossing], ends[crossing]
            if not len(a):
                continue
            t = (np.array([left, middle, right])[None, :] - a[:, :1]) / (b[:, :1] - a[:, :1])
            y = a[:, 1:] + np.clip(t, 0, 1) * (b[:, 1:] - a[:, 1:])
            y = y[np.argsort(y[:, 1], kind="stable")]
            for low, high in zip(y[::2], y[1::2], strict=True):
                if (right - left) * (high[1] - low[1]) < 1e-9:
                    continue
                yield np.array([[left, low[0]], [right, low[2]],
                                [right, high[2]], [left, high[0]]])


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
    # Pad only by repeating the last vertex; polygon coverage is unchanged.
    sizes = np.fromiter(map(len, faces), dtype=np.intp, count=len(faces))
    width = int(sizes.max())
    if sizes.min() == width:
        # Shipped meshes are triangles. Avoid one NumPy allocation and a generic
        # padding operation per face when the entire array is already regular.
        points = np.asarray(faces, dtype=float)
    else:
        points = np.empty((len(faces), width, 3), dtype=float)
        for row, face, size in zip(points, faces, sizes, strict=True):
            row[:size] = face
            row[size:] = face[-1]
    first = points[:, 0]
    normals = np.cross(points[:, 1] - first, points[:, -1] - first)
    lengths = np.linalg.norm(normals, axis=1)
    normals /= np.maximum(lengths[:, None], 1e-20)
    low, high = points.min(axis=1), points.max(axis=1)
    result = []
    # Include height in the spatial index. An x/z-only tree places every window
    # row on a frontage in the same bucket, making dense facades quadratic.
    for axis in (0, 2):
        selected = np.flatnonzero((np.abs(normals[:, 2]) >= np.abs(normals[:, 0])) == (axis == 0))
        selected = selected[(lengths[selected] > 1e-12) & (selected >= (split or 0))]
        boxes = shapely.box(
            low[:, axis] - DISTANCE,
            low[:, 1] - DISTANCE,
            high[:, axis] + DISTANCE,
            high[:, 1] + DISTANCE,
        )
        tree = shapely.STRtree(boxes if split is None else boxes[:split])
        for start in range(0, len(selected), 512):
            rows = selected[start : start + 512]
            local, js = tree.query(boxes[rows])
            ii = rows[local]
            keep = (js < ii) & (high[js, 1] > low[ii, 1]) & (low[js, 1] < high[ii, 1])
            if split is not None:
                keep &= js < split
            keep &= np.einsum("ij,ij->i", normals[js], normals[ii]) > PARALLEL
            keep &= (abs(normals[js, 1]) < 0.02) | (abs(normals[ii, 1]) < 0.02)
            keep &= ((high[js] + DISTANCE >= low[ii]) & (low[js] - DISTANCE <= high[ii])).all(
                axis=1
            )
            ii, js = ii[keep], js[keep]
            if not len(ii):
                continue
            separation = np.einsum("ijk,ik->ij", points[ii] - first[js, None], normals[js])
            keep = (separation.min(axis=1) <= DISTANCE) & (separation.max(axis=1) >= -DISTANCE)
            ii, js = ii[keep], js[keep]
            if not len(ii):
                continue
            direction = np.c_[-normals[ii, 2], np.zeros(len(ii)), normals[ii, 0]]
            direction /= np.linalg.norm(direction, axis=1)[:, None]
            basis = np.stack([direction, np.cross(normals[ii], direction)], axis=1)
            a = np.einsum("ijk,ilk->ijl", points[ii] - first[ii, None], basis)
            b = np.einsum("ijk,ilk->ijl", points[js] - first[ii, None], basis)
            keep = (
                (b.max(axis=1) > a.min(axis=1) + 0.00005)
                & (b.min(axis=1) < a.max(axis=1) - 0.00005)
            ).all(axis=1)
            # Intersection cannot exceed the overlap of the two projected boxes.
            # Retain a conservative rounding margin at the existing area threshold.
            extent = np.minimum(a.max(axis=1), b.max(axis=1)) - np.maximum(
                a.min(axis=1), b.min(axis=1)
            )
            keep &= np.maximum(extent, 0).prod(axis=1) > MIN_AREA - 1e-12
            ii, js, basis, a, b = ii[keep], js[keep], basis[keep], a[keep], b[keep]
            if not len(ii):
                continue
            common = shapely.intersection(shapely.polygons(a), shapely.polygons(b))
            for k in np.flatnonzero(shapely.area(common) > MIN_AREA):
                from turku3d.wall_assembly import band_polygon

                i, j = ii[k], js[k]
                polygon = common[k].buffer(-0.00005)
                if not polygon.is_empty:
                    polygon = band_polygon(
                        polygon, basis[k] @ normals[j], (first[i] - first[j]) @ normals[j], DISTANCE
                    )
                if polygon.area > MIN_AREA:
                    result.append(dict(first=ids[j], second=ids[i], area=float(polygon.area)))
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


def validate_neighbours(
    meshes,
    shells,
    tile,
    root,
    *,
    pass_kind="full",
    content_hash=None,
    geometry_key=None,
    report_cache=None,
):
    """Resolve comparable neighbour evidence under one serialized admission.

    Records carry the visual pass and generator content hash. Already admitted
    geometry takes precedence over a new duplicate, never over another pass or
    version. Ownership contradictions alone are programming errors.
    """
    import fcntl
    import hashlib
    import json
    from contextlib import nullcontext
    from pathlib import Path

    from turku3d import config as C
    from turku3d.coarse.packaging import code_hash
    from turku3d.package.gltf import GlbMesh
    from turku3d.wall_assembly import assemble_structures

    validate_ownership(shells, tile)
    content_hash = content_hash or code_hash()
    from turku3d import wall_assembly
    from turku3d.stage_cache import StageCache, source_key

    owners = sorted({b.key for b, _ in shells})
    directory = Path(root) / ".wall-surfaces"
    directory.mkdir(parents=True, exist_ok=True)
    # Retiring producers still write first/*.json under the legacy global lock.
    # Isolate first-pass records as well as admission so their temporary files
    # cannot race during a rolling deployment. Full passes retain the old lock.
    records = directory / ("first-v2" if pass_kind == "first" else pass_kind)
    records.mkdir(exist_ok=True)
    admission = (records if pass_kind == "first" else directory) / "admission.lock"
    with admission.open("a+b") as lock:
        with report_cache.measure("wall_admission_wait") if report_cache else nullcontext():
            fcntl.flock(lock, fcntl.LOCK_EX)
        with report_cache.measure("wall_neighbour_processing") if report_cache else nullcontext():
            neighbours = []
            neighbour_keys = []
            json_bytes = 0
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    if di == dj == 0:
                        continue
                    path = records / f"{C.VisualTile(0, tile.i + di, tile.j + dj).id}.json"
                    if not path.exists():
                        continue
                    payload = path.read_bytes()
                    json_bytes += len(payload)
                    record = json.loads(payload)
                    if (record.get("pass_kind"), record.get("content_hash")) != (
                        pass_kind,
                        content_hash,
                    ):
                        del payload
                        continue
                    duplicate = set(owners).intersection(record["owners"])
                    if duplicate:
                        raise ValueError(
                            f"wall ownership {tile.id}/{path.stem}: {sorted(duplicate)}"
                        )
                    neighbours.append(record)
                    # Exact record bytes cover every field without recursively
                    # hashing hundreds of thousands of face coordinates in Python.
                    neighbour_keys.append(hashlib.sha256(payload).hexdigest())
                    del payload
            if report_cache is not None:
                report_cache.metrics["wall_neighbour_processing"].update(
                    neighbours=len(neighbours),
                    faces=sum(len(r["faces"]) for r in neighbours),
                    json_bytes=json_bytes,
                )

            def resolve():
                result = list(meshes)
                removed = 0
                faces, ids = boundary_faces(result, tile)
                for record in neighbours:
                    violations = overlaps(
                        faces + record["faces"], ids + record["ids"], split=len(faces)
                    )
                    if violations:
                        points = np.asarray(record["faces"], float).reshape(-1, 3) - tile.origin
                        protected = GlbMesh(
                            "neighbour",
                            points,
                            np.arange(len(points), dtype=np.uint32).reshape(-1, 3),
                        )
                        result = assemble_structures(result, [protected], protected_only=True)
                        removed += len(violations)
                        faces, ids = boundary_faces(result, tile)
                return result, faces, ids, removed

            cache = StageCache(Path(root) / ".stages" / tile.id, retain=True)
            try:
                resolved, faces, ids, removed = cache.run(
                    "wall_neighbours_" + pass_kind,
                    source_key(
                        validate_neighbours,
                        overlaps,
                        boundary_faces,
                        wall_faces,
                        trapezoids,
                        wall_assembly.assemble_structures,
                        wall_assembly.band_polygon,
                    ),
                    [geometry_key if geometry_key is not None else meshes, neighbour_keys, tile],
                    resolve,
                )
            finally:
                if report_cache is not None:
                    report_cache.metrics.update(cache.metrics)
            meshes[:] = resolved
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
    return dict(
        resolved=removed,
        pass_kind=pass_kind,
        content_hash=content_hash,
        geometry_key=cache.metrics["wall_neighbours_" + pass_kind]["key"],
    )


def boundary_faces(meshes, tile):
    """Keep boundary evidence bounded even after subtracting a neighbour surface."""
    faces, ids = [], []
    box = tile.bbox
    for mesh in meshes:
        points = np.asarray(mesh.positions, np.float32).astype(float)
        triangles = points[np.asarray(mesh.indices)]
        normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        lengths = np.linalg.norm(normals, axis=1)
        selected = np.flatnonzero((lengths > 1e-12) & (abs(normals[:, 1]) < 0.02 * lengths))
        boundary = triangles[selected] + tile.origin
        lo, hi = boundary.min(axis=1), boundary.max(axis=1)
        keep = ~(
            (lo[:, 0] > box.xmin + DISTANCE)
            & (hi[:, 0] < box.xmax - DISTANCE)
            & (lo[:, 2] > box.zmin + DISTANCE)
            & (hi[:, 2] < box.zmax - DISTANCE)
        )
        owner = mesh.extras.get("building_id", mesh.name)
        faces.extend(boundary[keep].tolist())
        ids.extend(f"{tile.id}/{owner}/{mesh.name}/face:{i}" for i in selected[keep])
    return faces, ids


def instanced_layer(path):
    """Prototype layers pass intact through assembly; never treat them as placed walls."""
    from turku3d.package.gltf import read_glb

    document, _ = read_glb(path.read_bytes())
    nodes = [node for node in document["nodes"] if "mesh" in node]
    instanced = ["EXT_mesh_gpu_instancing" in node.get("extensions", {}) for node in nodes]
    if any(instanced) and not all(instanced):
        raise ValueError(f"{path}: separate instanced prototypes from static wall layers")
    return bool(nodes) and all(instanced)


def layer_meshes(paths):
    """Read generated wall-bearing layers before meshopt/atlas packing."""
    from turku3d.package.gltf import GlbMesh, accessor_array, read_glb

    meshes = []
    for path in paths:
        if instanced_layer(path):
            continue  # Retain GPU instancing; the decoded shipping audit expands it.
        document, binary = read_glb(path.read_bytes())
        for node in document["nodes"]:
            if "mesh" not in node:
                continue
            # Props reuse prototypes under transformed nodes. Bake their local
            # transforms into the shared tile frame before surface assembly.
            translation = np.asarray(node.get("translation", [0, 0, 0]))
            scale = np.asarray(node.get("scale", [1, 1, 1]))
            rotation = np.asarray(node.get("rotation", [0, 0, 0, 1]))

            def rotate(points, rotation=rotation):
                cross = np.cross(rotation[:3], points)
                return points + 2 * (rotation[3] * cross + np.cross(rotation[:3], cross))

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
                        rotate(accessor_array(document, binary, attrs["POSITION"]) * scale)
                        + translation,
                        accessor_array(document, binary, primitive["indices"]).reshape(-1, 3),
                        normals=rotate(accessor_array(document, binary, attrs["NORMAL"]) / scale)
                        if "NORMAL" in attrs
                        else None,
                        uvs=accessor_array(document, binary, attrs["TEXCOORD_0"])
                        if "TEXCOORD_0" in attrs
                        else None,
                        colors=accessor_array(document, binary, attrs["COLOR_0"])
                        if "COLOR_0" in attrs
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


if __name__ == "__main__":
    import json
    import sys

    payload = json.load(sys.stdin)
    violations = overlaps(payload["faces"], payload["ids"])
    json.dump(
        dict(faces=len(payload["faces"]), violations=len(violations), overlaps=violations),
        sys.stdout,
    )
