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
CROPS = 6  # a building: its larger walls first (the segmenter's 0.5 s a crop is what limits a city run)
COLOUR_ZOOM = 3
MIN_FOOTPRINT_M2 = 12.0
CACHE_CAP = int(float(os.environ.get("COLOUR_CACHE_GIB", "3")) * 2 ** 30)  # panorama bytes kept between cells
MAPS_STALL_S = 900.0  # no new label map anywhere for this long: the segmenter is away
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
            local.panos = Panoramas(root / "panoramas", google_zoom=COLOUR_ZOOM, upright_from_lines=False)
        packet = root / "buildings" / b.id
        if packet.is_dir():  # left by a cell that was stopped: its crops and label maps may not match new ones
            for path in list(packet.iterdir()):
                path.unlink()
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
    # The segmenter works through the crops of every cell in hand: the wait ends in failure only when no label
    # map has come for any of them for MAPS_STALL_S (the segmenter or its relay is away).
    seen, moved = -1, time.time()
    while any(not p.with_suffix(f".{tag}.png").exists() for p in crops):
        maps = sum(1 for _ in (root / "buildings").glob(f"*/*.{tag}.png"))
        if maps != seen:
            seen, moved = maps, time.time()
        elif time.time() - moved > MAPS_STALL_S:
            raise TimeoutError(f"cell {name_of(cell)}: no label map came for {MAPS_STALL_S:.0f} s: is colour_segment.py "
                               f"--watch --tag {tag} (or its --remote relay) running on {root / 'buildings'}?")
        time.sleep(3.0)
    mapped = time.time()
    failed = [{"building": key, "error": error} for key, record, error in records if record is None]

    def parts(item):
        key, record, _ = item
        packet = root / "buildings" / key
        overcast = {f"{c['panorama']['provider']}:{c['panorama']['id']}": light.figure(c["panorama"]) for c in record["crops"]}
        try:
            result = colour_parts.of_packet(packet, tag, overcast)
        except Exception as error:  # noqa: BLE001 - a broken crop or map must not stop the city
            failed.append({"building": key, "error": f"parts: {type(error).__name__}: {error}"[:200]})
            return None
        if result is None:
            return None
        return {**summary(result), "cell": name_of(cell), "footprint_m2": round(scene.buildings[key].footprint.area, 1),
                "walls_seen": sum(w["seen"] for w in record["walls"]), "unseen": len(record["unseen"])}

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        lines = [line for line in pool.map(parts, [r for r in records if r[1] is not None]) if line is not None]
    if not keep:
        for key, _, _ in records:
            packet = root / "buildings" / key
            if packet.is_dir():
                for path in list(packet.iterdir()):
                    path.unlink()
                packet.rmdir()
    out = root / "batch/results" / f"{name_of(cell)}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines))
    cache = Path(light.cache.root)
    freed = 0 if keep or root.resolve() not in cache.resolve().parents or os.environ.get("COLOUR_PRUNE", "1") == "0" \
        else prune(cache)
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


def cells_first(bounds, firsts: list, size: float = CELL_M) -> list[tuple[float, float]]:
    """The cells covering the bounds, those nearest to any of the `firsts` points (E, N) first: the places that
    matter now (built locations, the centre) are done in the first hours, then the rings round them grow."""
    found = cells(bounds, firsts[0] if firsts else None, size)
    if len(firsts) > 1:
        points = np.array(firsts, float)
        found.sort(key=lambda c: float(np.min(np.hypot(points[:, 0] - c[0] - size / 2, points[:, 1] - c[1] - size / 2))))
    return found


def with_buildings(size: float = CELL_M) -> dict:
    """{cell corner: buildings OpenStreetMap maps there} for the city's extract: the cells worth loading (LoD2 is
    read per cell; a cell of field, forest or water costs a scene load for nothing)."""
    import pyarrow.parquet as pq

    from turku3d.world.sources import Sources

    table = pq.read_table(Sources(log=lambda *_: None).osm.path(), columns=["building", "e0", "n0"]).to_pandas()
    table = table[table["building"].notna()]
    found: dict = {}
    for e, n in zip(table["e0"], table["n0"], strict=True):
        key = (float(np.floor(e / size) * size), float(np.floor(n / size) * size))
        found[key] = found.get(key, 0) + 1
    return found


def read_positions(store: Path) -> dict:
    """{building key: (E, N)} of the buildings a vision reading described (the ground truth to check against):
    the middle of their wall frames."""
    from .against import read_buildings

    out = {}
    for key, doc in read_buildings(store).items():
        points = [p for w in doc.get("walls", []) if "frame" in w for p in (w["frame"]["p0"], w["frame"]["p1"])]
        if points:
            out[key] = tuple(float(v) for v in np.mean(np.array(points, float)[:, :2], axis=0))
    return out


def own_cache(root: Path) -> None:
    """New panoramas go under the batch root (TURKUTEX_STREETVIEW), so `prune` cuts only what this batch fetched;
    shared caches are still read."""
    if not os.environ.get("TURKUTEX_STREETVIEW"):
        from turkutex.paths import streetview_dir

        shared = str(streetview_dir())
        os.environ["TURKUTEX_STREETVIEW"] = str(root / "streetlevel")
        os.environ["TURKUTEX_READ_CACHE"] = os.pathsep.join(p for p in (os.environ.get("TURKUTEX_READ_CACHE"), shared) if p)
        os.environ.setdefault("TURKUTEX_DEPTH", "0")  # colour needs no depth maps


def run(root: Path, work: dict, *, tag: str = colour_parts.TAG, jobs: int = 4, keep: bool = False, limit: int | None = None,
        cell_workers: int = 2) -> dict:
    """`work`: {cell corner: None for every building of the cell, or the set of building keys wanted}, in order.

    `cell_workers` cells are worked on at once, so the segmenter has crops of one cell while the views of the next
    are fetched. A cell that fails (its label maps did not come: the segmenter is away) stops the run after the
    cells in hand, with exit status 1: started again (by hand or by systemd) it goes on where it stopped."""
    own_cache(root)
    import threading

    from turkutex.streetview.panos import PanoCache

    light = colour_light.Light(root / "batch/light.jsonl", PanoCache())
    todo = [cell for cell in work if not (root / "batch/done" / f"{name_of(cell)}.json").exists()]
    if limit is not None:
        todo = todo[:limit]
    state = {"done": 0, "buildings": 0, "failed": None}
    guard = threading.Lock()

    def one(cell):
        if state["failed"] or (root / "batch/stop").exists():
            return
        try:
            line = cell_run(root, cell, work[cell], light, tag=tag, jobs=jobs, keep=keep)
        except Exception as error:  # noqa: BLE001 - recorded, and the run ends after the cells in hand
            with guard:
                state["failed"] = f"{name_of(cell)}: {type(error).__name__}: {error}"[:300]
            print(json.dumps({"cell": name_of(cell), "error": state["failed"]}), flush=True)
            return
        with guard:
            state["done"] += 1
            state["buildings"] += line["measured"]
        print(json.dumps(line), flush=True)

    with ThreadPoolExecutor(max_workers=max(1, cell_workers)) as pool:
        list(pool.map(one, todo))
    left = sum(not (root / "batch/done" / f"{name_of(cell)}.json").exists() for cell in work)
    return {"cells_done_now": state["done"], "buildings_now": state["buildings"], "cells_left": left, "cells": len(work),
            "failed": state["failed"]}


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


BY = "w2-colour batch 1"


def wall_block(result: dict) -> dict | None:
    """The stored `measured.wall` of a calibrated result: the paint colour with its spread, share and confidence,
    and the panorama it came from (provider, id, date, overcast 0..1), how much of the wall was free, how far
    the panoramas used agree and how many there were."""
    part = result["parts"].get("wall")
    if not part or "colour" not in part:
        return None
    return {"colour": part["colour"], "spread": part["spread"], "share": part["share"], "conf": {"*": part["conf"]},
            "visible": part["visible"], "agreement": part["agreement"], "n": part["n"],
            "from": {k: v for k, v in part["from"].items() if v is not None}}


def measured_lines(root: Path, *, white_l: float = colour_parts.WHITE_L, balance: bool = True, mode: str = "white") -> list[dict]:
    """One line a building of a batch root: {building, measured: {wall}, pixel, panoramas}, the parts combined
    again from the stored candidates (so the rules of `combine` can change without measuring again) and brought
    to the paint scale. `mode`: "white" (the visit's white point, `colour_parts.paint`), "pixel" (as photographed)."""
    found = results(root)
    for line in found:
        line["parts"] = colour_parts.combine(line["candidates"])
    whites = colour_parts.visit_white(found) if mode == "white" else {}
    out = []
    for line in found:
        for part in line["parts"].values():
            white = whites.get(colour_parts.visit_of(part["from"])) or (
                whites.get("*") if (part["from"].get("overcast") or 0) >= 0.5 else None)
            lab = colour_parts.paint(part["lab"], white, balance, white_l) if mode == "white" else np.array(part["lab"])
            part["colour"] = colour_parts.colour.to_hex(lab)
        block = wall_block(line)
        out.append({"building": line["building"], "address": line.get("address"),
                    "measured": {"wall": block} if block else {},
                    "pixel": (line["parts"].get("wall") or {}).get("pixel"),
                    "panoramas": sorted({f"{c['panorama']['provider']}:{c['panorama']['id']}" for c in line["candidates"]})[:6]})
    return out


def description(key: str, block: dict, panoramas: list) -> dict:
    """The stored description of a building only measured: tier auto, `measured` alone."""
    from datetime import UTC, datetime

    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"schema": "turku-description/building/1", "id": f"building/{key}", "tier": "auto",
            "provenance": [{"source": "panorama", "key": f"colour-{now}", "at": now, "by": BY,
                            "note": "wall colour measured from panoramas ranked by overcast light, local segmenter, "
                                    "paint colour by the visit's white point: " + ", ".join(panoramas[:6])}],
            "measured": block}


def store(lines: list[dict], least: float = 0.0) -> dict:
    """Stores `measured.wall` for the buildings whose wall colour nobody has read (a reading always wins; an earlier
    colour-only description is replaced; a description without a read wall colour keeps all it says and takes
    `measured` beside it). A block the description schema refuses is not stored."""
    from turku3d.world import store as world_store
    from turku3d.world.kinds.building import description as building_description

    from .colour import wall_colour_unread, with_measured

    stored = skipped = refused = unsure = 0
    for line in lines:
        wall = line["measured"].get("wall")
        if not wall:
            continue
        if wall["conf"]["*"] < least:
            unsure += 1
            continue
        doc = description(line["building"], {"wall": wall}, line.get("panoramas", []))
        if building_description.errors(doc):
            refused += 1
            continue
        held = world_store.read_description(f"building/{line['building']}")
        if not wall_colour_unread(held):
            skipped += 1
            continue
        world_store.write_description(with_measured(held, doc), BY, "wall colour measured")
        stored += 1
    return {"stored": stored, "described_already": skipped, "refused": refused, "under_least_conf": unsure}
