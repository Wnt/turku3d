"""Lidar points near building walls, in each wall's own coordinates.

A wall is a vertical rectangle over a 2-point baseline (EPSG:3877 E/N, N2000 base height).
Its points carry

  u  metres along the wall from its left edge as seen from outside (the facade image's left)
  h  metres above the wall base
  d  signed distance from the wall plane, positive outward (towards the street)

plus the point's RGB (8-bit; the city LAZ stores 8-bit colour scaled to 16 bits) and its
classification and returns. Sources are the city ``pistepilvi`` LAZ tiles (500 m,
``<xmin>_<ymax>``); they are streamed once in chunks and never written.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from turku3d import config as C

WALL_POINT_DTYPE = np.dtype(
    [
        ("u", "<f4"),
        ("h", "<f4"),
        ("d", "<f4"),
        ("rgb", "u1", (3,)),
        ("classification", "u1"),
        ("return_number", "u1"),
        ("number_of_returns", "u1"),
    ]
)
# Grid cell (metres) of the wall index a chunk's points are bucketed into.
INDEX_CELL = 2.0
CHUNK = 2_000_000


@dataclass(frozen=True)
class Wall:
    id: str
    p0: tuple[float, float]  # E, N
    p1: tuple[float, float]
    base: float  # N2000 height of the wall foot
    height: float
    outward: str = "right"  # outward normal side relative to p0 -> p1

    @property
    def length(self) -> float:
        return math.dist(self.p0, self.p1)

    def frame(self):
        """(left corner E/N, unit vector to the right as seen from outside, outward normal)."""
        p0, p1 = np.asarray(self.p0, float), np.asarray(self.p1, float)
        t = (p1 - p0) / np.linalg.norm(p1 - p0)
        right = np.array([t[1], -t[0]])
        if self.outward == "right":
            return p0, t, right
        return p1, -t, -right


@dataclass(frozen=True)
class Window:
    """How far around the wall rectangle points are kept (metres)."""

    inside: float = 0.6
    outside: float = 0.6
    below: float = 0.0
    above: float = 0.0
    side: float = 0.0


def walls_from_features(features) -> list[Wall]:
    """Walls of a buildings-layer facade inventory (``facades.geojson`` features)."""
    walls = []
    for feature in features:
        p = feature["properties"]
        (e0, n0), (e1, n1) = (c[:2] for c in feature["geometry"]["coordinates"][:2])
        tangent = np.array([e1 - e0, n1 - n0])
        right = np.array([tangent[1], -tangent[0]])
        # The layer's normal is engine x/y/z; engine z points south.
        normal_en = np.array([p["normal"][0], -p["normal"][2]])
        walls.append(
            Wall(
                f"{p['building_id']}_{p['facade_index']}",
                (e0, n0),
                (e1, n1),
                float(p["base_height"]),
                float(p["top_height"]) - float(p["base_height"]),
                "right" if right @ normal_en >= 0 else "left",
            )
        )
    return walls


def project(walls: list[Wall], xyz, rgb, classification, returns, window: Window) -> dict:
    """Points of one batch inside each wall's window, as WALL_POINT_DTYPE records by wall ID.

    xyz is E/N/H; rgb is (n, 3) 8-bit; returns is (return_number, number_of_returns)."""
    out: dict[str, list[np.ndarray]] = {}
    if not len(xyz):
        return out
    reach = max(window.inside, window.outside) + window.side
    origin = xyz[:, :2].min(axis=0)
    cells = np.floor((xyz[:, :2] - origin) / INDEX_CELL).astype(np.int64)
    width = int(cells[:, 0].max()) + 1
    keys = cells[:, 1] * width + cells[:, 0]
    order = np.argsort(keys, kind="stable")
    sorted_keys = keys[order]
    for wall in walls:
        left, right, normal = wall.frame()
        corners = np.array(
            [
                left - right * window.side + normal * s
                for s in (-window.inside - window.side, window.outside + window.side)
            ]
            + [
                left + right * (wall.length + window.side) + normal * s
                for s in (-window.inside - window.side, window.outside + window.side)
            ]
        )
        lo = np.floor((corners.min(axis=0) - reach - origin) / INDEX_CELL).astype(int)
        hi = np.floor((corners.max(axis=0) + reach - origin) / INDEX_CELL).astype(int)
        lo = np.maximum(lo, 0)
        hi[0] = min(hi[0], width - 1)
        if (hi < lo).any():
            continue
        parts = []
        for row in range(lo[1], hi[1] + 1):
            a = np.searchsorted(sorted_keys, row * width + lo[0], "left")
            b = np.searchsorted(sorted_keys, row * width + hi[0], "right")
            if b > a:
                parts.append(order[a:b])
        if not parts:
            continue
        idx = np.concatenate(parts)
        rel = xyz[idx, :2] - left
        u = rel @ right
        d = rel @ normal
        h = xyz[idx, 2] - wall.base
        keep = (
            (u >= -window.side)
            & (u <= wall.length + window.side)
            & (d >= -window.inside)
            & (d <= window.outside)
            & (h >= -window.below)
            & (h <= wall.height + window.above)
        )
        if not keep.any():
            continue
        sel = idx[keep]
        rec = np.zeros(len(sel), WALL_POINT_DTYPE)
        rec["u"], rec["h"], rec["d"] = u[keep], h[keep], d[keep]
        rec["rgb"] = rgb[sel]
        rec["classification"] = classification[sel]
        rec["return_number"], rec["number_of_returns"] = returns[0][sel], returns[1][sel]
        out.setdefault(wall.id, []).append(rec)
    return {k: np.concatenate(v) for k, v in out.items()}


def source_tiles(walls: list[Wall], window: Window, source_dir: Path) -> list[Path]:
    """The LAZ tiles any wall window touches; a missing one is an error, not empty ground."""
    reach = max(window.inside, window.outside) + window.side + 1.0
    names = set()
    for wall in walls:
        for e, n in (wall.p0, wall.p1):
            for de in (-reach, reach):
                for dn in (-reach, reach):
                    names.add(C.LazTile.at_en(e + de, n + dn).name)
    paths = [source_dir / f"{name}.laz" for name in sorted(names)]
    missing = [p for p in paths if not p.is_file()]
    if missing:
        raise FileNotFoundError(f"lidar tiles missing: {', '.join(map(str, missing))}")
    return paths


def wall_points(walls: list[Wall], window: Window | None = None, source_dir: Path | None = None):
    """Every wall's points (WALL_POINT_DTYPE) from the city LAZ tiles, by wall ID."""
    import laspy

    window = window or Window()

    source_dir = Path(source_dir or C.raw_dir("laz-3x3"))
    reach = max(window.inside, window.outside) + window.side + 1.0
    e = [c[0] for w in walls for c in (w.p0, w.p1)]
    n = [c[1] for w in walls for c in (w.p0, w.p1)]
    box = (min(e) - reach, min(n) - reach, max(e) + reach, max(n) + reach)
    found: dict[str, list[np.ndarray]] = {}
    for path in source_tiles(walls, window, source_dir):
        with laspy.open(path) as reader:
            for chunk in reader.chunk_iterator(CHUNK):
                x, y = np.asarray(chunk.x), np.asarray(chunk.y)
                keep = (x >= box[0]) & (x <= box[2]) & (y >= box[1]) & (y <= box[3])
                if not keep.any():
                    continue
                xyz = np.column_stack([x[keep], y[keep], np.asarray(chunk.z)[keep]])
                rgb = (
                    np.column_stack([np.asarray(chunk[c])[keep] for c in ("red", "green", "blue")])
                    >> 8
                ).astype(np.uint8)
                batch = project(
                    walls,
                    xyz,
                    rgb,
                    np.asarray(chunk.classification)[keep],
                    (
                        np.asarray(chunk.return_number)[keep],
                        np.asarray(chunk.number_of_returns)[keep],
                    ),
                    window,
                )
                for wall_id, records in batch.items():
                    found.setdefault(wall_id, []).append(records)
    empty = np.zeros(0, WALL_POINT_DTYPE)
    return {w.id: np.concatenate(found[w.id]) if w.id in found else empty for w in walls}


def save(path: Path, points: dict[str, np.ndarray]) -> None:
    """One NPZ of every wall's records (keys are wall IDs)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.npz")
    np.savez_compressed(tmp, **points)
    tmp.replace(path)


def load(path: Path) -> dict[str, np.ndarray]:
    with np.load(path) as data:
        return {k: data[k] for k in data.files}
