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
