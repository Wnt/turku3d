"""python -m turku3d.coarse build t0_2_0 [--out DIR]  (default $TURKU3D_WORK/coarse/<tile>)
python -m turku3d.coarse serve [--port P]        (tools/dev/coarse-tiles; default port base + 5)."""

import argparse
import json
import sys
from pathlib import Path

from turku3d import config as C

from .build import build


def coarse_root() -> Path:
    return C.work_root() / "coarse"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m turku3d.coarse", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build one L0 coarse tile")
    b.add_argument("tile")
    b.add_argument("--out", type=Path)
    sub.add_parser("serve", help="run the localhost companion service", add_help=False)
    if argv is None:
        argv = sys.argv[1:]
    if argv[:1] == ["serve"]:
        from .service import main as serve

        return serve(argv[1:])
    a = ap.parse_args(argv)
    manifest = build(a.tile, a.out or coarse_root() / a.tile)
    print(json.dumps({k: manifest[k] for k in ("tile", "buildings", "seconds")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
