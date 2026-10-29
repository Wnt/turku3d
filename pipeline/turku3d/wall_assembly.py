"""Subtract architectural wall coverage from secondary structural mesh surfaces."""

import copy

import numpy as np
import shapely

from .wall_surfaces import trapezoids


def band_polygon(polygon, coefficients, offset, tolerance=0.006):
    """Clip to the part of a plane within tolerance, even when the planes diverge."""
    lo_x, lo_y, hi_x, hi_y = polygon.bounds
    points = np.array([[lo_x, lo_y], [hi_x, lo_y], [hi_x, hi_y], [lo_x, hi_y]])
    for sign in (1, -1):
        output = []
        if not len(points):
            return shapely.Polygon()
        distances = sign * (points @ coefficients + offset) - tolerance
        for i, end in enumerate(points):
            start = points[i - 1]
            a, b = distances[i - 1], distances[i]
            if (a <= 0) != (b <= 0):
                output.append(start + (end - start) * a / (a - b))
            if b <= 0:
                output.append(end)
        points = np.asarray(output)
    return polygon.intersection(shapely.Polygon(points)) if len(points) >= 3 else shapely.Polygon()


def assemble_structures(meshes, protected=()):
    """One wall assembly across buildings, passages, portal frames and other layers.

    Facade paint is already partitioned and remains authoritative. Secondary wall
    triangles retain UVs/materials on exposed remainders; collision is unchanged.
    """
    records, refs = [], []
    for mi, mesh in enumerate([*protected, *meshes]):
        triangles = np.asarray(mesh.positions, np.float32).astype(float)[mesh.indices]
        normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        lengths = np.linalg.norm(normals, axis=1)
        for ti in np.flatnonzero((lengths > 1e-12) & (abs(normals[:, 1]) < 0.02 * lengths)):
            records.append((triangles[ti], normals[ti] / lengths[ti]))
            refs.append((mi - len(protected), ti))
    if not records:
        return list(meshes)
    points = np.array([p for p, _ in records])
    normals = np.array([n for _, n in records])
    low, high = points.min(axis=1), points.max(axis=1)
    direction = np.c_[-normals[:, 2], np.zeros(len(normals)), normals[:, 0]]
    direction /= np.linalg.norm(direction, axis=1)[:, None]
    basis = np.stack([direction, np.cross(normals, direction)], axis=1)
    xy = np.einsum("ijk,ilk->ijl", points - points[:, :1], basis)
    changed = {}
    # A vertical wall's x/z box contains all its floors. Index height and its
    # dominant horizontal span, then apply the original full 3D/plane filters.
    for axis in (0, 2):
        rows = np.flatnonzero((abs(normals[:, 2]) >= abs(normals[:, 0])) == (axis == 0))
        rows = rows[np.array([refs[i][0] >= 0 for i in rows], dtype=bool)]
        boxes = shapely.box(low[:, axis] - 0.006, low[:, 1],
                            high[:, axis] + 0.006, high[:, 1])
        tree = shapely.STRtree(boxes)
        for start in range(0, len(rows), 512):
            local, js = tree.query(boxes[rows[start:start+512]])
            ii = rows[start:start+512][local]
            keep = ((js < ii) & (high[js, 1] > low[ii, 1]) & (low[js, 1] < high[ii, 1])
                    & (np.einsum("ij,ij->i", normals[js], normals[ii]) > 0.99998))
            # Both old x/z boxes expanded by 6 mm, so retain the full 12 mm
            # broadphase band. Exact plane distance below still uses 6 mm.
            keep &= ((high[js][:, [0, 2]] + 0.012 >= low[ii][:, [0, 2]])
                     & (low[js][:, [0, 2]] - 0.012 <= high[ii][:, [0, 2]])).all(axis=1)
            ii, js = ii[keep], js[keep]
            if not len(ii):
                continue
            separation = np.einsum("ijk,ik->ij", points[js] - points[ii, :1], normals[ii])
            keep = (separation.min(axis=1) <= 0.006) & (separation.max(axis=1) >= -0.006)
            ii, js = ii[keep], js[keep]
            projected = np.einsum("ijk,ilk->ijl", points[js] - points[ii, :1], basis[ii])
            keep = ((projected.max(axis=1) > xy[ii].min(axis=1) + 1e-9)
                    & (projected.min(axis=1) < xy[ii].max(axis=1) - 1e-9)).all(axis=1)
            ii, js, projected = ii[keep], js[keep], projected[keep]
            if not len(ii):
                continue
            intersections = shapely.intersection(shapely.polygons(xy[ii]), shapely.polygons(projected))
            keep = shapely.area(intersections) > 1e-8
            ii, js, intersections = ii[keep], js[keep], intersections[keep]
            occluders = {}
            for i, j, polygon in zip(ii, js, intersections, strict=True):
                distance = (points[i] - points[j, 0]) @ normals[j]
                # A whole source triangle in the distance band needs no extra
                # rectangle intersection. Diverging planes retain exact clipping.
                if np.max(np.abs(distance)) > 0.006:
                    polygon = band_polygon(polygon, basis[i] @ normals[j],
                                           (points[i, 0] - points[j, 0]) @ normals[j])
                if polygon.area > 1e-8:
                    occluders.setdefault(i, []).append(polygon)
            for i, polygons in occluders.items():
                remainder = shapely.Polygon(xy[i]).difference(shapely.union_all(polygons))
                pieces = []
                for quad in trapezoids(remainder):
                    for indices in ([0, 1, 2], [0, 2, 3]):
                        coords = quad[indices]
                        if shapely.Polygon(coords).area > 1e-9:
                            pieces.append(coords)
                changed[refs[i]] = (pieces, xy[i], points[i, 0], basis[i])
    result = []
    for mi, original in enumerate(meshes):
        if not any(key[0] == mi for key in changed):
            result.append(original)
            continue
        mesh = copy.copy(original)
        positions, uvs, normals_out = [], [], []
        # Program ranges address vertices, not wall/triangle numbers. Retessellation
        # duplicates even untouched triangles, so every output vertex needs its
        # original instruction (including triangles preceding the first cut).
        rows = original.extras.get("facade_kit_walls")
        instructions = []
        vertex_rows = np.full(len(original.positions), -1, dtype=int)
        if rows is not None:
            for ri, row in enumerate(rows):
                vertex_rows[row["start"]:row["start"] + row["count"]] = ri
        last_row = None
        def instruction(indices):
            nonlocal last_row
            if rows is None:
                return
            owners = vertex_rows[indices]
            if owners[0] < 0 or np.any(owners != owners[0]):
                raise ValueError(f"Mixed facade instruction in {original.name}")
            if last_row == owners[0]:
                instructions[-1]["count"] += 3
            else:
                instructions.append({**rows[owners[0]], "start": (len(positions) - 1) * 3, "count": 3})
            last_row = owners[0]

        for ti, indices in enumerate(original.indices):
            replacement = changed.get((mi, ti))
            if replacement is None:
                positions.append(original.positions[indices])
                instruction(indices)
                if original.uvs is not None:
                    uvs.append(original.uvs[indices])
                if original.normals is not None:
                    normals_out.append(original.normals[indices])
                continue
            pieces, xy, origin, basis = replacement
            for coords in pieces:
                weights = np.linalg.solve(np.c_[xy, np.ones(3)].T, np.c_[coords, np.ones(3)].T).T
                positions.append(origin + coords @ basis)
                instruction(indices)
                if original.uvs is not None:
                    uvs.append(weights @ original.uvs[indices])
                if original.normals is not None:
                    normals_out.append(weights @ original.normals[indices])
        if not positions:
            continue
        mesh.positions = np.concatenate(positions)
        mesh.indices = np.arange(len(mesh.positions), dtype=np.uint32).reshape(-1, 3)
        mesh.uvs = np.concatenate(uvs) if uvs else None
        mesh.normals = np.concatenate(normals_out) if normals_out else None
        mesh.extras = {k: v for k, v in original.extras.items() if k != "component_box"}
        if rows is not None:
            mesh.extras["facade_kit_walls"] = instructions
        result.append(mesh)
    return result


def assemble_layers(tile, layer_files, building_meshes, shells, out, stage, cache=None):
    """Single architectural assembly before any packaged visual can be published."""
    import json

    from turku3d.coarse.build import _write
    from turku3d.layers import write_visual
    from turku3d.wall_surfaces import layer_meshes, validate_meshes, validate_neighbours

    if building_meshes:
        programs = [
            m.extras.pop("facade_program") for m in building_meshes if "facade_program" in m.extras
        ]
        _write(out / "facade-programs.json", json.dumps(programs, sort_keys=True).encode())
        write_visual(stage / "buildings.vis.glb", building_meshes, root_translation=tile.origin)
        layer_files.append(stage / "buildings.vis.glb")

    wall_layers = layer_meshes(layer_files)
    protected = [
        m for m in wall_layers if m.extras.get("material_key") in {"facade_kit", "facade_stored"}
    ]
    secondary = [
        m
        for m in wall_layers
        if m.extras.get("material_key") not in {"facade_kit", "facade_stored"}
    ]
    def assemble():
        result = assemble_structures(protected + secondary)
        validate_meshes(result, tile.id)
        return result

    if cache is None:
        wall_layers = assemble()
    else:
        from pathlib import Path
        from turku3d.stage_cache import source_key

        wall_layers = cache.run(
            "wall_meshes", source_key(Path(__file__), Path(__file__).with_name("wall_surfaces.py")),
            [path.read_bytes() for path in layer_files], assemble,
        )
    validate_neighbours(wall_layers, shells, tile, out.parent)
    write_visual(stage / "architecture.vis.glb", wall_layers, root_translation=tile.origin)
    layer_files = [p for p in layer_files if p.name == "vegetation.vis.glb"]
    layer_files.append(stage / "architecture.vis.glb")

    return layer_files
