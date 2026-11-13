"""Finished ground for the world pipeline: the city-wide rule of `turku3d.ground.finished`, as a height correction.

The city lidar is from April 2021. Streets and squares rebuilt around then were scanned mid-construction (pits,
trenches, spoil heaps where the city's records now describe a finished paved surface). `turku3d.ground.finished`
is the old generator's proven rule for that: register street parts completed or renovated in the lidar's year or
later, construction ground found in them by morphological closing and opening, post-lidar base-map spot heights,
and a thin-plate fill. This module runs that rule unchanged and hands its result to every height reader of the
world pipeline as a correction per 500 m lidar tile:

- `correction(name)`: the rule's finished surface on the tile's 1 m nodes (row 0 on its south edge, column 0 on
  its west edge, the nodes of `sources.terrain`) and a weight, 1 where the rule moved the ground, falling to 0
  within `BLEND` m round it; None where the rule changes nothing in the tile. Made once per tile and kept in the
  shared lidar products (`finished-ground/<recipe>/<name>.npz`, with the rule's report). Inside a fill a reader
  takes the rule's surface itself, not a difference added to its own raster: a 0.5 m raster of all returns puts
  a trench wall elsewhere than the 1 m key-point surface the rule solved, and the wall would show as a seam.
- `finish(e, n, heights)` / `finish_grid(grid, ...)`: a reader's scanned heights over finished ground.

The street rasters (`street.lidar.Window`: ground kind, bridge decks, rails, walls, collision through the ground
raster), the terrain grid (`sources.terrain`: buildings, lamps, signs, fences, furniture, region plans) and the
vegetation window (`green.lidar.Window`: trees, hedges, tufts) pass their heights through `finish`, so everything that stands on the ground
sees the finished surface.

Seams: the rule solves canonical 250 m windows (level-0 tiles of the old grid, which share the origin of the
500 m lidar grid) from every source row in their data region and blends neighbouring windows over a 50 m band, so
a node's correction depends only on its position. A 500 m region unit, a 62.5 m ground cell or a lidar tile that
reads it gets the same height wherever it is computed.
"""
from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path

import numpy as np

from turku3d import config as C
from turku3d.ground import finished as rule

TILE = C.LAZ_TILE_SIZE
NODES = int(TILE) + 1  # 1 m nodes, both edges included
BLEND = 2  # m beyond the moved ground over which the rule's surface gives way to the scan
EDGE = 4  # m of nodes beyond the tile the weight is decided from (> 1 + BLEND), so that tiles agree on their edges
LAYERS = {"streets": "GIS:Katualueet", "spots": "kanta:Korkeuspiste", "buildings": "bldg:Building_LOD2",
          "water": "kanta:Vesikohde"}


@lru_cache(maxsize=1)
def recipe() -> str:
    """Names the corrections' content: the rule's code and this adapter's."""
    digest = hashlib.sha256()
    for path in (Path(rule.__file__), Path(__file__)):
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def tile_corner(name: str) -> tuple[float, float]:
    """South-west corner of the lidar tile named by its north-west corner."""
    east, north = name.split("_")
    return float(east), float(north) - TILE


def tile_name(e0: float, n0: float) -> str:
    return f"{int(e0)}_{int(n0 + TILE)}"


def path(name: str) -> Path:
    from ..sources.lidar import products_root

    return products_root() / "finished-ground" / recipe() / f"{name}.npz"


def done(name: str) -> bool:
    return path(name).exists()


def visual_tile(name: str) -> C.VisualTile:
    """The old grid's 500 m tile that is this lidar tile (both grids share the origin)."""
    e0, n0 = tile_corner(name)
    i = round((e0 - C.ORIGIN_E) / TILE)
    j = round((C.ORIGIN_N - n0 - TILE) / TILE)
    tile = C.VisualTile(1, i, j)
    assert tile.bbox.to_en().as_tuple() == (e0, n0, e0 + TILE, n0 + TILE), (name, tile.bbox.to_en())
    return tile


# -- sources --------------------------------------------------------------------------------------------------------


def _frame(paths: list[Path], bbox: C.BBoxLocal, whole: bool = False):
    """The rows of a layer's block files that meet `bbox`, each feature row once, in a canonical order (the reading
    of `ground.finished.load`, for block files with or without engine-local geometry)."""
    import geopandas as gpd
    import pandas as pd
    import shapely

    from turku3d.fetch.wfs import to_local

    box = shapely.box(*bbox.as_tuple())
    frames = []
    for p in paths:
        data = gpd.read_parquet(p)
        if not len(data):
            continue
        if "geometry_local" not in data:
            data["geometry_local"] = gpd.GeoSeries(to_local(data.geometry.values), index=data.index, crs=None)
        hit = shapely.intersects(shapely.force_2d(data.geometry_local.values), box)
        if whole:
            hit = data.feature_key.isin(set(data.feature_key.values[hit])).values
        if hit.any():
            frames.append(pd.DataFrame(data[hit]))
    if not frames:
        return None
    merged = pd.concat(frames, ignore_index=True)
    subset = ["feature_key", "geom_index"] if "geom_index" in merged else ["feature_key"]
    merged["_wkb"] = [g.wkb if g is not None else b"" for g in merged.geometry_local]
    merged = merged.sort_values([*subset, "_wkb"], kind="stable")
    return merged.drop_duplicates(subset=subset).reset_index(drop=True)


def _blocks(sources, layer: str, bbox: C.BBoxLocal) -> list[Path]:
    from ..sources.wfs import BLOCK

    e0, n0, e1, n1 = bbox.to_en().as_tuple()
    found = []
    for x in np.arange(np.floor(e0 / BLOCK) * BLOCK, e1, BLOCK):
        for y in np.arange(np.floor(n0 / BLOCK) * BLOCK, n1, BLOCK):
            found.append(sources.wfs.block_path(layer, float(x), float(y)))  # one at a time: see sources.wfs
    return sorted(set(found))


def evidence(name: str, sources) -> rule.Evidence:
    """The rule's evidence for the windows of one lidar tile, from the world pipeline's source readers."""
    tile = visual_tile(name)
    bbox = tile.bbox.buffered(rule.REGION)
    streets = _frame(_blocks(sources, LAYERS["streets"], bbox), bbox)
    portals: list = []
    zones, structures, streets = rule.zone_rows(streets, portals) if streets is not None else ([], [], [])
    found = rule.Evidence(zones=zones, structures=structures, streets=streets, portals=portals)
    if not found.near(tile.bbox.buffered(rule.BAND + rule.REACH + 1)):
        return found  # no finished-state zone reaches the tile: nothing else is read
    spots = _frame(_blocks(sources, LAYERS["spots"], bbox), bbox)
    found.spots = rule.spot_rows(spots) if spots is not None else found.spots
    water = _frame(_blocks(sources, LAYERS["water"], bbox), bbox)
    found.water = rule.water_rows(water) if water is not None else []
    buildings = _frame(_blocks(sources, LAYERS["buildings"], bbox), bbox, whole=True)
    found.footprints = rule.on_land(rule.footprint_rows(buildings) if buildings is not None else [], found.water)
    e0, n0, e1, n1 = bbox.to_en().as_tuple()
    laz = []
    for x in np.arange(np.floor(e0 / TILE) * TILE, e1, TILE):
        for y in np.arange(np.floor(n0 / TILE) * TILE, n1, TILE):
            p = sources.terrain.laz((int(x), int(y + TILE)))  # the key-point tile named by its north-west corner
            if p is not None:
                laz.append(p)
    found.points = rule.key_points(laz, bbox)
    return found


# -- the correction of one tile -------------------------------------------------------------------------------------


def make(name: str, sources=None) -> dict:
    """Runs the rule over one lidar tile and keeps its surface; returns the rule's report (no sites when it changes
    nothing)."""
    from scipy import ndimage as ndi

    if sources is None:
        from ..sources import Sources

        sources = Sources(log=lambda *_: None)
    e0, n0 = tile_corner(name)
    found = evidence(name, sources)
    side = NODES + 2 * EDGE
    surface = np.zeros((side, side), np.float32)
    changed = np.zeros((side, side), bool)
    reports = []
    if found.zones and found.near(visual_tile(name).bbox.buffered(rule.BAND + rule.REACH + 1 + EDGE)):
        for child in visual_tile(name).children():
            b = child.bbox.buffered(EDGE)  # every child reaches EDGE m past the tile: the blend weight's reach
            xs = np.arange(b.xmin, b.xmax + 0.5, 1.0)
            zs = np.arange(b.zmin, b.zmax + 0.5, 1.0)
            finished, lidar, moved, windows = rule.surface(child, found, xs, zs)
            if moved.any():
                reports.append(rule.report(child, xs, zs, finished, lidar, moved, windows, 0, 0))
            # Engine z grows southward: node row r lies at N = n0 - EDGE + r, z = ORIGIN_N - N.
            cols = np.round(xs + C.ORIGIN_E - e0).astype(int) + EDGE
            rows = np.round(C.ORIGIN_N - zs - n0).astype(int) + EDGE
            surface[np.ix_(rows, cols)] = finished
            changed[np.ix_(rows, cols)] = moved
    weight = np.zeros((NODES, NODES), np.float32)
    if changed.any():
        # The rule's surface wherever it moved the ground and BLEND m round it, the scan beyond, so that a reader's
        # own raster (0.5 m returns or 1 m key points) never shows the scan's trench walls inside a fill.
        distance = ndi.distance_transform_edt(~changed)
        weight = np.clip(1.0 - (distance - 1.0) / BLEND, 0.0, 1.0).astype(np.float32)[EDGE:-EDGE, EDGE:-EDGE]
    report = {"tile": name, "recipe": recipe(), "changed_m2": sum(r["changed_m2"] for r in reports),
              "max_raise_m": max((r["max_raise_m"] for r in reports), default=0.0),
              "max_lower_m": max((r["max_lower_m"] for r in reports), default=0.0),
              "sites": rule.merge_sites([{"tile": name, "report": r} for r in reports])}
    target = path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(f"{target.stem}.{os.getpid()}.part.npz")
    empty = np.zeros((0, 0), np.float32)
    used = bool(weight.max() > 0)
    np.savez_compressed(part, surface=surface[EDGE:-EDGE, EDGE:-EDGE] if used else empty,
                        weight=weight if used else empty,
                        report=np.frombuffer(json.dumps(report).encode(), np.uint8))
    part.replace(target)
    return report


def report(name: str) -> dict:
    with np.load(_made(name)) as data:
        return json.loads(bytes(data["report"]).decode())


def _made(name: str) -> Path:
    """The tile's correction file, made under a lock the first time any process asks."""
    target = path(name)
    if not target.exists():
        from ..sources.lidar import _locked, sources_root

        with _locked(sources_root() / ".locks" / f"{name}-finished"):
            if not target.exists():
                make(name)
    return target


@lru_cache(maxsize=16)
def correction(name: str) -> tuple[np.ndarray, np.ndarray] | None:
    """(finished surface, weight) on the tile's 1 m nodes, or None where the rule changes nothing in the tile."""
    from .lidar import NotSurveyed, laz_path

    try:
        laz_path(name)
    except NotSurveyed:
        return None  # no scan, no construction-era ground
    with np.load(_made(name)) as data:
        surface, weight = data["surface"], data["weight"]
    return (surface, weight) if surface.size else None


def _bilinear(grid, u, v):
    c = np.clip(np.floor(u).astype(int), 0, NODES - 2)
    r = np.clip(np.floor(v).astype(int), 0, NODES - 2)
    fu, fv = np.clip(u - c, 0, 1), np.clip(v - r, 0, 1)
    return (grid[r, c] * (1 - fu) * (1 - fv) + grid[r, c + 1] * fu * (1 - fv)
            + grid[r + 1, c] * (1 - fu) * fv + grid[r + 1, c + 1] * fu * fv)


def finish(e, n, heights) -> np.ndarray:
    """`heights` (a reader's scanned ground at survey points, any shape) over finished ground: the rule's surface
    where it moved the ground, blended into the reader's own heights within BLEND m; the same array elsewhere."""
    heights = np.asarray(heights)
    e, n = np.broadcast_to(np.asarray(e, float), heights.shape), np.broadcast_to(np.asarray(n, float), heights.shape)
    te = np.floor(e / TILE) * TILE
    tn = np.floor(n / TILE) * TILE
    out = None
    for x, y in {(float(a), float(b)) for a, b in zip(te.ravel(), tn.ravel())}:
        found = correction(tile_name(x, y))
        if found is None:
            continue
        if out is None:
            out = heights.astype(np.float64)
        surface, weight = found
        pick = (te == x) & (tn == y)
        u, v = e[pick] - x, n[pick] - y
        w = _bilinear(weight, u, v)
        out[pick] = out[pick] + w * (_bilinear(surface, u, v) - out[pick])
    return heights if out is None else out.astype(heights.dtype)


def touches(e0: float, n0: float, e1: float, n1: float) -> bool:
    """Whether the rule changes ground in any lidar tile of a survey box."""
    return any(correction(tile_name(float(x), float(y))) is not None
               for x in np.arange(np.floor(e0 / TILE) * TILE, e1, TILE)
               for y in np.arange(np.floor(n0 / TILE) * TILE, n1, TILE))


def finish_grid(grid: np.ndarray, e_first: float, n_first: float, step: float) -> np.ndarray:
    """`grid[row, col]` (scanned heights at E = e_first + col * step, N = n_first + row * step) over finished
    ground."""
    rows, cols = grid.shape[:2]
    if not touches(e_first, n_first, e_first + (cols - 1) * step + 1e-6, n_first + (rows - 1) * step + 1e-6):
        return grid
    gn, ge = np.meshgrid(n_first + np.arange(rows) * step, e_first + np.arange(cols) * step, indexing="ij")
    return finish(ge, gn, grid)


def main(argv=None):
    """`python -m turku3d.world.street.finished survey E,N RADIUS`: makes the corrections of the lidar tiles within
    RADIUS of a point and prints where the rule fires (one JSON line per tile with a change, then a summary)."""
    import argparse
    import time

    parser = argparse.ArgumentParser(prog="python -m turku3d.world.street.finished")
    parser.add_argument("command", choices=["survey"])
    parser.add_argument("at")
    parser.add_argument("radius", type=float)
    args = parser.parse_args(argv)
    e, n = (float(v) for v in args.at.split(","))
    names = [tile_name(float(x), float(y))
             for x in np.arange(np.floor((e - args.radius) / TILE) * TILE, e + args.radius, TILE)
             for y in np.arange(np.floor((n - args.radius) / TILE) * TILE, n + args.radius, TILE)]
    total = 0
    for name in names:
        started = time.perf_counter()
        if correction(name) is None:
            print(json.dumps({"tile": name, "changed_m2": 0, "s": round(time.perf_counter() - started, 1)}), flush=True)
            continue
        found = report(name)
        total += found["changed_m2"]
        print(json.dumps(found | {"s": round(time.perf_counter() - started, 1)}), flush=True)
    print(json.dumps({"tiles": len(names), "changed_m2": total}))


if __name__ == "__main__":
    main()
