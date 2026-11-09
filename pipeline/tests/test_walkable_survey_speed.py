"""Exactness of walkable's faster point tests (same answers as the plain predicates)."""

from types import SimpleNamespace

import numpy as np
import shapely

from turku3d.coarse.retaining_survey import ground_points
from turku3d.coarse.walkable import intersects_parts


def test_station_interpolation_matches_geos_at_vertices_and_adjacent_floats():
    from turku3d.coarse.walkable import line_interpolator

    rng = np.random.default_rng(221)
    paths = [np.array([[0., 0.], [0., 0.], [3., 4.], [3., 4.], [-10., 7.]]),
             np.array([[2., 3.], [2., 3.]]),
             np.cumsum(rng.normal(size=(200, 2)), axis=0),
             np.array([[0., 0.], [1e6, 0.], [1e6, 1e-8], [1e6 + 1, 1.]])]
    for points in paths:
        line = shapely.LineString(points)
        segments = shapely.linestrings(np.stack([points[:-1], points[1:]], axis=1))
        knots = np.r_[0., np.cumsum(shapely.length(segments))]
        distances = np.clip(np.r_[knots, np.nextafter(knots, -np.inf),
                                  np.nextafter(knots, np.inf),
                                  rng.uniform(0, line.length, 400)], 0, line.length)
        expected = shapely.get_coordinates(shapely.line_interpolate_point(line, distances))
        np.testing.assert_array_equal(line_interpolator(line)(distances), expected)
        assert line_interpolator(line)(np.empty(0)).shape == (0, 2)


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


def test_survey_corridor_keeps_every_possible_voxel_winner():
    from turku3d.coarse.walkable import survey_region

    line = shapely.LineString([(-55, -31), (-9, 37), (15, 6), (60, 21)])
    width = 3.0
    region = survey_region([('curve', line, width)])
    rng = np.random.default_rng(33)
    points = rng.uniform([-70, -50], [80, 60], (12000, 2))
    measured = points[shapely.distance(shapely.points(points), line) < width / 2 + 7]
    cells = np.floor(measured / .25) * .25
    corners = np.concatenate([cells + [x, z] for x in (0, .25) for z in (0, .25)])
    assert shapely.intersects_xy(region, corners[:, 0], corners[:, 1]).all()
    # Dense arc samples catch an inscribed buffer clipping the endpoint halo.
    angles = np.arange(4096) * (2 * np.pi / 4096)
    radius = width / 2 + 7 + np.sqrt(2) * .25
    arc = [-55, -31] + radius * np.c_[np.cos(angles), np.sin(angles)]
    assert shapely.intersects_xy(region, arc[:, 0], arc[:, 1]).all()
    assert not region.intersects(shapely.Point(500, 500))


def test_survey_corridor_preserves_first_voxel_winner_at_oblique_bend():
    from turku3d.coarse.walkable import near_line, survey_region

    line = shapely.LineString([
        (44.93135665467664, -92.06232041501544),
        (-6.010337029407535, -6.010337029407535),
        (-90.2320604010362, -59.92416932050755),
    ])
    # The first equal-height return lies outside reach; the second is usable
    # but must still lose their shared voxel. A polyline round join lost the first.
    points = np.array([[.2499999, .2499999], [.0000001, .0000001]])
    assert np.array_equal(np.floor(points[0] / .25), np.floor(points[1] / .25))
    np.testing.assert_array_equal(near_line(line, points, 8.5), [False, True])
    region = survey_region([('bend', line, 3)])
    selected = points[shapely.intersects_xy(region, points[:, 0], points[:, 1])]
    np.testing.assert_array_equal(selected, points)
    _, first = np.unique(np.floor(selected / .25), axis=0, return_index=True)
    assert not near_line(line, selected[first], 8.5).any()
