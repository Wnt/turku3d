"""Canonical lidar-fitted riverside paving and smooth embankment cross sections."""

import json

import numpy as np
import shapely
from scipy.ndimage import median_filter

from turku3d import config as C


def robust_plane(xz, heights):
    """Dominant surface, followed by trimmed plane fits; water/wall returns are outliers."""
    if len(heights) < 6:
        return None
    bins, counts = np.unique(np.floor(heights / 0.2).astype(int), return_counts=True)
    centre = (bins[np.argmax(counts)] + 0.5) * 0.2
    keep = np.abs(heights - centre) < 0.35
    basis = np.c_[np.ones(len(xz)), xz]
    for _ in range(4):
        if keep.sum() < 6 or np.linalg.matrix_rank(basis[keep]) < 3:
            return None
        fit = np.linalg.lstsq(basis[keep], heights[keep], rcond=None)[0]
        residual = heights - basis @ fit
        mad = np.median(np.abs(residual[keep] - np.median(residual[keep])))
        keep = np.abs(residual) < max(0.06, min(0.18, 3 * 1.4826 * mad))
    if keep.sum() < 6 or np.quantile(np.abs(residual[keep]), 0.9) > 0.15:
        return None
    return fit


def coordinates(line, points):
    """Signed lateral distance and station on the complete, uncut OSM way."""
    query = shapely.points(points)
    station = shapely.line_locate_point(line, query)
    centre = shapely.get_coordinates(shapely.line_interpolate_point(line, station))
    a = shapely.get_coordinates(shapely.line_interpolate_point(line, np.maximum(0, station - 0.05)))
    b = shapely.get_coordinates(
        shapely.line_interpolate_point(line, np.minimum(line.length, station + 0.05))
    )
    direction = b - a
    direction /= np.maximum(np.linalg.norm(direction, axis=1), 1e-12)[:, None]
    normal = np.c_[-direction[:, 1], direction[:, 0]]
    # Projection clamps to the endpoints, but a path does not own the terrain
    # beyond its flat end cap. Keep the longitudinal residual there, both for
    # survey fitting and grading (notably upper walks ending at bridge decks).
    residual = np.sum((points - centre) * direction, axis=1)
    station += np.where((station < 1e-8) | (station > line.length - 1e-8), residual, 0)
    return station, np.sum((points - centre) * normal, axis=1)


def near_line(line, points, reach):
    """Exact cutoff for finite points, rejecting whole cells far from the path."""
    candidates = np.arange(len(points))
    if len(points) >= 4096:
        origin = points.min(axis=0)
        cells = np.floor((points - origin) / 4).astype(np.int64)
        size = cells.max(axis=0) + 1
        if int(size[0]) * int(size[1]) <= 1 << 20:
            flat = cells[:, 0] * size[1] + cells[:, 1]
            occupied = np.zeros(int(size[0]) * int(size[1]), bool)
            occupied[flat] = True
            used = np.flatnonzero(occupied)
            x, z = (origin + np.c_[used // size[1], used % size[1]] * 4).T
            boxes = shapely.box(x - 1e-3, z - 1e-3, x + 4 + 1e-3, z + 4 + 1e-3)
            # The expanded box contains every return assigned to it, so its
            # distance is a lower bound. Never accept a point from this bound.
            occupied[used] = shapely.distance(boxes, line) <= reach + 1e-6
            candidates = np.flatnonzero(occupied[flat])
    query = shapely.points(points[candidates])
    keep = np.zeros(len(points), bool)
    keep[candidates] = shapely.distance(query, line) < reach
    return keep


def measure(key, line, width, points, window):
    """Fixed eight-metre knots make fitting independent of tile crop and pass order."""
    bounds = line.buffer(width / 2 + 7).bounds
    near = (
        (points[:, 0] >= bounds[0])
        & (points[:, 0] <= bounds[2])
        & (points[:, 2] >= bounds[1])
        & (points[:, 2] <= bounds[3])
    )
    points = points[near]
    points = points[near_line(line, points[:, [0, 2]], width / 2 + 7)]
    station, lateral = coordinates(line, points[:, [0, 2]])
    knots = np.unique(np.r_[np.arange(0, line.length, 8), line.length])
    centres = shapely.get_coordinates(shapely.line_interpolate_point(line, knots))
    local = shapely.intersects_xy(window.buffer(32), centres[:, 0], centres[:, 1])
    knots = knots[local]
    if len(knots) < 2:
        return None
    half = width / 2
    cross = np.array([-half - 6, -half - 3, -half, 0, half, half + 3, half + 6])
    heights = np.full((len(knots), len(cross)), np.nan)
    # Endpoint fits may use one-sided support, but never another landing beyond
    # the mapped way. On a short quay connector that upper surface can outvote
    # all of its actual paving and create a metre-high artificial ramp.
    on_way = (station >= -1e-6) & (station <= line.length + 1e-6)
    # Each knot reads the returns within 10 m along the way: a station-sorted window
    # (1 µm wider) finds them, then the exact test keeps the original return order.
    order = np.argsort(station, kind="stable")
    ordered = station[order]
    for i, s in enumerate(knots):
        lo = np.searchsorted(ordered, s - 10 - 1e-6, side="left")
        hi = np.searchsorted(ordered, s + 10 + 1e-6, side="right")
        window = np.sort(order[lo:hi])
        rows = window[on_way[window] & (np.abs(station[window] - s) <= 10)]
        along, side, height = station[rows], lateral[rows], points[rows, 1]
        core = np.abs(side) < min(0.85, half * 0.65)
        fit = robust_plane(np.c_[along[core] - s, side[core]], height[core])
        if fit is not None and abs(fit[1]) <= 0.15 and abs(fit[2]) <= 0.12:
            heights[i, 2:5] = fit[0] + cross[2:5] * fit[2]
        for j in (0, 1, 5, 6):
            use = np.abs(side - cross[j]) < 0.85
            fit = robust_plane(np.c_[along[use] - s, side[use] - cross[j]], height[use])
            if fit is not None and abs(fit[1]) <= 0.25 and abs(fit[2]) <= 0.7:
                heights[i, j] = fit[0]
    # Do not bridge missing evidence. In particular, absent endpoint support must
    # not turn a neighbouring bridge deck or steep stair approach into paving.
    for j in range(len(cross)):
        known = np.isfinite(heights[:, j])
        if known.any():
            smoothed = median_filter(heights[known, j], size=3, mode="nearest")
            heights[known, j] = smoothed
    if not np.isfinite(heights[:, 3]).any():
        return None
    return dict(key=key, line=line, width=width, knots=knots, cross=cross, heights=heights)


def intersects_parts(geometry, x, y, cell=4.0):
    """shapely.intersects_xy, exactly, without testing every point against GEOS.

    A point intersects a collection exactly when it intersects one of its parts. A
    prepared GeometryCollection (water + building union with stray points) has no
    index: 27 µs per point, minutes for a coastal tile's millions of returns. Per
    part, grid cells whose (slightly grown) box GEOS finds disjoint or covered decide
    all their points; only points in cells on the part's boundary are tested singly.
    """
    hit = np.zeros(len(x), bool)
    order = np.argsort(x, kind="stable")
    xs = x[order]
    for part in shapely.get_parts(geometry):
        x0, y0, x1, y1 = part.bounds
        if not np.isfinite([x0, y0, x1, y1]).all():
            continue
        lo, hi = np.searchsorted(xs, x0, "left"), np.searchsorted(xs, x1, "right")
        rows = order[lo:hi]
        rows = rows[~hit[rows] & (y[rows] >= y0) & (y[rows] <= y1)]
        if not len(rows):
            continue
        shapely.prepare(part)
        nx, ny = int((x1 - x0) // cell) + 1, int((y1 - y0) // cell) + 1
        if nx * ny > 1 << 20:
            hit[rows] = shapely.intersects_xy(part, x[rows], y[rows])
            continue
        i = np.clip(((x[rows] - x0) // cell).astype(int), 0, nx - 1)
        j = np.clip(((y[rows] - y0) // cell).astype(int), 0, ny - 1)
        flat = i * ny + j
        used = np.unique(flat)
        ci, cj = used // ny, used % ny
        # Grown by 1 mm: every point lies inside the box of the cell it was binned to.
        boxes = shapely.box(x0 + ci * cell - 1e-3, y0 + cj * cell - 1e-3,
                            x0 + (ci + 1) * cell + 1e-3, y0 + (cj + 1) * cell + 1e-3)
        state = np.where(shapely.covers(part, boxes), 1, np.where(shapely.intersects(part, boxes), 2, 0))
        state = state[np.searchsorted(used, flat)]
        hit[rows[state == 1]] = True
        mixed = rows[state == 2]
        if len(mixed):
            hit[mixed] = shapely.intersects_xy(part, x[mixed], y[mixed])
    return hit


def area_strips(identity, geom):
    """Complete paved areas use their principal axis; keep holes and exact
    outlines as constraints rather than triangulating individual returns.

    A principal-axis strip describes only a strip-like area. For a ring sidewalk
    around a block, an L shape or a curved quay, area/chord length is not a width:
    the strip would grade a 30 m ruled plane over the plazas and stairs inside the
    ring. 4A/P is the width of a square or a long strip; a strip mostly outside its
    own area is no survey of it.
    """
    strips = []
    for i, area in enumerate(shapely.get_parts(geom)):
        rectangle = np.asarray(area.minimum_rotated_rectangle.exterior.coords)
        edges = np.diff(rectangle, axis=0)
        direction = edges[np.argmax(np.linalg.norm(edges, axis=1))]
        direction /= np.linalg.norm(direction)
        centre = np.asarray(area.centroid.coords)[0]
        axis = shapely.LineString([centre - direction * area.length, centre + direction * area.length])
        for j, line in enumerate(shapely.get_parts(axis.intersection(area))):
            if line.geom_type != "LineString" or line.length < 8:
                continue
            width = min(area.area / line.length, 4 * area.area / area.length, 30)
            strip = line.buffer(width / 2, cap_style="flat", join_style="mitre")
            if strip.intersection(area).area < 0.75 * strip.area:
                continue
            strips.append((f"{identity}:{i}:{j}", line, max(width, 1)))
    return strips


def prepare(tile, osm, water_path, paths):
    from turku3d.ground.inputs import read_returns
    from turku3d.spatial import frame, rows

    if not water_path or not paths:
        return []
    window = shapely.box(*tile.bbox.as_tuple())
    water = shapely.union_all(
        shapely.make_valid(
            shapely.force_2d(frame(water_path, tile.bbox.buffered(60)).geometry_local.values)
        )
    )
    from .banks import small_bodies

    context = shapely.force_2d(frame(water_path).geometry_local.values)
    water = water.difference(shapely.union_all(small_bodies([water], context)))
    if water.is_empty:
        return []
    from .underpasses import quay_passages

    records = list(rows(osm, tile.bbox.buffered(40)))
    passages = quay_passages([row for _, row in records])
    ways, blocked = [], []
    for key, row in records:
        tags = json.loads(row["tags"] or "{}")
        geom = shapely.force_2d(row["geometry_local"])
        if tags.get("building") not in (None, "no"):
            if geom.geom_type == "LineString" and geom.is_ring:
                geom = shapely.Polygon(geom)
            if geom.geom_type in ("Polygon", "MultiPolygon"):
                blocked.append(geom.buffer(0.3))
        if geom.geom_type == "LineString" and geom.is_ring and tags.get("area") == "yes":
            geom = shapely.make_valid(shapely.Polygon(geom))
        if (
            tags.get("highway") not in ("footway", "pedestrian", "path", "cycleway")
            or tags.get("surface") in ("wood", "metal", "grass", "ground", "dirt")
            or tags.get("bridge", "no") != "no"
            or tags.get("tunnel", "no") not in ("no", "building_passage")
            or tags.get("layer", "0") not in ("0", "")
            or tags.get("footway") == "crossing"
            or tags.get("step_count", "0") not in ("0", "")
            or geom.geom_type not in ("LineString", "Polygon", "MultiPolygon")
            or geom.length < 0.5
            or geom.distance(water) > 25
        ):
            continue
        try:
            width = np.clip(float(str(tags.get("width", 3)).removesuffix(" m")), 1, 12)
        except ValueError:
            width = 3.0
        if not np.isfinite(width):
            width = 3.0
        identity = str(row.get("osm_id", key))
        if geom.geom_type == "LineString":
            # Airborne returns on the bridge deck cannot survey its lower quay
            # passage. The tunnel owns that complete lower walk and its floor.
            if any(
                geom.intersection(line.buffer(0.1)).length > 0.8 * geom.length
                for _, line, _ in passages
            ):
                continue
            ways.append((identity, geom, width))
        else:
            ways.extend(area_strips(identity, geom))
    if not ways:
        return []
    bounds = tile.bbox.buffered(60)
    survey = {p.stem: p for p in paths if p.parent.name != "maanpinta"}

    def relevant(points):
        # Only points used by a path's measurement can affect its fitted section.
        # Keep a whole horizontal voxel around each measurement box so the original
        # first return in that voxel still wins the deduplication below.
        mask = np.zeros(len(points), dtype=bool)
        for _, line, width in ways:
            x0, z0, x1, z1 = line.buffer(width / 2 + 7).bounds
            mask |= (
                (points[:, 0] >= x0 - 0.25)
                & (points[:, 0] <= x1 + 0.25)
                & (points[:, 2] >= z0 - 0.25)
                & (points[:, 2] <= z1 + 0.25)
            )
        return mask

    if all(b.name in survey for b in C.laz_tiles_for_bbox(bounds)):
        from turku3d.lidar import TileStore

        cloud = (
            TileStore(source_paths=survey)
            .query(bounds)
            .query(classes=[2, 12], last_return=True)
        )
        from .retaining_survey import ground_points

        points = ground_points(cloud, within=relevant)
    else:
        points, _ = read_returns(
            [p for p in paths if p.parent.name == "maanpinta"], bounds, [1, 2, 8, 12]
        )
        points = points[relevant(points)]
    exclude = shapely.union_all([water, *blocked])
    points = points[~intersects_parts(exclude, points[:, 0], points[:, 2])]
    # Equal horizontal support prevents dense scan strips from outweighing the
    # rest of the paving. Keep separate height bands for robust outlier rejection.
    voxel = np.floor(points[:, [0, 2, 1]] / [0.25, 0.25, 0.1]).astype(np.int64)
    if not len(voxel):
        return []
    # A single integer has the same lexicographic order as the three integer
    # coordinates, without NumPy's expensive structured-row comparison sort.
    voxel -= voxel.min(axis=0)
    keys = np.ravel_multi_index(voxel.T, voxel.max(axis=0) + 1)
    _, first = np.unique(keys, return_index=True)
    points = points[first]
    result = [measure(key, line, width, points, window) for key, line, width in ways]
    return sorted((r for r in result if r is not None), key=lambda r: r["key"])


def grade(positions, scene, *, inner_only=False):
    """Flat/graded paving, ruled slopes, and a continuous blend at the outer limit."""
    from .proximity import near_distance

    if not scene or not len(positions):
        return positions
    result = positions.copy()
    # Closest paving owns a point; outer embankment blends cannot dent another path.
    best = np.full(len(result), np.inf)
    weighted, weights = np.zeros(len(result)), np.zeros(len(result))
    for patch in scene:
        line, half = patch["line"], patch["width"] / 2
        distance = near_distance(result[:, [0, 2]], line, half + 6)
        selection = np.flatnonzero((distance < half + 6))
        if not len(selection):
            continue
        station, lateral = coordinates(line, result[selection][:, [0, 2]])
        knots, cross, values = patch["knots"], patch["cross"], patch["heights"]
        # Vector overlay roundoff must not move an exact paving edge into an
        # unsupported slope band (NaN * zero would otherwise dent that vertex).
        for edge in cross:
            lateral[np.abs(lateral - edge) < 1e-7] = edge
        k = np.clip(np.searchsorted(knots, station, side="right") - 1, 0, len(knots) - 2)
        u = (station - knots[k]) / (knots[k + 1] - knots[k])
        j = np.clip(np.searchsorted(cross, lateral, side="right") - 1, 0, len(cross) - 2)
        v = np.clip((lateral - cross[j]) / (cross[j + 1] - cross[j]), 0, 1)

        def mix(a, b, weight):
            return np.where(
                weight <= 1e-10, a, np.where(weight >= 1 - 1e-10, b, a * (1 - weight) + b * weight)
            )

        a = mix(values[k, j], values[k, j + 1], v)
        b = mix(values[k + 1, j], values[k + 1, j + 1], v)
        target = mix(a, b, u)
        valid = np.isfinite(target) & (u >= 0) & (u <= 1)
        # Unsupported outer cross sections keep the original terrain, while
        # supported paving always receives its fitted plane all the way to its edge.
        blend = np.clip((half + 6 - distance[selection]) / 3, 0, 1)
        ids = selection[valid]
        core = distance[ids] <= half + 1e-7
        own = core & (distance[ids] < best[ids])
        result[ids[own], 1] = target[valid][own]
        best[ids[own]] = distance[ids[own]]
        # Several nearby walks must share one continuous bank. Picking the
        # nearest centreline outside paving switches abruptly between upper
        # and lower levels, creating the vertical grass shards at stairs.
        gap = np.maximum(distance[ids] - half, 0.001)
        weight = blend[valid] ** 2 / gap**2
        weighted[ids] += target[valid] * weight
        weights[ids] += weight
    outside = np.isinf(best) & (weights > 0)
    # Fade to the original survey where no fitted bank has strong support.
    strength = np.minimum(weights[outside] * 9, 1)
    result[outside, 1] = weighted[outside] / weights[outside] * strength + positions[outside, 1] * (
        1 - strength
    )
    return result


def constrain(positions, indices, materials, keep, scene):
    """Insert paving/slope breaklines before assigning heights; preserve every triangle remainder."""
    from .surfaces import split_boundaries

    if not scene:
        return positions, indices, materials, keep
    cores = [p["line"].buffer(p["width"] / 2, cap_style="flat", join_style="mitre") for p in scene]
    zones = []
    left, right = shapely.STRtree(cores).query(cores, predicate="intersects")
    for a, b in zip(left[left < right], right[left < right], strict=True):
        overlap = cores[a].intersection(cores[b])
        query = shapely.get_coordinates(shapely.segmentize(overlap, 4))
        if not len(query):
            continue
        positions_at_overlap = np.c_[query[:, 0], np.full(len(query), -999.0), query[:, 1]]
        levels = np.array([grade(positions_at_overlap, [scene[i]])[:, 1] for i in (a, b)])
        if np.any((levels > -900).all(axis=0) & (abs(levels[0] - levels[1]) > 0.35)):
            zones.append(overlap.buffer(max(scene[a]["width"], scene[b]["width"]) / 2 + 3))
    regions, detailed, ends = [], [], []
    for patch in scene:
        for radius in (patch["width"] / 2, patch["width"] / 2 + 3):
            regions.append((patch["line"].buffer(radius, cap_style="flat", join_style="mitre"), 0))
            for sign in (-1, 1):
                detailed.append(
                    patch["line"].buffer(sign * radius, single_sided=True, join_style="mitre")
                )
        supported = np.isfinite(patch["heights"][:, 3])
        for i in np.flatnonzero(supported[:-1] != supported[1:]):
            station = patch["knots"][i if supported[i] else i + 1]
            line = patch["line"]
            a, b = [
                np.asarray(line.interpolate(s).coords)[0]
                for s in (max(0, station - 0.05), min(line.length, station + 0.05))
            ]
            length = np.linalg.norm(b - a)
            if length < 1e-9:
                continue
            normal = np.array([a[1] - b[1], b[0] - a[0]]) / length
            centre = np.asarray(line.interpolate(station).coords)[0]
            radius = patch["width"] / 2 + 3
            section = shapely.LineString(
                [centre - (radius + 1e-6) * normal, centre + (radius + 1e-6) * normal]
            )
            ends.append(section)
            zones.append(section.buffer(radius, cap_style="flat"))
    if zones:
        # Surface overlays use last-region precedence. Preserve all intersecting
        # edges and centrelines only where distinct fitted levels overlap or
        # survey support ends. Ordinary junctions retain their existing mesh.
        zone = shapely.union_all(zones)
        boundaries = shapely.union_all([*shapely.boundary(detailed), *ends]).intersection(zone)
        boundaries = shapely.union_all([boundaries, zone.boundary])
        partitions = shapely.get_parts(shapely.polygonize(shapely.get_parts(boundaries)))
        regions.extend((g, 0) for g in partitions if zone.covers(g.representative_point()))
    # The split is geometric only: restore each cut piece's original material.
    p, ix, ids, _ = split_boundaries(
        positions, indices, materials, keep, regions, preserve_materials=True
    )
    return grade(p, scene, inner_only=True), ix, ids, np.ones(len(ix), bool)


def sampler(sample, scene):
    if not scene:
        return sample

    def height(x, z):
        return float(grade(np.array([[x, sample(x, z), z]]), scene, inner_only=True)[0, 1])

    return height


def stats(tile, scene):
    window = shapely.box(*tile.bbox.as_tuple())
    patches = [p for p in scene if p["line"].intersects(window)]
    return dict(
        paths=len(patches),
        supported_knots=sum(int(np.isfinite(p["heights"][:, 3]).sum()) for p in patches),
        fitted_length_m=round(sum(p["line"].intersection(window).length for p in patches), 3),
    )
