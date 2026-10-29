"""Synthetic support checks for N2000 camera-height calibration."""

import numpy as np
import pytest
from turkutex.streetview.height import fit_ground


def test_missing_ground_never_uses_lookaround_uncalibrated_elevation(monkeypatch):
    from types import SimpleNamespace
    from turkutex.streetview import height

    monkeypatch.setattr(height, "ground_height", lambda *args: None)
    meta = SimpleNamespace(source="lookaround", E=1, N=2, elevation=27.0, cam_height=2.4)
    elevation, source = height.camera_elevation(meta, 5.0)
    assert elevation == pytest.approx(7.4)
    assert "facade base" in source
    assert meta.elevation == 27.0
    meta.source = "google"
    assert height.camera_elevation(meta, 5.0)[0] == 27.0


def test_supported_plane_and_unreliable_neighbourhoods():
    xy = np.array([[-2, -2], [-2, 0], [-2, 2], [2, -2], [2, 0], [2, 2]], dtype=float)
    heights = 4 + xy[:, 0] * 0.05
    assert fit_ground(xy, heights, np.zeros(2)) == pytest.approx(4)
    assert fit_ground(xy + 30, heights, np.zeros(2)) is None
    assert fit_ground(xy, heights + np.array([0, 3, 0, 4, 0, 3]), np.zeros(2)) is None
    assert fit_ground(xy, heights + 2 * xy[:, 0], np.zeros(2)) is None
    assert fit_ground(np.column_stack([xy[:, 0], xy[:, 0]]), heights, np.zeros(2)) is None
