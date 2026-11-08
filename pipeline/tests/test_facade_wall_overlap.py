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


def test_preserves_separated_walls_and_opposite_walls_of_unemitted_context():
    first = wall(0, 10, 0, 5)
    opposite = first[[1, 0, 3, 2]]
    separate = wall(0, 10, 0, 5, .02)
    np.testing.assert_array_equal(exposed_wall_quads(np.concatenate([first, separate])),
                                  np.concatenate([first, separate]))
    # A neighbour tile's solid (context) never hides an emitted wall: it may load later.
    result = exposed_wall_quads(np.concatenate([first, opposite]), emit=np.array([True, False]),
                                groups=np.array([0, 1]))
    np.testing.assert_array_equal(result, first)


def test_back_to_back_part_walls_keep_only_what_rises_above_the_other_part():
    """t0_0_-1: every roof-part boundary carried two walls from the ground to the roof inside the
    building; a stepped roof showed five nested layers."""
    tall = wall(0, 10, 0, 9)
    low = wall(0, 10, 0, 4)[[1, 0, 3, 2]]  # the neighbouring part's wall, facing back into the tall part
    result = exposed_wall_quads(np.concatenate([tall, low])).reshape(-1, 4, 3)
    assert area(result) == pytest.approx(10 * (9 - 4))
    assert result[:, :, 1].min() == pytest.approx(4)
    # Equal heights: both walls are inside the building and nothing is painted.
    assert not len(exposed_wall_quads(np.concatenate([wall(0, 10, 0, 6), wall(0, 10, 0, 6)[[1, 0, 3, 2]]])))


def test_crossing_roof_edges_leave_each_wall_its_exposed_triangle():
    # Two roof planes meet along the boundary, crossing at its middle: each wall shows above the other.
    first = wall(0, 10, 0, 8)
    first[2, 1] = 4  # 8 m at x=0, 4 m at x=10
    second = wall(0, 10, 0, 4)
    second[2, 1] = 8  # 4 m at x=0, 8 m at x=10
    second = second[[1, 0, 3, 2]]
    result = exposed_wall_quads(np.concatenate([first, second])).reshape(-1, 4, 3)
    assert area(result) == pytest.approx(2 * (5 * 4 / 2))  # two triangles: 5 m along, 4 m high at the ends
    assert result[:, :, 1].min() == pytest.approx(4, abs=1e-6)


def test_a_wall_beside_a_raised_part_keeps_its_passage_below():
    ground = wall(0, 10, 0, 12)
    bridge = wall(2, 6, 3, 9)[[1, 0, 3, 2]]  # a part over a passage, 3 m above the ground
    result = exposed_wall_quads(np.concatenate([ground, bridge])).reshape(-1, 4, 3)
    assert area(result[:3]) + area(result[3:]) == pytest.approx(10 * 12 - 4 * 6)


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


def test_a_sliver_part_in_front_of_a_taller_facade_leaves_no_hole():
    """train12 t0_0_-1 (Uusi Börs, market pavilion): a millimetre roof-part sliver stood in front of
    a taller facade. The sliver's solid trimmed the facade, and the facade's untrimmed extent took the
    sliver's own outer wall: a full-height slot painted by neither."""
    facade = wall(0, 10, 0, 10)                       # the tall part's outer wall
    sliver_inner = wall(0, 10, 0, 6)[[1, 0, 3, 2]]    # the sliver's wall against it, facing back
    sliver_outer = wall(0, 10, 0, 6, -.003)           # the sliver's street face, 3 mm out (walls face right: -z)
    result = exposed_wall_quads(np.concatenate([facade, sliver_inner, sliver_outer])).reshape(-1, 4, 3)
    assert area(result) == pytest.approx(10 * 10)  # the whole street face, painted once
    lower = [q for q in result if q[:, 1].max() <= 6 + 1e-9]
    assert lower and all(abs(q[0, 2] + .003) < 1e-9 for q in lower)  # by the sliver's outer wall


def test_other_buildings_only_trim_when_both_are_emitted_but_own_parts_always_do():
    tall = wall(0, 10, 0, 9)
    low = wall(0, 10, 0, 4)[[1, 0, 3, 2]]
    source = np.concatenate([tall, low])
    same_building = exposed_wall_quads(source, emit=np.array([True, False]), groups=np.array([0, 0]))
    assert area(same_building) == pytest.approx(10 * 5)
    other_building = exposed_wall_quads(source, emit=np.array([True, False]), groups=np.array([0, 1]))
    np.testing.assert_array_equal(other_building, tall)


def test_back_to_back_faces_of_one_thin_part_keep_each_other():
    outer = wall(0, 10, 0, 6, -.002)
    inner = wall(0, 10, 0, 6)[[1, 0, 3, 2]]  # 2 mm behind the outer face, facing the other way
    result = exposed_wall_quads(np.concatenate([outer, inner]))
    assert area(result) == pytest.approx(2 * 60)


def test_walls_facing_each_other_across_a_millimetre_gap_are_both_hidden():
    a = wall(0, 10, 0, 6)
    b = wall(0, 10, 0, 6, -.004)[[1, 0, 3, 2]]  # 4 mm in front of a, facing back at it
    assert not len(exposed_wall_quads(np.concatenate([a, b])))
