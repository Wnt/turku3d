"""python -m turku3d.world: build a location, rebuild entities, watch descriptions (docs/design/world-pipeline.md)."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from .api import Area, Stub
from .store import Store, description_roots, read_description


def location_name(args) -> tuple[str, Area]:
    from .driver import area_of_views

    if args.area:
        area = area_of_views(args.area)
        if args.radius:
            area = area.grown(args.radius)
        return args.name or args.area, area
    e, n = (float(v) for v in args.at.split(","))
    area = Area(e, n, args.radius or 150.0)
    return args.name or f"at-{round(e)}-{round(n)}-r{round(area.radius)}", area


def watch(name: str, store: Store) -> None:
    """Rebuilds entities whose stored description changed; restarts itself when the pipeline's code changes."""
    from .driver import refresh

    code = Path(__file__).parent

    def code_state():
        return max(p.stat().st_mtime for p in code.rglob("*.py"))

    def description_state(ids):
        state = {}
        for root in description_roots():
            for entity in ids:
                path = root / f"{entity}.json"
                if path.exists():
                    state[entity] = max(state.get(entity, 0.0), path.stat().st_mtime)
        return state

    refresh(name, store)
    ids = [row["id"] for row in store.location(name)["entities"]]
    code_seen, seen = code_state(), description_state(ids)
    print(f"watching {len(ids)} entities of {name}; descriptions in {', '.join(map(str, description_roots()))}", flush=True)
    while True:
        time.sleep(0.2)
        if code_state() != code_seen:
            print("code changed: restarting", flush=True)
            os.execv(sys.executable, [sys.executable, "-m", "turku3d.world", *sys.argv[1:]])
        state = description_state(ids)
        changed = [entity for entity in set(state) | set(seen) if state.get(entity) != seen.get(entity)]
        if changed:
            seen = state
            try:
                refresh(name, store, changed)
            except Exception as error:  # a half-written or invalid description: report and keep watching
                print(f"  {type(error).__name__}: {error}", flush=True)


def warm_order(tiles: list[str], points: list[tuple[float, float]], reach: float) -> list[str]:
    """The tiles a location at each point needs (its background of `reach` metres) first, point by point, then
    the others as they came."""
    from .sources import lidar

    known, first = set(tiles), []
    for e, n in points:
        first += [name for name in lidar.names((e - reach, n - reach, e + reach, n + reach), lidar.RING) if name in known]
    first = list(dict.fromkeys(first))
    return first + [name for name in tiles if name not in set(first)]


def warm_tiles(args) -> None:
    """Makes the lidar products of the survey's tiles in rounds: first the tiles that locations at the points of
    `--first` need, then nearest first to `--at`, leaving one of the machine's lidar slots to builds someone waits
    for. A tile that has everything costs nothing, so the command can be stopped and started again; a tile whose
    process fails is logged and left to the next run. It waits while a disk is below `--keep-free` or the machine
    has less than `--keep-memory` available."""
    import shutil

    import numpy as np

    from .sources import lidar

    def say(text: str) -> None:
        print(f"{time.strftime('%H:%M:%SZ', time.gmtime())} {text}", flush=True)

    def room() -> list[str]:
        short = []
        for label, path in (("data", lidar.sources_root()), ("products", lidar.products_root())):
            path.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(path).free / 2**30
            if free < args.keep_free:
                short.append(f"{free:.0f} GiB free on the {label} disk, floor {args.keep_free:g}")
        try:
            available = int(re.search(r"MemAvailable:\s+(\d+)", Path("/proc/meminfo").read_text()).group(1)) / 2**20
            if available < args.keep_memory:
                short.append(f"{available:.0f} GiB of memory available, floor {args.keep_memory:g}")
        except OSError:  # not Linux: no guard
            pass
        return short

    at = tuple(float(v) for v in args.at.split(",")) if args.at else (None, None)
    tiles = lidar.city_tiles(*at)
    if args.reach is not None:
        if not args.at:
            raise SystemExit("--reach needs --at")
        tiles = [name for name in tiles if np.hypot(*(np.array(lidar.tile_corner(name)) + lidar.TILE / 2 - at)) <= args.reach]
    points = []
    for path in args.first or []:
        for line in Path(path).read_text().splitlines():
            numbers = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", line) if float(v) > 1e6]
            if len(numbers) >= 2:
                points.append((numbers[0], numbers[1]))
    tiles = warm_order(tiles, points, args.first_reach)
    known = set(tiles)
    jobs = args.jobs or max(1, lidar.slots() - 1)
    phases = lidar.missing(tiles)
    lacking = set(phases[0]) | set(phases[1])
    say(f"{len(tiles)} tiles, {len(lacking & known)} lack a product ({len(phases[0])} to reduce, {len(phases[1])} to search), "
        f"{len(points)} points first, {jobs} worker(s) of {lidar.slots()} slots, rounds of {args.batch}; products in {lidar.products_root()}")
    if args.plan:
        return
    started, made, checked, failed = time.perf_counter(), 0, 0, []
    for first in range(0, len(tiles), args.batch):
        batch = tiles[first:first + args.batch]
        checked += len(batch)
        if not lacking.intersection(batch):
            continue
        while short := room():
            say("waiting 5 min: " + "; ".join(short))
            time.sleep(300)
        near = sorted({name for tile in batch for name in lidar.neighbours(tile) if name in known} - set(batch))
        done = lidar.make(batch, lambda line: print(line, flush=True), jobs, spare=1, also=near, keep_going=True)
        made += len(lacking.intersection(batch)) - len(set(done["failed"]) & set(batch))
        failed += done["failed"]
        hours = (time.perf_counter() - started) / 3600
        left = len(lacking.intersection(tiles[first + args.batch:]))
        say(f"{checked} of {len(tiles)} tiles checked, {made} made in {hours * 60:.1f} min ({made / hours:.0f} an hour), {left} left: "
            f"about {left / max(made / hours, 1e-6):.1f} h; largest worker {done['peakMb']} MB, {len(failed)} failed")
    say(f"pass complete: {made} tiles made in {(time.perf_counter() - started) / 3600:.2f} h"
        + (f"; FAILED, made on the next run: {' '.join(sorted(set(failed)))}" if failed else ""))


def main() -> None:
    if sys.argv[1:2] == ["check-readiness"]:  # its own options: docs/design/render-readiness.md, section 9
        from .readiness.__main__ import main as check_readiness

        raise SystemExit(check_readiness(sys.argv[2:]))
    parser = argparse.ArgumentParser(prog="python -m turku3d.world", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check-readiness", help="report how a built location obeys the render-readiness rules (--help)")
    build = commands.add_parser("build", help="discover and compile a location; prints per-stage timings")
    where = build.add_mutually_exclusive_group(required=True)
    where.add_argument("--area", help="a pilot area: the query comes from its reference views (qa/views/views.jsonl)")
    where.add_argument("--at", metavar="E,N", help="any point in EPSG:3877")
    build.add_argument("--radius", type=float, help="metres of full detail around the point (default 150)")
    build.add_argument("--background", type=float, default=400.0, help="metres to which cheap surroundings are built")
    build.add_argument("--name", help="location name (default: the area, or at-E-N-rR)")
    for name, text in (("entity", "recompile entities of a built location from cached source facts"),
                       ("refresh", "recompile every entity of a built location whose description or generator changed"),
                       ("watch", "refresh, then rebuild entities as their stored descriptions change")):
        command = commands.add_parser(name, help=text)
        if name == "entity":
            command.add_argument("ids", nargs="+", metavar="ID")
        command.add_argument("--location", required=True)
    roads = commands.add_parser("roads", help="give a built location its street graphs (manifest `roads`) without building it again")
    roads.add_argument("--location", required=True)
    warm = commands.add_parser("warm", help="make the lidar products of survey tiles ahead, nearest first, so that later builds there are not cold")
    warm.add_argument("--at", metavar="E,N", help="the point to start from (default: the middle of the survey)")
    warm.add_argument("--reach", type=float, help="metres from the point to which tiles are made (default: the whole survey)")
    warm.add_argument("--jobs", type=int, help="worker processes (default: all but one of the machine's lidar slots)")
    warm.add_argument("--batch", type=int, default=12, help="tiles per round; each round is complete when it ends")
    warm.add_argument("--first", action="append", metavar="FILE", help="a file of points, one line each with E and N: the tiles locations there need are made first")
    warm.add_argument("--first-reach", type=float, default=400.0, help="metres around each point of --first (a location's background)")
    warm.add_argument("--keep-free", type=float, default=25.0, help="GiB that stay free on the data disk and the products disk; the batch waits below that")
    warm.add_argument("--keep-memory", type=float, default=10.0, help="GiB of available memory below which no new round starts")
    warm.add_argument("--plan", action="store_true", help="say what is missing and stop")
    describe = commands.add_parser("describe", help="print the effective description of an entity of a built location")
    describe.add_argument("id")
    commands.add_parser("gc", help="delete objects that no location manifest of this store references")
    args = parser.parse_args()
    store = Store()
    if args.command == "build":
        from .driver import build as run

        name, area = location_name(args)
        with store.building(name):
            run(name, area, args.background, store)
    elif args.command in ("entity", "refresh"):
        from .driver import refresh

        with store.building(args.location):
            refresh(args.location, store, getattr(args, "ids", None))
    elif args.command == "roads":
        from .roads import location_roads
        from .sources import Sources

        with store.building(args.location):
            manifest = store.location(args.location)
            if manifest is None:
                raise SystemExit(f"no location {args.location} in {store.root}")
            manifest["roads"] = location_roads(store, manifest["entities"], Sources())
            store.put_location(manifest)
    elif args.command == "watch":
        watch(args.location, store)
    elif args.command == "warm":
        warm_tiles(args)
    elif args.command == "describe":
        from .kinds import KINDS

        saved = store.facts(args.id)
        stub = Stub(saved["id"], tuple(saved["box"]), saved["sources"], saved["facts"])
        json.dump(KINDS[stub.kind].describe(stub, read_description(stub.id)), sys.stdout, indent=1, ensure_ascii=False)
    elif args.command == "gc":
        # A running build has stored meshes that no manifest names yet: collecting now would take them from under it.
        if running := store.builds_running():
            raise SystemExit("gc refused: " + ", ".join(f"{m['name']} is being built (pid {m['pid']} on {m['host']})" for m in running))
        keep, dropped, freed = store.referenced(), set(), 0
        for path in (store.root / "objects").glob("*/*"):
            url = f"objects/{path.parent.name}/{path.name}"
            if path.suffix in (".glb",) and url not in keep:
                freed += path.stat().st_size
                path.unlink()
                dropped.add(path.name)
        # A build whose mesh is gone must compile again when its key comes back.
        stale = 0
        for path in (store.root / "builds").glob("*.json"):
            if dropped.intersection(re.findall(r"[0-9a-f]{64}\.glb", path.read_text())):
                path.unlink()
                stale += 1
        print(f"dropped {len(dropped)} unreferenced meshes, {freed / 1e6:.1f} MB, and {stale} builds that pointed at them")


if __name__ == "__main__":
    main()
