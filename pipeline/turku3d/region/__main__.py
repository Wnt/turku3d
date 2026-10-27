"""make region AREA=name [STEPS='fetch assemble validate'] [DRY=1]; make tile-add T=t0_i_j; quality."""

import argparse
import fcntl
import json
import os
import sys
from pathlib import Path

from turku3d import config as C

from .areas import geometry, load_area
from .estimate import estimate
from .runner import ORDER, execute, plan


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] in (["add-tile"], ["list-tile"]):
        from .tiles import main as tiles

        return tiles(argv)
    if argv[:1] == ["quality"]:
        from .quality import main as quality

        return quality(argv[1:])
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--area", default="office-block")
    ap.add_argument("--steps", nargs="+", choices=ORDER, default=list(ORDER))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--quota-cap", type=float, default=100)
    ap.add_argument("--averages", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.workers < 1 or not 0 < a.quota_cap <= 100:
        ap.error("workers must be positive and quota-cap in (0, 100]")
    area = load_area(a.area)
    report = C.work_root() / "reports/region" / area.name
    report.mkdir(parents=True, exist_ok=True)
    result = estimate(area, a.workers, a.averages)
    source = (
        "work-3x3" if geometry(C.WORK_3X3).covers(geometry(area).buffer(area.margin)) else area.name
    )
    steps = [s for s in plan(area, source, a.workers, report) if s.name in a.steps]
    result["steps"] = [
        {
            "name": s.name,
            "available": bool(s.commands),
            "reason": s.reason if not s.commands else "",
        }
        for s in steps
    ]
    (report / "estimate.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2), flush=True)
    if a.dry_run:
        return 0
    env = dict(
        os.environ,
        REGION_WORKERS=str(a.workers),
        REGION_FETCH_READY=str(report / "fetch-ready.json"),
    )
    run_lock = (report.parent / ".run.lock").open("a")
    fcntl.flock(run_lock, fcntl.LOCK_EX)
    # Fetch may select a local input mirror. Re-plan after fetch so fingerprints use it.
    results = []
    for step in steps:
        if step.name != "fetch" and (report / "fetch-ready.json").exists():
            root = json.loads((report / "fetch-ready.json").read_text())["data_root"]
            env["TURKU3D_DATA"] = root
            os.environ["TURKU3D_DATA"] = root
            step = next(s for s in plan(area, source, a.workers, report) if s.name == step.name)
        rows = execute([step], report, a.workers, a.quota_cap, env=env)
        results.extend(rows)
        if rows[-1]["status"] == "failed":
            break
    (report / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
    run_lock.close()
    return int(any(r["status"] == "failed" for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
