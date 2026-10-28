"""Prebuild listed tiles and their 3x3 ring, including replacement-parent siblings."""

from __future__ import annotations

import concurrent.futures
import json
import sys
import time
from pathlib import Path

from turku3d import config as C
from turku3d.coarse.service import Workers
from turku3d.coarse.sources import Sources, fetch


def ring(tiles):
    result = set()
    for name in tiles:
        _, i, j = name.split("_")
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                x, z = int(i) + di, int(j) + dj
                for a in (0, 1):
                    for b in (0, 1):
                        result.add(f"t0_{2 * (x // 2) + a}_{2 * (z // 2) + b}")
    return sorted(result, key=lambda t: sum(int(n) ** 2 for n in t.split("_")[1:]))


def main():
    names = ring(json.loads(Path(sys.argv[1]).read_text())["tiles"])
    root = C.work_root() / "coarse"
    root.mkdir(parents=True, exist_ok=True)
    report = C.work_root() / "prebuild.json"
    workers = Workers()
    results = []

    def build(name):
        started = time.perf_counter()
        try:
            # Sources and fetch registration are per job; fetchers persist raw responses.
            for unit in Sources().missing(C.VisualTile.parse(name)):
                fetch(unit)
            proc = workers(name, root / name)
            return {
                "tile": name,
                "code": proc.returncode,
                "seconds": round(time.perf_counter() - started, 2),
                "error": (proc.stderr or "")[-500:],
            }
        except Exception as e:
            return {
                "tile": name,
                "code": 1,
                "seconds": round(time.perf_counter() - started, 2),
                "error": str(e)[-500:],
            }

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            jobs = [pool.submit(build, name) for name in names]
            for job in concurrent.futures.as_completed(jobs):
                row = job.result()
                results.append(row)
                report.write_text(
                    json.dumps(
                        {"total": len(names), "finished": len(results), "results": results},
                        indent=2,
                    )
                    + "\n"
                )
                print(
                    json.dumps({"finished": len(results), "total": len(names), **row}), flush=True
                )
    finally:
        workers.close()
    return int(any(r["code"] for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
