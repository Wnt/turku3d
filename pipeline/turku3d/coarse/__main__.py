"""python -m turku3d.coarse build t0_2_0 [--out DIR] [--fetch]  (default $TURKU3D_WORK/coarse/<tile>)
python -m turku3d.coarse fetch lidar/23460000_6704500         (one prefetch unit into the raw cache)
python -m turku3d.coarse serve [--port P]                      (tools/dev/coarse-tiles; port base + 5)

A build reads the raw cache only and exits with PENDING (3) and {"pending": [units]} when inputs
are missing; --fetch fetches them first (hand-run builds)."""

import argparse
import json
import sys
import time
from pathlib import Path

from turku3d import config as C

from .build import build
from .sources import MissingInputs, Sources, Unit, fetch, size, unit_paths

PENDING = 3


def coarse_root() -> Path:
    return C.work_root() / "coarse"


def fetch_unit(key: str) -> dict:
    unit = Unit.parse(key)
    paths = unit_paths(unit)
    before = size(paths)
    started = time.perf_counter()
    fetch(unit, log=lambda *a: print(*a, file=sys.stderr))
    return {
        "unit": unit.key,
        "bytes": size(paths) - before,
        "seconds": round(time.perf_counter() - started, 3),
        "paths": [str(p) for p in paths if p.exists()],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m turku3d.coarse",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build one L0 coarse tile")
    b.add_argument("tile")
    b.add_argument("--out", type=Path)
    b.add_argument("--fetch", action="store_true", help="fetch missing inputs first")
    f = sub.add_parser("fetch", help="fetch one prefetch unit (<dataset>/<block>)")
    f.add_argument("unit")
    sub.add_parser("serve", help="run the localhost companion service", add_help=False)
    if argv is None:
        argv = sys.argv[1:]
    if argv[:1] == ["serve"]:
        from .service import main as serve

        return serve(argv[1:])
    a = ap.parse_args(argv)
    if a.cmd == "fetch":
        print(json.dumps(fetch_unit(a.unit)))
        return 0
    tile = C.VisualTile.parse(a.tile)
    if a.fetch:
        for unit in Sources().missing(tile):
            print(json.dumps(fetch_unit(unit.key)), file=sys.stderr)
    try:
        manifest = build(a.tile, a.out or coarse_root() / a.tile)
    except MissingInputs as missing:
        print(json.dumps({"pending": missing.units}))
        return PENDING
    print(json.dumps({k: manifest[k] for k in ("tile", "buildings", "seconds")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
