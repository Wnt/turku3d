"""Ground cleanup must preserve lawns and paint while removing elevated canopy and cars."""

import numpy as np
import shapely
from turku3d import config as C
from turku3d.coarse.ortho_cleanup import clean, vehicle_mask
from turku3d.col.materials import mat_id


def test_canopy_on_pavement_is_filled_from_same_surface_not_lawn():
    tile = C.VisualTile(0, 0, 0)
    rgb = np.full((250, 250, 3), 95, np.uint8)
    rgb[10:20, 10:20] = [45, 110, 35]
    rgb[10:20, 130:140] = [45, 110, 35]
    rgb[40:44, 40:44] = 50  # Grey canopy detected by height, without green.
    streets = [(shapely.box(0, 0, 100, 250), mat_id("road"))]
    canopy = np.array([[x + 0.5, 8, z + 0.5] for x in range(40, 44) for z in range(40, 44)])
    output, stats = clean(tile, rgb, streets, [], canopy, np.zeros((2, 2)))
    assert np.all(output[10:20, 10:20] == 95)
    assert np.all(output[40:44, 40:44] == 95)
    np.testing.assert_array_equal(output[10:20, 130:140], rgb[10:20, 130:140])
    assert stats["road"]["canopy"] >= 116
    assert np.array_equal(rgb[10, 10], [45, 110, 35])


def test_vehicle_blobs_do_not_remove_lane_lines_or_zebra_bars():
    rgb = np.full((160, 160, 3), 90, np.uint8)
    rgb[40:49, 30:34] = [200, 40, 20]  # 4.5 x 2 m car.
    rgb[20:130, 60] = 240
    for row in range(60, 100, 5):
        rgb[row : row + 1, 90:98] = 240  # 0.5 x 4 m zebra bars.
    mask = vehicle_mask(rgb, np.ones((160, 160), bool), 0.5, None)
    assert mask[44, 32]
    assert not mask[20:130, 60].any()
    assert not mask[60:100, 90:98].any()


def test_too_little_clean_surface_uses_procedural_fallback_and_blocks_buildings():
    tile = C.VisualTile(0, 0, 0)
    rgb = np.full((64, 64, 3), [30, 100, 20], np.uint8)
    streets = [(shapely.box(0, 0, 250, 250), mat_id("road"))]
    building = shapely.box(0, 0, 100, 100)
    output, stats = clean(
        tile, rgb, streets, [], np.empty((0, 3)), np.zeros((2, 2)), buildings=[building]
    )
    np.testing.assert_array_equal(output[:25, :25], rgb[:25, :25])
    assert np.all(output[40:, 40:, 1] < 100)
    assert 0 < stats["road"]["masked_share"] < 1


def test_cleanup_code_is_visual_only():
    from turku3d.coarse.collision_key import paths
    from turku3d.coarse.packaging import producer_paths

    assert "ortho_cleanup.py" in {p.name for p in producer_paths()}
    assert "ortho_cleanup.py" not in {p.name for p in paths()}
