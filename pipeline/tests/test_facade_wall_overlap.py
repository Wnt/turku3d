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


def test_preserves_walls_two_centimetres_apart():
    first, separate = wall(0, 10, 0, 5), wall(0, 10, 0, 5, .02)
    np.testing.assert_array_equal(exposed_wall_quads(np.concatenate([first, separate])),
                                  np.concatenate([first, separate]))


def block(x0, x1, z0, z1, base, top, top_at_x1=None):
    """A box part as coarse/extrusion._extrude makes it: a counter-clockwise (x, z) footprint, so its
    walls face out, from ``base`` up to a roof that may slope along x."""
    def height(x):
        return top if top_at_x1 is None else top + (top_at_x1 - top) * (x - x0) / (x1 - x0)
    ring = [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]
    walls = [[[p[0], base, p[1]], [q[0], base, q[1]], [q[0], height(q[0]), q[1]], [p[0], height(p[0]), p[1]]]
             for p, q in zip(ring, ring[1:] + ring[:1], strict=True)]
    roof = np.array([[x, height(x), z] for x, z in ring])
    return np.array(walls, float), roof[[[0, 1, 2], [0, 2, 3]]], base


def trim(*blocks, emit=None, groups=None, extra=None):
    walls = np.concatenate([b[0] for b in blocks] + ([extra[0]] if extra else []))
    owner = np.repeat(np.arange(len(blocks)), 4)
    if extra:
        owner = np.r_[owner, [extra[1]] * len(extra[0])]
    emit = np.ones(len(blocks), bool) if emit is None else np.asarray(emit, bool)
    groups = np.zeros(len(blocks), int) if groups is None else np.asarray(groups)
    solids = (np.concatenate([b[1] for b in blocks]), np.repeat([b[2] for b in blocks], 2).astype(float),
              np.repeat(groups, 2), np.repeat(emit, 2))
    return exposed_wall_quads(walls.reshape(-1, 3), emit=emit[owner], groups=groups[owner],
                              solids=solids).reshape(-1, 4, 3)


def facing(quads, direction):
    """Painted area of the walls facing ``direction`` (an (x, z) unit vector), in wall metres."""
    total = 0.0
    for q in quads:
        run = q[1, [0, 2]] - q[0, [0, 2]]
        length = np.linalg.norm(run)
        if length < 1e-12 or np.dot([run[1], -run[0]], direction) / length < .999:
            continue
        along = (q[:, [0, 2]] - q[0, [0, 2]]) @ (run / length)
        total += shapely.Polygon(np.c_[along, q[:, 1]]).area
    return total


SOUTH, NORTH = (0, -1), (0, 1)  # -z, +z


def test_back_to_back_part_walls_keep_only_what_rises_above_the_other_part():
    """t0_0_-1: every roof-part boundary carried two walls from the ground to the roof inside the
    building; a stepped roof showed five nested layers."""
    tall, low = block(0, 10, 0, 10, 0, 9), block(0, 10, -5, 0, 0, 4)
    result = trim(tall, low)
    boundary = result[np.all(np.abs(result[:, :, 2]) < 1e-9, axis=1)]
    assert facing(boundary, SOUTH) == pytest.approx(10 * (9 - 4))  # the tall part above the low roof
    assert facing(boundary, NORTH) == pytest.approx(0)  # the low part's wall is inside the tall one
    equal = trim(block(0, 10, 0, 10, 0, 6), block(0, 10, -5, 0, 0, 6))
    assert not len(equal[np.all(np.abs(equal[:, :, 2]) < 1e-9, axis=1)])


def test_crossing_roof_edges_leave_each_wall_its_exposed_triangle():
    # Two roof planes meet along the boundary, crossing at its middle: each wall shows above the other.
    result = trim(block(0, 10, 0, 10, 0, 8, 4), block(0, 10, -5, 0, 0, 4, 8))
    boundary = result[np.all(np.abs(result[:, :, 2]) < 1e-9, axis=1)]
    assert facing(boundary, SOUTH) == pytest.approx(5 * 4 / 2)
    assert facing(boundary, NORTH) == pytest.approx(5 * 4 / 2)


def test_a_wall_beside_a_raised_part_keeps_its_passage_below():
    result = trim(block(0, 10, 0, 10, 0, 12), block(2, 6, -5, 0, 3, 9))  # a part over a 3 m passage
    boundary = result[np.all(np.abs(result[:, :, 2]) < 1e-9, axis=1)]
    assert facing(boundary, SOUTH) == pytest.approx(10 * 12 - 4 * 6)


def test_a_sliver_part_in_front_of_a_taller_facade_leaves_no_hole():
    """train12 t0_0_-1 (Uusi Börs, market pavilion): a millimetre roof-part sliver stood in front of
    a taller facade. Neither painted the street face where the sliver stood: a full-height slot."""
    result = trim(block(0, 10, 0, 10, 0, 10), block(0, 10, -.003, 0, 0, 6))
    street = result[np.all(result[:, :, 2] < 1e-9, axis=1)]
    assert facing(street, SOUTH) == pytest.approx(10 * 10)  # painted once
    lower = [q for q in street if q[:, 1].max() <= 6 + 1e-9 and facing([q], SOUTH) > 0]
    assert lower and all(np.allclose(q[:, 2], -.003) for q in lower)  # by the sliver's outer wall


def test_other_buildings_only_trim_when_both_are_emitted_but_own_parts_always_do():
    tall, low = block(0, 10, 0, 10, 0, 9), block(0, 10, -5, 0, 0, 4)
    same_building = trim(tall, low, emit=[True, False], groups=[0, 0])
    assert facing(same_building[np.all(np.abs(same_building[:, :, 2]) < 1e-9, axis=1)], SOUTH) == pytest.approx(50)
    other_building = trim(tall, low, emit=[True, False], groups=[0, 1])
    assert facing(other_building[np.all(np.abs(other_building[:, :, 2]) < 1e-9, axis=1)], SOUTH) == pytest.approx(90)


def test_back_to_back_faces_of_one_thin_part_keep_each_other():
    result = trim(block(0, 10, -.002, 0, 0, 6))
    assert facing(result, SOUTH) == pytest.approx(60)
    assert facing(result, NORTH) == pytest.approx(60)


def test_walls_facing_each_other_across_a_sub_millimetre_gap_are_both_hidden():
    result = trim(block(0, 10, 0, 5, 0, 6), block(0, 10, -5, -.0005, 0, 6))
    between = result[np.all(np.abs(result[:, :, 2]) < .001, axis=1)]
    assert facing(between, SOUTH) == pytest.approx(0) and facing(between, NORTH) == pytest.approx(0)


def test_a_ring_running_along_its_own_edge_both_ways_keeps_the_street_face():
    """train16 t0_0_-1 (Building_567784's end wall, the market pavilion): the footprint ring ran along
    a facade both ways, adding an inward-facing wall on the street face with nothing in front of it.
    Trimming by opposite-facing walls took the whole end wall away."""
    part = block(0, 10, 0, 10, 0, 9)
    inward = part[0][:1][:, [1, 0, 3, 2]]  # the same street edge, run the other way
    result = trim(part, extra=(inward, 0))
    street = result[np.all(np.abs(result[:, :, 2]) < 1e-9, axis=1)]
    assert facing(street, SOUTH) == pytest.approx(90)
    assert facing(street, NORTH) == pytest.approx(0)  # its solid is behind: inside the part


def test_a_roof_step_inside_one_footprint_keeps_the_wall_above_the_lower_roof():
    """train16 t0_0_-1 (Uusi Börs between its towers): one footprint, two roof heights; the wall on
    the step rises above the lower roof in front of it."""
    high, low = block(0, 10, 0, 10, 0, 9), block(0, 10, -5, 0, 0, 5)
    result = trim(high, low, groups=[0, 0])
    boundary = result[np.all(np.abs(result[:, :, 2]) < 1e-9, axis=1)]
    assert facing(boundary, SOUTH) == pytest.approx(10 * 4)


def test_a_part_that_only_touches_a_wall_at_its_end_does_not_break_the_overlay():
    """train15 t0_0_-1 failed in GEOS when a cover only touched a wall: lines and points."""
    result = trim(block(0, 10, 0, 10, 0, 9), block(10, 20, -.002, 10, 0, 9), block(0, 10, -.004, -.0005, 0, 4))
    assert facing(result[np.all(result[:, :, 2] < 1e-9, axis=1)], SOUTH) == pytest.approx(10 * 9 + 10 * 9)
