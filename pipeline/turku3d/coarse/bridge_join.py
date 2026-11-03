"""Shared bridge-end terrain, collision and surface-height joins."""

import numpy as np
from scipy.spatial import cKDTree


def terrain_sampler(points):
    """Sample the rendered ground triangles, also beyond the current tile boundary.

    A nearest-keypoint height can sit metres above the interpolated bank TIN.
    Bridge joins must use the latter, on the same global two-metre lattice.
    """
    from functools import lru_cache

    from scipy.interpolate import LinearNDInterpolator

    from turku3d import config as C

    from .inputs import CELLS

    linear = LinearNDInterpolator(points[:, [0, 2]], points[:, 1])
    tree = cKDTree(points[:, [0, 2]])
    step = C.VISUAL_TILE_SIZE / CELLS

    @lru_cache(maxsize=8192)
    def vertex(i, j):
        xz = [i * step, j * step]
        value = float(linear([xz])[0])
        return value if np.isfinite(value) else float(points[tree.query(xz)[1], 1])

    def sample(x, z):
        u, v = x / step, z / step
        i, j = int(np.floor(u)), int(np.floor(v))
        fu, fv = u - i, v - j
        if fu + fv <= 1:
            return vertex(i, j) * (1 - fu - fv) + vertex(i + 1, j) * fu + vertex(i, j + 1) * fv
        return (
            vertex(i + 1, j + 1) * (fu + fv - 1)
            + vertex(i, j + 1) * (1 - fu)
            + vertex(i + 1, j) * (1 - fv)
        )

    return sample


def connected_approaches(members, ways):
    """A shared road deck also owns the land continuations of its side paths."""
    from .bridges import PATHS, RAILS

    def mode(way):
        return way["tags"].get("railway") in RAILS, way["tags"].get("highway") in PATHS

    return [
        way
        for way in ways
        if not way["bridge"]
        and any(
            mode(way) == mode(member)
            and way["line"].boundary.distance(member["line"].boundary) < 0.05
            for member in members
        )
    ]


def approach_patches(way, line, ramps, height_at, sample, blocked, feather):
    """Canonical landward tapers at the axis and every mapped member end."""
    import shapely
    from shapely.ops import substring

    if way.get("landing") or way.get("stair_access"):
        return []
    patches = []

    def patch(path, top, width, lateral):
        if path.length < 0.05:
            return
        knots = np.unique(np.r_[np.arange(0, path.length, 2), path.length])
        query = shapely.get_coordinates(shapely.line_interpolate_point(path, knots))
        values = top(query)
        last = np.asarray(path.coords[-1])
        previous = np.asarray(path.coords[-2])
        direction = (last - previous) / np.linalg.norm(last - previous)
        area_line = shapely.LineString([*path.coords, last + direction * feather])
        area = area_line.buffer(width / 2 + lateral, cap_style="flat").difference(blocked)
        if not area.is_empty:
            patches.append(
                dict(
                    line=path,
                    knots=knots,
                    heights=values,
                    width=width,
                    lateral=lateral,
                    feather=feather,
                    blocked=blocked,
                    area=area,
                )
            )

    axis = way["line"]
    start, end = ramps
    for path in (
        substring(line, start, 0) if start else None,
        substring(line, start + axis.length, line.length) if end else None,
    ):
        if path is not None:
            patch(path, lambda q: height_at(q) - 0.02, way["width"], 2.0)
    members = way.get("member_ways", [way])
    for approach in way.get("approaches", []):
        if approach["tags"].get("highway") == "steps":
            continue  # Mapped flights, not a terrain ramp, own these climbs.
        for endpoint in (0, -1):
            coords = list(approach["line"].coords)
            if endpoint == -1:
                coords.reverse()
            origin = np.asarray(coords[0])
            at = shapely.Point(origin)
            if not any(member["line"].boundary.distance(at) < 0.05 for member in members):
                continue
            path = shapely.LineString(coords)
            maximum = min(30.0, path.length)
            first = min(3.0, maximum)
            top = float(height_at(origin[None])[0])
            reach = maximum
            for distance in np.unique(np.r_[first, np.arange(4, maximum, 2), maximum]):
                q = np.asarray(path.interpolate(float(distance)).coords[0])
                ahead = np.asarray(path.interpolate(min(path.length, distance + 2)).coords[0])
                if (
                    abs(top - sample(*q)) <= 0.08 * distance
                    and abs(sample(*ahead) - sample(*q)) <= 0.2
                ):
                    reach = float(distance)
                    break
            path = substring(path, 0, reach)
            bottom = sample(*path.coords[-1])

            def level(query, path=path, top=top, bottom=bottom):
                station = shapely.line_locate_point(path, shapely.points(query))
                return top + (bottom - top) * station / path.length

            patch(path, level, approach["width"], feather)
    return patches


def grade(positions, patches, base_heights=None):
    """Raise land to the same end surface in grid, final terrain and collision."""
    import shapely
    from .walkable import coordinates

    positions = np.asarray(positions, dtype=np.float64)
    result = positions.copy()
    baseline = positions[:, 1] if base_heights is None else np.asarray(base_heights)
    # Every taper evaluates the original surface. Overlapping lanes must not
    # compound their weights or depend on source order.
    for patch in patches:
        ids = np.flatnonzero(
            (
                shapely.intersects_xy(patch["area"].buffer(1e-7), positions[:, 0], positions[:, 2])
                & ~shapely.intersects_xy(patch["blocked"], positions[:, 0], positions[:, 2])
            )
        )
        if not len(ids):
            continue
        station, lateral = coordinates(patch["line"], positions[ids][:, [0, 2]])
        weight = np.clip(1 - (abs(lateral) - patch["width"] / 2) / patch["lateral"], 0, 1)
        weight *= np.clip(1 - (station - patch["line"].length) / patch["feather"], 0, 1)
        weight[station < -1e-7] = 0
        target = np.interp(station, patch["knots"], patch["heights"])
        use = (weight > 1e-12) & (target > baseline[ids])
        ids, target, weight = ids[use], target[use], weight[use]
        result[ids, 1] = np.maximum(
            result[ids, 1], baseline[ids] + weight * (target - baseline[ids])
        )
    return result


def constrain(mesh, patches, base=None):
    """Insert end/path edges before grading, preserving ground holes/materials."""
    import shapely
    from turku3d.ground.mesh import HeightQuery, Mesh
    from .surfaces import split_boundaries

    if not patches:
        return mesh
    boundaries = []
    for patch in patches:
        core = (
            patch["line"].buffer(patch["width"] / 2, cap_style="flat").difference(patch["blocked"])
        )
        boundaries.extend([core.boundary, patch["area"].boundary])
    partitions = shapely.get_parts(
        shapely.polygonize(shapely.get_parts(shapely.union_all(boundaries)))
    )
    triangles = mesh.positions[mesh.indices]
    planar = (
        abs(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])[:, 1])
        > 1e-9
    )
    p, ix, material, _ = split_boundaries(
        mesh.positions,
        mesh.indices,
        mesh.material,
        planar,
        [(g, 0) for g in partitions],
        preserve_materials=True,
    )
    # Banks and road grading consume the already raised grid. Evaluate the
    # final contact edges against its original TIN, rather than blending that
    # raised result a second time and progressively inflating the bank.
    baseline = None if base is None else HeightQuery(base)(p[:, [0, 2]])
    if baseline is not None:
        baseline = np.where(np.isfinite(baseline), baseline, p[:, 1])
    raised = grade(p, patches, baseline)
    centres = p[ix].mean(axis=1)
    area = shapely.union_all([patch["area"] for patch in patches])
    active = shapely.contains_xy(area, centres[:, 0], centres[:, 2])
    # Keep the span-side triangles at their original height. Sharing raised
    # cap vertices with a sparse unmodified triangle would fill the underpass.
    out_indices = ix.copy()
    out_indices[active] += len(p)
    out_positions = [p, raised]
    out_material = [material, mesh.material[~planar]]
    out_indices = [out_indices, mesh.indices[~planar]]
    # Close the intentional vertical abutment where the land meets the open
    # span or a protected lower corridor. Outer feather edges have zero rise.
    from turku3d.structures.geometry import Faces
    from turku3d.col.materials import mat_id

    faces = Faces()
    if active.any():
        _, vertex = np.unique(np.round(p, 8), axis=0, return_inverse=True)
        edges = np.concatenate([ix[active][:, side] for side in ([0, 1], [1, 2], [2, 0])])
        _, first, counts = np.unique(
            np.sort(vertex[edges], axis=1), axis=0, return_index=True, return_counts=True
        )
        for a, b in edges[first[counts == 1]]:
            if max(raised[a, 1] - p[a, 1], raised[b, 1] - p[b, 1]) > 1e-7:
                faces.face([p[a], p[b], raised[b], raised[a]])
    if faces.indices:
        offset = sum(len(part) for part in out_positions)
        out_positions.append(np.asarray(faces.positions))
        out_indices.append(np.asarray(faces.indices, np.uint32) + offset)
        out_material.append(np.full(len(faces.indices), mat_id("quay"), np.uint8))
    return Mesh(
        np.concatenate(out_positions), np.concatenate(out_indices), np.concatenate(out_material)
    ).compact()


def sampler(sample, mesh, patches):
    """Sample the actual land triangles, including the upper abutment edge.

    At a vertical cap both the lower span floor and the raised land have the
    same x/z edge. A walker on the land must choose its upper triangle there.
    """
    import shapely
    from turku3d.ground.mesh import Mesh
    from .street_profiles import ProfiledSurface, sampler as surface_sampler

    if not patches:
        return sample
    area = shapely.union_all([patch["area"] for patch in patches])
    triangles = mesh.positions[mesh.indices]
    centres = triangles.mean(axis=1)
    active = shapely.contains_xy(area, centres[:, 0], centres[:, 2])
    active &= (
        abs(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])[:, 1])
        > 1e-9
    )
    upper = Mesh(mesh.positions, mesh.indices[active], mesh.material[active])
    return surface_sampler(sample, ProfiledSurface(upper, area.buffer(1e-7)))
