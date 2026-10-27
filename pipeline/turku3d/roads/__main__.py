"""Publish the road graph layer from the read-only OSM snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from turku3d import config as C
from turku3d.layers import layer_root, write_manifest
from turku3d.package.manifest import _atomic_write

from . import CAR, FOOT, build_graph, code_hash, encode, osm_ways

GRAPH_FILE = "roadgraph.json"


def build(out: Path, osm: Path, area: str) -> dict:
    bbox = C.area(area).core
    graph = build_graph(osm_ways(osm, bbox), bbox.as_tuple(), area)
    data = encode(graph)
    _atomic_write(out / GRAPH_FILE, data)
    stat = osm.stat()
    edges = graph["edges"]
    params = {
        "graph": {
            "file": GRAPH_FILE,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        },
        "area": area,
        "nodes": len(graph["nodes"]) // 2,
        "edges": len(edges),
        "foot_km": round(sum(e[3] for e in edges if e[2] & FOOT) / 1000, 2),
        "car_km": round(sum(e[3] for e in edges if e[2] & CAR) / 1000, 2),
    }
    inputs = {
        "osm": {
            "path": str(osm),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": hashlib.sha256(osm.read_bytes()).hexdigest(),
        }
    }
    write_manifest(out, inputs=inputs, params=params, code=code_hash(), tiles=[])
    return params


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--area", default="playable-tiles", choices=C.area_names())
    ap.add_argument(
        "--osm", type=Path, default=C.raw_dir("osm") / "work-3x3/work-3x3-261024.parquet"
    )
    ap.add_argument("--out", type=Path, default=layer_root() / "roads")
    args = ap.parse_args(argv)
    print(json.dumps(build(args.out, args.osm, args.area)))


if __name__ == "__main__":
    main()
