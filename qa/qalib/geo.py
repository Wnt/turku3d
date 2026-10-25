"""Geo helpers. Reuses pipeline/turku3d/config.py (origin O, EPSG:3877 <-> WGS84) instead of
re-defining the origin. Heights are N2000 metres throughout; yaw is a compass bearing in degrees
(clockwise from grid north of EPSG:3877; meridian convergence at Turku is < 0.3 deg and ignored)."""
from __future__ import annotations

import math

from .paths import ensure_pipeline_on_path

ensure_pipeline_on_path()
from turku3d import config as C  # noqa: E402

ORIGIN_E, ORIGIN_N, ORIGIN_H = C.ORIGIN_E, C.ORIGIN_N, C.ORIGIN_H
OFFICE_E, OFFICE_N = C.OFFICE_E, C.OFFICE_N


def en_to_wgs84(e: float, n: float) -> tuple[float, float]:
    lat, lon = C.en_to_wgs84(e, n)
    return float(lat), float(lon)


def wgs84_to_en(lat: float, lon: float) -> tuple[float, float]:
    e, n = C.wgs84_to_en(lat, lon)
    return float(e), float(n)


def bearing(e0: float, n0: float, e1: float, n1: float) -> float:
    """Compass bearing (deg, 0 = north, 90 = east) from (e0, n0) to (e1, n1)."""
    return math.degrees(math.atan2(e1 - e0, n1 - n0)) % 360.0


def distance(e0: float, n0: float, e1: float, n1: float) -> float:
    return math.hypot(e1 - e0, n1 - n0)


def en_to_engine(e: float, n: float, h: float) -> tuple[float, float, float]:
    """Engine frame (ARCHITECTURE: x = E-E0, y = H, z = -(N-N0))."""
    return e - ORIGIN_E, h - ORIGIN_H, -(n - ORIGIN_N)
