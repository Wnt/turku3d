"""LOD2 part walls must not paint the same outward-facing area twice."""

import numpy as np
import pytest
import shapely

from turku3d.coarse.stored_facades import exposed_wall_quads


def wall(left, right, bottom, top, z=0):
    return np.array([[left, bottom, z], [right, bottom, z], [right, top, z], [left, top, z]], float)


def area(quads):
    return sum(shapely.Polygon(q[:, :2]).area for q in quads.reshape(-1, 4, 3))


def test_near_coplanar_roof_sliver_keeps_one_paint_surface():
    # Linnankatu 72: a fallback roof strip emitted a second 71 m wall
    # 0.1 mm from the surveyed part, with a different facade program.
    original = np.concatenate([wall(0, 71, 2.4, 7.9), wall(0, 71, 2.4, 9.8, .0001)])
    result = exposed_wall_quads(original)
    assert area(result) == pytest.approx(71 * (9.8 - 2.4))
    assert len(result) == 4


def test_partial_overlap_preserves_sloping_exposed_union():
    lower = wall(0, 10, 0, 5)
    upper = wall(3, 7, 0, 8, .001)
    upper[2, 1] = 3
    result = exposed_wall_quads(np.concatenate([lower, upper]))
    source = [shapely.Polygon(q[:, :2]) for q in [lower, upper]]
    assert area(result) == pytest.approx(shapely.union_all(source).area)
    parts = [shapely.Polygon(q[:, :2]) for q in result.reshape(-1, 4, 3)]
    for i, part in enumerate(parts):
        for other in parts[:i]:
            assert part.intersection(other).area < 1e-8


def test_preserves_opposite_facing_and_separated_walls():
    first = wall(0, 10, 0, 5)
    opposite = first[[1, 0, 3, 2]]
    separate = wall(0, 10, 0, 5, .02)
    source = np.concatenate([first, opposite, separate])
    np.testing.assert_array_equal(exposed_wall_quads(source), source)


def test_equal_walls_have_stable_single_owner():
    source = np.concatenate([wall(0, 10, 0, 5)] * 3)
    np.testing.assert_array_equal(exposed_wall_quads(source), source[:4])


def test_short_overlap_uses_shared_frontage_for_plane_distance():
    long = wall(0, 71, 0, 8)
    long[1:3, 2] = .04
    short = wall(0, .2, 0, 5)
    result = exposed_wall_quads(np.concatenate([short, long]))
    assert len(result) == 4
    np.testing.assert_array_equal(result, long)
