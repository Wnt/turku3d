"""Lighting spans across a street that the wire stage does not find.

The survey classes the returns of many span wires as high vegetation, and the wire stage (`wires.py`, the old
generator's) reads unclassified returns only. Such a span is still plain in the scan: returns that hang free 6 to
12 m over a carriageway (nothing under them down to car height), on one thin line across the street. This module
finds those lines per 500 m survey tile and stores them beside the wire stage's records.

A found span is {a, b, sag, kind: "span", ends, returns} and a lamp {at, wire} as `wires.in_box` gives them.
"""

from __future__ import annotations

import json
import os

import numpy as np
import shapely

from .lidar import TILE, data_root

VERSION = "1"
HIGH = (6.0, 12.5)  # m above ground where a lighting span hangs
STREET = 6.0  # m from a carriageway centreline
CLEAR = 2.5  # m: returns under this are cars and people, not what a crown or a wall has under it
BELOW = 1.2  # m under a return within which other returns are the same wire and lamp
CELL = 0.5  # m: grid of the free-hanging test
LINK = 1.5  # m between returns of one span
RETURNS = 10
LENGTH = 4.0  # m: shortest run of returns that is read as a span
THIN = 0.3  # m: horizontal spread across the line
SMOOTH = 0.25  # m: spread of heights about a parabola along the line
ACROSS = 0.77  # cosine of 40 degrees: a lighting span crosses its street, a branch along it is not one
COVER = 0.6  # share of the line's metres with returns
REACH = 14.0  # m a span is followed past its last return to the wall that carries it
SAG = 0.025  # of the length, where the scan does not show the ends


def _free(points: np.ndarray, high: np.ndarray) -> np.ndarray:
    """Which of the `high` returns hang free: no return between car height and `BELOW` under them in the 3 x 3
    cells around them."""
    body = points[points[:, 2] > CLEAR]
    e0, n0 = body[:, 0].min(), body[:, 1].min()
    columns = int((body[:, 0].max() - e0) / CELL) + 3
    rows = int((body[:, 1].max() - n0) / CELL) + 3
    lowest = np.full((rows, columns), np.inf)
    np.minimum.at(lowest, (((body[:, 1] - n0) / CELL).astype(int) + 1, ((body[:, 0] - e0) / CELL).astype(int) + 1), body[:, 2])
    around = np.minimum.reduce([lowest[1 + dr:rows - 1 + dr, 1 + dc:columns - 1 + dc] for dr in (-1, 0, 1) for dc in (-1, 0, 1)])
    r, c = ((high[:, 1] - n0) / CELL).astype(int), ((high[:, 0] - e0) / CELL).astype(int)
    return around[r, c] >= high[:, 2] - BELOW


def find(points: np.ndarray, cars: list, roofed, ground) -> tuple[list[dict], list[dict]]:
    """Spans and their lamps in lidar rows (E, N, height above ground, class). `cars` are carriageway centrelines,
    `roofed(e, n)` says whether a building stands at a point and `ground(e, n)` gives the ground height."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree

    if not len(points) or not cars:
        return [], []
    high = points[(points[:, 2] >= HIGH[0]) & (points[:, 2] <= HIGH[1])]
    if len(high) < RETURNS:
        return [], []
    corridor = shapely.union_all([line.buffer(STREET) for line in cars])
    shapely.prepare(corridor)
    high = high[shapely.contains_xy(corridor, high[:, 0], high[:, 1])]
    if len(high) < RETURNS:
        return [], []
    high = high[_free(points, high)]
    if len(high) < RETURNS:
        return [], []
    pairs = cKDTree(high[:, :3]).query_pairs(LINK, output_type="ndarray")
    graph = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(len(high), len(high)))
    _, labels = connected_components(graph, directed=False)
    tree = shapely.STRtree(cars)
    spans, lamps = [], []
    for label in np.nonzero(np.bincount(labels) >= RETURNS)[0]:
        cluster = high[labels == label]
        centre = cluster[:, :2].mean(axis=0)
        _, _, axes = np.linalg.svd(cluster[:, :2] - centre, full_matrices=False)
        u = axes[0]
        along, across = (cluster[:, :2] - centre) @ u, (cluster[:, :2] - centre) @ axes[1]
        length = float(along.max() - along.min())
        if length < LENGTH or float(across.std()) > THIN:
            continue
        fit = np.polyfit(along, cluster[:, 2], 2)
        if float((cluster[:, 2] - np.polyval(fit, along)).std()) > SMOOTH:
            continue
        if np.unique(np.floor(along - along.min())).size < COVER * np.ceil(length):
            continue
        way = cars[int(tree.nearest(shapely.Point(centre)))]
        s = way.project(shapely.Point(centre))
        p, q = way.interpolate(max(s - 1.0, 0.0)), way.interpolate(min(s + 1.0, way.length))
        tangent = np.array([q.x - p.x, q.y - p.y])
        if np.hypot(*tangent) < 1e-6 or abs(float(u @ tangent) / float(np.hypot(*tangent))) > ACROSS:
            continue
        # Each end is followed along the line to the wall that carries it.
        ends, reach = [], []
        for sign, last in ((-1, along.min()), (1, along.max())):
            hit = next((d for d in np.arange(0.5, REACH + 0.01, 0.5) if roofed(*(centre + u * (last + sign * d)))), None)
            ends.append("facade" if hit is not None else "open")
            reach.append(last + sign * (hit if hit is not None else 0.0))
        base = ground(float(centre[0]), float(centre[1]))
        if not np.isfinite(base):
            continue
        total = reach[1] - reach[0]
        sag = round(float(np.clip(SAG * total, 0.15, 1.0)), 2)
        height = base + float(np.median(cluster[:, 2])) + sag  # the returns are the low middle of the span
        a, b = centre + u * reach[0], centre + u * reach[1]
        wire = {"a": [round(float(a[0]), 2), round(float(a[1]), 2), round(height, 2)], "b": [round(float(b[0]), 2), round(float(b[1]), 2), round(height, 2)],
                "sag": sag, "kind": "span", "ends": ends, "returns": int(len(cluster))}
        spans.append(wire)
        # A luminaire is a knot of returns on the line: several times what a metre of bare wire gives.
        bins = np.floor((along - along.min()) / 0.6).astype(int)
        counts = np.bincount(bins)
        peak = int(np.argmax(counts))
        if counts[peak] >= 6 and counts[peak] >= 3 * np.median(counts[counts > 0]):
            at = centre + u * float(along[bins == peak].mean())
            t = (float(along[bins == peak].mean()) - reach[0]) / total
            lamps.append({"at": [round(float(at[0]), 2), round(float(at[1]), 2), round(height - sag * 4 * t * (1 - t), 2)], "wire": len(spans) - 1})
    return spans, lamps


def tile(sources, te: float, tn: float) -> dict:
    """The stored spans of the survey tile whose south-west corner is (te, tn), found on first use."""
    from . import registers

    name = f"{int(te)}_{int(tn + TILE)}"
    path = data_root() / "sources" / "object-spans" / VERSION / f"{name}.json"
    if path.exists():
        return json.loads(path.read_text())
    lidar = registers.lidar_of(sources)
    points = lidar.box(te, tn, te + TILE, tn + TILE)
    cars = registers.Streets(sources, (te, tn, te + TILE, tn + TILE), margin=STREET).cars
    spans, lamps = find(points, cars, lidar.roofed, lambda e, n: registers.ground_height(sources, e, n))
    record = {"version": VERSION, "wires": spans, "lamps": lamps}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.stem}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(record, separators=(",", ":")))
    tmp.replace(path)
    sources.log(f"  street spans of tile {name}: {len(spans)} spans, {len(lamps)} lamps on them")
    return record


def in_box(sources, box) -> tuple[list[dict], list[dict]]:
    """Spans touching a survey box and the lamps on them; each lamp carries its wire."""
    e0, n0, e1, n1 = box
    wires, lamps = [], []
    for te in np.arange(np.floor(e0 / TILE) * TILE, e1, TILE):
        for tn in np.arange(np.floor(n0 / TILE) * TILE, n1, TILE):
            record = tile(sources, float(te), float(tn))
            for w in record["wires"]:
                lo, hi = np.minimum(w["a"][:2], w["b"][:2]), np.maximum(w["a"][:2], w["b"][:2])
                if lo[0] <= e1 and hi[0] >= e0 and lo[1] <= n1 and hi[1] >= n0:
                    wires.append(w)
            for lamp in record["lamps"]:
                if e0 <= lamp["at"][0] < e1 and n0 <= lamp["at"][1] < n1:
                    lamps.append({"at": lamp["at"], "wire": record["wires"][lamp["wire"]]})
    return wires, lamps
