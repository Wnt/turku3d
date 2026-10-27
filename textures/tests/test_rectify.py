"""Synthetic wall-plane sampling; no panorama files or network access."""

import numpy as np
import pytest
from turkutex.streetview.camera import EquirectModel
from turkutex.streetview.rectify import rectified_points, sample_wall


def test_metric_pixel_centres_and_cap():
    corners = np.array([[-2, 10, 0], [2, 10, 0], [2, 10, 2], [-2, 10, 2]])
    points = rectified_points(corners, pixels_per_metre=10)
    assert points.shape == (20, 40, 3)
    assert points[0, 0] == pytest.approx([-1.95, 10, 1.95])
    assert points[-1, -1] == pytest.approx([1.95, 10, 0.05])
    assert rectified_points(corners, pixels_per_metre=100, max_side=32).shape == (16, 32, 3)
    with pytest.raises(ValueError, match="exactly one"):
        rectified_points(corners, 32, pixels_per_metre=10)
    with pytest.raises(ValueError, match="positive"):
        rectified_points(corners, pixels_per_metre=-1)


def test_analytic_panorama_recovers_wall_coordinates_across_seam():
    # Encode the intersection of each panorama ray with north wall y=10 as RGB.
    model = EquirectModel(np.pi)
    u, v = np.meshgrid(np.arange(2048) + 0.5, np.arange(1024) + 0.5)
    rays = model.dir_from_uv(u, v, 2048, 1024)
    # Lower hemisphere/backs never enter this test crop.
    hits = rays * (10 / np.maximum(rays[..., 1], 1e-6))[..., None]
    pano = np.stack([hits[..., 0] * 20 + 100, hits[..., 2] * 20 + 50, np.zeros_like(u)], -1)
    pano = np.clip(pano, 0, 255).astype(np.uint8)
    corners = np.array([[-2, 10, 0], [2, 10, 0], [2, 10, 2], [-2, 10, 2]])
    points = rectified_points(corners, pixels_per_metre=8)
    rgb, valid = sample_wall(pano, model, points, np.zeros(3))
    assert valid.all()
    assert np.max(np.abs(rgb[..., 0] - (points[..., 0] * 20 + 100))) < 1.1
    assert np.max(np.abs(rgb[..., 1] - (points[..., 2] * 20 + 50))) < 1.1
