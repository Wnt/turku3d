"""Publish the road graph layer from the read-only OSM snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from turku3d import config as C
from turku3d.layers import layer_root, write_manifest
from turku3d.package.manifest import _atomic_write

from . import CAR, FOOT, build_graph, code_hash, encode, reversed_oneway, way_flags

GRAPH_FILE = "roadgraph.json"


def osm_ways(path: Path, bbox: C.BBoxLocal):
    import geopandas as gpd
    from shapely import box, get_parts

    frame = gpd.read_parquet(path, columns=["highway", "tags", "geometry_local"])
    frame = frame[frame["highway"].notna()]
    clip = box(*bbox.as_tuple())
    for row in frame.itertuples():
        line = row.geometry_local
        if line.geom_type != "LineString" or not line.intersects(clip):
            continue
        tags = json.loads(row.tags)
        flags = way_flags(row.highway, tags)
        if not flags:
            continue
        for part in get_parts(line.intersection(clip)):
            if part.geom_type != "LineString" or part.is_empty:
                continue
            coords = [(x, z) for x, z, *_ in part.coords]
            yield (coords[::-1] if reversed_oneway(tags) else coords), flags


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
