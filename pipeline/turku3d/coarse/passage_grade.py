"""A drivable floor for car passages under buildings.

The ground model has no measurement under a building: its interpolation there can step metres within one grid cell
(Tapiola: Merituulentie drops 4.5 m within 10 m under the Ainoa mall, a 45 % grade in the middle of a primary road).
A car building passage's floor keeps its measured ends and every part of its profile already within MAX_GRADE; a
steeper step becomes the steepest ramp that fits, centred on it. The terrain grid in the passage corridor moves by
the centreline correction, so its crossfall stays; the passage floors and the published road heights read the same
graded surface. Footways and cycleways keep their steps (stairs and ramps are mapped separately).
"""

from __future__ import annotations

import numpy as np
import shapely

PATHS = {"footway", "path", "cycleway", "pedestrian", "steps", "bridleway", "corridor"}
MAX_GRADE = 0.08  # steepest passage floor; ordinary ramps into garages and underpasses stay below it
MIN_CHANGE = 0.05  # m; a profile closer than this to its limited version is left alone


def limited(heights, distances, grade=MAX_GRADE):
    """The mean of the lower and upper `grade`-Lipschitz envelopes: unchanged where the profile already keeps the
    grade, a centred ramp across a steeper step."""
    span = np.abs(distances[:, None] - distances[None, :]) * grade
    low = np.min(heights[None, :] + span, axis=1)
    high = np.max(heights[None, :] - span, axis=1)
    return (low + high) / 2


FADE = 4.0  # m beyond a passage's half width over which its correction fades out into the open ground


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


def profiles(ways, sample, step):
    """(line, distances, correction, half width) per car passage chain whose floor needs grading."""
    out = []
    for line, width in chains(ways):
        # Stations every metre: half the ground grid's spacing, so the correction follows each grid kink.
        distances = np.linspace(0, line.length, max(2, int(np.ceil(line.length)) + 1))
        xz = shapely.get_coordinates(shapely.line_interpolate_point(line, distances))
        heights = np.array([sample(x, z) for x, z in xz])
        if not np.isfinite(heights).all():
            continue
        correction = limited(heights, distances) - heights
        if np.max(np.abs(correction)) < MIN_CHANGE:
            continue
        out.append((line, distances, correction, width / 2 + step))
    return out


def grader(ways, sample, step):
    """`grade(query xz (n, 2), heights (n,)) -> heights` for points in car passages, or None when none needs it.
    Each chain moves the ground fully within its half width and fades out over FADE beyond it; where corridors
    overlap (the two carriageways of one passage) the corrections blend by those weights."""
    graded = profiles(ways, sample, step)
    if not graded:
        return None

    def grade(query, heights):
        heights = np.array(heights, float, copy=True)
        points = shapely.points(query)
        total, weight = np.zeros(len(heights)), np.zeros(len(heights))
        for line, distances, correction, half in graded:
            near = shapely.dwithin(line, points, half + FADE)
            if not near.any():
                continue
            ds = shapely.line_locate_point(line, points[near])
            # A point beyond either end of the chain projects onto that end: it is outside the passage.
            inside = (ds > 1e-6) & (ds < line.length - 1e-6)
            d = shapely.distance(line, points[near])
            w = np.clip((half + FADE - d) / FADE, 0, 1) * inside
            total[near] += w * np.interp(ds, distances, correction)
            weight[near] += w
        heights += total / np.maximum(1.0, weight)
        return heights

    return grade


def wrap(sample, grade):
    """A height sampler that reads `sample` graded in the passages."""

    def graded(x, z):
        return float(grade(np.array([[x, z]]), [sample(x, z)])[0])

    return graded
