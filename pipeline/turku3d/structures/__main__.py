"""Build structures only inside the worker sandbox; never fetch shared inputs."""

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

from turku3d import config as C

from .acceptance import validate
from .build import build
from .inputs import Ground, lidar_paths, load_lidar, office_bounds, read_surveys, survey_sources
from .layer import cached_manifest, sha256, write_layer
from .snapshot import snapshot_ground


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ground-layer", type=Path, default=C.work_root() / "build/layers/ground")
    ap.add_argument("--area", choices=sorted(C.AREAS), default="office-block")
    a = ap.parse_args(argv)
    start = time.monotonic()
    report_dir = C.work_root() / "reports/structures"
    report_dir.mkdir(parents=True, exist_ok=True)
    metrics = {"status": "BLOCKED", "issues": []}
    phase_times = {}
    phase_started = start

    def phase(name):
        nonlocal phase_started
        now = time.monotonic()
        phase_times[name] = round(now - phase_started, 3)
        phase_started = now
        print(f"structures: {name} {phase_times[name]:.3f}s", file=sys.stderr, flush=True)

    try:
        bounds = office_bounds(a.area)
        records = read_surveys(bounds)
        metrics["input_counts"] = dict(Counter(r["code"] for r in records))
        phase("surveys")
        snapshot, evidence = snapshot_ground(a.ground_layer)
        ground = Ground(snapshot)
        phase("ground_snapshot")
        paths = lidar_paths(bounds)
        inputs = {str(p): sha256(p) for p in [*survey_sources(), *ground.paths, *paths]}
        phase("input_hashes")
        out = C.work_root() / "build/layers/structures"
        params = {"area": a.area, "bbox": list(bounds.as_tuple())}
        manifest = cached_manifest(out, inputs, params)
        if manifest is not None:
            metrics = json.loads((out / "metrics.json").read_text())
            metrics["cached"] = True
        else:
            lidar = load_lidar(paths, bounds)
            phase("lidar")
            meshes, metrics = build(records, lidar, ground)
            phase("geometry")
            metrics["ground_input"] = {k: v for k, v in evidence.items() if k != "files"}
            metrics["ground_triangles"] = len(ground.triangles)
            metrics["ground_material_counts"] = dict(Counter(map(int, ground.materials)))
            if meshes:
                physics, physics_issues = validate(
                    meshes, ground, metrics["measurements"], report_dir
                )
                metrics["physics"] = {k: v for k, v in physics.items() if k != "results"}
                metrics["issues"].extend(physics_issues)
                metrics["status"] = "BLOCKED" if metrics["issues"] else "PASS"
                metrics["acceptance"] = (
                    "Measured geometry and real-ground Rapier probes; full failures retained."
                )
                phase("physics")
                manifest, cached = write_layer(
                    meshes, out, inputs=inputs, params=params, metrics=metrics
                )
                metrics["layer_tiles"] = manifest["tiles"]
                metrics["cached"] = cached
                phase("layer_write")
    except (FileNotFoundError, ValueError) as e:
        metrics["issues"].append({"object_id": "inputs", "reason": str(e)})
    metrics["phase_seconds"] = phase_times
    metrics["elapsed_seconds"] = round(time.monotonic() - start, 3)
    (report_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, allow_nan=False) + "\n")
    (report_dir / "report.md").write_text(
        "# Structures acceptance report\n\n"
        + f"Status: **{metrics['status']}**\n\n```json\n"
        + json.dumps(metrics, indent=2, allow_nan=False)
        + "\n```\n"
    )
    print(json.dumps(metrics, indent=2, allow_nan=False))
    return 1 if metrics["status"] == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
