"""Dense clipped structures must fit streaming budgets without moving collision."""

import numpy as np
from turku3d.coarse.collision import compact_collision
from turku3d.col.format import ColTile, read_col, write_col
from turku3d.col.materials import mat_id


def test_compaction_preserves_corner_bits_material_order_and_seams():
    # Two faces share coordinates but have independent indices, as clipped rails do.
    # Keep signed zero distinct and a one-ulp height difference unmodified.
    p = np.array(
        [
            [0, 0, 0],
            [0, 0, 1],
            [62.5, 0, 0],
            [-0.0, 0, 0],
            [62.5, 0, 0],
            [62.5, 0, 1],
            [999, 999, 999],
            [0, 0, 0],
        ],
        np.float32,
    )
    p[5, 1] = np.nextafter(np.float32(1), np.float32(2))
    ix = np.array([[0, 1, 2], [3, 4, 5], [7, 1, 4], [0, 7, 2]], np.uint32)
    ids = np.array([mat_id("curb"), mat_id("quay"), mat_id("curb"), mat_id("curb")], np.uint8)
    col = ColTile(-3, -8, p, ix, ids)
    expected = p[ix[:2]].view(np.uint32).copy()
    compact_collision(col)
    assert len(col.positions) == 6
    np.testing.assert_array_equal(col.positions[col.indices].view(np.uint32), expected)
    np.testing.assert_array_equal(col.materials, ids[:2])
    assert len(np.unique(col.indices)) == len(col.positions)
    back = read_col(write_col(col, encoding="raw", optimize=False))
    np.testing.assert_array_equal(back.positions[back.indices].view(np.uint32), expected)
    np.testing.assert_array_equal(back.materials, ids[:2])


def test_coincident_water_and_solid_faces_keep_both_collision_classes():
    p = np.array([[0, 0, 0], [0, 0, 1], [1, 0, 0]], np.float32)
    ids = np.array([mat_id("soil"), mat_id("water")], np.uint8)
    col = ColTile(0, 0, p, [[0, 1, 2], [0, 1, 2]], ids)
    compact_collision(col)
    np.testing.assert_array_equal(col.materials, ids)
    np.testing.assert_array_equal(col.positions[col.indices], [p, p])
    read_col(write_col(col, encoding="raw", optimize=False))


def test_dense_triangle_soup_fits_budget_after_exact_welding():
    # 36k rail/retaining faces with separate corners exceed 1 MB even though
    # they describe a small connected strip. No decimation is needed.
    n = 36_000
    p = np.c_[np.arange(n + 2) % 2, np.zeros(n + 2), np.arange(n + 2) * 0.01].astype(np.float32)
    ix = np.arange(n)[:, None] + np.arange(3)
    soup = p[ix].reshape(-1, 3)
    col = ColTile(
        0, 0, soup, np.arange(len(soup)).reshape(-1, 3), np.full(n, mat_id("curb"), np.uint8)
    )
    expected = col.positions[col.indices].view(np.uint32).copy()
    compact_collision(col)
    assert len(col.positions) == n + 2
    assert len(write_col(col, encoding="raw", optimize=False)) < 1_000_000
    np.testing.assert_array_equal(col.positions[col.indices].view(np.uint32), expected)
