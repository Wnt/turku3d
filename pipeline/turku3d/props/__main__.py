"""Build the props layer per listed L0 tile from its own input area (turku3d.tile_layer)."""

import argparse
import json

from turku3d import config as C
from turku3d import tile_layer
from turku3d.layers import layer_root
from turku3d.props import PRODUCER


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tiles", nargs="+", help="L0 tiles (default: the playable tile list)")
    args = parser.parse_args(argv)
    result = tile_layer.run(layer_root() / "props", PRODUCER, args.tiles or C.playable_tile_ids())
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
