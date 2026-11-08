"""Lidar soffits of open ground floors: the facade returns' lower edge over the open outline."""

import numpy as np
import pytest
import shapely
from turku3d.coarse.inputs import Building
from turku3d.coarse.soffits import apply, lower_edge, measure


def ropewalk_cloud(soffit=5.2, open_below=True, seed=1):
    """A 30 x 6 m raised ropewalk on level ground at 2.0 m: dense facade returns from `soffit` to the 11 m eaves
    in a band outside both long faces, a few column/lamp returns below, ground returns around it and, when the
    ground floor is open, under it."""
    rng = np.random.default_rng(seed)
    rows = []
    for z0 in (-0.3, 6.3):  # just outside the long faces
        n = 1500
        x, z = rng.uniform(0, 30, n), z0 + rng.uniform(-0.2, 0.2, n)
        y = rng.uniform(soffit if open_below else 2.0, 11.0, n)
        rows.append(np.c_[x, y, z, np.full(n, 6)])
        k = 25  # columns and lamps under the soffit
        rows.append(np.c_[rng.uniform(0, 30, k), rng.uniform(2.2, soffit, k), np.full(k, z0), np.ones(k)])
    gx, gz = np.meshgrid(np.arange(-3, 33, 0.5), np.arange(-3, 9, 0.5))
    outside = ~shapely.contains_xy(shapely.box(0, 0, 30, 6), gx.ravel(), gz.ravel())
    rows.append(np.c_[gx.ravel()[outside], np.full(outside.sum(), 2.0), gz.ravel()[outside], np.full(outside.sum(), 2)])
    if open_below:
        n = 200
        rows.append(np.c_[rng.uniform(0.5, 29.5, n), np.full(n, 2.05), rng.uniform(0.5, 5.5, n), np.full(n, 12)])
    roof = np.c_[rng.uniform(0, 30, 2000), np.full(2000, 11.0), rng.uniform(0, 6, 2000), np.full(2000, 6)]
    cloud = np.vstack([*rows, roof])
    return cloud[:, :3], cloud[:, 3].astype(int)


def test_lower_edge_skips_sparse_columns_under_a_dense_facade():
    heights = np.r_[np.linspace(2.5, 4.9, 12), np.linspace(5.2, 11, 600)]
    assert lower_edge(heights, 3.8, 9.0) == pytest.approx(5.2, abs=0.2)
    assert lower_edge(heights[:20], 3.8, 9.0) is None  # too few returns to measure


def test_open_ropewalk_soffit_is_measured_and_replaces_the_storey_prior():
    footprint = shapely.geometry.polygon.orient(shapely.box(0, 0, 30, 6), 1.0)
    points, classes = ropewalk_cloud()
    found = measure(footprint, footprint, 11.0, points, classes)
    assert found["soffit"] == pytest.approx(5.2, abs=0.21) and found["ground"] == pytest.approx(2.0)
    b = Building("ropewalk", footprint, 2.0, 11.0, "facade_plaster")
    b.open_ground = [(footprint, 3.0, 2.0)]  # building:min_level=1 -> 3 m storey prior
    apply(b, [("ropewalk", footprint, found)])
    assert b.open_ground[0][1] == pytest.approx(3.2, abs=0.21) and b.open_ground[0][2] == 2.0


def test_a_closed_ground_floor_or_silent_lidar_keeps_the_osm_height():
    footprint = shapely.geometry.polygon.orient(shapely.box(0, 0, 30, 6), 1.0)
    points, classes = ropewalk_cloud(open_below=False)
    assert measure(footprint, footprint, 11.0, points, classes) is None  # no returns under it, facade to the ground
    b = Building("ropewalk", footprint, 2.0, 11.0, "facade_plaster")
    b.open_ground = [(footprint, 3.0, 2.0)]
    apply(b, [])
    assert b.open_ground == [(footprint, 3.0, 2.0)]
    # A soffit leaving less than a storey above it is no open ground floor's: the prior stays.
    apply(b, [("ropewalk", footprint, dict(soffit=10.0, ground=2.0, facade=900, under=200))])
    assert b.open_ground[0][1] == 3.0


def test_a_measured_edge_a_storey_away_from_the_tags_keeps_the_osm_height():
    """The lidar refines the storey OSM names: an edge 3 m above it is another facade's (a taller neighbour behind a
    bridge part), not this soffit."""
    footprint = shapely.geometry.polygon.orient(shapely.box(0, 0, 30, 6), 1.0)
    b = Building("bridge", footprint, 2.0, 14.0, "facade_plaster")
    b.open_ground = [(footprint, 3.0, 2.0)]
    apply(b, [("bridge", footprint, dict(soffit=8.1, ground=2.0, facade=900, under=200))])
    assert b.open_ground[0][1] == 3.0
    apply(b, [("bridge", footprint, dict(soffit=6.2, ground=2.0, facade=900, under=200))])
    assert b.open_ground[0][1] == 3.0  # 1.2 m off: a pilotis block's 3 m soffit measured 1.7 m
    apply(b, [("bridge", footprint, dict(soffit=5.6, ground=2.0, facade=900, under=200))])
    assert b.open_ground[0][1] == pytest.approx(3.6)
