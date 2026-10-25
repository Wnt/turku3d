from types import SimpleNamespace
import math

import numpy as np
import pytest

from turkutex.streetview.mapillary_camera import MapillaryCamera


def camera(monkeypatch, kind='perspective', rotation=(0, 0, 0)):
    monkeypatch.setattr('turkutex.streetview.mapillary_camera.config.true_to_grid_bearing', lambda *a: 0)
    return MapillaryCamera(SimpleNamespace(E=0., N=0., extra={
        'camera_type': kind, 'camera_parameters': [.5, 0., 0.], 'computed_rotation': rotation}))


def test_calibrated_perspective_pixel_projection_and_visibility(monkeypatch):
    model = camera(monkeypatch)
    rays = np.array([[0., 0., 10.], [1., 2., 10.], [0., 0., -1.], [50., 0., 1.]])
    u, v, visible = model.project(rays, 4000, 3000)
    np.testing.assert_allclose(u[:2], [2000, 2200])
    np.testing.assert_allclose(v[:2], [1500, 1900])
    assert visible.tolist() == [True, True, False, False]


def test_world_to_camera_rotation_faces_north(monkeypatch):
    model = camera(monkeypatch, rotation=(math.pi / 2, 0, 0))
    u, v, visible = model.project(np.array([[0., 10., 0.], [0., 10., 1.]]), 4000, 3000)
    np.testing.assert_allclose(u, 2000)
    np.testing.assert_allclose(v, [1500, 1300])
    assert visible.all()


def test_fisheye_uses_angle_instead_of_perspective_tangent(monkeypatch):
    model = camera(monkeypatch, 'fisheye')
    u, v, visible = model.project(np.array([[1., 0., 1.], [0., 0., 1.]]), 4000, 3000)
    assert u[0] == pytest.approx(2000 + 2000 * math.pi / 4)
    assert u[1] == 2000 and np.all(v == 1500) and visible.all()
