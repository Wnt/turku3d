"""Publish traffic_static per listed L0 tile from its OSM clip, city street areas and ground."""

import argparse
import json

from turku3d import config as C
from turku3d import tile_layer
from turku3d.layers import layer_root
from turku3d.parked.layer import PRODUCER


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tiles", nargs="+", help="L0 tiles (default: the playable tile list)")
    args = ap.parse_args(argv)
    tiles = args.tiles or C.playable_tile_ids()
    print(json.dumps(tile_layer.run(layer_root() / "traffic_static", PRODUCER, tiles), indent=2))


if __name__ == "__main__":
    main()
