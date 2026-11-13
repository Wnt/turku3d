"""Stair flights: what a mapped flight measures (profile, width, material, rails) and what it draws (treads on
the slope, the mapped count)."""
import numpy as np
import shapely
from turku3d.world.kinds.steps import Steps, indoor

E0, N0 = 23459600.0, 6704400.0


class Slope:
    """A lidar window whose ground rises 0.2 m a metre to the north."""

    def sample(self, key, e, n):
        return 3.0 + 0.2 * (np.asarray(n, float) - N0)


def measure(line, tags, stairs=(), zones=()):
    return Steps._measure("1", line, tags, Slope(), list(stairs), list(zones), frozenset({"Liikekeskusta"}),
                          lambda geom, zones: zones[0][1] if zones else None)


def test_a_flight_runs_up_with_the_register_width_and_material():
    down = shapely.LineString([(E0, N0 + 10.0), (E0, N0)])  # drawn downhill
    part = shapely.box(E0 - 1.5, N0, E0 + 1.5, N0 + 10.0)
    flight = measure(down, {"handrail": "yes", "handrail:left": "no"}, stairs=[(part, "Betoni")])
    assert flight["line"][0][1] == N0 and flight["line"][-1][1] == N0 + 10.0
    assert abs(flight["heights"][-1] - flight["heights"][0] - 2.0) < 0.05
    assert abs(flight["width"] - 3.0) < 0.1 and flight["material"] == "concrete"
    assert flight["count"] == round(2.0 / 0.16)
    # Drawn southwards, the way's left is east: going up (north) the survey's right, the engine's left.
    assert flight["rails"] == [False, True]


def test_an_unmapped_surface_is_granite_in_the_town_and_concrete_elsewhere():
    line = shapely.LineString([(E0, N0), (E0, N0 + 4.0)])
    town = [(shapely.box(E0 - 50, N0 - 50, E0 + 50, N0 + 50), "Liikekeskusta")]
    assert measure(line, {}, zones=town)["material"] == "granite"
    assert measure(line, {})["material"] == "concrete"
    assert measure(line, {"surface": "wood"}, zones=town)["material"] == "wood"
    flat = shapely.LineString([(E0, N0), (E0 + 4.0, N0)])  # level: not a flight
    assert measure(flat, {}) is None


def test_steps_mapped_indoors_or_above_the_ground_are_not_drawn():
    assert indoor({"indoor": "yes"}) and indoor({"level": "1;2"}) and indoor({"location": "underground"})
    assert not indoor({"level": "0;-1"}) and not indoor({})


def test_treads_stand_on_the_slope_with_the_mapped_count():
    line = shapely.LineString([(E0, N0), (E0, N0 + 10.0)])
    facts = measure(line, {"step_count": "12", "handrail": "yes"})
    steps = Steps()
    from turku3d.world.api import Stub

    output = steps.compile(steps.describe(Stub("steps/c_1_1", (0, 0, 1, 1), [], {"cell": [1, 1], "flights": [facts]}), None))
    body = next(p for p in output.parts if p.name == "steps")
    tri = body.positions[body.triangles]
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    up = normal[:, 1] / np.linalg.norm(normal, axis=1) > 0.99
    assert up.sum() == 12 * 2  # one tread a step
    corners = tri[up].reshape(-1, 3)
    n = 6704500.0 - (corners[:, 2] + 1 * 62.5)  # engine z back to N within cell j = 1
    slope = 3.0 + 0.2 * (n - N0)
    assert np.all(corners[:, 1] + output.origin[1] >= slope - 1e-6)
    assert abs(corners[:, 1].max() + output.origin[1] - 5.0) < 0.05
    assert any(p.name == "steps-rail" for p in output.parts)
