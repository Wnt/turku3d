"""Exercise the real facade background lane in an isolated local work directory.

This uses actual reference acquisition, model inference and optional Astra calls.
The builder stub only records tile refresh requests; it does not build a tile.
All crops, descriptions and events stay under --output.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from pathlib import Path


class Builder:
    def __init__(self, tiles, motion):
        self.lock = threading.Lock()
        self.servable = set(tiles)
        self.motion = motion
        self.draining = False
        self.version = 0
        self.requests = []

    def request(self, tile, distance):
        self.requests.append((tile, distance))


def run(args):
    # The worker inherits this sandbox subdirectory, including its facade store.
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ["TURKU3D_WORK"] = str((args.output / "work").resolve())

    from turku3d import config
    from turku3d.coarse.facades import CALLS_PER_HOUR, DescribeQueue
    from turku3d.coarse.service import rank

    from . import facade_fast as fast
    from .streetview.facades import load_facades

    root = args.output / "work/coarse"
    motion = (
        config.OFFICE_E - config.ORIGIN_E,
        config.ORIGIN_N - config.OFFICE_N,
        0.0,
        0.0,
    )
    chosen = []
    for wall in load_facades(args.inventory):
        tile = fast.tile_of(wall)
        if rank(tile, 0, motion) is None or rank(tile, 0, motion)[0] > 1:
            continue
        if any(x.props["building_id"] == wall.props["building_id"] for x in chosen):
            continue
        view, _ = fast.best_view(wall, args.refs)
        if view:
            chosen.append(wall)
        if len(chosen) == args.buildings:
            break
    if len(chosen) < args.buildings:
        raise ValueError(f"only {len(chosen)} eligible ring/cone buildings have references")
    tiles = {}
    for wall in chosen:
        tiles.setdefault(fast.tile_of(wall), []).append(wall.to_feature())
    for tile, features in tiles.items():
        target = root / tile / "facade-candidates.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(dict(type="FeatureCollection", features=features)))
    telemetry = config.work_root() / "telemetry"
    telemetry.mkdir(parents=True, exist_ok=True)
    (telemetry / "priority.json").write_text(
        json.dumps(
            dict(
                buildings=[
                    dict(
                        building_id=wall.props["building_id"],
                        score=len(chosen) - i,
                        walls=[dict(facade_id=wall.id, score=len(chosen) - i)],
                    )
                    for i, wall in enumerate(chosen)
                ]
            )
        )
    )
    builder = Builder(tiles, motion)
    queue = DescribeQueue(root, builder)
    try:
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            with queue.lock:
                events = list(queue.events)
            hybrid = {
                r["building"]
                for r in events
                if r["engine"] == "hybrid" and r["state"] == "upgraded"
            }
            calls = sum(r.get("calls") or 0 for r in events if r["engine"] == "astra")
            astra_upgraded = sum(
                r["engine"] == "astra" and r["state"] == "upgraded" for r in events
            )
            if (
                len(hybrid) >= args.buildings
                and calls >= args.astra_calls
                and astra_upgraded >= args.astra_calls
            ):
                break
            time.sleep(1)
    finally:
        queue.stop()
        queue.thread.join(timeout=5)
    with queue.lock:
        events = list(queue.events)
    hybrid = {r["building"] for r in events if r["engine"] == "hybrid" and r["state"] == "upgraded"}
    first = min((r["started_at"] for r in events), default=time.time())
    last = max((r["finished_at"] for r in events), default=first)
    elapsed = max(last - first, 0.001)
    calls = sum(r.get("calls") or 0 for r in events if r["engine"] == "astra")
    result = dict(
        requested_buildings=args.buildings,
        hybrid_buildings=len(hybrid),
        observation_seconds=round(elapsed, 3),
        actual_hybrid_buildings_per_minute=60 * len(hybrid) / elapsed,
        actual_astra_calls=calls,
        astra_upgrades=sum(r["engine"] == "astra" and r["state"] == "upgraded" for r in events),
        actual_calls_last_hour=sum(
            r.get("calls") or 0
            for r in events
            if r["engine"] == "astra" and r["finished_at"] > last - 3600
        ),
        configured_call_cap_per_hour=CALLS_PER_HOUR,
        unknown_call_jobs=sum(r.get("calls", 0) is None for r in events),
        states=[(r["engine"], r["state"]) for r in events],
        refresh_requests=len(builder.requests),
    )
    (args.output / "aggregate.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)
    if (
        len(hybrid) < args.buildings
        or result["actual_astra_calls"] < args.astra_calls
        or result["astra_upgrades"] < args.astra_calls
    ):
        raise TimeoutError("queue did not complete its requested hybrid/Astra observation")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--refs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--buildings", type=int, default=5)
    parser.add_argument("--astra-calls", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args()
    args.inventory, args.refs, args.output = (
        x.resolve() for x in (args.inventory, args.refs, args.output)
    )
    run(args)


if __name__ == "__main__":
    main()
