"""Manual real-tile first/full build timings; run through host_slots.py build, outside the gate."""

import argparse
import json
import shutil
from pathlib import Path

from turku3d import config as C
from turku3d.coarse.build import build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tiles", nargs="+", default=["t0_2_-2", "t0_5_-3", "t0_-1_1", "t0_-2_2"])
    parser.add_argument("--root", type=Path, default=C.work_root() / "reports/draft-speed")
    args = parser.parse_args()
    summary = {}
    args.root.mkdir(parents=True, exist_ok=True)
    for tile in args.tiles:
        summary[tile] = {}
        for phase in ("first", "full"):
            out = args.root / "after" / tile
            manifest = build(tile, out, first_pass=phase == "first")
            summary[tile][phase] = manifest["seconds"]
            snapshot = args.root / phase / tile
            shutil.copytree(
                out, snapshot, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".stage")
            )
            (args.root / "timings.json").write_text(json.dumps(summary, indent=2) + "\n")
            print(
                json.dumps({"tile": tile, "phase": phase, "seconds": manifest["seconds"]}),
                flush=True,
            )
    shared = args.root / "after/shared"
    if shared.exists():
        shutil.copytree(shared, args.root / "full/shared", dirs_exist_ok=True)


if __name__ == "__main__":
    main()
