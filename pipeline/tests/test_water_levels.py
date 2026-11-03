"""Measured inland water surfaces: flat pools, steps at mapped structures, tidal datum kept."""

import numpy as np
import shapely

from turku3d import config as C
from turku3d.coarse import water_levels
from turku3d.coarse.banks import produce
from turku3d.coarse.build import grid_mesh
from turku3d.coarse.inputs import CELLS
from turku3d.col.materials import mat_id
import pytest


@pytest.fixture(autouse=True)
def no_whole_piece_reads(monkeypatch):
    """Synthetic pieces never read the real survey; the tile's own points measure them."""
    monkeypatch.setattr(water_levels, "reference", lambda piece: np.nan)


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


def test_pier_inside_the_sea_outline_keeps_the_datum_but_mapped_rapids_climb(monkeypatch):
    tile = C.VisualTile(0, 0, 0)
    sea = shapely.box(-40, -40, 290, 140)
    pier = shapely.box(40, 40, 70, 70)
    rapids = shapely.box(180, -40, 290, 140)
    def level_at(x):
        return np.where(x > 180, 1.8, 0.15)
    points = river_points(np.random.default_rng(4), sea, level_at, 20000)
    on_pier = shapely.contains_xy(pier, points[:, 0], points[:, 2])
    points[on_pier, 1] = 1.7
    monkeypatch.setattr(water_levels, "key_points", lambda tile, paths: points)
    levels = water_levels.measure(tile, [sea], [], [], [rapids])
    assert levels.at([[55.0, 55.0]])[0] == 0
    assert abs(levels.at([[240.0, 50.0]])[0] - 1.8) < 0.05


def test_piers_boats_and_bridge_footings_float_on_an_inland_pool():
    from types import SimpleNamespace

    pool = shapely.box(0, 0, 200, 40)
    levels = water_levels.Levels([pool], (0.0, 0.0), (2, 2), np.full((1, 2, 2), np.nan), np.array([7.5]))
    pier = dict(kind="pier", outline=shapely.box(20, 5, 30, 8), deck=0.55, water=0.0,
                deck_source="waterline-fallback", floating=True)
    ship = dict(kind="ship", outline=shapely.box(50, 5, 70, 12), deck=1.5, water=0.2)
    way = dict(line=shapely.LineString([(100, -20), (100, 60)]), survey={})
    crane = dict(kind="crane", outline=shapely.box(80, 5, 82, 7), height=20.0)
    inputs = SimpleNamespace(boats=[pier, ship, crane], water=[pool], roads={"bridge_ways": [way]})
    water_levels.follow(inputs, levels)
    water_levels.follow(inputs, levels)
    assert pier["water"] == 7.5 and abs(pier["deck"] - 8.05) < 1e-9
    assert ship["water"] == 7.5 and ship["deck"] >= 8.4
    assert way["survey"]["water_level"] == 7.5
    sea = water_levels.Levels([pool], (0.0, 0.0), (2, 2), np.full((1, 2, 2), np.nan), np.array([0.0]))
    tidal = dict(kind="pier", outline=shapely.box(20, 5, 30, 8), deck=0.55, water=0.0,
                 deck_source="waterline-fallback", floating=True)
    water_levels.follow(SimpleNamespace(boats=[tidal], water=[pool], roads={}), sea)
    assert tidal["water"] == 0.0 and tidal["deck"] == 0.55


def test_a_tile_without_water_returns_takes_the_canonical_piece_level(monkeypatch):
    tile = C.VisualTile(0, 0, 0)
    river = shapely.box(-40, 100, 290, 140)
    monkeypatch.setattr(water_levels, "key_points", lambda tile, paths: np.empty((0, 3)))
    monkeypatch.setattr(water_levels, "reference", lambda piece: 7.55)
    levels = water_levels.measure(tile, [river], [], [])
    assert levels.at([[125.0, 120.0]])[0] == 7.55
