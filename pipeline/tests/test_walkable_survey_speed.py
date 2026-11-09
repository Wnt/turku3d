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


def test_path_distance_cells_keep_exact_cutoff_and_return_order():
    from turku3d.coarse.walkable import near_line

    rng = np.random.default_rng(19)
    line = shapely.LineString([(-80, -10), (-10, -10), (-10, 55), (70, 55), (70, -25)])
    reach = 8.5
    xz = np.r_[rng.uniform([-100, -50], [100, 80], (25000, 2)),
               [[-40, -1.5], [-40, np.nextafter(-1.5, -np.inf)],
                [-40, np.nextafter(-1.5, np.inf)], [-80, -10], [-100, -10]] * 5]
    expected = shapely.distance(shapely.points(xz), line) < reach
    np.testing.assert_array_equal(near_line(line, xz, reach), expected)
    np.testing.assert_array_equal(near_line(line, xz[:100], reach), expected[:100])
    assert not near_line(line, np.empty((0, 2)), reach).any()
    # Sparse returns spanning more than the bounded cell table use exact distances.
    wide = np.r_[xz, [[1e6, -1e6]]]
    np.testing.assert_array_equal(near_line(line, wide, reach),
                                  shapely.distance(shapely.points(wide), line) < reach)
    # At zero, subtraction cannot round away adjacent representable distances.
    horizontal = shapely.LineString([(-100, 0), (100, 0)])
    cutoff = np.array([[0, np.nextafter(reach, -np.inf)], [0, reach],
                       [0, np.nextafter(reach, np.inf)]])
    expected_cutoff = shapely.distance(shapely.points(cutoff), horizontal) < reach
    np.testing.assert_array_equal(expected_cutoff, [True, False, False])
    np.testing.assert_array_equal(near_line(horizontal, np.r_[xz, cutoff], reach)[-3:],
                                  expected_cutoff)
