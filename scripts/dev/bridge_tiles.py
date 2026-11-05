#!/usr/bin/env python3
"""The tiles that build a bridge deck, its ramps or the ground cut under it (``bridge-ends`` spot rebuild).

A tile builds a bridge when the deck line, buffered by half its width plus ``bridges.SELECT_MARGIN``,
reaches its bbox (``bridges.selects``). The deck lines are the BRIDGE edges of every finished tile's
roads.json in a generation's products, so the set follows the data: a tile with only a lower road
under a deck, or only a ramp end, is in it even when its manifest counts no deck.

  python3 scripts/dev/bridge_tiles.py PRODUCTS_DIR > pipeline/turku3d/migrations/bridge_ends_tiles.json
"""
import glob
import json
import math
import os
import sys

SIZE = 250.0
MARGIN = 5.0 + 1.0  # bridges.SELECT_MARGIN plus the corridor's outline slack


def main(products):
    tiles = set()
    for manifest_path in sorted(glob.glob(os.path.join(products, "t0_*", "manifest.json"))):
        manifest = json.load(open(manifest_path))
        roads = manifest.get("roads")
        if not roads:
            continue
        path = os.path.join(os.path.dirname(manifest_path), os.path.basename(roads["url"]))
        if not os.path.exists(path):
            continue
        graph = json.load(open(path))
        bit = graph["bits"]["edge_flags"]["BRIDGE"]
        column = graph["edgeAttrColumns"].index("flags")
        width = graph["edgeAttrColumns"].index("width")
        nodes = graph["nodes"]
        for edge, attrs in zip(graph["edges"], graph["edgeAttrs"]):
            if not attrs[column] & bit:
                continue
            reach = float(attrs[width]) / 2 + MARGIN
            xs = [nodes[2 * edge[0]], *edge[4::2], nodes[2 * edge[1]]]
            zs = [nodes[2 * edge[0] + 1], *edge[5::2], nodes[2 * edge[1] + 1]]
            for i in range(math.floor((min(xs) - reach) / SIZE), math.floor((max(xs) + reach) / SIZE) + 1):
                for j in range(math.floor((min(zs) - reach) / SIZE), math.floor((max(zs) + reach) / SIZE) + 1):
                    tiles.add(f"t0_{i}_{j}")
    print(json.dumps(sorted(tiles), indent=0))


if __name__ == "__main__":
    main(sys.argv[1])
