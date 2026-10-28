"""Measure isolated single builds and four simultaneous cold draft builds on the serving host."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path


def measure(tile: str, mode: str, root: Path) -> dict:
    out = root / mode / tile
    log = root / f"{mode}-{tile}.log"
    started = time.perf_counter()
    with log.open("wb") as stream:
        proc = subprocess.run(
            [sys.executable, "-m", "turku3d.coarse", "build", tile, "--out", str(out)],
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=False,
        )
    row = {
        "tile": tile,
        "mode": mode,
        "wall_seconds": round(time.perf_counter() - started, 3),
        "exit_code": proc.returncode,
        "log": log.name,
    }
    manifest = out / "manifest.json"
    if proc.returncode == 0 and manifest.exists():
        data = json.loads(manifest.read_text())
        row["stages"] = data.get("seconds", {})
        row["buildings"] = data.get("buildings")
        row["code"] = data.get("code")
    elif proc.returncode == 0:
        row["exit_code"] = 1
        row["error"] = "successful process did not write a manifest"
    print(json.dumps(row), flush=True)
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True, type=Path, help="new, empty benchmark directory")
    ap.add_argument("--commit", required=True, help="full source archive commit")
    ap.add_argument("--tiles", nargs=4, default=["t0_1_2", "t0_0_3", "t0_7_7", "t0_8_7"])
    a = ap.parse_args()
    # Refuse an existing directory: cached inputs or payloads invalidate a cold comparison.
    a.root.mkdir(parents=True, exist_ok=False)
    report = {
        "commit": a.commit,
        "platform": platform.platform(),
        "python": sys.version,
        "load_start": os.getloadavg(),
        "tiles": a.tiles,
        "limits_seconds": {"near": 90, "outer": 30},
        "results": [],
    }

    def save():
        (a.root / "benchmark.json").write_text(json.dumps(report, indent=2) + "\n")

    save()
    for tile in a.tiles:
        report["results"].append(measure(tile, "single", a.root))
        save()
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        tasks = [pool.submit(measure, t, "parallel", a.root) for t in a.tiles]
        for task in concurrent.futures.as_completed(tasks):
            report["results"].append(task.result())
            save()
    report["parallel_wall_seconds"] = round(time.perf_counter() - started, 3)
    report["load_end"] = os.getloadavg()
    report["passed"] = all(
        r["exit_code"] == 0
        and (r["mode"] != "single" or r["wall_seconds"] <= (90 if r["tile"] in a.tiles[:2] else 30))
        for r in report["results"]
    )
    save()
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
