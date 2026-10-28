"""Manual before/after real-data bridge QA. Outputs remain in the sandbox."""

import argparse
import json
import subprocess
import sys
import types

import numpy as np

from turku3d import config as C
from turku3d.coarse import bridges
from turku3d.coarse.build import build, grid_mesh
from turku3d.coarse.inputs import gather
from turku3d.coarse.sources import MissingInputs

# Covers complete named crossings and all incidental bridge instances in these tiles.
TILES = [
    "t0_-2_2",
    "t0_-3_2",
    "t0_-1_1",
    "t0_0_1",
    "t0_1_0",
    "t0_1_1",
    "t0_2_-1",
    "t0_6_-2",
    "t0_5_-2",
    "t0_5_-3",
    "t0_3_-5",
    "t0_3_-6",
    "t0_-2_3",
]


def deck_error(deck):
    points = np.asarray(deck.way.get("survey", {}).get("validation_points", []))
    if not len(points):
        return {"status": "unmeasured"}
    error = np.abs(deck.height_at(points[:, [0, 2]]) - points[:, 1])
    return {
        "count": len(error),
        "median_m": float(np.median(error)),
        "p90_m": float(np.quantile(error, 0.9)),
        "max_m": float(error.max()),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["before", "after"])
    parser.add_argument("--baseline-ref", default="d78eb3c7bc45")
    parser.add_argument("--tiles", nargs="+", default=TILES)
    args = parser.parse_args()
    root = C.work_root() / "reports/bridge-quality" / args.phase
    root.mkdir(parents=True, exist_ok=True)
    if args.phase == "before":
        name = "turku3d.coarse._baseline_bridges"
        module = types.ModuleType(name)
        module.__package__ = "turku3d.coarse"
        sys.modules[name] = module
        source = subprocess.check_output(
            ["git", "show", f"{args.baseline_ref}:pipeline/turku3d/coarse/bridges.py"], text=True
        )
        exec(compile(source, name, "exec"), module.__dict__)
        for key in ("produce", "visual", "collision"):
            setattr(bridges, key, getattr(module, key))
    inventory = root / "inventory.json"
    summary = json.loads(inventory.read_text()) if inventory.exists() else {}
    failed = False
    for name in args.tiles:
        try:
            tile = C.VisualTile.parse(name)
            inputs = gather(tile, root.parent)
            pos, _ = grid_mesh(tile, inputs.heights)
            decks = bridges.produce(tile, pos, inputs)
            summary[name] = [
                dict(
                    id=d.way["id"],
                    name=d.way["tags"].get("name"),
                    centre=list(d.way["line"].centroid.coords[0]),
                    axis=list(d.way["line"].coords),
                    width=d.way["width"],
                    length=d.way["line"].length,
                    members=d.way.get("members", [d.way["id"]]),
                    survey={
                        k: v for k, v in d.way.get("survey", {}).items() if k != "validation_points"
                    },
                    deck_error=deck_error(d),
                    tags=d.way["tags"],
                    lamps=len(d.way.get("lamps", [])),
                    triangles=sum(len(m.indices) for m in d.meshes),
                    meshes=len(d.meshes),
                    height=float(np.median(d.heights)),
                    extent=list(d.way.get("outline", d.way["line"]).bounds),
                )
                for d in decks
            ]
            build(name, root / name, inputs=inputs)
            print(name, "built", len(decks), flush=True)
        except (ValueError, FileNotFoundError, MissingInputs) as exc:
            failed = True
            summary[name] = {"error": str(exc)}
            print(name, str(exc), flush=True)
        inventory.write_text(json.dumps(summary, indent=2) + "\n")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
