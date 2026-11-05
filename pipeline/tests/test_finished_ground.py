"""Finished ground (ground.finished, finished-ground@1): construction-era lidar ground in renovated
street parts is replaced by the finished surface; every other ground and the tile seams stay."""

import numpy as np
import pandas as pd
import pytest
import shapely

from turku3d import config as C
from turku3d.coarse.inputs import grid_axes, grid_heights
from turku3d.ground import finished as F


def plane(x, z):
    return 5.0 + 0.01 * x - 0.005 * z


def key_points(bbox, relief=lambda x, z: 0.0, step=3.0):
    xs = np.arange(bbox.xmin, bbox.xmax + 1e-9, step)
    zs = np.arange(bbox.zmin, bbox.zmax + 1e-9, step)
    gx, gz = np.meshgrid(xs, zs)
    x, z = gx.ravel(), gz.ravel()
    return np.column_stack([x, plane(x, z) + relief(x, z), z])


def evidence(tile, zone, relief, spots=()):
    region = tile.bbox.buffered(F.REGION)
    return F.Evidence(
        zones=[(zone, {"key": "z", "kind": "Muu", "year": 2022, "material": "Luonnonkivi"})],
        spots=np.asarray(spots, np.float64).reshape(-1, 3),
        points=key_points(region, relief),
    )


def tile_points(tile, ev):
    b = tile.bbox.buffered(F.MARGIN)
    p = ev.points
    return p[(p[:, 0] >= b.xmin) & (p[:, 0] <= b.xmax) & (p[:, 2] >= b.zmin) & (p[:, 2] <= b.zmax)]


def finished_at(tile, points, x, z):
    xs, zs = grid_axes(tile)
    grid = grid_heights(points, xs, zs)
    from turku3d.coarse.surfaces import grid_height

    return np.array([grid_height(tile, grid, a, b) for a, b in zip(x, z, strict=True)])


TILE = C.VisualTile(0, 0, 0)
SQUARE = shapely.box(80, 80, 170, 170)


def trench(x, z):
    """A 4 m wide, 1.5 m deep trench across the square and a 1 m retaining step inside it."""
    cut = np.where((np.abs(x - 125) < 2) & (z > 90) & (z < 160), -1.5, 0.0)
    return cut + np.where(z > 150, 1.0, 0.0)


def test_trench_is_filled_and_steps_and_other_ground_stay():
    ev = evidence(TILE, SQUARE, trench)
    points, report, region = F.apply(TILE, tile_points(TILE, ev), ev)
    assert report is not None and report["sites"]
    assert region.contains(shapely.Point(125, 120)) and not region.contains(shapely.Point(20, 20))
    xs = np.full(20, 125.0)
    zs = np.linspace(95, 145, 20)
    after = finished_at(TILE, points, xs, zs)
    assert np.abs(after - plane(xs, zs)).max() < 0.15  # trench floor back on the square's surface
    # The retaining step (wider than any closing radius along its line) is not construction.
    z_high = np.full(5, 158.0)
    x_step = np.linspace(95, 115, 5)
    before = finished_at(TILE, tile_points(TILE, ev), x_step, z_high)
    assert np.allclose(finished_at(TILE, points, x_step, z_high), before, atol=0.02)
    # Ground outside the zone keeps its key points exactly.
    far = (points[:, 0] < 40) | (points[:, 2] > 220)
    original = tile_points(TILE, ev)
    keep = (original[:, 0] < 40) | (original[:, 2] > 220)
    assert np.array_equal(np.sort(points[far], axis=0), np.sort(original[keep], axis=0))


def test_tile_without_finished_state_zone_is_unchanged():
    ev = evidence(TILE, shapely.box(1000, 1000, 1050, 1050), trench)
    points = tile_points(TILE, ev)
    out, report, region = F.apply(TILE, points, ev)
    assert out is points and report is None and region is None


def test_wide_excavation_meets_the_post_lidar_spot_height():
    def pit(x, z):
        return np.where((x > 100) & (x < 150) & (z > 100) & (z < 150), -3.0, 0.0)

    spot = (125.0, 125.0, float(plane(125.0, 125.0)) + 0.1)
    ev = evidence(TILE, SQUARE, pit, spots=[spot])
    points, report, region = F.apply(TILE, tile_points(TILE, ev), ev)
    assert report is not None
    assert abs(finished_at(TILE, points, [125.0], [125.0])[0] - spot[2]) < 0.1
    assert abs(finished_at(TILE, points, [110.0], [140.0])[0] - plane(110.0, 140.0)) < 0.3


def test_neighbouring_tiles_meet_exactly_over_a_site_on_their_seam():
    east = C.VisualTile(0, 1, 0)
    zone = shapely.box(200, 60, 300, 190)

    def cut(x, z):
        return np.where((np.abs(z - 125) < 2.5) & (x > 210) & (x < 290), -1.2, 0.0)

    ev = evidence(TILE, zone, cut)
    ev_east = evidence(east, zone, cut)
    west_points, west, _ = F.apply(TILE, tile_points(TILE, ev), ev)
    east_points, east_report, _ = F.apply(east, tile_points(east, ev_east), ev_east)
    assert west and east_report
    xs, zs = grid_axes(TILE)
    west_grid = grid_heights(west_points, xs, zs)
    east_grid = grid_heights(east_points, *grid_axes(east))
    assert np.abs(west_grid[:, -1] - east_grid[:, 0]).max() < 1e-9
    assert abs(west_grid[64, -1] - plane(250.0, 125.0)) < 0.15


def test_zone_rows_take_renovated_paved_parts_and_keep_structures_out():
    rows = pd.DataFrame({
        "geometry_local": [shapely.box(0, 0, 10, 10)] * 4,
        "Katuosalaji": ["Ajorata", "Jalkakäytävä", "Portaat", "Muu"],
        "Valmistumisvuosi": ["0", "0", "0", "2024"],
        "Peruskorjausvuosi": ["2022", "2019", "2023", "0"],
        "Pintamateriaali": ["Asfalttibetoni", "Betonilaatta", "Luonnonkivi", "Nupukivi"],
        "feature_key": ["a", "b", "c", "d"],
    })
    zones, structures, streets = F.zone_rows(rows)
    assert [a["key"] for _, a in zones] == ["a", "d"]
    assert len(structures) == 1 and len(streets) == 3


def test_chain_ends_with_the_finished_ground_spot_rebuild():
    from turku3d.migrations import finished_ground
    from turku3d.migrations.core import Rebuild
    from turku3d.migrations.registry import CHAIN

    keys = [m.key for m in CHAIN]
    assert "finished-ground@1" in keys
    step = CHAIN[keys.index("finished-ground@1")]
    assert isinstance(step, Rebuild)
    # A product of a generator with the rule carries its report and is never rebuilt again.
    assert finished_ground.affected({"tile": "t0_0_0", "finished_ground": None}) is False


@pytest.mark.parametrize("value", ["2021", 2022, "x", None])
def test_year_parsing(value):
    assert F._year(value) in (0, 2021, 2022)


def test_street_profiles_in_finished_ground_are_dropped():
    from turku3d.coarse.inputs import outside_finished_ground

    inside = {"line": shapely.LineString([(0, 0), (10, 0)])}
    outside = {"line": shapely.LineString([(50, 50), (60, 50)])}
    value = {"region": shapely.box(-1, -1, 20, 20)}
    assert outside_finished_ground([inside, outside], value) == [outside]
    assert outside_finished_ground([inside], {"region": None}) == [inside]


def test_a_quay_by_the_water_with_a_moored_ship_is_no_site():
    def river(x, z):
        return np.where(z < 100, -3.0, 0.0)  # quay edge at z = 100, water below

    water = shapely.box(-400, -400, 700, 100)
    ship = shapely.box(90, 92, 150, 99)  # an LoD2 restaurant boat moored at the quay
    ev = evidence(TILE, shapely.box(60, 100, 190, 112), river)
    ev.water = [water]
    ev.footprints = F.on_land([ship], ev.water)
    assert ev.footprints == []
    out, report, region = F.apply(TILE, tile_points(TILE, ev), ev)
    assert report is None and region is None


def test_kerb_seam_sampler_reads_a_changed_tiles_own_finished_ground_across_the_seam():
    from turku3d.coarse.inputs import CELLS
    from turku3d.coarse.kerbs import height_sampler

    points = key_points(TILE.bbox.buffered(F.MARGIN))
    heights = np.zeros((CELLS + 1, CELLS + 1))
    sample = height_sampler(TILE, heights, root=None, sources=None, finished_points=points)
    assert abs(sample(255.0, 125.0) - plane(255.0, 125.0)) < 1e-6  # east neighbour, no inputs needed
    assert sample(125.0, 125.0) == 0.0  # the tile's own grid stays the tile's own
