"""House colours of every building of an area, measured unattended and resumably, without a model call.

The area is worked through in square cells, nearest to a start point first. A cell is one unit of work:

1. views: for each building the panoramas that see its walls, ranked by light (`views.by_light`: the visit's
   overcast figure from a small fetch, `colour_light.Light`) and view quality; rectified crops of the best three a
   wall (Street View at zoom 3: colour does not need more);
2. label maps: a segmenter running beside this command (`colour_segment.py --watch` in its own torch
   environment) writes `<crop>.<tag>.png` for every crop it finds;
3. parts: `colour_parts.of_packet` per building, one JSON line a building in `batch/results/<cell>.jsonl` (the
   candidates with their parts, light and white, and the combined parts as pixel colours: calibration and storing
   are separate steps, `colour --apply-batch`, so they can change without measuring again);
4. the cell's crops and label maps are removed, the panorama cache is cut back to its cap, and
   `batch/done/<cell>.json` records counts, seconds and bytes.

A cell with a done record is skipped, so the command is stopped and started freely (a file `batch/stop` ends it
after the cell in hand). Nothing but the result lines is kept: panoramas, crops and maps stay on the machine that
runs it, bounded by the cache cap.
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from . import colour_light, colour_parts, lod2
from . import prepare as prepare_module
from .panoramas import Panoramas

CELL_M = 250.0
MARGIN_M = 150.0  # buildings round a cell that hide its walls or are hidden
PER_WALL = 3
CROPS = 8  # a building: its larger walls first
COLOUR_ZOOM = 3
MIN_FOOTPRINT_M2 = 12.0
CACHE_CAP = 3 * 2 ** 30  # bytes of panorama pictures kept between cells
MAPS_WAIT_S = (120.0, 4.0)  # for a cell's label maps: this long, plus this long a crop
PRUNED = (".jpg", ".heic", ".npy")


def cells(bounds, start=None, size: float = CELL_M) -> list[tuple[float, float]]:
    """South-west corners of the cells covering the bounds, nearest to `start` (E, N) first."""
    e0, n0 = np.floor(bounds[0] / size) * size, np.floor(bounds[1] / size) * size
    found = [(float(e), float(n)) for e in np.arange(e0, bounds[2], size) for n in np.arange(n0, bounds[3], size)]
    centre = start or ((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2)
    return sorted(found, key=lambda c: (c[0] + size / 2 - centre[0]) ** 2 + (c[1] + size / 2 - centre[1]) ** 2)


def cells_of(points: dict, size: float = CELL_M) -> dict:
    """{cell corner: {keys}} for buildings named with a position ({key: (E, N)}): the cell is a neighbourhood to
    load, the keys say which of its buildings are wanted."""
    found: dict = {}
    for key, (e, n) in points.items():
        found.setdefault((float(np.floor(e / size) * size), float(np.floor(n / size) * size)), set()).add(key)
    return found


def name_of(cell) -> str:
    return f"{round(cell[0])}_{round(cell[1])}"


def prune(cache: Path, cap: int = CACHE_CAP) -> int:
    """Cuts the panorama cache back to 60 % of its cap, oldest pictures first; metadata stays. Returns bytes freed."""
    files = []
    for folder in ("google", "lookaround"):
        if (cache / folder).is_dir():
            for entry in os.scandir(cache / folder):
                if entry.is_dir():
                    files += [(f.stat().st_mtime, f.stat().st_size, f.path) for f in os.scandir(entry.path)
                              if f.name.endswith(PRUNED)]
    total = sum(size for _, size, _ in files)
    freed = 0
    if total > cap:
        for _, size, path in sorted(files):
            if total - freed <= 0.6 * cap:
                break
            os.unlink(path)
            freed += size
    return freed


def summary(result: dict) -> dict:
    """A result line: everything of_packet measured, without what only a sheet needs."""
    keep = ("file", "wall", "panorama", "distance_m", "oblique_deg", "quality", "overcast", "white", "visible", "free_m2",
            "in_front", "gable", "parts", "score")
    return {**{k: v for k, v in result.items() if k != "candidates"},
            "candidates": [{k: row[k] for k in keep if k in row} for row in result["candidates"]]}


def cell_run(root: Path, cell, only: set | None, light: colour_light.Light, *, tag: str, jobs: int, keep: bool,
             size: float = CELL_M) -> dict:
    started = time.time()
    e0, n0 = cell
    scene = lod2.Scene((e0 - MARGIN_M, n0 - MARGIN_M, e0 + size + MARGIN_M, n0 + size + MARGIN_M))
    inside = []
    for b in scene.buildings.values():
        at = b.footprint.representative_point()
        if only is not None and b.id not in only:
            continue
        if only is None and not (e0 <= at.x < e0 + size and n0 <= at.y < n0 + size and b.footprint.area >= MIN_FOOTPRINT_M2):
            continue
        inside.append(b)
    loaded = time.time()
    import threading

    local = threading.local()

    def prepared(b):
        if not hasattr(local, "panos"):
            local.panos = Panoramas(root / "panoramas", google_zoom=COLOUR_ZOOM)
        packet = root / "buildings" / b.id
        try:
            record = prepare_module.prepare(scene, b, local.panos, packet, per_wall=PER_WALL, limit=CROPS,
                                            light=light.of, contexts=keep)
        except Exception as error:  # noqa: BLE001 - one building must not stop the cell
            return b.id, None, f"{type(error).__name__}: {error}"[:200]
        packet.mkdir(parents=True, exist_ok=True)
        (packet / "views.json").write_text(json.dumps(record, ensure_ascii=False))
        return b.id, record, None

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        records = list(pool.map(prepared, inside))
    viewed = time.time()
    crops = [root / "buildings" / key / Path(c["file"]).name for key, record, _ in records if record for c in record["crops"]]
    limit = time.time() + MAPS_WAIT_S[0] + MAPS_WAIT_S[1] * len(crops)
    while any(not p.with_suffix(f".{tag}.png").exists() for p in crops):
        if time.time() > limit:
            raise TimeoutError(f"cell {name_of(cell)}: label maps of {len(crops)} crops did not come in time: is "
                               f"colour_segment.py --watch --tag {tag} running on {root / 'buildings'}?")
        time.sleep(1.0)
    mapped = time.time()
    lines, failed = [], []
    for key, record, error in records:
        packet = root / "buildings" / key
        if record is None:
            failed.append({"building": key, "error": error})
            continue
        overcast = {f"{c['panorama']['provider']}:{c['panorama']['id']}": light.figure(c["panorama"]) for c in record["crops"]}
        result = colour_parts.of_packet(packet, tag, overcast)
        lines.append({**summary(result), "cell": name_of(cell), "footprint_m2": round(scene.buildings[key].footprint.area, 1),
                      "walls_seen": sum(w["seen"] for w in record["walls"]), "unseen": len(record["unseen"])})
        if not keep:
            for path in list(packet.iterdir()):
                path.unlink()
            packet.rmdir()
    out = root / "batch/results" / f"{name_of(cell)}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines))
    cache = Path(light.cache.root)
    freed = 0 if keep else prune(cache)
    done = {"cell": name_of(cell), "buildings": len(inside), "measured": len(lines), "failed": failed, "crops": len(crops),
            "with_wall": sum("wall" in line["parts"] for line in lines),
            "seconds": {"scene": round(loaded - started, 1), "views": round(viewed - loaded, 1),
                        "maps_wait": round(mapped - viewed, 1), "parts": round(time.time() - mapped, 1),
                        "total": round(time.time() - started, 1)},
            "result_bytes": out.stat().st_size, "cache_freed_bytes": freed, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    marker = root / "batch/done" / f"{name_of(cell)}.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(done))
    return done


def run(root: Path, work: dict, *, tag: str = colour_parts.TAG, jobs: int = 4, keep: bool = False, limit: int | None = None) -> dict:
    """`work`: {cell corner: None for every building of the cell, or the set of building keys wanted}, in order."""
    from turkutex.streetview.panos import PanoCache

    light = colour_light.Light(root / "batch/light.jsonl", PanoCache())
    done = buildings = 0
    for cell, only in work.items():
        if (root / "batch/stop").exists() or (limit is not None and done >= limit):
            break
        if (root / "batch/done" / f"{name_of(cell)}.json").exists():
            continue
        line = cell_run(root, cell, only, light, tag=tag, jobs=jobs, keep=keep)
        done += 1
        buildings += line["measured"]
        print(json.dumps(line), flush=True)
    left = sum(not (root / "batch/done" / f"{name_of(cell)}.json").exists() for cell in work)
    return {"cells_done_now": done, "buildings_now": buildings, "cells_left": left, "cells": len(work)}


def results(root: Path) -> list[dict]:
    """Every result line of a batch root."""
    found = []
    for path in sorted((root / "batch/results").glob("*.jsonl")):
        found += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return found


def progress(root: Path) -> dict:
    """Counts and throughput of a batch root from its done records."""
    done = [json.loads(p.read_text()) for p in sorted((root / "batch/done").glob("*.json"))]
    if not done:
        return {"cells": 0}
    busy = [d for d in done if d["measured"]]
    seconds = sum(d["seconds"]["total"] for d in done)
    measured = sum(d["measured"] for d in done)
    return {"cells": len(done), "buildings": measured, "with_wall": sum(d["with_wall"] for d in done),
            "crops": sum(d["crops"] for d in done), "failed": sum(len(d["failed"]) for d in done),
            "hours": round(seconds / 3600, 2), "buildings_per_hour": round(measured / max(seconds, 1) * 3600),
            "seconds_a_building": {k: round(sum(d["seconds"][k] for d in busy) / max(1, measured), 2)
                                   for k in ("scene", "views", "maps_wait", "parts")},
            "result_bytes_a_building": round(sum(d["result_bytes"] for d in done) / max(1, measured)),
            "first": min(d["at"] for d in done), "last": max(d["at"] for d in done)}
