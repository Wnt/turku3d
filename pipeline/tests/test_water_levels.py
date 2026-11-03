"""Measured inland water surfaces: flat pools, steps at mapped structures, tidal datum kept."""

import numpy as np
import shapely

from turku3d import config as C
from turku3d.coarse import water_levels
from turku3d.coarse.banks import produce
from turku3d.coarse.build import grid_mesh
from turku3d.coarse.inputs import CELLS
from turku3d.col.materials import mat_id


def river_points(rng, body, level_at, count=4000):
    xs = rng.uniform(-40, 290, count)
    zs = rng.uniform(-40, 290, count)
    inside = shapely.contains_xy(body, xs, zs)
    heights = np.where(inside, level_at(xs) + rng.normal(0, 0.02, count), 12.0)
    return np.column_stack([xs, heights, zs])


def test_pool_above_a_weir_is_flat_and_steps_at_the_structure(monkeypatch):
    tile = C.VisualTile(0, 0, 0)
    river = shapely.box(-40, 100, 290, 140)
    weir = shapely.LineString([(125, 90), (125, 150)])
    rng = np.random.default_rng(1)
    points = river_points(rng, river, lambda x: np.where(x < 125, 7.6, 1.6))
    monkeypatch.setattr(water_levels, "key_points", lambda tile, paths: points)
    levels = water_levels.measure(tile, [river], [weir], [])
    x = np.arange(0, 250, 5.0)
    surface = levels.at(np.column_stack([x, np.full(len(x), 120.0)]))
    upstream, downstream = surface[x < 118], surface[x > 132]
    assert np.ptp(upstream) < 0.03 and abs(np.median(upstream) - 7.6) < 0.02
    assert abs(np.median(downstream) - 1.6) < 0.02
    # Flat across the channel.
    across = levels.at(np.column_stack([np.full(9, 60.0), np.linspace(101, 139, 9)]))
    assert np.ptp(across) < 0.03


def test_tidal_water_keeps_the_sea_datum(monkeypatch):
    tile = C.VisualTile(0, 0, 0)
    sea = shapely.box(-40, -40, 290, 140)
    points = river_points(np.random.default_rng(2), sea, lambda x: np.full(len(x), 0.18))
    monkeypatch.setattr(water_levels, "key_points", lambda tile, paths: points)
    levels = water_levels.measure(tile, [sea], [], [])
    assert np.all(levels.at(np.column_stack([np.arange(0, 250, 10.0), np.full(25, 50.0)])) == 0)


def test_unmeasured_ditch_uses_its_lowest_bank_returns(monkeypatch):
    tile = C.VisualTile(0, 0, 0)
    ditch = shapely.box(-40, 120, 290, 122)
    xs = np.repeat(np.arange(-40, 290, 1.0), 2)
    zs = np.tile([119.0, 123.0], len(xs) // 2)
    points = np.column_stack([xs, np.full(len(xs), 16.6), zs])
    monkeypatch.setattr(water_levels, "key_points", lambda tile, paths: points)
    levels = water_levels.measure(tile, [ditch], [], [])
    np.testing.assert_allclose(levels.at([[125.0, 121.0]]), 16.4, atol=1e-6)


def test_inland_ditch_meets_natural_banks_at_its_own_surface(monkeypatch):
    tile = C.VisualTile(0, 0, 0)
    ditch = shapely.box(-10, 118.3, 260, 122.1)
    heights = np.full((CELLS + 1, CELLS + 1), 16.8)
    p, ix = grid_mesh(tile, heights)
    points = river_points(np.random.default_rng(3), ditch, lambda x: np.full(len(x), 16.2), 20000)
    monkeypatch.setattr(water_levels, "key_points", lambda tile, paths: points)
    levels = water_levels.measure(tile, [ditch], [], [])
    dry, wet, bank, solid = produce(p, ix, np.full(len(ix), mat_id("grass"), np.uint8),
                                    np.ones(len(ix), bool), [ditch], 0.0, levels=levels)
    np.testing.assert_allclose(wet.positions[:, 1], 16.2, atol=0.03)
    shore = shapely.distance(shapely.points(dry.positions[:, [0, 2]]), ditch) < 1e-6
    # The natural bank meets the water just above its surface, never at sea level.
    np.testing.assert_allclose(dry.positions[shore, 1], 16.25, atol=0.03)
    assert dry.positions[:, 1].min() > 16.1
    faces = bank.positions[bank.indices]
    assert np.ptp(faces[:, :, 1], axis=1).max() < 0.5
    bed = solid.positions[solid.positions[:, 1] < 16.1, 1]
    assert 16.2 - 3.0 < bed.min() < 16.2 - 0.6
