"""The QA camera convention, shared by reference views, the game URL contract and the stub.

Pose (all angles in degrees):
  E, N   EPSG:3877 metres (ETRS-GK23)      H    N2000 metres of the camera centre (not ground)
  yaw    compass bearing of the view axis, 0 = north, 90 = east (clockwise)
  pitch  elevation of the view axis, + = looking up
  roll   rotation about the view axis, + = camera turned clockwise as seen from behind
         (right side down; the scene appears rotated counter-clockwise in the image)
  fov    VERTICAL field of view of the full image height (same as three.js camera.fov)
  Image aspect = width / height of the reference image; hfov = 2 atan(tan(fov/2) * W/H).

three.js equivalent (engine x = E-E0, y = H, z = -(N-N0)):
  camera.position.set(E-E0, H, -(N-N0)); camera.rotation.order = 'YXZ';
  camera.rotation.set(rad(pitch), -rad(yaw), -rad(roll)); camera.fov = fov; camera.aspect = W/H
"""

from __future__ import annotations

import math

import numpy as np


def basis(yaw: float, pitch: float, roll: float = 0.0):
    """Return (forward, right, up) unit vectors in ENU (east, north, up)."""
    y, p, r = (math.radians(v) for v in (yaw, pitch, roll))
    f = np.array([math.sin(y) * math.cos(p), math.cos(y) * math.cos(p), math.sin(p)])
    rt = np.array([math.cos(y), -math.sin(y), 0.0])
    up = np.cross(rt, f)
    rt2 = rt * math.cos(r) - up * math.sin(r)
    up2 = up * math.cos(r) + rt * math.sin(r)
    return f, rt2, up2


def hfov(fov_v: float, w: int, h: int) -> float:
    return math.degrees(2 * math.atan(math.tan(math.radians(fov_v) / 2) * w / h))


def vfov(fov_h: float, w: int, h: int) -> float:
    return math.degrees(2 * math.atan(math.tan(math.radians(fov_h) / 2) * h / w))


def pixel_rays(pose: dict, w: int, h: int) -> np.ndarray:
    """(h, w, 3) ENU ray directions through pixel centres."""
    f, rt, up = basis(pose["yaw"], pose["pitch"], pose.get("roll", 0.0))
    ty = math.tan(math.radians(pose["fov"]) / 2)
    tx = ty * w / h
    xs = ((np.arange(w) + 0.5) / w * 2 - 1) * tx
    ys = (1 - (np.arange(h) + 0.5) / h * 2) * ty
    X, Y = np.meshgrid(xs, ys)
    d = f[None, None, :] + X[..., None] * rt[None, None, :] + Y[..., None] * up[None, None, :]
    return d / np.linalg.norm(d, axis=-1, keepdims=True)


def project(pose: dict, w: int, h: int, e: float, n: float, z: float):
    """Project a world point (E, N, H) to pixel coordinates (px, py), or None if behind the camera.
    px, py are continuous (0,0 = top-left corner of the image, pixel centres at +0.5)."""
    f, rt, up = basis(pose["yaw"], pose["pitch"], pose.get("roll", 0.0))
    d = np.array([e - pose["E"], n - pose["N"], z - pose["H"]])
    zc = d @ f
    if zc <= 1e-6:
        return None
    ty = math.tan(math.radians(pose["fov"]) / 2)
    tx = ty * w / h
    x = (d @ rt) / zc / tx
    y = (d @ up) / zc / ty
    return ((x + 1) / 2 * w, (1 - y) / 2 * h)
