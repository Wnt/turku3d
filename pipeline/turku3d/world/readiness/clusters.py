"""Runs the cluster hierarchy builder (clusterlod.mjs, meshoptimizer) on welded meshes."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
MODULE_DIRS = (
    REPO / "game/node_modules/meshoptimizer",
    Path.home() / "turku3d/wt/loop-integration/repo/game/node_modules/meshoptimizer",
)
MIN_TRIANGLES = 2  # a hierarchy of one cluster is still a hierarchy; only empty meshes are skipped


def hierarchies(
    meshes: dict[str, tuple[np.ndarray, np.ndarray]], timeout: float = 600.0
) -> dict[str, dict]:
    """{name: result} for {name: (welded points (n, 3), triangles (m, 3))}; {"skipped": reason} when the builder
    cannot run here."""
    node = shutil.which("node")
    module = next(
        (path for path in MODULE_DIRS if (path / "meshopt_clusterizer.js").exists()), None
    )
    if node is None or module is None:
        reason = (
            "node not found"
            if node is None
            else "meshoptimizer not installed (npm ci --prefix game)"
        )
        return {name: {"skipped": reason} for name in meshes}
    out: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="readiness-") as scratch:
        jobs = []
        for number, (name, (points, triangles)) in enumerate(meshes.items()):
            if len(triangles) < MIN_TRIANGLES:
                out[name] = {"skipped": "empty"}
                continue
            file = Path(scratch) / f"{number}.bin"
            with file.open("wb") as handle:
                handle.write(np.array([len(points), triangles.size], dtype="<u4").tobytes())
                handle.write(np.ascontiguousarray(points, dtype="<f4").tobytes())
                handle.write(np.ascontiguousarray(triangles, dtype="<u4").tobytes())
            jobs.append({"name": name, "file": str(file)})
        if jobs:
            listing = Path(scratch) / "jobs.json"
            listing.write_text(json.dumps(jobs))
            run = subprocess.run(
                [node, str(HERE / "clusterlod.mjs"), str(module), str(listing)],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                stdin=subprocess.DEVNULL,
            )
            for line in run.stdout.splitlines():
                row = json.loads(line)
                out[row.pop("name")] = row
            for job in jobs:
                out.setdefault(
                    job["name"],
                    {"failed": (run.stderr.strip().splitlines() or ["no result"])[-1][:200]},
                )
    return out
