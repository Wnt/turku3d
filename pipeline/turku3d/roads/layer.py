"""The road graph layer, built per listed L0 tile from the tile's own OSM clip.

Each tile's build clips the routable OSM ways to the tile and keeps them as its records; the
graph is built over every listed tile's ways. Ways that cross a tile edge meet at the same
clip point on both sides, so the graph stays connected across tiles."""

from __future__ import annotations

import hashlib
from pathlib import Path

from turku3d import config as C
from turku3d import tile_layer
from turku3d.package.manifest import _atomic_write

from . import CAR, FOOT, build_graph, encode, osm_ways

GRAPH_FILE = "roadgraph.json"


def code_hash() -> str:
    base = Path(__file__).parents[1]
    return tile_layer.code_hash(
        Path(__file__).parent, base / "tile_layer.py", base / "config.py", base / "layers.py"
    )


def tile_key(tile):
    return {"osm": tile_layer.identities({"osm": tile_layer.osm(tile)})}


def build_tile(tile, staging):
    ways = [[coords, flags] for coords, flags in osm_ways(tile_layer.osm(tile), tile.bbox)]
    return {"records": ways, "metrics": {"ways": len(ways)}}


def touching_groups(tile_ids: list[str]) -> list[set[str]]:
    """Edge-connected playable regions; a corner does not join their road networks."""
    remaining = {C.VisualTile.parse(tile_id) for tile_id in tile_ids}
    groups = []
    while remaining:
        start = min(remaining)
        remaining.remove(start)
        group = {start}
        frontier = [start]
        while frontier:
            tile = frontier.pop()
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                near = C.VisualTile(0, tile.i + di, tile.j + dj)
                if near in remaining:
                    remaining.remove(near)
                    group.add(near)
                    frontier.append(near)
        groups.append({tile.id for tile in group})
    return groups


def graph_ways(graph: dict):
    nodes = list(zip(graph["nodes"][::2], graph["nodes"][1::2], strict=True))
    for a, b, flags, _, *interior in graph["edges"]:
        yield [nodes[a], *zip(interior[::2], interior[1::2], strict=True), nodes[b]], flags


def combine(directory: Path, tiles: dict) -> dict:
    area = C.tile_area("playable-tiles", "listed tiles", list(tiles))
    bounds = area.core.as_tuple()
    groups = touching_groups(list(tiles))
    selected = []
    for group in groups:
        ways = [(coords, flags) for tile in group for coords, flags in tiles[tile]["records"]]
        selected.extend(graph_ways(build_graph(ways, bounds, "playable-tiles")))
    graph = build_graph(selected, bounds, "playable-tiles", connected=False)
    data = encode(graph)
    _atomic_write(Path(directory) / GRAPH_FILE, data)
    edges = graph["edges"]
    return {
        "graph": {
            "file": GRAPH_FILE,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        },
        "area": "playable-tiles",
        "nodes": len(graph["nodes"]) // 2,
        "edges": len(edges),
        "foot_km": round(sum(e[3] for e in edges if e[2] & FOOT) / 1000, 2),
        "car_km": round(sum(e[3] for e in edges if e[2] & CAR) / 1000, 2),
    }


PRODUCER = tile_layer.Producer("roads", code_hash, tile_key, build_tile, combine)
