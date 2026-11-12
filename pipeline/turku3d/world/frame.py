"""The world frame of docs/specs/coordinates.md for the world pipeline. The numbers stay in turku3d.config."""

from __future__ import annotations

import math

from ..config import COLLISION_TILE_SIZE, ORIGIN_E, ORIGIN_H, ORIGIN_N

CELL = COLLISION_TILE_SIZE  # areal kinds cut the world on this origin-aligned grid (62.5 m)
ORIGIN = (ORIGIN_E, ORIGIN_N, ORIGIN_H)


def to_engine(e, n, h=0.0):
    """EPSG:3877 E, N and N2000 H -> engine x, y, z (works on numpy arrays)."""
    return e - ORIGIN_E, h - ORIGIN_H, ORIGIN_N - n


def to_survey(x, y, z):
    """Engine x, y, z -> E, N, H."""
    return x + ORIGIN_E, ORIGIN_N - z, y + ORIGIN_H


def cell_at(e: float, n: float) -> tuple[int, int]:
    """The grid cell (i east, j south) that owns a survey point."""
    return math.floor((e - ORIGIN_E) / CELL), math.floor((ORIGIN_N - n) / CELL)


def cell_key(i: int, j: int) -> str:
    return f"c_{i}_{j}"


def cell_from_key(key: str) -> tuple[int, int]:
    _, i, j = key.split("_")
    return int(i), int(j)


def cell_box_en(i: int, j: int) -> tuple[float, float, float, float]:
    """Survey bounds (e0, n0, e1, n1) of a cell."""
    e0, n1 = ORIGIN_E + i * CELL, ORIGIN_N - j * CELL
    return e0, n1 - CELL, e0 + CELL, n1
