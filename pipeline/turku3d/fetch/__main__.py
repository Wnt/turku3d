"""CLI: python -m turku3d.fetch {laz,wfs,wms,osm,all,report,verify} --area office-block

All steps are idempotent: re-running skips complete files (size + sha256 registry) and resumes partial ones.
"""

from __future__ import annotations

import argparse
import sys
import time

from .. import config


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m turku3d.fetch")
    ap.add_argument("step", choices=["laz", "wfs", "wms", "osm", "all", "report", "verify"])
    ap.add_argument("--area", default="office-block", choices=sorted(config.AREAS))
    ap.add_argument("--layers", nargs="*", help="restrict WFS/WMS layers (names or WMS slugs)")
    ap.add_argument("--workers", type=int, default=4, help="parallel WMS requests")
    ap.add_argument("--verify", action="store_true", help="re-hash cached LAZ files before trusting them")
    a = ap.parse_args(argv)
    area = config.AREAS[a.area]
    t0 = time.time()
    log = lambda s: print(s, flush=True)  # noqa: E731
    log(f"fetch {a.step} area={area.name} data_root={config.data_root()}")
    failed = False
    if a.step in ("laz", "all"):
        from . import laz
        laz.fetch_area(area, verify=a.verify, log=log)
    if a.step in ("wfs", "all"):
        from . import wfs
        res = wfs.fetch_area(area, layers=a.layers, log=log)
        failed |= any(r["status"] == "error" or (r["status"] == "unauthorized" and not r.get("expected"))
                      for r in res)
    if a.step in ("wms", "all"):
        from . import wms
        res = wms.fetch_area(area, layers=a.layers, workers=a.workers, log=log)
        failed |= any(r["errors"] for r in res)
    if a.step in ("osm", "all"):
        from . import osm
        osm.fetch_area(area, log=log)
    if a.step in ("report", "all"):
        from . import report
        report.build(area, log=log)
    if a.step == "verify":
        from .common import Registry, kind_dir
        for k in ("laz", "wfs", "ortho", "osm"):
            bad = Registry(kind_dir(k)).verify_all()
            log(f"verify {k}: {'OK' if not bad else 'BAD ' + ', '.join(bad[:10])}")
            failed |= bool(bad)
    log(f"done in {time.time() - t0:.0f} s" + ("  (with errors)" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
