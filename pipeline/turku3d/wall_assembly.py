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


def assemble_structures(meshes, protected=(), *, protected_only=False):
    """One wall assembly across buildings, passages, portal frames and other layers.

    Facade paint is already partitioned and remains authoritative. Secondary wall
    triangles retain UVs/materials on exposed remainders; collision is unchanged.
    Neighbour admission cuts already assembled meshes against protected faces only.
    """
    if protected_only and not protected:
        return list(meshes)
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

    def projected_candidates(a, b):
        # For any axis n, intersection area <= projected overlap width / |n|
        # times diameter(a). Its L1 bounding-box diameter is an upper bound;
        # max(abs(n)) is a lower bound for |n|. This also bounds touching pairs.
        # Round every bound operation outwards, including the dot products.
        # Rounded edge subtraction only chooses an axis, so needs no correction.
        keep = np.ones(len(a), dtype=bool)
        finite = np.isfinite(a).all(axis=(1, 2)) & np.isfinite(b).all(axis=(1, 2))

        def interval(points, nx, ny):
            x = points[:, :, 0] * nx[:, None]
            y = points[:, :, 1] * ny[:, None]
            low = np.nextafter(np.nextafter(x, -np.inf) + np.nextafter(y, -np.inf), -np.inf)
            high = np.nextafter(np.nextafter(x, np.inf) + np.nextafter(y, np.inf), np.inf)
            return low.min(axis=1), high.max(axis=1)

        with np.errstate(over="ignore", invalid="ignore", divide="ignore", under="ignore"):
            diameter = np.nextafter(
                np.nextafter(np.ptp(a[:, :, 0], axis=1), np.inf)
                + np.nextafter(np.ptp(a[:, :, 1], axis=1), np.inf),
                np.inf,
            )
            for edge in range(3):
                active = np.flatnonzero(keep & finite)
                if not len(active):
                    break
                aa, bb = a[active], b[active]
                direction = aa[:, (edge + 1) % 3] - aa[:, edge]
                nx, ny = -direction[:, 1], direction[:, 0]
                lo_a, hi_a = interval(aa, nx, ny)
                lo_b, hi_b = interval(bb, nx, ny)
                width = np.maximum(
                    0,
                    np.nextafter(
                        np.minimum(hi_a, hi_b) - np.maximum(lo_a, lo_b),
                        np.inf,
                    ),
                )
                denominator = np.maximum(np.abs(nx), np.abs(ny))
                upper = np.nextafter(
                    np.nextafter(width * diameter[active], np.inf) / denominator,
                    np.inf,
                )
                valid = np.isfinite(upper) & np.isfinite(direction).all(axis=1) & (denominator > 0)
                # Ambiguous arithmetic stays with GEOS; retain the scalar cut gate.
                keep[active[valid & (upper < 1e-8)]] = False
        return keep

    # A neighbour pass must not repeat local cuts after another float32 conversion.
    indexed_count = (
        next((i for i, (mi, _) in enumerate(refs) if mi >= 0), len(refs))
        if protected_only
        else len(refs)
    )
    points = np.array([p for p, _ in records])
    normals = np.array([n for _, n in records])
    low, high = points.min(axis=1), points.max(axis=1)
    direction = np.c_[-normals[:, 2], np.zeros(len(normals)), normals[:, 0]]
    direction /= np.linalg.norm(direction, axis=1)[:, None]
    bases = np.stack([direction, np.cross(normals, direction)], axis=1)
    projected_sources = np.einsum("ijk,ilk->ijl", points - points[:, :1], bases)
    changed = {}
    original_boxes = shapely.box(
        low[:, 0] - 0.006, low[:, 2] - 0.006, high[:, 0] + 0.006, high[:, 2] + 0.006
    )
    original_tree = shapely.STRtree(original_boxes[:indexed_count])
    # A vertical wall's x/z box contains all its floors. Index height and its
    # dominant horizontal span, then apply the original full 3D/plane filters.
    for axis in (0, 2):
        rows = np.flatnonzero((abs(normals[:, 2]) >= abs(normals[:, 0])) == (axis == 0))
        rows = rows[np.array([refs[i][0] >= 0 for i in rows], dtype=bool)]
        boxes = shapely.box(low[:, axis] - 0.006, low[:, 1], high[:, axis] + 0.006, high[:, 1])
        tree = shapely.STRtree(boxes[:indexed_count])
        for start in range(0, len(rows), 512):
            local, js = tree.query(boxes[rows[start : start + 512]])
            ii = rows[start : start + 512][local]
            keep = (js < ii) & (high[js, 1] > low[ii, 1]) & (low[js, 1] < high[ii, 1])
            ii, js = ii[keep], js[keep]
            # Both old x/z boxes expanded by 6 mm, so retain the full 12 mm
            # broadphase band before gathering normals. Exact plane distance
            # below still uses 6 mm.
            keep = (
                (high[js, 0] + 0.012 >= low[ii, 0])
                & (low[js, 0] - 0.012 <= high[ii, 0])
                & (high[js, 2] + 0.012 >= low[ii, 2])
                & (low[js, 2] - 0.012 <= high[ii, 2])
            )
            ii, js = ii[keep], js[keep]
            keep = np.einsum("ij,ij->i", normals[js], normals[ii]) > 0.99998
            ii, js = ii[keep], js[keep]
            if not len(ii):
                continue
            separation = np.einsum("ijk,ik->ij", points[js] - points[ii, :1], normals[ii])
            keep = (separation.min(axis=1) <= 0.006) & (separation.max(axis=1) >= -0.006)
            ii, js = ii[keep], js[keep]
            projected = np.einsum("ijk,ilk->ijl", points[js] - points[ii, :1], bases[ii])
            keep = (
                (projected.max(axis=1) > projected_sources[ii].min(axis=1) - 1e-9)
                & (projected.min(axis=1) < projected_sources[ii].max(axis=1) + 1e-9)
            ).all(axis=1)
            ii, js, projected = ii[keep], js[keep], projected[keep]
            if not len(ii):
                continue
            keep = projected_candidates(projected_sources[ii], projected)
            ii, js, projected = ii[keep], js[keep], projected[keep]
            if not len(ii):
                continue
            intersections = shapely.intersection(
                shapely.polygons(projected_sources[ii]), shapely.polygons(projected)
            )
            # Native projection is only a conservative rejection stage. Near
            # parallel distance bands amplify tiny rounding differences, so actual
            # cuts retain the predecessor's scalar basis and STRtree order.
            # Match the scalar cut admission area. Numerical touching-edge slivers
            # below this threshold cannot produce an occluder, but used to send
            # dense facade tessellation through the expensive scalar loop.
            for i in np.unique(ii[shapely.area(intersections) > 1e-8]):
                mi, ti = refs[i]
                js = original_tree.query(original_boxes[i])
                if protected_only:
                    # Keep external coverage in its original protected record order.
                    js.sort()
                js = js[
                    (js < i)
                    & (normals[js] @ normals[i] > 0.99998)
                    & (high[js, 1] > low[i, 1])
                    & (low[js, 1] < high[i, 1])
                ]
                if not len(js):
                    continue
                p, n = records[i]
                separation = (points[js] - p[0]) @ n
                js = js[(separation.min(axis=1) <= 0.006) & (separation.max(axis=1) >= -0.006)]
                if not len(js):
                    continue
                u = np.array([-n[2], 0, n[0]])
                u /= np.linalg.norm(u)
                v = np.cross(n, u)
                basis = np.array([u, v])
                xy = (p - p[0]) @ basis.T
                surface = shapely.Polygon(xy)
                projected = (points[js] - p[0]) @ basis.T
                # Reject touching tessellation edges before constructing GEOS polygons.
                keep = (
                    (projected.max(axis=1) > xy.min(axis=0) + 1e-9)
                    & (projected.min(axis=1) < xy.max(axis=0) - 1e-9)
                ).all(axis=1)
                js = js[keep]
                occluders = []
                for j in js:
                    q = points[j]
                    polygon = shapely.Polygon((q - p[0]) @ basis.T)
                    polygon = surface.intersection(polygon)
                    if polygon.area <= 1e-8:
                        continue
                    polygon = band_polygon(polygon, basis @ normals[j], (p[0] - q[0]) @ normals[j])
                    if polygon.area > 1e-8:
                        occluders.append(polygon)
                if not occluders:
                    continue
                remainder = surface.difference(shapely.union_all(occluders))
                pieces = []
                for quad in trapezoids(remainder):
                    for indices in ([0, 1, 2], [0, 2, 3]):
                        coords = quad[indices]
                        if shapely.Polygon(coords).area > 1e-9:
                            # The basis follows the original normal: preserve front-face winding.
                            pieces.append(coords)
                changed[mi, ti] = (pieces, xy, p[0], basis)
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
                vertex_rows[row["start"] : row["start"] + row["count"]] = ri
        last_row = None

        def instruction(
            indices,
            rows=rows,
            vertex_rows=vertex_rows,
            original=original,
            instructions=instructions,
            positions=positions,
        ):
            nonlocal last_row
            if rows is None:
                return
            owners = vertex_rows[indices]
            if owners[0] < 0 or np.any(owners != owners[0]):
                raise ValueError(f"Mixed facade instruction in {original.name}")
            if last_row == owners[0]:
                instructions[-1]["count"] += 3
            else:
                instructions.append(
                    {**rows[owners[0]], "start": (len(positions) - 1) * 3, "count": 3}
                )
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
    from pathlib import Path

    from turku3d.coarse.build import _write
    from turku3d.layers import write_visual
    from turku3d.stage_cache import source_key
    from turku3d.wall_surfaces import instanced_layer, layer_meshes, validate_neighbours

    if building_meshes:
        programs = [
            m.extras.pop("facade_program") for m in building_meshes if "facade_program" in m.extras
        ]
        _write(out / "facade-programs.json", json.dumps(programs, sort_keys=True).encode())

        def buildings(directory):
            write_visual(
                directory / "buildings.vis.glb", building_meshes, root_translation=tile.origin
            )

        if cache is None:
            buildings(stage)
        else:
            cache.files(
                "building_layer",
                source_key(
                    Path(__file__).with_name("layers.py"), Path(__file__).parent / "package/gltf.py"
                ),
                [tile, cache.metrics.get("building_visual", {}).get("key", building_meshes)],
                stage,
                buildings,
            )
        layer_files.append(stage / "buildings.vis.glb")

    def assemble():
        wall_layers = layer_meshes(layer_files)
        protected = [
            m
            for m in wall_layers
            if m.extras.get("material_key") in {"facade_kit", "facade_stored"}
        ]
        secondary = [
            m
            for m in wall_layers
            if m.extras.get("material_key") not in {"facade_kit", "facade_stored"}
        ]
        return assemble_structures(protected + secondary)

    if cache is None:
        wall_layers = assemble()
    else:
        wall_layers = cache.run(
            "wall_meshes",
            source_key(
                assemble_structures,
                band_polygon,
                instanced_layer,
                layer_meshes,
                trapezoids,
                Path(__file__).parent / "package/gltf.py",
            ),
            [path.read_bytes() for path in layer_files],
            assemble,
        )
    geometry_key = cache.metrics["wall_meshes"]["key"] if cache else None
    neighbours = validate_neighbours(
        wall_layers, shells, tile, out.parent, geometry_key=geometry_key, report_cache=cache
    )

    def architecture(directory):
        write_visual(directory / "architecture.vis.glb", wall_layers, root_translation=tile.origin)

    if cache is None:
        architecture(stage)
    else:
        cache.files(
            "wall_visual",
            source_key(
                Path(__file__).with_name("layers.py"), Path(__file__).parent / "package/gltf.py"
            ),
            [tile, neighbours["geometry_key"]],
            stage,
            architecture,
        )
    layer_files = [p for p in layer_files if instanced_layer(p)]
    layer_files.append(stage / "architecture.vis.glb")

    return layer_files
