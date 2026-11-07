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
        return float(np.interp(x, [98.0, 100.0], [6.5, 2.0]))

    grade = grader([("a", a, tags), ("b", b, tags)], ground, 2.0)
    xs = np.arange(1.0, 199.0, 2.0)  # halfway between the ground grid vertices
    q = np.column_stack([xs, np.zeros_like(xs)])
    h = grade(q, [ground(x, z) for x, z in q])
    assert np.max(np.abs(np.diff(h))) <= 2 * MAX_GRADE + 0.05  # one continuous 8 % ramp, no joint step
    # Beside the corridor the correction fades out instead of leaving a cliff at its edge.
    side = np.column_stack([np.full(12, 100.5), np.linspace(0, 11, 12)])
    hs = grade(side, [ground(x, z) for x, z in side])
    assert np.max(np.abs(np.diff(hs))) < 1.0


def grid_ground(step, height):
    """A sampler of the ground grid's triangles over world-aligned vertices at multiples of `step`."""

    def sample(x, z):
        u, v = x / step, z / step
        i, j = int(np.floor(u)), int(np.floor(v))
        fu, fv = u - i, v - j
        h = lambda a, b: height(a * step, b * step)  # noqa: E731
        if fu + fv <= 1:
            return h(i, j) * (1 - fu - fv) + h(i + 1, j) * fu + h(i, j + 1) * fv
        return h(i + 1, j + 1) * (fu + fv - 1) + h(i, j + 1) * (1 - fu) + h(i + 1, j) * (1 - fv)

    return sample


def test_parallel_carriageways_over_a_diagonal_step_get_one_ramp_without_a_hump():
    """Tapiola: under Ainoa the interpolated ground falls 4.5 m along a diagonal across both Merituulentie
    carriageways and the service passage. Each corridor graded along its own centreline, blended where they
    overlap, lifted the westbound lane into a 1.5 m hump the car could not climb."""
    step = 250 / 128
    tags = {"highway": "primary", "tunnel": "building_passage", "lanes": "2"}
    service = {"highway": "service", "tunnel": "building_passage"}
    ways = [
        ("west", shapely.LineString([(250, 0), (0, 0)]), tags),
        ("east", shapely.LineString([(0, 7.5), (250, 7.5)]), tags),
        ("service", shapely.LineString([(0, 14), (250, 14)]), service),
    ]
    ground = grid_ground(step, lambda x, z: 6.5 if x + 1.2 * z < 125 else 2.0)
    grade = grader(ways, ground, step)
    xs = np.arange(0, 251) * step / 2  # every vertex and the midpoint between
    for lane in (0.0, 7.5, 14.0):
        q = np.column_stack([xs, np.full_like(xs, lane)])
        h = grade(q, [ground(x, z) for x, z in q])
        inner = (xs > 30) & (xs < 220)
        assert np.all(np.diff(h[inner]) <= 1e-9)  # falls all the way: no hump on any lane
        assert np.max(np.abs(np.diff(h[inner]) / np.diff(xs[inner]))) <= MAX_GRADE + 1e-6
        assert h[inner].max() <= 6.5 + 1e-9 and h[inner].min() >= 2.0 - 1e-9
    # Across the carriageways the floor keeps the grade too.
    zs = np.arange(-4, 18.01, step / 2)
    for x in (100.0, 115.0, 130.0):
        q = np.column_stack([np.full_like(zs, x), zs])
        h = grade(q, [ground(*p) for p in q])
        assert np.max(np.abs(np.diff(h) / np.diff(zs))) <= MAX_GRADE + 1e-6


def test_separate_floors_do_not_pull_on_each_other():
    """Two passages 60 m apart and 9 m apart in height: each floor is graded alone."""
    step = 2.0
    tags = {"highway": "primary", "tunnel": "building_passage", "lanes": "2"}
    ways = [("low", shapely.LineString([(0, 0), (100, 0)]), tags), ("high", shapely.LineString([(0, 60), (100, 60)]), tags)]

    def ground(x, z):
        return 1.0 if z < 30 else 10.0 + (0 if x < 50 else -3.0)

    grade = grader(ways, ground, step)
    q = np.array([[20.0, 0.0], [80.0, 0.0]])
    np.testing.assert_allclose(grade(q, [ground(*p) for p in q]), [1.0, 1.0])
