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


def profiles(ways, sample, step):
    """(line, distances, correction, footprint) per car passage whose floor needs grading."""
    from .tunnels import dimensions

    out = []
    for _, line, tags in ways:
        if tags.get("tunnel") != "building_passage" or tags.get("highway") in PATHS or not tags.get("highway"):
            continue
        if line.length < 4:
            continue
        width, _ = dimensions(tags)
        distances = np.linspace(0, line.length, max(2, int(np.ceil(line.length / 2)) + 1))
        xz = shapely.get_coordinates(shapely.line_interpolate_point(line, distances))
        heights = np.array([sample(x, z) for x, z in xz])
        if not np.isfinite(heights).all():
            continue
        correction = limited(heights, distances) - heights
        if np.max(np.abs(correction)) < MIN_CHANGE:
            continue
        footprint = line.buffer(width / 2 + step, cap_style="flat", join_style="mitre")
        out.append((line, distances, correction, footprint))
    return out


def grader(ways, sample, step):
    """`grade(query xz (n, 2), heights (n,)) -> heights` for points in car passages, or None when none needs it."""
    graded = profiles(ways, sample, step)
    if not graded:
        return None

    def grade(query, heights):
        heights = np.array(heights, float, copy=True)
        points = shapely.points(query)
        total, count = np.zeros(len(heights)), np.zeros(len(heights))
        for line, distances, correction, footprint in graded:
            use = shapely.covers(footprint, points)
            if not use.any():
                continue
            ds = shapely.line_locate_point(line, points[use])
            total[use] += np.interp(ds, distances, correction)
            count[use] += 1
        # Overlapping corridors (both carriageways of one passage) share the mean correction.
        moved = count > 0
        heights[moved] += total[moved] / count[moved]
        return heights

    return grade


def wrap(sample, grade):
    """A height sampler that reads `sample` graded in the passages."""

    def graded(x, z):
        return float(grade(np.array([[x, z]]), [sample(x, z)])[0])

    return graded
