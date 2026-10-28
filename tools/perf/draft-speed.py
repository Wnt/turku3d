"""Manual real-tile first/full build timings; run through host_slots.py build, outside the gate."""

import argparse
import json
import shutil
import time
import urllib.request
from pathlib import Path

from turku3d import config as C
from turku3d.coarse.build import build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tiles", nargs="+", default=["t0_2_-2", "t0_5_-3", "t0_-1_1", "t0_-2_2"])
    parser.add_argument("--root", type=Path, default=C.work_root() / "reports/draft-speed")
    parser.add_argument(
        "--service",
        type=int,
        help="measure an admitted diagnostic service instead of building directly",
    )
    args = parser.parse_args()
    summary = {}
    args.root.mkdir(parents=True, exist_ok=True)
    for tile in args.tiles:
        if args.service:
            _, i, j = tile.split("_")
            with urllib.request.urlopen(
                f"http://127.0.0.1:{args.service}/coarse/want?x={(int(i) + 0.5) * 250}&z={(int(j) + 0.5) * 250}&vx=0&vz=0"
            ) as response:
                response.read()
        summary[tile] = {}
        for phase in ("first", "full"):
            out = args.root / "after" / tile
            if args.service:
                started = time.perf_counter()
                base = f"http://127.0.0.1:{args.service}/coarse/"
                while True:
                    with urllib.request.urlopen(base + tile + "/manifest.json?d=0") as response:
                        manifest = json.load(response)
                        if (
                            response.status == 200
                            and phase == "first"
                            and manifest.get("pass_kind") == "full"
                        ):
                            raise RuntimeError(
                                f"{tile} is already full; use a fresh diagnostic service root"
                            )
                        if response.status == 200 and manifest.get("pass_kind") == phase:
                            break
                    if time.perf_counter() - started > 900:
                        raise TimeoutError(f"{tile}: {phase} did not finish within 15 minutes")
                    time.sleep(0.25)
                manifest["seconds"]["observed"] = round(time.perf_counter() - started, 3)
                out = args.root / phase / tile
                out.mkdir(parents=True, exist_ok=True)
                entries = [manifest["visual"][tile], *manifest["collision"].values()]
                if manifest.get("roads"):
                    entries.append(manifest["roads"])
                for entry in entries:
                    dest = out / entry["url"]
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with urllib.request.urlopen(base + tile + "/" + entry["url"]) as response:
                        dest.write_bytes(response.read())
                shutil.copyfile(out / manifest["visual"][tile]["url"], out / "vis.glb")
                (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
            else:
                manifest = build(tile, out, first_pass=phase == "first")
            summary[tile][phase] = manifest["seconds"]
            snapshot = args.root / phase / tile
            if out != snapshot:
                shutil.copytree(
                    out, snapshot, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".stage")
                )
            (args.root / "timings.json").write_text(json.dumps(summary, indent=2) + "\n")
            print(
                json.dumps({"tile": tile, "phase": phase, "seconds": manifest["seconds"]}),
                flush=True,
            )
    shared = args.root / ("drive/shared" if args.service else "after/shared")
    if shared.exists():
        shutil.copytree(shared, args.root / "full/shared", dirs_exist_ok=True)


if __name__ == "__main__":
    main()
