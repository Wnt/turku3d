from types import SimpleNamespace

import numpy as np
import rasterio
from rasterio.transform import from_origin
import shapely

from turku3d import config as C
from turku3d.coarse import water_appearance as appearance


def test_colour_samples_body_interior_not_neighbour_water_or_bright_rim(tmp_path):
    rgb = np.full((3, 40, 40), 255, dtype='uint8')
    rgb[:, 2:18, 2:18] = np.array([110, 190, 220])[:, None, None]
    rgb[:, 22:38, 22:38] = np.array([70, 60, 40])[:, None, None]
    path = tmp_path / 'ortho.tif'
    with rasterio.open(path, 'w', driver='GTiff', width=40, height=40, count=3,
                       dtype='uint8', transform=from_origin(C.ORIGIN_E, C.ORIGIN_N, 1, 1)) as ds:
        ds.write(rgb)
    blue = appearance.sample_colour(shapely.box(0, 0, 20, 20), path)
    brown = appearance.sample_colour(shapely.box(20, 20, 40, 40), path)
    np.testing.assert_allclose(blue, np.array([110, 190, 220]) / 255, atol=1e-5)
    np.testing.assert_allclose(brown, np.array([70, 60, 40]) / 255, atol=1e-5)


def test_same_level_water_bodies_keep_separate_colour_and_all_triangles():
    tile = C.VisualTile(0, 0, 0)
    p = np.array([[0, 1, 0], [0, 1, 2], [2, 1, 0], [10, 1, 0], [10, 1, 2], [12, 1, 0]], float)
    ix = np.array([[0, 1, 2], [3, 4, 5]], dtype='uint32')
    records = [(shapely.box(-1, -1, 3, 3), 'pool', [.5, .8, .9]),
               (shapely.box(9, -1, 13, 3), 'river', [.3, .2, .1])]
    meshes = appearance.visuals(tile, SimpleNamespace(positions=p, indices=ix), records)
    assert [m.extras['water_body'] for m in meshes] == ['pool', 'river']
    assert [m.extras['water_color'] for m in meshes] == [r[2] for r in records]
    assert sum(len(m.indices) for m in meshes) == len(ix)


def test_sloped_reach_and_level_bands_get_their_own_mirror_meshes():
    tile = C.VisualTile(0, 0, 0)
    p = np.array([[0, 7.6, 0], [0, 7.6, 2], [2, 7.6, 0],
                  [10, 7.6, 0], [10, 7.6, 2], [12, 7.4, 0],
                  [20, 0, 0], [20, 0, 2], [22, 0, 0]], float)
    ix = np.array([[0, 1, 2], [3, 4, 5], [6, 7, 8]], dtype='uint32')
    records = [(shapely.box(-1, -1, 30, 3), 'aura', None)]
    meshes = appearance.visuals(tile, SimpleNamespace(positions=p, indices=ix), records)
    by_id = {m.extras['object_id']: m for m in meshes}
    assert set(by_id) == {'water_t0_0_0_aura', 'water_t0_0_0_aura_b15', 'water_t0_0_0_aura_slope'}
    assert by_id['water_t0_0_0_aura_slope'].extras['water_slope'] is True
    assert 'water_band' not in by_id['water_t0_0_0_aura'].extras
    assert by_id['water_t0_0_0_aura_b15'].extras['water_band'] == '15'
    assert all(np.all(m.normals == [0, 1, 0]) for m in meshes)


def test_a_measured_pool_is_one_mirror_reach_across_level_band_edges():
    from turku3d.coarse.water_levels import Levels
    tile = C.VisualTile(0, 0, 0)
    pool = shapely.box(-1, -1, 30, 3)
    levels = Levels([pool], (0.0, 0.0), (2, 2), np.full((1, 2, 2), np.nan), np.array([7.55]))
    p = np.array([[0, 7.45, 0], [0, 7.45, 2], [2, 7.45, 0],
                  [10, 7.6, 0], [10, 7.6, 2], [12, 7.6, 0]], float)
    ix = np.array([[0, 1, 2], [3, 4, 5]], dtype='uint32')
    meshes = appearance.visuals(tile, SimpleNamespace(positions=p, indices=ix),
                                [(pool, 'aura', None)], levels)
    assert [m.extras['water_band'] for m in meshes] == ['7.55+0']
