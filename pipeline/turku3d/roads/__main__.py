"""Publish the road graph layer per listed L0 tile from each tile's OSM clip."""

from __future__ import annotations

import argparse
import json

from turku3d import config as C
from turku3d import tile_layer
from turku3d.layers import layer_root

from .layer import PRODUCER


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tiles", nargs="+", help="L0 tiles (default: the playable tile list)")
    args = ap.parse_args(argv)
    result = tile_layer.run(layer_root() / "roads", PRODUCER, args.tiles or C.playable_tile_ids())
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
