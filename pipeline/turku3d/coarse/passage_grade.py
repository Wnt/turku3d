"""A drivable floor for car passages under buildings.

The ground model has no measurement under a building: its interpolation there can step metres within one grid cell
(Tapiola: under Ainoa the ground falls from 6.5 m to 2 m across both Merituulentie carriageways and the service passage
beside them, along a diagonal that meets each carriageway at a different place). The joined car passage corridors
form one paved floor, graded as a surface on the world-aligned ground grid vertices: the mean of its lower and upper
MAX_GRADE-Lipschitz envelopes in every direction. Ground already within MAX_GRADE keeps its heights and crossfall; a
steeper step becomes the steepest ramp that fits, centred on it. Grading each carriageway along its centreline and
blending the corrections where corridors overlap lifted the westbound lane 1.5 m into a hump where the eastbound
correction was largest. The correction fades out over FADE beyond the floor; the passage floors and the published road
heights read the same graded surface. Footways and cycleways keep their steps (stairs and ramps are mapped separately).
"""

from __future__ import annotations

import numpy as np
import shapely

PATHS = {"footway", "path", "cycleway", "pedestrian", "steps", "bridleway", "corridor"}
MAX_GRADE = 0.08  # steepest passage floor; ordinary ramps into garages and underpasses stay below it
MIN_CHANGE = 0.05  # m; a floor closer than this to its limited version is left alone
FADE = 4.0  # m beyond a floor's edge over which its correction fades out into the open ground
CHUNK = 256  # envelope rows per block: CHUNK x vertices pairwise distances in memory


def limited(heights, positions, grade=MAX_GRADE):
    """The mean of the lower and upper `grade`-Lipschitz envelopes of `heights` at `positions` (distances along a
    line, (n,), or points, (n, 2)): unchanged where the heights already keep the grade, a centred ramp across a
    steeper step."""
    heights = np.asarray(heights, float)
    positions = np.asarray(positions, float)
    if positions.ndim == 1:
        positions = positions[:, None]
    low, high = np.empty_like(heights), np.empty_like(heights)
    for start in range(0, len(heights), CHUNK):
        rows = slice(start, start + CHUNK)
        span = np.sqrt(((positions[rows, None, :] - positions[None, :, :]) ** 2).sum(axis=2)) * grade
        low[rows] = np.min(heights[None, :] + span, axis=1)
        high[rows] = np.max(heights[None, :] - span, axis=1)
    return (low + high) / 2


def chains(ways):
    """Car passage centrelines joined end to end (one per run of OSM ways), each with its widest way's width: a
    carriageway split into short ways gets one continuous floor, not a step at every joint."""
    from .tunnels import dimensions

    lines, widths = [], []
    for _, line, tags in ways:
        if tags.get("tunnel") != "building_passage" or tags.get("highway") in PATHS or not tags.get("highway"):
            continue
        lines.append(line)
        widths.append(dimensions(tags)[0])
    if not lines:
        return []
    merged = shapely.line_merge(shapely.union_all(lines))
    parts = list(getattr(merged, "geoms", [merged]))
    tree = shapely.STRtree(lines)
    out = []
    for part in parts:
        if part.geom_type != "LineString" or part.length < 4:
            continue
        near = tree.query(part, predicate="dwithin", distance=0.5)
        out.append((part, max(widths[int(i)] for i in near) if len(near) else max(widths)))
    return out


def floors(ways, step):
    """The connected car passage floors: joined corridors of half width plus one grid step, each graded alone."""
    parts = [line.buffer(width / 2 + step, cap_style="flat") for line, width in chains(ways)]
    if not parts:
        return []
    union = shapely.union_all(parts)
    return [part for part in getattr(union, "geoms", [union]) if part.area > 0]


def corrections(ways, sample, step):
    """(first lattice index (i, j), correction per world-aligned grid vertex [j, i]) or None when no floor needs it."""
    from scipy.spatial import cKDTree

    found = floors(ways, step)
    if not found:
        return None
    xmin, zmin, xmax, zmax = shapely.union_all(found).buffer(FADE).bounds
    i0, j0 = int(np.floor(xmin / step)) - 1, int(np.floor(zmin / step)) - 1
    i1, j1 = int(np.ceil(xmax / step)) + 1, int(np.ceil(zmax / step)) + 1
    ii, jj = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1))
    xz = np.column_stack([ii.ravel() * step, jj.ravel() * step])
    points = shapely.points(xz)
    correction = np.zeros(len(xz))
    graded, moved = np.zeros(len(xz), bool), []
    for floor in found:
        shapely.prepare(floor)
        inside = np.flatnonzero(shapely.covers(floor, points))
        heights = np.array([sample(x, z) for x, z in xz[inside]])
        inside, heights = inside[np.isfinite(heights)], heights[np.isfinite(heights)]
        if len(inside) < 2:
            continue
        change = limited(heights, xz[inside]) - heights
        if np.max(np.abs(change)) < MIN_CHANGE:
            continue
        correction[inside] = change
        graded[inside] = True
        moved.append(floor)
    if not moved:
        return None
    # Beyond a graded floor's edge its nearest vertex's correction fades out over FADE into the open ground.
    edge = shapely.union_all(moved)
    outside = np.flatnonzero(~graded & shapely.dwithin(edge, points, FADE))
    if len(outside):
        _, nearest = cKDTree(xz[graded]).query(xz[outside])
        fade = np.clip(1 - shapely.distance(edge, points[outside]) / FADE, 0, 1)
        correction[outside] = correction[np.flatnonzero(graded)[nearest]] * fade
    return (i0, j0), correction.reshape(ii.shape)


def grader(ways, sample, step):
    """`grade(query xz (n, 2), heights (n,)) -> heights` for points on or near car passage floors, or None when none
    needs it. Grid vertices move by their own correction; points between them by the ground triangles'
    interpolation of it, so a graded sampler equals the graded grid."""
    found = corrections(ways, sample, step)
    if found is None:
        return None
    (i0, j0), correction = found
    rows, cols = correction.shape

    def grade(query, heights):
        query = np.asarray(query, float).reshape(-1, 2)
        u, v = query[:, 0] / step - i0, query[:, 1] / step - j0
        i, j = np.floor(u).astype(np.int64), np.floor(v).astype(np.int64)
        valid = (i >= 0) & (j >= 0) & (i < cols - 1) & (j < rows - 1)
        i, j = np.clip(i, 0, cols - 2), np.clip(j, 0, rows - 2)
        fu, fv = u - i, v - j
        c = correction
        lower = c[j, i] * (1 - fu - fv) + c[j, i + 1] * fu + c[j + 1, i] * fv
        upper = c[j + 1, i + 1] * (fu + fv - 1) + c[j + 1, i] * (1 - fu) + c[j, i + 1] * (1 - fv)
        return np.array(heights, float, copy=True) + np.where(valid, np.where(fu + fv <= 1, lower, upper), 0.0)

    return grade


def wrap(sample, grade):
    """A height sampler that reads `sample` graded on the passage floors."""

    def graded(x, z):
        return float(grade(np.array([[x, z]]), [sample(x, z)])[0])

    return graded
