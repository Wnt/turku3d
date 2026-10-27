"""Small synthetic checks for local measurements, with no photos or model weights."""

import numpy as np
import pytest
from turkutex.facade_cv import colours, grid_axis, measure
from turkutex.facade_fast_render import validate


def test_projection_grid():
    mask = np.zeros((240, 360), bool)
    for y in (30, 90, 150, 210):
        for x in (30, 90, 150, 210, 270, 330):
            mask[y - 15 : y + 15, x - 10 : x + 10] = True
    rows, pitch, confidence = grid_axis(mask, 1, 30)
    assert (rows, pitch) == (4, 60)
    assert confidence == pytest.approx(1)
    assert grid_axis(mask, 0, 60)[0] == 6
    assert grid_axis(np.zeros_like(mask), 1, 30)[0] == 0
    # A solid pane is not evidence of repeated storeys.
    assert grid_axis(np.ones_like(mask), 1, 30)[0] == 0


def test_wall_colour_ignores_masked_glass():
    rgb = np.full((60, 80, 3), (190, 150, 100), np.uint8)
    rgb[:, :20] = (20, 30, 70)
    mask = np.ones((60, 80), bool)
    mask[:, :20] = False
    wall, _, confidence = colours(rgb, mask)
    actual = np.array([int(wall[i : i + 2], 16) for i in (1, 3, 5)])
    assert np.max(np.abs(actual - (190, 150, 100))) <= 3
    assert confidence > 0.9


def test_schema_and_low_confidence_on_blank_crop():
    answer, sidecar = measure(np.full((80, 120, 3), 180, np.uint8), "synthetic_0", 9, 6)
    assert validate(answer, "synthetic_0") == []
    assert answer["storeys"] == 0
    assert sidecar["needs_review"]
    assert sidecar["confidence"]["storeys"] == 0
    assert "confidence" not in answer
    black, _ = measure(np.zeros((80, 120, 3), np.uint8), "black_0", 9, 6)
    assert not black["wall_visible"]
    assert "black" in black["problem"]
