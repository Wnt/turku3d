"""Exactness of walkable's faster point tests (same answers as the plain predicates)."""

from types import SimpleNamespace

import numpy as np
import shapely

from turku3d.coarse.retaining_survey import ground_points
from turku3d.coarse.walkable import intersects_parts


def test_part_by_part_point_test_matches_prepared_collection():
    rng = np.random.default_rng(4)
    ring = shapely.Point(50, 50).buffer(30).difference(shapely.box(40, 40, 60, 60))
    geometry = shapely.GeometryCollection([
        ring, shapely.box(0, 0, 10, 4), shapely.Point(70.25, 10.5),
        shapely.LineString([(0, 90), (95, 95)]), shapely.Polygon(),
    ])
    x, y = rng.uniform(-5, 105, 20000).round(2), rng.uniform(-5, 105, 20000).round(2)
    # Exact boundary and vertex hits, which a binned shortcut must not misjudge.
    coords = shapely.get_coordinates(geometry)
    x = np.r_[x, coords[:, 0], 70.25, 10, 5, np.nan]
    y = np.r_[y, coords[:, 1], 10.5, 2, 4, 1]
    expected = shapely.intersects_xy(geometry, x, y)
    assert expected.any() and not expected.all()
    np.testing.assert_array_equal(intersects_parts(geometry, x, y, cell=3.0), expected)


def test_ground_points_within_equals_filtering_afterwards():
    rng = np.random.default_rng(5)
    points = np.c_[rng.uniform(0, 50, 4000), rng.normal(0, .3, 4000), rng.uniform(0, 50, 4000)]
    classes = rng.choice([2, 12], 4000)
    cloud = SimpleNamespace(points=points, records={"classification": classes})

    def within(p):
        return (p[:, 0] < 20) | (p[:, 2] > 35)

    full = ground_points(cloud)
    np.testing.assert_array_equal(ground_points(cloud, within=within), full[within(full)])
