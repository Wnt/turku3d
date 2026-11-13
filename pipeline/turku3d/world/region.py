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


def _trees(roots: dict[int, str]) -> dict[str, tuple[float, float]]:
    """CPU seconds (user + system) and resident MB of each root process with all its descendants (Linux /proc)."""
    parent, ticks, rss = {}, {}, {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
        except OSError:
            continue
        pid = int(entry.name)
        parent[pid], ticks[pid], rss[pid] = int(fields[1]), int(fields[11]) + int(fields[12]), int(fields[21])
    hz, page = os.sysconf("SC_CLK_TCK"), os.sysconf("SC_PAGE_SIZE")
    out = {}
    for root, uid in roots.items():
        members, frontier = {root}, [root]
        while frontier:
            frontier = [pid for pid, up in parent.items() if up in frontier and pid not in members]
            members.update(frontier)
        out[uid] = (sum(ticks.get(pid, 0) for pid in members) / hz, sum(rss.get(pid, 0) for pid in members) * page / 2**20)
    return out


def _cpu_sampler(path: Path, stop: threading.Event, running: dict, every: float = 5.0) -> None:
    """Every few seconds one JSON line: host CPU busy share and load, and per running unit the cores its process tree
    used since the last line and its resident memory (Linux /proc; load only elsewhere). The 64-core proof is made
    from these per-unit profiles (status/w2-region.md)."""
    linux = Path("/proc/stat").exists()

    def host():
        parts = [float(v) for v in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
        return sum(parts), parts[3] + parts[4]
    last, before, then = (host() if linux else None), {}, time.time()
    with path.open("a") as out:
        while not stop.wait(every):
            now_t = time.time()
            row = {"t": round(now_t, 1), "load": round(os.getloadavg()[0], 2), "cores": os.cpu_count()}
            if linux:
                current = host()
                total, idle = current[0] - last[0], current[1] - last[1]
                row["busy"] = round(1 - idle / total, 4) if total else None
                last = current
                trees = _trees({pid: entry[0] for pid, entry in list(running.items())})
                row["units"] = {uid: {"cores": round((cpu - before.get(uid, cpu)) / (now_t - then), 2), "mb": round(mb)}
                                for uid, (cpu, mb) in trees.items()}
                before = {uid: cpu for uid, (cpu, _) in trees.items()}
            then = now_t
            out.write(json.dumps(row) + "\n")
            out.flush()


def build_units(store: Store, name: str, units: list[str], parallel: int, margin: float = MARGIN, log=print) -> list[dict]:
    """Builds units in processes of their own, `parallel` at once; returns per unit wall and CPU seconds, peak
    memory and exit code. Logs and a host CPU trace go to $TURKU3D_WORK/region/NAME/."""
    work = Path(os.environ.get("TURKU3D_WORK", store.root.parent)) / "region" / name
    work.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    queue, running, results = list(units), {}, []
    sampler = threading.Thread(target=_cpu_sampler, args=(work / "cpu.jsonl", stop, running), daemon=True)
    sampler.start()
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

def assemble(store: Store, name: str, log=print, place: bool = True) -> dict:
    """Writes the region index `locations/NAME.json` and its chunk objects from the built units' manifests; with
    `place`, the start and focus come from the street and ground at the region's centre (sources are read)."""
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
    # A player starts on the street nearest the region's centre, not at its home unit's middle.
    start = home.get("start")
    try:
        if not place:
            raise LookupError("not asked")
        from .collision import street_start
        from .sources import Sources

        sources = Sources(log=lambda *_: None)
        start = street_start(Area(*plan["centre"], 150.0), sources, log) or start
        ground = float(sources.terrain.heights(*plan["centre"])[0])
        if math.isfinite(ground):
            index["focus"] = [round(float(v), 3) for v in to_engine(*plan["centre"], ground)]
    except Exception as error:  # noqa: BLE001 - the home unit's start is the fallback
        if place:
            log(f"  start at the centre failed ({type(error).__name__}: {error}): the home unit's is used")
    if start:
        index["start"] = start
    store.put_location(index)
    log(f"region {name} r{index['revision']}: {len(manifests)} units, {len(index_chunks)} chunks, {len(rows)} entities, "
        f"{len(collision)} collision cells, {len(roads)} road tiles; seams {index['seams']['equal']} equal, "
        f"{index['seams']['differ']} differ, {len(adopted)} adopted")
    for border, row in sorted(seams.items()):
        if row["differ"]:
            log(f"  seam {border}: {row['equal']} equal, {row['differ']} differ: {', '.join(row['ids'][:6])}")
    return index


# ------------------------------------------------------------------------------------------------- border views

def border_views(store: Store, name: str, per_border: int = 2, back: float = 30.0) -> dict:
    """A capture view set (`turku-capture-views/1`) that looks across the borders between built units: on each
    border, the streets that cross it (OpenStreetMap, larger streets first, crossings at least 60 m apart), a camera
    at eye height `back` metres before the crossing looking along the street over the border."""
    import shapely

    from .collision import DRIVABLE
    from .sources import Sources

    plan = read_plan(store, name)
    built = [u for u in all_units(plan or {"rings": []}) if store.location(f"{name}/{u}") is not None]
    sources = Sources()
    kinds = (*DRIVABLE, "pedestrian", "footway", "cycleway")
    views = []
    for uid in built:
        i, j = unit_of(uid)
        for other, edge in ((unit_id(i + 1, j), "east"), (unit_id(i, j + 1), "south")):
            if other not in built:
                continue
            x0, z0, x1, z1 = square(i, j)
            a = (x1, z0) if edge == "east" else (x0, z1)
            b = (x1, z1) if edge == "east" else (x1, z1)
            (ea, na, _), (eb, nb, _) = to_survey(a[0], 0, a[1]), to_survey(b[0], 0, b[1])
            border = shapely.LineString([(ea, na), (eb, nb)])
            rows = sources.osm.features(border.buffer(5).bounds)
            crossings = []
            for rank, highway in enumerate(kinds):
                for line in rows[rows["highway"] == highway].geometry:
                    if line.geom_type != "LineString" or not line.intersects(border):
                        continue
                    hit = line.intersection(border)
                    point = hit if hit.geom_type == "Point" else hit.representative_point()
                    if any(point.distance(c[1]) < 60 for c in crossings):
                        continue
                    crossings.append((rank, point, line))
            for rank, point, line in sorted(crossings, key=lambda c: c[0])[:per_border]:
                along = line.project(point)
                p, q = line.interpolate(max(along - 3, 0)), line.interpolate(min(along + 3, line.length))
                d = (q.x - p.x, q.y - p.y)
                norm = math.hypot(*d) or 1.0
                d = (d[0] / norm, d[1] / norm)
                e, n = point.x - back * d[0], point.y - back * d[1]
                ground = float(sources.terrain.heights(e, n)[0])
                views.append({"id": f"{name}-{uid}-{edge}-{len(views):02d}", "group": f"{uid}|{other}", "aim": "street",
                              "pose": {"E": round(e, 2), "N": round(n, 2), "H": round(ground + 1.7, 2),
                                       "yaw": round(math.degrees(math.atan2(d[0], d[1])) % 360, 1), "pitch": -3.0, "roll": 0.0, "fov": 65.0}})
    return {"schema": "turku-capture-views/1", "scene": None, "quality": "medium", "time": "2026-06-15T14:00:00+03:00",
            "weather": "clear", "viewport": [960, 720], "renderScale": 1,
            "note": f"Region {name}: streets crossing the borders between its built units, seen from {back:g} m before the border "
                    "(pipeline/turku3d/world/region.py border_views).", "views": views}


# ------------------------------------------------------------------------------------------- the 64-core proof

def unit_profiles(store: Store, name: str, jobs: int, slots: int) -> list[dict]:
    """Each built unit as its stages: CPU seconds (from the per-unit samples of `build_units`, or wall seconds where
    no sample exists), the most cores the stage can use (compile: `jobs`, lidar: `slots` workers of ~4 threads,
    everything else one process) and its peak memory. Wall time between stages is a serial stage `rest`."""
    from datetime import datetime

    work = Path(os.environ.get("TURKU3D_WORK", store.root.parent)) / "region" / name
    samples = [json.loads(line) for line in (work / "cpu.jsonl").read_text().splitlines()] if (work / "cpu.jsonl").exists() else []
    ends = {}
    if (work / "units.jsonl").exists():
        for line in (work / "units.jsonl").read_text().splitlines():
            row = json.loads(line)
            if row["exit"] == 0:
                ends[row["unit"]] = row
    profiles = []
    for uid in all_units(read_plan(store, name) or {"rings": []}):
        manifest = store.location(f"{name}/{uid}")
        if manifest is None or "stages" not in manifest.get("timings", {}):
            continue
        run = ends.get(uid)
        stop = datetime.fromisoformat(run["at"].replace("Z", "+00:00")).timestamp() if run else None
        begin = stop - run["wall"] if run else None
        stages, at = [], begin
        for stage, value in manifest["timings"]["stages"].items():
            seconds = float(value["s"])
            cpu = None
            if at is not None:
                inside = [s["units"].get(uid, {}).get("cores", 0.0) for s in samples if "units" in s and at < s["t"] <= at + seconds]
                cpu = sum(inside) * 5.0 if inside else None
            width = jobs if stage == "compile" else 4 * slots if stage == "lidar" else 1
            stages.append({"stage": stage, "wall": seconds, "cpu": cpu if cpu is not None else min(seconds, seconds * width),
                           "width": width, "mb": value.get("peakMb", 0)})
            if at is not None:
                at += seconds
        rest = manifest["timings"]["totalMs"] / 1000 - sum(s["wall"] for s in stages)
        if rest > 1:
            stages.append({"stage": "rest", "wall": rest, "cpu": rest, "width": 1, "mb": manifest["timings"].get("peakMb", 0)})
        profiles.append({"unit": uid, "stages": stages, "peakMb": manifest["timings"].get("peakMb", 0),
                         "cpu": run["cpu"] if run else sum(s["cpu"] for s in stages), "wall": run["wall"] if run else None})
    return profiles


def simulate(profiles: list[dict], cores: int, memory_gb: float, speed: float, hours: float = 1.0, step: float = 1.0) -> dict:
    """Packs copies of the measured units onto a machine for `hours`: a unit starts when the memory of the running
    units' peaks leaves room for its peak; at every step the cores are shared out (each stage takes at most its
    width, the rest goes to others); a stage's CPU seconds shrink by `speed` (target core against the measuring
    host's). Returns the utilisation over time and the units finished."""
    budget = memory_gb * 1024 * 0.85
    queue = [p for _ in range(1000) for p in profiles]
    running, done, trace, t = [], 0, [], 0.0
    horizon = hours * 3600
    while t < horizon:
        while queue and sum(r["peakMb"] for r in running) + queue[0]["peakMb"] <= budget:
            p = queue.pop(0)
            running.append({"peakMb": p["peakMb"], "stages": [dict(s, left=s["cpu"] / speed) for s in p["stages"]]})
        wants = [min(r["stages"][0]["width"], r["stages"][0]["left"] / step) for r in running]
        free, give = float(cores), [0.0] * len(running)
        # Water filling: everyone gets an equal share up to what its stage can use.
        while free > 1e-9:
            hungry = [k for k, w in enumerate(wants) if give[k] < w - 1e-9]
            if not hungry:
                break
            share = free / len(hungry)
            for k in hungry:
                extra = min(share, wants[k] - give[k])
                give[k] += extra
                free -= extra
        trace.append(round(sum(give) / cores, 4))
        for r, g in zip(running, give):
            r["stages"][0]["left"] -= g * step
            while r["stages"] and r["stages"][0]["left"] <= 1e-9:
                r["stages"].pop(0)
        finished = [r for r in running if not r["stages"]]
        done += len(finished)
        running = [r for r in running if r["stages"]]
        t += step
    minutes = [round(sum(trace[i:i + 60]) / len(trace[i:i + 60]), 3) for i in range(0, len(trace), 60)]
    return {"cores": cores, "memoryGb": memory_gb, "speed": speed, "utilisation": round(sum(trace) / len(trace), 4),
            "byMinute": minutes, "unitsDone": done, "km2": done * (UNIT / 1000) ** 2, "concurrent": len(running)}
