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


def warm_tiles(args) -> None:
    """Makes the lidar products of the survey's tiles, nearest first, in rounds, leaving one of the machine's lidar
    slots to builds someone waits for. A tile that has everything costs nothing, so the command can be stopped
    and started again."""
    import numpy as np

    from .sources import lidar

    at = tuple(float(v) for v in args.at.split(",")) if args.at else (None, None)
    tiles = lidar.city_tiles(*at)
    if args.reach is not None:
        if not args.at:
            raise SystemExit("--reach needs --at")
        tiles = [name for name in tiles if np.hypot(*(np.array(lidar.tile_corner(name)) + lidar.TILE / 2 - at)) <= args.reach]
    jobs = args.jobs or max(1, lidar.slots() - 1)
    started, made = time.perf_counter(), 0
    print(f"{len(tiles)} tiles, {jobs} worker(s), rounds of {args.batch}", flush=True)
    for first in range(0, len(tiles), args.batch):
        done = lidar.make(tiles[first:first + args.batch], lambda line: print(line, flush=True), jobs, spare=1)
        made += done["made"]
        elapsed = time.perf_counter() - started
        print(f"{time.strftime('%H:%M:%SZ', time.gmtime())} {min(first + args.batch, len(tiles))} of {len(tiles)} tiles checked, {made} made, "
              f"{elapsed / 60:.1f} min, largest worker {done['peakMb']} MB", flush=True)


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
