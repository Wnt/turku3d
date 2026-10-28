"""Manual six-tile texture build evidence; outside the gate and raw inputs stay read-only."""

import argparse
import json
import subprocess
from pathlib import Path

from turku3d import config as C

TILES = ("t0_-1_1", "t0_-2_2", "t0_-2_3", "t0_5_-3", "t0_0_0", "t0_0_1")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("before", "after"))
    parser.add_argument("--baseline-ref")
    parser.add_argument("--tiles", nargs="+", default=TILES)
    args = parser.parse_args()
    root = C.work_root() / "reports/texture-sanity" / args.phase
    root.mkdir(parents=True, exist_ok=True)
    if args.baseline_ref:
        import turku3d.coarse

        target = root / ".baseline"
        target.mkdir(exist_ok=True)
        paths = subprocess.check_output(
            ["git", "ls-tree", "-r", "--name-only", args.baseline_ref, "pipeline/turku3d/coarse"],
            text=True,
        ).splitlines()
        for name in paths:
            if name.endswith(".py"):
                (target / Path(name).name).write_bytes(
                    subprocess.check_output(["git", "show", f"{args.baseline_ref}:{name}"])
                )
        turku3d.coarse.__path__ = [str(target)]
    from turku3d.coarse.build import build

    summary_path = root / "build-summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    for name in args.tiles:
        manifest = build(name, root / name)
        summary[name] = {k: manifest[k] for k in ("visual", "seconds", "buildings")}
        for key in ("ground_surfaces", "marking_triangles", "kerb_triangles"):
            if key in manifest:
                summary[name][key] = manifest[key]
        summary_path.write_text(json.dumps(summary, indent=2) + "\n")
        print(name, manifest["visual"][name]["brotli_bytes"], flush=True)


if __name__ == "__main__":
    main()
