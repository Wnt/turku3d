"""Infer border lamps from shared survey windows, then assign by the street crossing.

The tile-owned wire list is insufficient at corners: two fragments can belong to
diagonally opposite tiles while their street crossing belongs to a third. Each
250 m grid edge therefore has one content-addressed survey window. Both sides
read the same returns and centrelines, infer once over all its fragments, and
keep only lamps in their half-open tile bounds. No neighbouring product is read.
The existing wire-lamp index -1 represents a lamp whose wire another tile draws.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from functools import lru_cache
from pathlib import Path

from turku3d import config as C
from turku3d.coarse import span_lamps as S, wires as W

BAND = W.SEARCH


def seam(x, z):
    """Canonical nearest L0 grid edge, or None away from the border band."""
    size = C.visual_tile_size(0)
    i, j = math.floor(x / size + 0.5), math.floor(z / size + 0.5)
    dx, dz = abs(x - i * size), abs(z - j * size)
    if min(dx, dz) >= BAND:
        return None
    return ("x", i, math.floor(z / size)) if dx <= dz else ("z", j, math.floor(x / size))


def edges(tile):
    return (("x", tile.i, tile.j), ("x", tile.i + 1, tile.j),
            ("z", tile.j, tile.i), ("z", tile.j + 1, tile.i))


def bounds(key):
    axis, line, along = key
    size = C.visual_tile_size(0)
    a, b = line * size, along * size
    return (C.BBoxLocal(a - BAND, b, a + BAND, b + size) if axis == "x" else
            C.BBoxLocal(b, a - BAND, b + size, a + BAND))


@lru_cache(maxsize=1)
def recipe():
    from turku3d import spatial, wire_points
    from turku3d.lidar import cache, decoded
    from turku3d.source_code import selected_source

    digest = hashlib.sha256()
    # Attachment to a tile's wire list does not change the source candidates.
    # Keep this source cache warm across client and unrelated generator edits.
    for module in (C, W, S, spatial, wire_points, cache, decoded):
        path = Path(module.__file__)
        digest.update(path.name.encode() + b"\0" + path.read_bytes())
    digest.update(selected_source(Path(__file__).read_text(), ["candidates"]))
    return digest.hexdigest()


def candidates(key):
    """Shared source-only inference, cached per edge and input content hash."""
    from turku3d.coarse.sources import current_sources
    from turku3d.tile_layer import identities

    box = bounds(key)
    outer = box.buffered(W.SEARCH)
    paths = {block.name: C.raw_dir("laz") / "pistepilvi" / f"{block.name}.laz"
             for block in C.laz_tiles_for_bbox(outer)}
    axis, line, along = key
    source_tile = C.VisualTile(0, line, along) if axis == "x" else C.VisualTile(0, along, line)
    osm = current_sources().osm_source(source_tile)
    if osm is None or any(not p.is_file() for p in paths.values()):
        return None  # No fetching or generation of missing source data here.
    ways = S.streets(osm, box)
    inputs = identities({**paths, "osm": osm})
    digest = hashlib.sha256(json.dumps([key, recipe(), inputs], sort_keys=True).encode()).hexdigest()
    shared = os.environ.get("TURKU3D_SHARED_CACHE")
    root = (Path(shared) / "raw-derived" / "span-lamp-seams" if shared else C.cache_dir("span-lamp-seams"))
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{digest}.json"
    if target.is_file():
        return json.loads(target.read_text())
    from turku3d.lidar import cache as lidar_cache, decoded

    # Reuse the farm's existing spatial shards. Wire fitting sorts its input,
    # so it does not need the full source-order scan used by other reducers.
    lidar_root = C.cache_dir("lidar")
    if shared:
        version = hashlib.sha256(b"".join(Path(m.__file__).read_bytes()
                                          for m in (C, lidar_cache, decoded))).hexdigest()
        lidar_root = Path(shared) / "raw-derived" / "lidar" / version
    points = lidar_cache.TileStore(cache_dir=lidar_root, source_paths=paths).query(outer)
    xyz, classes = points.points, points.records["classification"]
    # Keep all candidates in the common support window, including wire middles
    # outside the narrow edge strip. Tile wire ownership must not discard them.
    found, lamps = W.detect(xyz, classes, outer.as_tuple(), search=0)
    record = W.record(found, lamps)
    ground = W.Ground(xyz[classes == W.GROUND], outer.as_tuple())
    updated, _ = S.infer(record, ways, ground=ground)
    all_lamps = updated["lamps"] if updated else (record or {}).get("lamps", [])
    result = [lamp[:3] for lamp in all_lamps if seam(lamp[0], lamp[2]) == key]
    temporary = target.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(result, separators=(",", ":")))
    temporary.replace(target)
    return result


def infer(tile, record, ways, hung=(), ground=None):
    """Interior inference plus shared border inference; existing surveyed lamps survive."""
    shared = {key: candidates(key) for key in edges(tile)}
    if any(value is None for value in shared.values()):
        return None  # Incomplete common inputs must not erase existing lamps.
    original = record or {"version": 1, "kinds": ["span", "utility", "power"], "wires": [], "lamps": []}
    # The shared survey is authoritative at borders, including its detected
    # lamps. Replacing that band also corrects products already on span-lamps@1.
    lamps = [list(l) for l in original.get("lamps") or [] if seam(l[0], l[2]) is None]
    wires = [list(w) for w in original.get("wires") or []]
    updated, _ = S.infer(original, ways, hung, ground)
    if updated:
        for lamp in updated["lamps"][len(original.get("lamps") or []):]:
            if seam(lamp[0], lamp[2]) is None and tile.bbox.contains(lamp[0], lamp[2]):
                lamps.append(lamp)
                wires[int(lamp[3])] = updated["wires"][int(lamp[3])]
    lit = [(l[0], l[2]) for l in lamps] + list(hung)
    span = original["kinds"].index("span") if "span" in original["kinds"] else -1
    for key, points in shared.items():
        for x, y, z in points:
            if not tile.bbox.contains(x, z) or any(math.hypot(x - a, z - b) < S.SEPARATION for a, b in lit):
                continue
            index = -1
            best = S.CARRIED
            for i, w in enumerate(wires):
                if int(w[7]) != span:
                    continue
                dx, dz = w[3] - w[0], w[5] - w[2]
                length = math.hypot(dx, dz)
                if length < 1e-6:
                    continue
                along = ((x - w[0]) * dx + (z - w[2]) * dz) / length
                if along < -S.EXTEND or along > length + S.EXTEND:
                    continue
                distance = abs((x - w[0]) * dz - (z - w[2]) * dx) / length
                if distance < best:
                    index, best = i, distance
            if index >= 0:
                w = wires[index]
                dx, dz = w[3] - w[0], w[5] - w[2]
                t = ((x - w[0]) * dx + (z - w[2]) * dz) / (dx * dx + dz * dz)
                if t < 0:
                    w[:3] = [x, y, z]
                elif t > 1:
                    w[3:6] = [x, y, z]
            lamps.append([x, y, z, index])
            lit.append((x, z))
    result = {**original, "wires": wires, "lamps": lamps}
    return result if result != original else None
