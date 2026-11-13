"""Regions: one continuous world built in 500 m units and streamed by 250 m chunks (docs/design/world-regions.md).

A unit is one `driver.build` over its square grown by a margin, in a process of its own; several run at once into
one store. `assemble` keeps the rows each unit owns (origin inside its square), writes one content-addressed chunk
object per 250 m chunk and the region index `locations/NAME.json` (`turku-region/1`), and checks the seams: a row a
unit built in its margin must have the same hash as the owning neighbour's row.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from .api import Area
from .driver import area_of_views, now, stats_of
from .frame import CELL, to_engine, to_survey
from .store import Store, write_atomic

UNIT = 500.0
CHUNK = 250.0
MARGIN = 62.5
CHUNKS_PER_UNIT = int(UNIT / CHUNK)
CELLS_PER_UNIT = int(UNIT / CELL)


def unit_id(i: int, j: int) -> str:
    return f"u_{i}_{j}"


def unit_of(uid: str) -> tuple[int, int]:
    _, i, j = uid.split("_")
    return int(i), int(j)


def square(i: int, j: int) -> tuple[float, float, float, float]:
    """Engine x0, z0, x1, z1 of unit (i, j): i east, j south, as the cells and chunks."""
    return i * UNIT, j * UNIT, (i + 1) * UNIT, (j + 1) * UNIT


def unit_area(i: int, j: int, margin: float = MARGIN) -> Area:
    x0, z0, x1, z1 = square(i, j)
    e, n, _ = to_survey((x0 + x1) / 2, 0.0, (z0 + z1) / 2)
    return Area(round(e, 3), round(n, 3), UNIT / 2 + margin, "square")


def units_meeting(e: float, n: float, radius: float) -> set[tuple[int, int]]:
    """The units whose square meets the circle around a survey point."""
    x, _, z = to_engine(e, n)
    found = set()
    for i in range(math.floor((x - radius) / UNIT), math.floor((x + radius) / UNIT) + 1):
        for j in range(math.floor((z - radius) / UNIT), math.floor((z + radius) / UNIT) + 1):
            x0, z0, x1, z1 = square(i, j)
            if math.hypot(max(x0 - x, 0, x - x1), max(z0 - z, 0, z - z1)) <= radius:
                found.add((i, j))
    return found


def owns(uid: str, origin) -> bool:
    x0, z0, x1, z1 = square(*unit_of(uid))
    return x0 <= origin[0] < x1 and z0 <= origin[2] < z1


# ------------------------------------------------------------------------------------------------------- the plan

def plan_path(store: Store, name: str) -> Path:
    return store.root / "units" / name / "region.json"


def read_plan(store: Store, name: str) -> dict | None:
    path = plan_path(store, name)
    return json.loads(path.read_text()) if path.exists() else None


def extend(store: Store, name: str, at: tuple[float, float] | None, radius: float | None, areas: list[str],
           units: list[str] | None = None) -> dict:
    """Adds a ring (a circle around the region's centre, plus the queries of named pilot areas) to the region's plan;
    the first ring sets the centre. Returns the plan with `rings[-1]` the new ring's units."""
    plan = read_plan(store, name) or {"schema": "turku-region-plan/1", "name": name, "rings": []}
    if at is not None and "centre" not in plan:
        plan["centre"] = [float(at[0]), float(at[1])]
    if "centre" not in plan:
        raise SystemExit(f"region {name} has no centre yet: give --at E,N")
    wanted: set[tuple[int, int]] = set()
    if radius:
        wanted |= units_meeting(*plan["centre"], radius)
    for area_name in areas:
        area = area_of_views(area_name)
        wanted |= units_meeting(area.e, area.n, area.radius)
    if units:
        wanted |= {unit_of(u) for u in units}
    known = {u for ring in plan["rings"] for u in ring["units"]}
    new = sorted(unit_id(*u) for u in wanted if unit_id(*u) not in known)
    if new:
        plan["rings"].append({"radius": radius, "areas": areas, "units": new, "added": now()})
    write_atomic(plan_path(store, name), json.dumps(plan, indent=1).encode())
    return plan


def all_units(plan: dict) -> list[str]:
    return [u for ring in plan["rings"] for u in ring["units"]]


# ------------------------------------------------------------------------------------------------------ one unit

def build_unit(store: Store, name: str, uid: str, margin: float = MARGIN, log=print) -> dict:
    """Builds one unit as a location `NAME/UNIT` over its square and margin; every row is `scene`."""
    from .driver import build

    i, j = unit_of(uid)
    area = unit_area(i, j, margin)
    location = f"{name}/{uid}"
    with store.building(location):
        manifest = build(location, area, area.radius, store, log=log)
        manifest["unit"] = {"region": name, "id": uid, "square": list(square(i, j)), "margin": margin}
        store.put_location(manifest)
    return manifest


def _cpu_sampler(path: Path, stop: threading.Event, every: float = 5.0) -> None:
    """Host CPU busy share and load every few seconds, one JSON line each (Linux /proc/stat; load only elsewhere)."""
    def ticks():
        try:
            parts = [float(v) for v in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
            return sum(parts), parts[3] + parts[4]
        except OSError:
            return None
    last = ticks()
    with path.open("a") as out:
        while not stop.wait(every):
            current = ticks()
            row = {"t": round(time.time(), 1), "load": round(os.getloadavg()[0], 2), "cores": os.cpu_count()}
            if current and last:
                total, idle = current[0] - last[0], current[1] - last[1]
                row["busy"] = round(1 - idle / total, 4) if total else None
            last = current
            out.write(json.dumps(row) + "\n")
            out.flush()


def build_units(store: Store, name: str, units: list[str], parallel: int, margin: float = MARGIN, log=print) -> list[dict]:
    """Builds units in processes of their own, `parallel` at once; returns per unit wall and CPU seconds, peak
    memory and exit code. Logs and a host CPU trace go to $TURKU3D_WORK/region/NAME/."""
    work = Path(os.environ.get("TURKU3D_WORK", store.root.parent)) / "region" / name
    work.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    sampler = threading.Thread(target=_cpu_sampler, args=(work / "cpu.jsonl", stop), daemon=True)
    sampler.start()
    queue, running, results = list(units), {}, []
    started = time.time()
    try:
        while queue or running:
            while queue and len(running) < parallel:
                uid = queue.pop(0)
                logfile = (work / f"{uid}.log").open("w")
                command = [sys.executable, "-u", "-m", "turku3d.world", "region", "unit", name, uid, "--margin", str(margin)]
                process = subprocess.Popen(command, stdout=logfile, stderr=subprocess.STDOUT)
                running[process.pid] = (uid, process, logfile, time.time())
                log(f"{now()} unit {uid} started (pid {process.pid}), {len(queue)} queued")
            pid, status, usage = os.wait4(-1, 0)
            if pid not in running:
                continue
            uid, process, logfile, begun = running.pop(pid)
            process.returncode = os.waitstatus_to_exitcode(status)
            logfile.close()
            peak = usage.ru_maxrss / (1024 if sys.platform == "linux" else 1024 * 1024)
            row = {"unit": uid, "exit": process.returncode, "start": round(begun - started, 1),
                   "wall": round(time.time() - begun, 1), "cpu": round(usage.ru_utime + usage.ru_stime, 1),
                   "peakMb": round(peak), "log": str(work / f"{uid}.log")}
            results.append(row)
            with (work / "units.jsonl").open("a") as out:
                out.write(json.dumps({**row, "at": now()}) + "\n")
            log(f"{now()} unit {uid} exit {row['exit']}: wall {row['wall']} s, cpu {row['cpu']} s, peak {row['peakMb']} MB")
    finally:
        stop.set()
    return results


# ------------------------------------------------------------------------------------------------------ assemble

def assemble(store: Store, name: str, log=print) -> dict:
    """Writes the region index `locations/NAME.json` and its chunk objects from the built units' manifests."""
    plan = read_plan(store, name)
    if plan is None:
        raise SystemExit(f"no region {name} in {store.root}")
    manifests = {}
    for uid in all_units(plan):
        manifest = store.location(f"{name}/{uid}")
        if manifest is None:
            log(f"  unit {uid} is not built: left out")
            continue
        manifests[uid] = manifest
    if not manifests:
        raise SystemExit(f"region {name}: no unit is built")
    # Ownership: the unit whose square holds the row's origin; an entity no unit owns that way (its origin lies
    # outside every unit that found it) is taken from the first unit that has it, and reported.
    owner: dict[str, tuple[str, dict]] = {}
    for uid, manifest in manifests.items():
        for row in manifest["entities"]:
            if owns(uid, row["origin"]) and row["id"] not in owner:
                owner[row["id"]] = (uid, row)
    adopted = []
    for uid, manifest in manifests.items():
        for row in manifest["entities"]:
            if row["id"] in owner:
                continue
            x, z = row["origin"][0], row["origin"][2]
            holder = unit_id(math.floor(x / UNIT), math.floor(z / UNIT))
            if holder in manifests:  # the owning unit did not find it: a hole unless taken from here
                owner[row["id"]] = (uid, row)
                adopted.append(row["id"])
    # Seams: every row a unit built in its margin against the owner's row of the same id.
    seams: dict[str, dict] = {}
    for uid, manifest in manifests.items():
        for row in manifest["entities"]:
            if row["id"] not in owner or owner[row["id"]][0] == uid:
                continue
            other, kept = owner[row["id"]]
            border = seams.setdefault("|".join(sorted((uid, other))), {"equal": 0, "differ": 0, "ids": []})
            if kept["hash"] == row["hash"]:
                border["equal"] += 1
            else:
                border["differ"] += 1
                if len(border["ids"]) < 12:
                    border["ids"].append(row["id"])
    chunks: dict[str, list[dict]] = {}
    for _, row in owner.values():
        key = f"{math.floor(row['origin'][0] / CHUNK)}_{math.floor(row['origin'][2] / CHUNK)}"
        chunks.setdefault(key, []).append(row)
    index_chunks = {}
    for key in sorted(chunks, key=lambda k: tuple(int(v) for v in k.split("_"))):
        rows = sorted(chunks[key], key=lambda r: r["id"])
        i, j = (int(v) for v in key.split("_"))
        stored = store.put_json({"schema": "turku-chunk/1", "chunk": [i, j], "entities": rows})
        ys = [(r["origin"][1] + r["bounds"][1], r["origin"][1] + r["bounds"][4]) for r in rows]
        stats = stats_of(rows)
        index_chunks[key] = {"url": stored["url"], "sha256": stored["sha256"], "bytes": stored["bytes"],
                             "entities": len(rows), "triangles": stats["triangles"], "meshBytes": stats["bytes"],
                             "y": [round(min(y[0] for y in ys), 1), round(max(y[1] for y in ys), 1)]}
    collision, roads = {}, {}
    for uid, manifest in manifests.items():
        i, j = unit_of(uid)
        for cell, entry in manifest.get("collision", {}).items():
            _, ci, cj = cell.split("_")
            if i * CELLS_PER_UNIT <= int(ci) < (i + 1) * CELLS_PER_UNIT and j * CELLS_PER_UNIT <= int(cj) < (j + 1) * CELLS_PER_UNIT:
                collision[cell] = entry
        for tile, entry in manifest.get("roads", {}).items():
            _, ti, tj = tile.split("_")
            if i * CHUNKS_PER_UNIT <= int(ti) < (i + 1) * CHUNKS_PER_UNIT and j * CHUNKS_PER_UNIT <= int(tj) < (j + 1) * CHUNKS_PER_UNIT:
                roads[tile] = entry
    centre_unit = unit_id(*(math.floor(v / UNIT) for v in (to_engine(*plan["centre"])[0], to_engine(*plan["centre"])[2])))
    home = manifests.get(centre_unit) or next(iter(manifests.values()))
    rows = [row for _, row in owner.values()]
    previous = store.location(name)
    index = {
        "schema": "turku-region/1", "name": name,
        "revision": (previous or {}).get("revision", 0) + 1, "built": now(),
        "origin": home["origin"], "centre": plan["centre"], "focus": home["focus"],
        "chunk": CHUNK, "unit": UNIT,
        "chunks": index_chunks, "collision": dict(sorted(collision.items())), "roads": dict(sorted(roads.items())),
        "units": [{"id": uid, "square": list(square(*unit_of(uid))), "revision": m["revision"], "built": m["built"],
                   "entities": sum(1 for o, _ in owner.values() if o == uid), "failures": len(m.get("failures", [])),
                   "totalMs": m["timings"].get("totalMs")} for uid, m in sorted(manifests.items())],
        "seams": {"borders": seams, "equal": sum(b["equal"] for b in seams.values()),
                  "differ": sum(b["differ"] for b in seams.values()), "adopted": adopted[:50]},
        "failures": [f for m in manifests.values() for f in m.get("failures", [])],
        "stats": stats_of(rows),
    }
    if "start" in home:
        index["start"] = home["start"]
    store.put_location(index)
    log(f"region {name} r{index['revision']}: {len(manifests)} units, {len(index_chunks)} chunks, {len(rows)} entities, "
        f"{len(collision)} collision cells, {len(roads)} road tiles; seams {index['seams']['equal']} equal, "
        f"{index['seams']['differ']} differ, {len(adopted)} adopted")
    for border, row in sorted(seams.items()):
        if row["differ"]:
            log(f"  seam {border}: {row['equal']} equal, {row['differ']} differ: {', '.join(row['ids'][:6])}")
    return index
