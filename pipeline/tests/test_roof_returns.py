"""Roof-terrace and eave returns at a footprint do not raise terrain spikes."""

import numpy as np
import pytest
import shapely
from turku3d import config as C
from turku3d.coarse import roof_returns
from turku3d.coarse.inputs import Building, grid_axes, grid_heights

TILE = C.VisualTile(0, 0, 0)
FOOTPRINT = shapely.Polygon([(100, 90), (108, 106), (114, 103), (106, 87)])  # skew edge
NORMAL = np.array([16, -8]) / np.hypot(16, 8)  # from the river facade into the building
DEPTH = 6.7  # river facade to street facade


def across(xz):
    return (xz - [100, 90]) @ NORMAL


def lattice(step=0.7):
    x, z = np.meshgrid(np.arange(70, 150, step), np.arange(60, 140, step))
    return np.c_[x.ravel(), np.zeros(x.size), z.ravel()]


def terrace_points():
    """Quay at 4.1 m, eave line at 7.4 m within 1 m of the wall, roof terrace and street at 8 m."""
    p = lattice()
    xz = shapely.points(p[:, [0, 2]])
    d = shapely.distance(FOOTPRINT, xz)
    inside = shapely.contains(FOOTPRINT, xz)
    # The street lies beyond the far facade; the river quay in front of the near one.
    street = across(p[:, [0, 2]]) > DEPTH
    p[:, 1] = np.where(street, 8.0, 4.1)
    eave = ~inside & (d <= 0.9) & ~street
    p[eave, 1] = 7.4
    p[inside, 1] = 7.8
    return p


def terrace_building(roof=7.6):
    building = Building("b", FOOTPRINT, 5.09, 13.0, "facade_plaster")
    building.roof_parts = [(FOOTPRINT, np.array([0.0, 0.0, roof]))]
    return building


def test_roof_and_eave_returns_are_lowered_to_the_wall_bottom():
    points = terrace_points()
    building = terrace_building()
    raw = grid_heights(points, *grid_axes(TILE))
    heights, count = roof_returns.terrain_heights(TILE, points, raw, [building])
    assert count > 0
    xs, zs = grid_axes(TILE)
    gx, gz = np.meshgrid(xs, zs)
    nodes = shapely.points(np.c_[gx.ravel(), gz.ravel()])
    d = shapely.distance(FOOTPRINT, nodes).reshape(gx.shape)
    s = across(np.c_[gx.ravel(), gz.ravel()]).reshape(gx.shape)
    front = (d > 0) & (d < 2.5) & (s < 0)
    assert raw[front].max() > 6.5  # the spike the raw TIN makes in front of the facade
    # No visible ground in front of the facade rises above the wall bottom, and none
    # drops below the quay: the wall still meets the ground.
    assert heights[front].max() <= 5.09 + 1e-6
    assert heights[front].min() >= 4.1 - 1e-6
    # The higher street behind the building keeps its measured level.
    behind = (d > 1.5) & (d < 4) & (s > DEPTH)
    np.testing.assert_allclose(heights[behind], raw[behind])
    # Away from the footprint the grid is untouched.
    far = d > 8
    np.testing.assert_array_equal(heights[far], raw[far])


def test_sloped_ground_and_uphill_walls_keep_their_points():
    points = lattice()
    points[:, 1] = 0.6 * (points[:, 0] - 70)  # a 31 degree slope across the building
    base = float(0.6 * (100 - 70))
    building = Building("b", FOOTPRINT, base, base + 12, "facade_plaster")
    # Even a roof surveyed level with the slope does not make the slope a roof return.
    building.roof_parts = [(FOOTPRINT, np.array([0.6, 0.0, -42.0]))]
    cleaned, changed = roof_returns.lower(points, [building])
    assert not changed.any()
    np.testing.assert_array_equal(cleaned, points)


def test_raised_ground_below_the_roof_is_not_a_roof_return():
    # The same step, but the surveyed roof is far above it: a terrace or landing.
    points = terrace_points()
    _, changed = roof_returns.lower(points, [terrace_building(roof=20.0)])
    assert not changed.any()


def test_mapped_retaining_wall_keeps_the_measured_step():
    points = terrace_points()
    building = terrace_building()
    wall = shapely.LineString(FOOTPRINT.exterior.coords[:2]).offset_curve(0.6)
    guards = roof_returns.guards([(wall, {"barrier": "retaining_wall"}, False)])
    _, changed = roof_returns.lower(points, [building], guards)
    xz = shapely.points(points[changed][:, [0, 2]])
    assert not np.any(shapely.distance(wall, xz) <= roof_returns.GUARD)


def test_roof_deck_inside_a_footprint_leaves_the_ground_model():
    """A garage deck on the LoD2 roof drapes no mound; ground under passages stays."""
    footprint = shapely.box(100, 100, 140, 130)
    garage = Building("g", footprint, 21.0, 29.5, "facade_plaster")
    garage.roof_parts = [(footprint, (0.0, 0.0, 29.5))]
    p = lattice(1.0)
    inside = shapely.contains_xy(footprint, p[:, 0], p[:, 2])
    p[:, 1] = 21.0
    p[inside, 1] = 29.5
    passage = inside & (p[:, 0] > 118) & (p[:, 0] < 122)
    p[passage, 1] = 21.2  # walk-through at street level keeps its ground
    lowered, changed_points = roof_returns.roof_decks(p, [garage])
    assert changed_points[inside & ~passage].all() and not changed_points[passage | ~inside].any()
    assert np.allclose(lowered[inside & ~passage, 1], 21.0)
    heights, changed = roof_returns.terrain_heights(TILE, p, grid_heights(p, *grid_axes(TILE)), [garage])
    xs, zs = grid_axes(TILE)
    outside = np.ix_((zs > 132) & (zs < 136), (xs > 100) & (xs < 140))
    assert changed > 0 and heights[outside].max() < 21.5  # no mound beyond the wall


def test_shell_starts_at_the_low_open_terrain_when_lod2_ground_floats():
    from turku3d.coarse.register import settle_base

    footprint = shapely.box(100, 100, 140, 130)
    garage = Building("g", footprint, 23.2, 33.3, "facade_plaster")
    p = lattice(1.0)
    p[:, 1] = np.where(p[:, 0] < 120, 20.5, 31.0)  # street below one side, uphill road behind
    settle_base(garage, p)
    assert garage.base == pytest.approx(20.5) and garage.lod2_base == 23.2
    level = Building("l", footprint, 20.4, 30.0, "facade_plaster")
    settle_base(level, p)
    assert level.base == 20.4 and not hasattr(level, "lod2_base")  # within 0.5 m: unchanged
