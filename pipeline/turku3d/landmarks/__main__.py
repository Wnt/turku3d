"""Build the landmarks layer per listed L0 tile (turku3d.tile_layer)."""

import argparse
import json

from turku3d import config as C
from turku3d import tile_layer
from turku3d.landmarks import PRODUCER
from turku3d.layers import layer_root


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tiles", nargs="+", help="L0 tiles (default: the playable tile list)")
    args = parser.parse_args(argv)
    tiles = args.tiles or C.playable_tile_ids()
    print(json.dumps(tile_layer.run(layer_root() / PRODUCER.layer, PRODUCER, tiles), indent=2))


if __name__ == "__main__":
    main()
