"""python -m turku3d.coarse build t0_2_0 [--out DIR] [--fetch]  (default $TURKU3D_WORK/coarse/<tile>)
python -m turku3d.coarse fetch lidar/23460000_6704500         (one prefetch unit into the raw cache)
python -m turku3d.coarse prepare t0_2_0 [--root DIR]           (prepare a tile's inputs on disk)
python -m turku3d.coarse serve [--port P]                      (tools/dev/coarse-tiles; port base + 5)
python -m turku3d.coarse worker                                (the service's persistent build worker)

A build reads the raw cache only and exits with PENDING (3) and {"pending": [units]} when inputs
are missing; --fetch fetches them first (hand-run builds)."""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from turku3d import config as C

from .build import build
from .imagery import ImagerySources
from .imagery import fetch as fetch_imagery
from .imagery import paths as imagery_paths
from .sources import MissingInputs, Unit, fetch, size, unit_paths

PENDING = 3


def coarse_root() -> Path:
    return C.work_root() / "coarse"


def fetch_unit(key: str) -> dict:
    unit = Unit.parse(key)
    paths = imagery_paths(unit.block) if unit.dataset == "ortho" else unit_paths(unit)
    before = size(paths)
    started = time.perf_counter()
    def log(*args):
        print(*args, file=sys.stderr)

    if unit.dataset == "ortho":
        fetch_imagery(unit.block, log=log)
    else:
        fetch(unit, log=log)
    return {
        "unit": unit.key,
        "bytes": size(paths) - before,
        "seconds": round(time.perf_counter() - started, 3),
        "paths": [str(p) for p in paths if p.exists()],
    }


def worker() -> int:
    """Persistent build worker: one {"tile", "out"} JSON line in, one result line out:
    {"code": 0 | PENDING | 1, "stdout": ..., "stderr": ...}, like a one-shot build's exit code and
    output. The producer, the raw-data listings and the node packager stay loaded between tiles."""
    # Results get their own copy of fd 1; fd 1 itself (C libraries, child processes) and
    # sys.stdout go to the log, so nothing else can interleave with a result line.
    results = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    sources = ImagerySources()
    for line in sys.stdin:
        job = json.loads(line)
        sources.invalidate()
        try:
            m = build(
                job["tile"],
                Path(job["out"]),
                sources=sources,
                first_pass=job.get("first_pass", False),
            )
            result = {"code": 0, "stdout": json.dumps({k: m[k] for k in ("tile", "buildings")})}
        except MissingInputs as missing:
            result = {"code": PENDING, "stdout": json.dumps({"pending": missing.units})}
        except Exception as e:  # reported like a failed one-shot build's last stderr line
            result = {"code": 1, "stderr": f"{type(e).__module__}.{type(e).__name__}: {e}"}
        results.write(json.dumps(result) + "\n")
        results.flush()
    return 0


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
    b.add_argument(
        "--first-pass",
        action="store_true",
        help="flat-textured playable draft before survey enrichment",
    )
    b.add_argument("--fetch", action="store_true", help="fetch missing inputs first")
    f = sub.add_parser("fetch", help="fetch one prefetch unit (<dataset>/<block>)")
    f.add_argument("unit")
    p = sub.add_parser("prepare", help="prepare one tile's inputs that are on disk")
    p.add_argument("tile")
    p.add_argument("--root", type=Path, help="coarse root (default $TURKU3D_WORK/coarse)")
    sub.add_parser("serve", help="run the localhost companion service", add_help=False)
    sub.add_parser("worker", help="build tiles named on stdin, one JSON line each (the service's)")
    if argv is None:
        argv = sys.argv[1:]
    if argv[:1] == ["serve"]:
        from .service import main as serve

        return serve(argv[1:])
    a = ap.parse_args(argv)
    if a.cmd == "fetch":
        print(json.dumps(fetch_unit(a.unit)))
        return 0
    if a.cmd == "worker":
        return worker()
    if a.cmd == "prepare":
        from .inputs import prepare

        print(json.dumps(prepare(C.VisualTile.parse(a.tile), a.root or coarse_root())))
        return 0
    tile = C.VisualTile.parse(a.tile)
    if a.fetch:
        for unit in ImagerySources().missing(tile):
            print(json.dumps(fetch_unit(unit.key)), file=sys.stderr)
    try:
        manifest = build(a.tile, a.out or coarse_root() / a.tile, first_pass=a.first_pass,
                         sources=ImagerySources())
    except MissingInputs as missing:
        print(json.dumps({"pending": missing.units}))
        return PENDING
    print(json.dumps({k: manifest[k] for k in ("tile", "buildings", "seconds")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
