"""Buildings set into slopes and OSM funicular profiles (synthetic, no raw data)."""

import numpy as np
import shapely

from turku3d.coarse import funicular, slope_buildings
from turku3d.coarse.inputs import Building


def hillside(front=6.0, hill=10.2, step_x=0.0):
    """Key points: open street at ``front`` for x < step_x, hill at ``hill`` beyond."""
    x, z = np.meshgrid(np.arange(-20, 30, 0.7), np.arange(-15, 15, 0.7))
    y = np.where(x < step_x, front, hill)
    return np.column_stack([x.ravel(), y.ravel(), z.ravel()])


def station(base=8.23, top=10.31):
    # Front wall at x = 0 faces the street; the back half is dug into the hill.
    b = Building("Building_s", shapely.box(0.0, -5.0, 8.0, 5.0), base, top, "facade_plaster")
    b.attributes = {}
    return b


def test_station_dug_into_slope_stands_on_its_exposed_grade():
    b = station()
    # The hill reaches the roof behind (x > 4); the street lies 2.2 m below the LoD2 base.
    points = hillside(step_x=4.0)
    assert slope_buildings.apply([b], points) == 1
    assert abs(b.base - 6.0) < 0.05
    evidence = b.attributes["set_into_slope"]
    assert evidence["lod2_base"] == 8.23 and evidence["buried_m"] >= 3 and evidence["exposed_m"] >= 3


def test_ordinary_plinth_and_flat_ground_keep_their_base():
    # A 0.6 m drop under a 3 m wall is a footing, not a buried storey.
    plinth = station(base=5.1, top=8.0)
    assert slope_buildings.apply([plinth], hillside(front=4.5, hill=7.8, step_x=4.0)) == 0
    assert plinth.base == 5.1
    # No hill reaching the roof: an ordinary building on level ground.
    flat = station(base=6.0, top=16.0)
    assert slope_buildings.apply([flat], hillside(front=6.0, hill=6.0)) == 0
    assert flat.base == 6.0


def test_funicular_profile_climbs_monotonically_from_the_lower_end():
    line = shapely.LineString([(60, 0), (0, 0)])  # mapped from the top down
    x = np.arange(-5, 66, 0.8)
    pts = np.column_stack([x, 6 + 0.5 * x + np.sin(x) * 0.8, np.zeros_like(x)])
    xz, s, y = funicular.profile(line, pts, [])
    assert xz[0][0] < xz[-1][0]  # reversed to start at the low end
    assert np.all(np.diff(y) >= -1e-9)
    assert abs(y[len(y) // 2] - (6 + 0.5 * xz[len(y) // 2][0])) < 1.5


def test_funicular_lower_end_meets_a_set_into_slope_station():
    b = station(base=6.0, top=10.31)
    b.attributes = {"set_into_slope": {"exposed_grade": 6.0}}
    line = shapely.LineString([(4, 0), (60, 0)])
    x = np.arange(0, 66, 0.8)
    pts = np.column_stack([x, 10 + 0.4 * x, np.zeros_like(x)])
    _, _, y = funicular.profile(line, pts, [b])
    assert y[0] < 8.5 and np.all(np.diff(y) >= -1e-9)


def test_roof_terrace_gets_a_railing_only_on_its_exposed_edges():
    b = station()
    points = hillside(step_x=4.0)
    slope_buildings.apply([b], points)
    rails = slope_buildings.railings([b], points)
    assert rails
    xs = np.concatenate([m.positions[:, 0] for m in rails])
    # The street side (x = 0) and the side walls down to the hill line; never the buried back.
    assert xs.min() < 0.5 and xs.max() < 7.5
