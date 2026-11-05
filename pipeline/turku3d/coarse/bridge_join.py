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


# A mapped walk or flight this far below an apron's top is a separate level:
# a quay path beside an abutment, or the foot of the stair down to it.
LEVEL_GAP = 1.0


def walk_levels(walkable=(), stairs=()):
    """Mapped walk and flight centrelines with their own measured surface heights."""

    def number(value, default):
        try:
            n = float(str(value).removesuffix(" m"))
        except (TypeError, ValueError):
            return default
        return n if np.isfinite(n) else default

    levels = []
    for walk in walkable:
        centre = np.asarray(walk["heights"])[:, int(np.argmin(np.abs(walk["cross"])))]
        known = np.isfinite(centre)
        if known.any():
            # The margin keeps the abutment face clear of the walk's paving edge.
            levels.append(
                (walk["line"], walk["width"] / 2 + 0.3, np.asarray(walk["knots"])[known],
                 centre[known])
            )
    for _, line, tags in stairs:
        profile = tags.get("_profile") or {}
        heights = np.asarray(profile.get("heights", ()), dtype=float)
        if len(heights) and np.isfinite(heights).all():
            # Same width as the flight cut. Its side stays flush with the raised
            # land; the round end keeps the landing in front of the lower step.
            width = np.clip(number(tags.get("width"), 2.5), 0.5, 30)
            levels.append((line, width / 2, np.asarray(profile["stations"], float), heights))
    return levels


def lower_levels(path, knots, heights, reach, levels):
    """Parts of other mapped walks inside an apron's reach, a level below its top."""
    import shapely
    from shapely.ops import substring

    parts = []
    shapely.prepare(reach)
    for line, half, stations, values in levels:
        if line.length < 0.05 or line.distance(reach) > half:
            continue
        s = np.unique(np.r_[np.arange(0, line.length, 0.5), line.length])
        points = shapely.line_interpolate_point(line, s)
        top = np.interp(shapely.line_locate_point(path, points), knots, heights)
        low = shapely.distance(reach, points) <= half
        low &= np.interp(s, stations, values) < top - LEVEL_GAP
        runs = np.flatnonzero(low)
        for run in np.split(runs, np.flatnonzero(np.diff(runs) > 1) + 1):
            if not len(run):
                continue
            piece = substring(line, max(0.0, s[run[0]] - 0.5), min(line.length, s[run[-1]] + 0.5))
            parts.append(piece.buffer(half, join_style="mitre"))
    return shapely.union_all(parts) if parts else shapely.Polygon()


def approach_patches(way, line, ramps, height_at, sample, blocked, feather, levels=()):
    """Canonical landward tapers at the axis and every mapped member end.

    Lower mapped walks and flights (`walk_levels`) are protected like the
    crossed corridors in `blocked`: the taper stops at a vertical abutment face.
    """
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
        reach = area_line.buffer(width / 2 + lateral, cap_style="flat")
        lower = lower_levels(path, knots, values, reach, levels).difference(
            path.buffer(width / 2, cap_style="flat")
        )
        guard = blocked if lower.is_empty else shapely.union_all([blocked, lower])
        area = reach.difference(guard)
        if not area.is_empty:
            patches.append(
                dict(
                    line=path,
                    knots=knots,
                    heights=values,
                    width=width,
                    lateral=lateral,
                    feather=feather,
                    blocked=guard,
                    lower=lower,
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
    from .bridges import building_region

    region = building_region(way)
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
            maximum = min(APPROACH_MAX, path.length, region_exit(path, region))
            first = min(3.0, maximum)
            top = float(height_at(origin[None])[0])
            reach = maximum
            for distance in np.unique(np.r_[first, np.arange(4, maximum, 2), maximum]):
                q = np.asarray(path.interpolate(float(distance)).coords[0])
                ahead = np.asarray(path.interpolate(min(path.length, distance + 2)).coords[0])
                rise = abs(top - sample(*q))
                if (
                    rise <= 0.08 * distance
                    and rise <= vertical_curve_rise(distance)
                    and abs(sample(*ahead) - sample(*q)) <= 0.2
                ):
                    reach = float(distance)
                    break
            path = substring(path, 0, reach)
            end = np.asarray(path.coords[-1])
            bottom = sample(*end)
            # The deck's own grade at its end and the ground's grade at the ramp foot.
            direction = (end - origin) / max(np.linalg.norm(end - origin), 1e-9)
            back = origin - direction * 2.0
            grade_top = (top - float(height_at(back[None])[0])) / 2.0
            foot = end + direction * 2.0
            grade_bottom = (sample(*foot) - bottom) / 2.0

            def level(query, path=path, top=top, bottom=bottom, g0=grade_top, g1=grade_bottom):
                station = shapely.line_locate_point(path, shapely.points(query))
                return vertical_curve(station, path.length, top, bottom, g0, g1)

            patch(path, level, approach["width"], feather)
    return patches


APPROACH_MAX = 30.0  # m: the longest landward ramp from a bridge end
# Largest curvature (1/m) of a ramp's vertical curve: 1.5 m/s^2 at 40 km/h (v^2 * k), below the
# 0.3 g a speed hump gives. A straight ramp's grade breaks are a kink the car's suspension slams.
CURVE_KAPPA = 0.012
GRADE_LIMIT = 0.12  # the grade a matched end slope may carry into the curve


def region_exit(path, region, margin=0.5):
    """Distance along ``path`` (starting inside ``region``) to its first exit, less ``margin``."""
    import shapely

    if region.is_empty:
        return path.length
    crossings = shapely.get_parts(shapely.intersection(path, region.boundary))
    distances = [d for d in (path.project(shapely.Point(c)) for g in crossings
                             for c in shapely.get_coordinates(g)) if d > 0.01]
    return max(0.05, min(distances) - margin) if distances else path.length


def vertical_curve_rise(length):
    """The largest rise a smooth ramp of this length takes within CURVE_KAPPA (6 h / L^2)."""
    return CURVE_KAPPA * length * length / 6.0


def vertical_curve(station, length, top, bottom, grade_top, grade_bottom):
    """Ramp height at ``station`` from the deck end: a cubic Hermite curve that leaves the deck at
    its own grade and meets the ground at the ground's grade, instead of a straight ramp with a
    grade break at each end (a jolt at speed). Shared by terrain, collision and lane heights."""
    station = np.clip(np.asarray(station, dtype=np.float64), 0.0, max(length, 1e-9))
    if length <= 1e-9:
        return np.full_like(station, top)
    t = station / length
    g0, g1 = (float(np.clip(g, -GRADE_LIMIT, GRADE_LIMIT)) * length
              for g in (grade_top, grade_bottom))
    h00, h10 = 2 * t**3 - 3 * t**2 + 1, t**3 - 2 * t**2 + t
    h01, h11 = -2 * t**3 + 3 * t**2, t**3 - t**2
    return h00 * top + h10 * g0 + h01 * bottom + h11 * g1


def grade(positions, patches, base_heights=None, edges=False):
    """Raise land to the same end surface in grid, final terrain and collision.

    With `edges`, land vertices on a protected boundary rise too. Only the land
    side's own copies use them, so the drop closes as one vertical face rather
    than a wedge sloping across the last triangle to the lower corridor.
    """
    import shapely
    from .walkable import coordinates

    positions = np.asarray(positions, dtype=np.float64)
    result = positions.copy()
    baseline = positions[:, 1] if base_heights is None else np.asarray(base_heights)
    # Every taper evaluates the original surface. Overlapping lanes must not
    # compound their weights or depend on source order.
    for patch in patches:
        blocked = patch["blocked"].buffer(-1e-6) if edges else patch["blocked"]
        ids = np.flatnonzero(
            (
                shapely.intersects_xy(patch["area"].buffer(1e-7), positions[:, 0], positions[:, 2])
                & ~shapely.intersects_xy(blocked, positions[:, 0], positions[:, 2])
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
    from .surfaces import split_boundaries, steep

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
    raised = grade(p, patches, baseline, edges=True)
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
    # Along a protected lower walk or flight the raised sheet ends at one clean
    # open edge: the surface closure seals it with a single face down to the
    # lower level. Per-edge quads there, on the cut's many short pieces, left a
    # comb of slivers with gaps under the raised land (issue 023).
    lower = shapely.union_all([q["lower"] for q in patches if not q.get("lower", shapely.Polygon()).is_empty])
    if active.any():
        _, vertex = np.unique(np.round(p, 8), axis=0, return_inverse=True)
        edges = np.concatenate([ix[active][:, side] for side in ([0, 1], [1, 2], [2, 0])])
        _, first, counts = np.unique(
            np.sort(vertex[edges], axis=1), axis=0, return_index=True, return_counts=True
        )
        edges = edges[first[counts == 1]]
        if not lower.is_empty and len(edges):
            middle = (p[edges[:, 0]] + p[edges[:, 1]]) / 2
            edges = edges[~shapely.intersects_xy(lower.buffer(1e-4), middle[:, 0], middle[:, 2])]
        for a, b in edges:
            if max(raised[a, 1] - p[a, 1], raised[b, 1] - p[b, 1]) > 1e-7:
                faces.face([p[a], p[b], raised[b], raised[a]])
    if faces.indices:
        offset = sum(len(part) for part in out_positions)
        out_positions.append(np.asarray(faces.positions))
        out_indices.append(np.asarray(faces.indices, np.uint32) + offset)
        out_material.append(np.full(len(faces.indices), mat_id("quay"), np.uint8))
    positions, indices = np.concatenate(out_positions), np.concatenate(out_indices)
    material = np.concatenate(out_material)
    # Where the source terrain already steps down to a protected lower walk, its
    # near-vertical faces are the same exposed abutment: masonry, not lawn or paving.
    triangles = positions[indices]
    exposed = steep(triangles) & (np.ptp(triangles[:, :, 1], axis=1) > 0.3)
    if exposed.any():
        centres = triangles[exposed].mean(axis=1)
        near = shapely.intersects_xy(area.buffer(1.0), centres[:, 0], centres[:, 2])
        material = material.copy()
        material[np.flatnonzero(exposed)[near]] = mat_id("quay")
    return Mesh(positions, indices, material).compact()


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
