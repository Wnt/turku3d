"""Warm the playable ring through the same bounded queue as public requests."""

from __future__ import annotations

import concurrent.futures
import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from turku3d import config as C


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
    # Spawn collision crosses the office tile's south/east neighbours. Warm by
    # distance to that point, rather than integer tile indices around the origin.
    x, z = C.OFFICE_E - C.ORIGIN_E, C.ORIGIN_N - C.OFFICE_N

    def distance(name):
        tile = C.VisualTile.parse(name)
        b = tile.bbox
        return max(b.xmin - x, 0, x - b.xmax) ** 2 + max(b.zmin - z, 0, z - b.zmax) ** 2

    return sorted(result, key=lambda name: (distance(name), name))


def main():
    settings = json.loads(Path(__file__).with_name("config.json").read_text())
    names = ring(json.loads(Path(sys.argv[1]).read_text())["tiles"])
    report = C.work_root() / "prebuild.json"
    results = []

    def build(name):
        started = time.perf_counter()
        error = "prebuild timed out"
        while time.perf_counter() - started < 600:
            try:
                with urlopen(
                    f"http://127.0.0.1:{settings['port']}/coarse/{name}/manifest.json", timeout=30
                ) as reply:
                    if reply.status == 200 and not json.load(reply).get("stale", False):
                        return {
                            "tile": name,
                            "code": 0,
                            "seconds": round(time.perf_counter() - started, 2),
                            "error": "",
                        }
            except HTTPError as e:
                if e.code not in (429, 502, 503, 504):
                    error = f"HTTP {e.code}: tile unavailable (see service log)"
                    break
            except (URLError, TimeoutError):
                pass  # Supervised service may be restarting; reconnect without launching duplicate builds.
            time.sleep(5)
        return {
            "tile": name,
            "code": 1,
            "seconds": round(time.perf_counter() - started, 2),
            "error": error,
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=settings["workers"]) as pool:
        jobs = [pool.submit(build, name) for name in names]
        for job in concurrent.futures.as_completed(jobs):
            row = job.result()
            results.append(row)
            report.write_text(
                json.dumps(
                    {"total": len(names), "finished": len(results), "results": results}, indent=2
                )
                + "\n"
            )
            print(json.dumps({"finished": len(results), "total": len(names), **row}), flush=True)
    return int(any(r["code"] for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
