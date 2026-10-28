"""Real-input cold/warm first/full stage timings and optional cProfile evidence.

Run with host_slots.py build and an agent-owned work root. Cold means empty
generated caches, not flushed OS caches; raw city inputs are always read-only.
"""

import argparse
import cProfile
import json
import platform
import pstats
import shutil
import time
from pathlib import Path

from turku3d.coarse.build import build
from turku3d.coarse.sources import Sources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--tiles", nargs="+", default=["t0_1_2"])
    parser.add_argument("--profile", action="store_true")
    parser.add_argument(
        "--phases", nargs="+", choices=("first", "full", "structural"), default=["first", "full"]
    )
    parser.add_argument(
        "--assets", type=Path, help="reuse prepared shared texture assets, as the live service does"
    )
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=False)
    report = dict(platform=platform.platform(), profiled=args.profile, runs=[])
    sources = Sources()
    for tile in args.tiles:
        for phase in args.phases:
            out = args.root / tile / phase / tile
            if args.assets:
                out.parent.mkdir(parents=True, exist_ok=True)
                for pattern in ("shared", ".surface-library*", ".texture-cache"):
                    for asset in args.assets.glob(pattern):
                        if asset.is_dir():
                            shutil.copytree(asset, out.parent / asset.name, dirs_exist_ok=True)
            for warmth in ("cold", "warm"):
                name = f"{tile}-{phase}-{warmth}"
                profiler = cProfile.Profile() if args.profile else None
                started = time.perf_counter()
                if profiler:
                    profiler.enable()
                manifest = build(
                    tile,
                    out,
                    first_pass=phase != "full",
                    sources=sources,
                    collision_only=phase == "structural",
                )
                if profiler:
                    profiler.disable()
                    profiler.dump_stats(str(args.root / (name + ".prof")))
                    with (args.root / (name + ".txt")).open("w") as stream:
                        pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats(
                            "cumulative"
                        ).print_stats(65)
                row = dict(
                    tile=tile,
                    phase=phase,
                    warmth=warmth,
                    wall_seconds=round(time.perf_counter() - started, 3),
                    seconds=manifest["seconds"],
                    code=manifest["code"],
                )
                report["runs"].append(row)
                (args.root / "timings.json").write_text(json.dumps(report, indent=2) + "\n")
                print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
