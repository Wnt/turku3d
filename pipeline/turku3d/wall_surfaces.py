"""Planar wall partitioning and fail-closed visible-surface validation."""

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
        for left, right in zip(cuts, cuts[1:]):
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
                    yield np.array([[left, lo.min()], [right, hi.min()],
                                    [right, hi.max()], [left, lo.max()]])


def paint_partition(layers, origin, direction):
    """Resolve paint precedence in wall metres; emit no depth-offset decals."""
    covered = shapely.Polygon()
    result = []
    # Explicit paint priority replaces the old millimetre depth hierarchy.
    for _, (points, uv, style, priority) in sorted(
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


def overlaps(faces, ids):
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
    bounds = shapely.box(low[:, 0] - DISTANCE, low[:, 2] - DISTANCE,
                         high[:, 0] + DISTANCE, high[:, 2] + DISTANCE)
    tree = shapely.STRtree(bounds)
    result = []
    for i, p in enumerate(points):
        # Only pairs with at least one vertical wall matter here (roof seams included).
        if lengths[i] < 1e-12:
            continue
        js = tree.query(bounds[i])
        js = js[(js < i) & (high[js, 1] > low[i, 1]) & (low[js, 1] < high[i, 1])]
        js = js[(normals[js] @ normals[i] > PARALLEL)
                & ((abs(normals[js, 1]) < .01) | (abs(normals[i, 1]) < .01))]
        if not len(js):
            continue
        direction = np.array([-normals[i, 2], 0, normals[i, 0]])
        direction /= np.linalg.norm(direction)
        a = shapely.Polygon(np.c_[(p - p[0]) @ direction, p[:, 1]])
        for j in js:
            q = points[j]
            b = shapely.Polygon(np.c_[(q - p[0]) @ direction, q[:, 1]])
            common = a.intersection(b)
            if common.area <= MIN_AREA:
                continue
            # Plane separation at shared frontage, not at distant edge endpoints.
            xy = shapely.get_coordinates(common)
            probe = p[0] + xy[:, :1] * direction
            probe[:, 1] = xy[:, 1]
            if np.max(np.abs((probe - q[0]) @ normals[j])) <= DISTANCE:
                result.append(dict(first=ids[j], second=ids[i], area=float(common.area)))
    return result


def validate_meshes(meshes, tile):
    faces, ids = [], []
    for mesh in meshes:
        # GLB stores tile-local float32: validate the precision actually shipped.
        points = np.asarray(mesh.positions, np.float32).astype(float)
        faces.extend(points[np.asarray(mesh.indices)])
        owner = mesh.extras.get("building_id", mesh.name)
        ids.extend(f"{owner}/{mesh.name}/face:{i}" for i in range(len(mesh.indices)))
    violations = overlaps(faces, ids)
    if violations:
        raise ValueError(f"wall surface invariant {tile}: {violations[:20]}")
    return dict(faces=len(faces), violations=0)


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


def validate_neighbours(meshes, shells, tile, root):
    """Serialize boundary admission so concurrent neighbour builds cannot both pass.

    Records are geometry evidence, never served assets. Every newly admitted tile
    checks all previous neighbours; rebuilding either side rechecks the pair.
    """
    import fcntl
    import json
    from pathlib import Path

    from turku3d import config as C

    validate_ownership(shells, tile)
    owners = sorted({b.key for b, _ in shells})
    faces, ids = [], []
    box = tile.bbox
    for mesh in meshes:
        vertices = np.asarray(mesh.positions, np.float32).astype(float) + tile.origin
        for i, face in enumerate(vertices[np.asarray(mesh.indices)]):
            lo, hi = face.min(axis=0), face.max(axis=0)
            if (lo[0] > box.xmin + DISTANCE and hi[0] < box.xmax - DISTANCE
                    and lo[2] > box.zmin + DISTANCE and hi[2] < box.zmax - DISTANCE):
                continue
            faces.append(face.tolist())
            ids.append(f"{tile.id}/{mesh.extras.get('building_id', mesh.name)}/{mesh.name}/{i}")
    directory = Path(root) / ".wall-surfaces"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "admission.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                if di == dj == 0:
                    continue
                neighbour = C.VisualTile(0, tile.i + di, tile.j + dj).id
                path = directory / f"{neighbour}.json"
                if not path.exists():
                    continue
                record = json.loads(path.read_text())
                duplicate = set(owners).intersection(record["owners"])
                if duplicate:
                    raise ValueError(f"wall ownership {tile.id}/{neighbour}: {sorted(duplicate)}")
                violations = overlaps(faces + record["faces"], ids + record["ids"])
                # Both records have already passed their own tile check.
                violations = [v for v in violations
                              if v["first"].split('/')[0] != v["second"].split('/')[0]]
                if violations:
                    raise ValueError(f"wall neighbour invariant {tile.id}: {violations[:20]}")
        path = directory / f"{tile.id}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(dict(owners=owners, faces=faces, ids=ids)))
        temporary.replace(path)
