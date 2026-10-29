"""Noisy river returns must not make dents, shoreline gaps or tile-dependent paving."""

import numpy as np
import shapely
from turku3d import config as C
from turku3d.coarse import banks, stair_survey, walkable
from turku3d.col.materials import mat_id


def surveyed():
    x, z = np.meshgrid(np.arange(-20, 521, 0.5), np.arange(18.1, 32, 0.3))
    x, z = x.ravel(), z.ravel()
    rng = np.random.default_rng(17)
    h = 2.2 + x * 0.001 + np.maximum(z - 21.5, 0) * 0.13
    h += rng.normal(0, 0.01, len(h))
    h[::7] += 1.8
    return np.c_[x, h, z]


def test_dominant_paving_fit_rejects_outliers_and_is_identical_across_tile_crops():
    line = shapely.LineString([(-20, 20), (520, 20)])
    points = surveyed()
    left, right = [
        walkable.measure("walk", line, 3, points, shapely.box(x, 0, x + 250, 250)) for x in (0, 250)
    ]
    query = np.array([[250, -5, z] for z in np.arange(18.6, 24, 0.2)])
    a, b = walkable.grade(query, [left]), walkable.grade(query, [right])
    np.testing.assert_array_equal(a, b)
    paving = walkable.grade(
        np.c_[np.arange(50, 450, 0.25), np.zeros(1600), np.full(1600, 20)], [left, right]
    )
    expected = 2.2 + paving[:, 0] * 0.001
    assert np.max(np.abs(paving[:, 1] - expected)) < 0.02
    assert np.max(np.abs(np.diff(paving[:, 1], n=2))) < 0.003


def test_constrained_river_paving_has_no_area_holes_and_bank_top_matches_dry_ground():
    line = shapely.LineString([(-10, 20), (260, 20)])
    patch = walkable.measure("walk", line, 3, surveyed(), shapely.box(0, 0, 250, 250))
    x, z = np.meshgrid(np.arange(0, 31, 2.5), np.arange(10, 31, 2.5))
    pos = np.c_[x.ravel(), 2.2 + np.sin(x.ravel()) * 0.5, z.ravel()]
    n = x.shape[1]
    a = (np.arange(x.shape[0] - 1)[:, None] * n + np.arange(n - 1)).ravel()
    idx = np.r_[np.c_[a, a + n, a + 1], np.c_[a + 1, a + n, a + n + 1]].astype(np.uint32)
    water = shapely.box(-10, -10, 40, 18.6)
    dry, wet, bank, solid = banks.produce(
        pos,
        idx,
        np.full(len(idx), mat_id("paving_slab"), np.uint8),
        np.ones(len(idx), bool),
        [water],
        0.2,
        walks=[patch],
    )
    footprints = shapely.polygons(solid.positions[solid.indices][:, :, [0, 2]])
    cover = shapely.union_all(footprints[shapely.area(footprints) > 1e-12])
    assert shapely.box(0, 10, 30, 30).difference(cover).area < 1e-9
    # A wet triangle centre must not hide its dry paving remainder. A bed ray
    # or a water sensor below the missing paving is insufficient coverage.
    window = shapely.box(0, 10, 30, 30)
    dry_cover = shapely.union_all(shapely.polygons(dry.positions[dry.indices][:, :, [0, 2]]))
    wet_cover = shapely.union_all(shapely.polygons(wet.positions[wet.indices][:, :, [0, 2]]))
    assert dry_cover.symmetric_difference(window.difference(water)).area < 1e-9
    assert wet_cover.symmetric_difference(window.intersection(water)).area < 1e-9
    top = bank.positions.reshape(-1, 4, 3)[:, :2].reshape(-1, 3)
    dry_points = {tuple(v.round(8)) for v in dry.positions}
    assert all(tuple(v.round(8)) in dry_points for v in top)
    assert np.all(top[:, 1] > 2.1)
    assert np.all(wet.positions[:, 1] == 0.2)
    centres = dry.positions[dry.indices].mean(axis=1)
    core = (centres[:, 2] > 18.6) & (centres[:, 2] < 21.5)
    assert np.all(dry.material[core] == mat_id("paving_slab"))


def test_ordinary_quay_stairs_receive_lidar_without_a_bridge(monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    cloud = SimpleNamespace(points=np.zeros((1, 3)), records={"classification": [2]})
    monkeypatch.setattr(
        "turku3d.lidar.TileStore", lambda **kw: SimpleNamespace(query=lambda box: cloud)
    )
    monkeypatch.setattr(C, "laz_tiles_for_bbox", lambda box: [])
    monkeypatch.setattr(stair_survey, "measure", lambda *args: {"heights": [4, 2]})
    row = ("quay", shapely.LineString([(0, 0), (0, 5)]), {"incline": "down"})
    stair_survey.attach([row], [Path("source.laz")], [], [])
    assert row[2]["_profile"]["heights"] == [4, 2]


def test_vector_cut_keeps_submillimetre_surface_remainders():
    from turku3d.coarse.surfaces import split_boundaries

    p = np.array([[0.0, 3, 0], [0, 3, 1], [1, 3, 0]])
    tiny = shapely.box(0.2, 0.2, 0.2002, 0.2002)
    pos, idx, _, _ = split_boundaries(
        p, np.array([[0, 1, 2]], np.uint32), np.array([1], np.uint8), np.array([True]), [(tiny, 2)]
    )
    cover = shapely.union_all(shapely.polygons(pos[idx][:, :, [0, 2]]))
    assert shapely.Polygon(p[:, [0, 2]]).difference(cover).area < 1e-12


def test_exact_paving_edge_does_not_inherit_missing_slope_samples():
    line = shapely.LineString([(-20, 20), (520, 20)])
    patch = walkable.measure("walk", line, 3, surveyed(), shapely.box(0, 0, 250, 250))
    patch["heights"][:, [0, 1, 5, 6]] = np.nan
    query = np.array([[100, -2, z] for z in (18.5 - 1e-12, 18.5, 20, 21.5, 21.5 + 1e-12)])
    result = walkable.grade(query, [patch])
    np.testing.assert_allclose(result[:, 1], 2.3, atol=0.02)
