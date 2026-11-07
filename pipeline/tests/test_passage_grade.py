"""Car passages under buildings get a drivable floor (coarse/passage_grade.py)."""

import numpy as np
import shapely

from turku3d.coarse.passage_grade import MAX_GRADE, grader, limited


def test_a_gentle_profile_is_kept_and_a_step_becomes_a_centred_ramp():
    d = np.arange(0, 201, 2.0)
    gentle = 6 - 0.03 * d
    np.testing.assert_allclose(limited(gentle, d), gentle)
    # Merituulentie under Ainoa: 6.5 m, then 2.0 m beyond a 10 m step.
    step = np.where(d < 100, 6.5, np.where(d > 110, 2.0, 6.5 - 0.45 * (d - 100)))
    out = limited(step, d)
    assert np.max(np.abs(np.diff(out) / np.diff(d))) <= MAX_GRADE + 1e-9
    assert out[0] == step[0] and out[-1] == step[-1]  # measured ends kept
    assert abs(out[np.searchsorted(d, 105)] - 4.25) < 0.1  # centred on the step


def test_grader_moves_the_car_passage_corridor_only():
    line = shapely.LineString([(0, 0), (200, 0)])

    def ground(x, z):
        return 6.5 if x < 100 else 2.0

    grade = grader([("road", line, {"highway": "primary", "tunnel": "building_passage", "lanes": "2"})], ground, 2.0)
    q = np.array([[20.0, 0.0], [90.0, 2.0], [110.0, -2.0], [180.0, 0.0], [100.0, 30.0]])
    h = grade(q, [ground(x, z) for x, z in q])
    assert h[1] < 6.5 and h[2] > 2.0  # the step is ramped inside the corridor
    assert h[4] == 2.0  # outside the corridor nothing moves
    assert h[0] <= 6.5 and h[3] >= 2.0
    # Footway passages keep their steps (stairs are mapped on their own); no change, no grader.
    assert grader([("walk", line, {"highway": "footway", "tunnel": "building_passage"})], ground, 2.0) is None


def test_a_carriageway_split_into_ways_is_graded_as_one_floor():
    """Merituulentie under Ainoa is several short OSM ways; grading each alone left steps at the joints."""
    a = shapely.LineString([(0, 0), (100, 0)])
    b = shapely.LineString([(100, 0), (200, 0)])
    tags = {"highway": "primary", "tunnel": "building_passage", "lanes": "2"}

    def ground(x, z):  # the ground grid steps 4.5 m between two vertices 2 m apart
        return float(np.interp(x, [99.0, 101.0], [6.5, 2.0]))

    grade = grader([("a", a, tags), ("b", b, tags)], ground, 2.0)
    xs = np.arange(1.5, 199.0, 2.0)  # ground grid vertices 2 m apart
    q = np.column_stack([xs, np.zeros_like(xs)])
    h = grade(q, [ground(x, z) for x, z in q])
    assert np.max(np.abs(np.diff(h))) <= 2 * MAX_GRADE + 0.05  # one continuous 8 % ramp, no joint step
    # Beside the corridor the correction fades out instead of leaving a cliff at its edge.
    side = np.column_stack([np.full(12, 100.5), np.linspace(0, 11, 12)])
    hs = grade(side, [ground(x, z) for x, z in side])
    assert np.max(np.abs(np.diff(hs))) < 1.0
