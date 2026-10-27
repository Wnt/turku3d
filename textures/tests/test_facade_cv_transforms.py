"""Pure synthetic transform and fusion checks; no model weights or real images."""

import numpy as np
import pytest
from turkutex.facade_cv import measure
from turkutex.facade_cv_transforms import (
    fuse_images,
    metric_peaks,
    metric_resize,
    normalize_light,
    vote_answers,
)


def test_metric_spacing_and_clipped_edge_opening():
    mask = np.zeros((120, 180), bool)
    for y in (0, 30, 60, 90):
        mask[max(0, y - 4) : y + 5, 20:150] = True
    assert metric_peaks(mask, np.ones_like(mask), 1, 12, 1.8)[0] == 4
    assert metric_peaks(np.zeros_like(mask), np.ones_like(mask), 1, 12, 1.8) == (0, 0.0)
    rgb = np.full((50, 50, 3), 120, np.uint8)
    assert metric_resize(rgb, 0.1, 20, ppm=16).shape == (320, 2, 3)
    with pytest.raises(ValueError):
        metric_resize(rgb, 0, 2)


def test_fusion_removes_minority_occluder_and_keeps_unsupported_pixels():
    wall = np.full((40, 60, 3), (190, 150, 100), np.uint8)
    tree = wall.copy()
    tree[10:30, 15:45] = (20, 160, 20)
    assert np.array_equal(fuse_images([tree, wall, wall]), wall)
    black = np.zeros_like(wall)
    assert np.array_equal(fuse_images([black, black]), black)


def test_vote_ties_keep_rank_and_invisible_view_does_not_hide_visible_view():
    rgb = np.full((40, 60, 3), 180, np.uint8)
    first = measure(rgb, "test_0", 6, 4)
    first[0].update(wall_visible=True, storeys=2, window_columns=3)
    second = measure(rgb, "test_0", 6, 4)
    second[0].update(wall_visible=True, storeys=3, window_columns=4)
    answer, _ = vote_answers([first, second])
    assert answer["storeys"] == 2
    assert answer["window_columns"] == 3
    invisible = measure(np.zeros_like(rgb), "test_0", 6, 4)
    answer, confidence = vote_answers([invisible, second])
    assert answer["wall_visible"] and answer["storeys"] == 3
    assert confidence["needs_review"]
    assert first[1]["confidence"]["storeys"] == 0


def test_light_normalisation_does_not_invent_texture_on_constant_wall():
    rgb = np.full((40, 60, 3), (180, 160, 120), np.uint8)
    normal = normalize_light(rgb, "shade")
    assert np.max(np.abs(normal.astype(int) - rgb)) <= 3
    assert np.max(np.ptp(normal.astype(int), axis=(0, 1))) == 0
