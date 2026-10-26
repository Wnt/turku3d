#!/usr/bin/env python3
"""Select browser specs from changed paths using the gate's subsystem map."""

import argparse
import json
import re
import tomllib
from pathlib import Path

from gate_lib import changes, matches, related

ROOT = Path(__file__).resolve().parents[2]
TAG = re.compile(r"@[a-z][a-z0-9-]*")


def select(paths, root=ROOT):
    config = tomllib.loads((root / "scripts/dev/gate.toml").read_text())
    stage = next(row for row in config["stages"] if row["name"] == "game-e2e")
    touched = [path for path in paths if matches(path, stage["triggers"])]
    if not touched:
        return {"game": [], "tile": [], "tags": []}
    if all(
        path.startswith("game/tests/e2e/") or path.startswith("game/tests/e2e-tile/")
        for path in touched
    ):
        return {
            "game": sorted(
                path.removeprefix("game/")
                for path in touched
                if path.startswith("game/tests/e2e/") and path.endswith(".spec.ts")
            ),
            "tile": sorted(
                path.removeprefix("game/")
                for path in touched
                if path.startswith("game/tests/e2e-tile/") and path.endswith(".spec.ts")
            ),
            "tags": [],
        }
    tags = related(stage, touched, "tags")
    selected = {}
    for kind, directory in (("game", "e2e"), ("tile", "e2e-tile")):
        specs = sorted((root / "game/tests" / directory).glob("*.spec.ts"))
        direct = {root / path for path in touched if path.startswith(f"game/tests/{directory}/")}
        if tags is None:
            selected[kind] = [str(path.relative_to(root / "game")) for path in specs]
            continue
        if kind == "tile" and not set(tags).intersection(stage["tile_tags"]):
            selected[kind] = []
            continue
        selected[kind] = [
            str(path.relative_to(root / "game"))
            for path in specs
            if path in direct or set(TAG.findall(path.read_text())).intersection(tags)
        ]
    return {**selected, "tags": tags or []}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="changed paths relative to the repository")
    parser.add_argument("--base", default="main", help="git base when paths are omitted")
    args = parser.parse_args()
    print(json.dumps(select(args.paths or changes(ROOT, args.base)), indent=2))


if __name__ == "__main__":
    main()
