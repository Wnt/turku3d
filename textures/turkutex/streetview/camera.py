"""Equirect <-> world directions and perspective crops (pure numpy, unit-tested).

World frame for directions: (E, N, U) metres, EPSG:3877 axes. Azimuth is clockwise from north.

Equirect convention (Google Street View via streetlevel, VERIFIED in docs/research/streetview.md):
  azimuth(u) = heading + sign * (u / W - 0.5) * 2*pi      (sign = +1 for Google)
  elevation(v) = (0.5 - v / H) * pi
Google depth from streetlevel 0.12.13 must be np.fliplr-ed to match the image (Verification #3);
`panos.py` stores the raw array and flips at load time.

Provider pitch/roll and true-to-grid convergence are applied before sampling. Even one degree
can clip a window row at the far end of a wide wall.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class EquirectModel:
    heading_rad: float  # azimuth of the image centre column
    sign: float = 1.0  # +1: azimuth grows to the right (Google)
    rotation: np.ndarray | None = None  # image ENU -> grid ENU, when the full pose is known

    def uv_from_dir(self, d: np.ndarray, W: int, H: int):
        if self.rotation is not None:
            d = d @ self.rotation
        az = np.arctan2(d[..., 0], d[..., 1])
        el = np.arctan2(d[..., 2], np.hypot(d[..., 0], d[..., 1]))
        u = ((self.sign * (az - self.heading_rad) / (2 * np.pi) + 0.5) % 1.0) * W
        v = (0.5 - el / np.pi) * H
        return u, v

    def dir_from_uv(self, u, v, W: int, H: int) -> np.ndarray:
        az = self.heading_rad + self.sign * (np.asarray(u) / W - 0.5) * 2 * np.pi
        el = (0.5 - np.asarray(v) / H) * np.pi
        d = np.stack([np.sin(az) * np.cos(el), np.cos(az) * np.cos(el), np.sin(el)], -1)
        return d if self.rotation is None else d @ self.rotation.T


def panorama_pose(heading, pitch, roll, convergence=0.0, *, lookaround=False):
    """Provider image directions -> grid ENU, following the providers' upright correction.

    Look Around uses Photo Sphere Viewer's sphereCorrection (YXZ); GPano uses its
    panoramaPose (-pitch, -heading, roll, YXZ). PSV axes are (-E, U, N). Apple's
    stitched centre faces the reverse travel direction, unlike the GPano centre.
    Sources: sk-zk/lookaround-map/js/viewer/viewer.js and
    mistic100/Photo-Sphere-Viewer/packages/core/src/services/Renderer.ts.
    """
    pan, tilt = (heading, pitch) if lookaround else (-heading, -pitch)
    cy, sy, cx, sx, cz, sz = (
        math.cos(pan),
        math.sin(pan),
        math.cos(tilt),
        math.sin(tilt),
        math.cos(roll),
        math.sin(roll),
    )
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    to_enu = np.array([[-1, 0, 0], [0, 0, 1], [0, 1, 0]])
    from_image = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]]) if lookaround else to_enu.T
    c, s = math.cos(convergence), math.sin(convergence)
    grid = np.array([[c, s, 0], [-s, c, 0], [0, 0, 1]])
    return grid @ to_enu @ ry @ rx @ rz @ from_image


@dataclass
class PerspectiveCam:
    pos: np.ndarray  # (E, N, H)
    yaw_deg: float  # clockwise from north
    pitch_deg: float  # up positive
    hfov_deg: float
    width: int
    height: int

    def basis(self):
        y, p = math.radians(self.yaw_deg), math.radians(self.pitch_deg)
        f = np.array([math.sin(y) * math.cos(p), math.cos(y) * math.cos(p), math.sin(p)])
        r = np.array([math.cos(y), -math.sin(y), 0.0])
        u = np.cross(r, f)
        return f, r, u

    @property
    def tan_h(self) -> float:
        return math.tan(math.radians(self.hfov_deg) / 2)

    @property
    def tan_v(self) -> float:
        return self.tan_h * self.height / self.width

    @property
    def vfov_deg(self) -> float:
        return math.degrees(2 * math.atan(self.tan_v))

    def rays(self) -> np.ndarray:
        f, r, u = self.basis()
        xs = (2 * (np.arange(self.width) + 0.5) / self.width - 1) * self.tan_h
        ys = (1 - 2 * (np.arange(self.height) + 0.5) / self.height) * self.tan_v
        X, Y = np.meshgrid(xs, ys)
        d = f[None, None] + X[..., None] * r[None, None] + Y[..., None] * u[None, None]
        return d / np.linalg.norm(d, axis=-1, keepdims=True)

    def project(self, pts: np.ndarray) -> np.ndarray:
        """World points (N,3) -> pixel (N,2) (x right, y down); NaN behind the camera."""
        f, r, u = self.basis()
        d = np.atleast_2d(pts) - self.pos
        z = d @ f
        x = (d @ r) / z
        y = (d @ u) / z
        px = (x / self.tan_h + 1) / 2 * self.width
        py = (1 - y / self.tan_v) / 2 * self.height
        out = np.stack([px, py], -1)
        out[z <= 0] = np.nan
        return out

    def to_dict(self) -> dict:
        return {
            "pos_EN_H": [round(float(v), 3) for v in self.pos],
            "yaw_deg": round(self.yaw_deg, 3),
            "pitch_deg": round(self.pitch_deg, 3),
            "hfov_deg": round(self.hfov_deg, 3),
            "vfov_deg": round(self.vfov_deg, 3),
            "width": self.width,
            "height": self.height,
            "convention": "yaw clockwise from north, pitch up positive, pinhole, no roll",
        }


def look_at(pos: np.ndarray, target: np.ndarray) -> tuple[float, float]:
    d = np.asarray(target, float) - np.asarray(pos, float)
    yaw = math.degrees(math.atan2(d[0], d[1])) % 360
    pitch = math.degrees(math.atan2(d[2], math.hypot(d[0], d[1])))
    return yaw, pitch


def fit_camera(
    pos: np.ndarray,
    corners: np.ndarray,
    long_side: int = 1536,
    margin: float = 1.12,
    min_hfov: float = 25.0,
    max_hfov: float = 110.0,
    max_aspect: float = 3.0,
) -> PerspectiveCam:
    """Aim at the facade centre and choose FOV/aspect so all 4 corners fit with a margin."""
    target = corners.mean(0)
    yaw, pitch = look_at(pos, target)
    cam = PerspectiveCam(np.asarray(pos, float), yaw, pitch, 90.0, 1000, 1000)
    f, r, u = cam.basis()
    d = corners - cam.pos
    z = d @ f
    if np.any(z <= 0.1):
        raise ValueError("facade corner behind the camera")
    tx = np.max(np.abs((d @ r) / z)) * margin
    ty = np.max(np.abs((d @ u) / z)) * margin
    hfov = min(max(math.degrees(2 * math.atan(tx)), min_hfov), max_hfov)
    tx = math.tan(math.radians(hfov) / 2)
    aspect = min(max(tx / max(ty, 1e-6), 1 / max_aspect), max_aspect)
    if aspect >= 1:
        w, h = long_side, max(64, int(round(long_side / aspect)))
    else:
        w, h = max(64, int(round(long_side * aspect))), long_side
    return PerspectiveCam(cam.pos, yaw, pitch, hfov, w, h)


def sample_bilinear(img: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """img (H,W,C); u,v pixel coords (u wraps horizontally, v clamps)."""
    H, W = img.shape[:2]
    u = u - 0.5
    v = np.clip(v - 0.5, 0, H - 1)
    u0 = np.floor(u).astype(np.int64)
    v0 = np.floor(v).astype(np.int64)
    fu = (u - u0)[..., None]
    fv = (v - v0)[..., None]
    u0 %= W
    u1 = (u0 + 1) % W
    v1 = np.minimum(v0 + 1, H - 1)
    im = img  # Gather only crop pixels; a full 16K float panorama costs 1.6 GB.
    if im.ndim == 2:
        im = im[..., None]
    a = im[v0, u0] * (1 - fu) + im[v0, u1] * fu
    b = im[v1, u0] * (1 - fu) + im[v1, u1] * fu
    return a * (1 - fv) + b * fv


def sample_nearest(img: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    H, W = img.shape[:2]
    ui = np.floor(u).astype(np.int64) % W
    vi = np.clip(np.floor(v).astype(np.int64), 0, H - 1)
    return img[vi, ui]


def render(equi: np.ndarray, model: EquirectModel, cam: PerspectiveCam) -> np.ndarray:
    d = cam.rays()
    u, v = model.uv_from_dir(d, equi.shape[1], equi.shape[0])
    out = sample_bilinear(equi, u, v)
    return np.clip(out + 0.5, 0, 255).astype(np.uint8).squeeze()


def facade_hits(cam: PerspectiveCam, corners: np.ndarray):
    """Per pixel: ray distance to the facade plane and whether the hit is inside the facade rectangle."""
    d = cam.rays()
    a, b, _, top = corners[0], corners[1], corners[2], corners[3]
    t_axis = b - a
    L = np.linalg.norm(t_axis[:2])
    t_axis = t_axis / np.linalg.norm(t_axis)
    n = np.array([t_axis[1], -t_axis[0], 0.0])  # horizontal normal (sign irrelevant here)
    den = d @ n
    with np.errstate(divide="ignore", invalid="ignore"):
        t = ((a - cam.pos) @ n) / den
    hit = cam.pos + d * t[..., None]
    s = (hit - a) @ t_axis
    h = hit[..., 2]
    inside = (t > 0) & (s >= 0) & (s <= L) & (h >= a[2]) & (h <= top[2])
    return t, inside


def view_angle_deg(cam_en: np.ndarray, centre_en: np.ndarray, normal_en: np.ndarray) -> float:
    """Angle between the facade normal and the direction facade->camera (0 = frontal)."""
    v = np.asarray(cam_en, float) - np.asarray(centre_en, float)
    c = float(v @ normal_en / (np.linalg.norm(v) * np.linalg.norm(normal_en)))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))
