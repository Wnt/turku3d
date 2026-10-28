"""Manual real-data build/count evidence; never part of the fast test gate.

python -m turku3d.coarse.sanity before|after [--baseline-ref COMMIT] [--region aura|hame]
"""

import argparse
import importlib
import json
import subprocess
from collections import Counter

from turku3d import config as C
from turku3d.coarse import bridges, fences, tunnels
from turku3d.coarse.build import build, grid_height, grid_mesh
from turku3d.coarse.inputs import gather


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["before", "after"])
    parser.add_argument("--baseline-ref")
    parser.add_argument("--counts-only", action="store_true")
    parser.add_argument("--region", choices=["aura", "hame"], default="aura")
    args = parser.parse_args()
    if args.baseline_ref:
        for module in (bridges, fences, tunnels, importlib.import_module("turku3d.coarse.build")):
            relative = f"pipeline/turku3d/coarse/{module.__name__.rsplit('.', 1)[-1]}.py"
            source = subprocess.check_output(
                ["git", "show", f"{args.baseline_ref}:{relative}"], text=True
            )
            exec(compile(source, module.__file__, "exec"), module.__dict__)
    root = C.work_root() / "reports/bridge-sanity" / args.phase
    root.mkdir(parents=True, exist_ok=True)
    summary_file = root / f"decks-{args.region}.json"
    summary = json.loads(summary_file.read_text()) if summary_file.exists() else {}
    pairs = (
        ((i, j) for i in range(-3, 1) for j in range(1, 4))
        if args.region == "aura"
        else ((i, j) for i in range(4, 7) for j in range(-4, -1))
    )
    for i, j in pairs:
        name = f"t0_{i}_{j}"
        previous_error = summary.get(name, {}).get("error")
        tile = C.VisualTile.parse(name)
        inputs = gather(tile, root)
        pos, _ = grid_mesh(tile, inputs.heights)
        decks = bridges.produce(tile, pos, inputs)
        records = inputs.barriers
        barrier_counts = {}
        for kind in sorted({t["barrier"] for _, t, _ in records}):
            subset = [r for r in records if r[1]["barrier"] == kind]
            kwargs = {} if args.baseline_ref else {"water": inputs.water}
            batch = fences.place(
                tile,
                subset,
                inputs.streets,
                lambda x, z, tile=tile, pos=pos: grid_height(tile, pos, x, z),
                **kwargs,
            )
            barrier_counts[kind] = {
                "source_records": len(subset),
                "segments": len(batch),
                "metres": round(sum(s.length for s in batch), 2),
            }
        summary[name] = dict(
            baseline_ref=args.baseline_ref,
            decks=[
                dict(
                    id=d.way["id"],
                    name=d.way["tags"].get("name"),
                    members=d.way.get("members", [d.way["id"]]),
                    span=d.way["line"].length,
                    length=d.line.length,
                    heights=[float(d.heights.min()), float(d.heights.max())],
                    under=[w["id"] for w in d.way["under"]],
                    landing=d.way.get("landing", False),
                )
                for d in decks
            ],
            barriers=barrier_counts,
            source_bridge_ways=len({m for d in decks for m in d.way.get("members", [d.way["id"]])}),
            deck_instances=len(decks),
            crossings=len([d for d in decks if not d.way.get("landing", False)]),
            landing_instances=len([d for d in decks if d.way.get("landing", False)]),
            material_counts=dict(Counter(m.material for d in decks for m in d.meshes)),
        )
        try:
            if not args.counts_only and (
                not args.baseline_ref or not (root / name / "manifest.json").exists()
            ):
                build(name, root / name, inputs=inputs)
        except ValueError as exc:
            summary[name]["error"] = str(exc)
        if args.counts_only and previous_error:
            summary[name]["error"] = previous_error
        summary_file.write_text(json.dumps(summary, indent=2) + "\n")
        status = "counted" if args.counts_only else "built"
        print(name, summary[name].get("error", status), flush=True)


if __name__ == "__main__":
    main()
