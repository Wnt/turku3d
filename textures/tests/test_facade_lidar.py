"""Lidar facade colour measurement on synthetic walls: cells, inpainting, smear numbers."""

import numpy as np
from turkutex import facade_lidar as FL

DTYPE = np.dtype(
    [
        ("u", "<f4"),
        ("h", "<f4"),
        ("d", "<f4"),
        ("rgb", "u1", (3,)),
        ("classification", "u1"),
        ("return_number", "u1"),
        ("number_of_returns", "u1"),
    ]
)


def records(u, h, d, rgb, cls=6):
    out = np.zeros(len(u), DTYPE)
    out["u"], out["h"], out["d"] = u, h, d
    out["rgb"] = rgb
    out["classification"] = cls
    return out


def grid_points(width, height, rgb, d=0.0, step=0.25):
    u, h = np.meshgrid(np.arange(0.1, width, step), np.arange(0.4, height, step))
    return records(u.ravel(), h.ravel(), np.full(u.size, d), np.tile(rgb, (u.size, 1)))


def test_facade_points_drop_ground_far_and_vegetation():
    r = records(
        [1, 1, 1, 1, 1],
        [2.0, 0.1, 2.0, 2.0, 2.0],
        [0.0, 0.0, 0.9, 0.0, 0.0],
        [(1, 1, 1)] * 5,
        [6, 6, 6, 2, 5],
    )
    assert len(FL.facade_points(r, 4.0, 4.0)) == 1


def test_colour_cells_rows_from_top():
    r = records([0.2, 0.2, 0.2], [3.8, 3.9, 0.4], [0, 0, 0], [(200, 0, 0), (100, 0, 0), (0, 0, 9)])
    cells, counts = FL.colour_cells(r, 1.0, 4.0)
    assert cells.shape == (8, 2, 3) and counts[0, 0] == 2 and counts[7, 0] == 1
    assert cells[0, 0, 0] == 150 and cells[7, 0, 2] == 9
    assert np.isnan(cells[3, 1]).all()


def test_inpaint_fills_holes_and_keeps_observed():
    cells = np.full((6, 6, 3), np.nan, np.float32)
    counts = np.zeros((6, 6), int)
    cells[:, :3], counts[:, :3] = (100, 50, 25), 4
    filled = FL.inpaint(cells, counts, fallback=(0, 0, 0))
    assert np.allclose(filled[:, :3], (100, 50, 25))
    assert np.allclose(filled[:, 5], (100, 50, 25), atol=1)
    empty = FL.inpaint(cells * np.nan, counts * 0, fallback=(7, 8, 9))
    assert np.allclose(empty, (7, 8, 9))


def test_denoise_keeps_a_flat_layer():
    flat = np.full((5, 5, 3), 120.0, np.float32)
    assert np.allclose(FL.denoise(flat, np.ones((5, 5), int)), 120.0)


def test_measure_wall_reads_facade_colour():
    albedo = np.full((64, 96, 3), (180, 140, 90), np.uint8)
    wall = grid_points(6.0, 4.0, (180, 140, 90))
    out = FL.measure_wall(wall, albedo, 6.0, 4.0, default=np.array([227, 212, 194]))
    assert out["coverage_1"] == 1.0 and out["delta_e_dominant"] < 1
    assert out["lidar_beats_default"]


def test_measure_wall_detects_roof_smear():
    albedo = np.full((64, 96, 3), (180, 140, 90), np.uint8)
    roof_rgb = (120, 135, 150)
    wall = grid_points(6.0, 4.0, roof_rgb)
    roof = grid_points(6.0, 1.0, roof_rgb, d=-1.0)
    roof["h"] += 4.0
    ground = grid_points(6.0, 0.9, (90, 90, 90), d=1.5)
    ground["h"] -= 0.8
    ground["classification"] = 2
    out = FL.measure_wall(
        np.concatenate([wall, roof, ground]), albedo, 6.0, 4.0, default=np.array([227, 212, 194])
    )
    assert out["delta_e_to_roof"] < 1 and out["smeared_cells"] == 1.0
    assert out["bands_nearest"] == {"low": "roof", "mid": "roof", "high": "roof"}


def test_comparison_image_size():
    albedo = np.full((64, 96, 3), 128, np.uint8)
    sheet = FL.comparison_image(albedo, grid_points(6.0, 4.0, (1, 2, 3)), 6.0, 4.0, scale=10)
    assert sheet.size[1] == 40 + 16 + 16
