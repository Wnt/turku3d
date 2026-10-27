"""Build the structures layer per listed L0 tile inside the worker sandbox (producer.py)."""

import argparse
import json

from turku3d import config as C
from turku3d import tile_layer
from turku3d.layers import layer_root

from .producer import PRODUCER


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tiles", nargs="+", help="L0 tiles (default: the playable tile list)")
    args = ap.parse_args(argv)
    tiles = args.tiles or C.playable_tile_ids()
    print(json.dumps(tile_layer.run(layer_root() / "structures", PRODUCER, tiles), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
