"""python -m turku3d.lidar: cache, query, surface, profiles, objects and AOI relief."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter

import laspy
import numpy as np
from shapely.geometry import LineString

from turku3d import config as C

from .cache import TileStore
from .edges import detect_edges
from .objects import small_objects
from .profiles import profiles
from .surface import Surface, select_ground, surfaces


def benchmark(tile, buffer=0.0, repeats=3):
    """Fresh store each warm read; force all point pages to be read via xyz checksum."""
    bbox = C.CollisionTile.parse(tile).bbox.buffered(buffer)
    store = TileStore()
    start = perf_counter()
    cached = store.tile(tile, buffer)
    build_s = perf_counter() - start
    expected = cached.points.sum(axis=0)
    warm, cold = [], []
    for _ in range(repeats):
        start = perf_counter()
        p = TileStore().tile(tile, buffer)
        checksum = p.points.sum(axis=0)
        warm.append(perf_counter() - start)
        np.testing.assert_allclose(checksum, expected, rtol=1e-12)
        start = perf_counter()
        selected = []
        for source in C.laz_tiles_for_bbox(bbox):
            las = laspy.read(store.source_dir / f"{source.name}.laz")
            x, z = np.asarray(las.x) - C.ORIGIN_E, C.ORIGIN_N - np.asarray(las.y)
            keep = (x >= bbox.xmin) & (x < bbox.xmax) & (z >= bbox.zmin) & (z < bbox.zmax)
            selected.append(np.c_[x[keep], np.asarray(las.z)[keep] - C.ORIGIN_H, z[keep]])
        data = np.concatenate(selected)
        checksum = data.sum(axis=0)
        cold.append(perf_counter() - start)
        assert len(data) == len(cached)
        np.testing.assert_allclose(checksum, expected, rtol=1e-10)
    return {
        "tile": tile,
        "buffer_m": buffer,
        "points": len(cached),
        "cache_prepare_s": build_s,
        "cached_read_s": warm,
        "laz_read_crop_s": cold,
        "speedup": float(np.median(cold) / np.median(warm)),
        "method": "Fresh TileStore, mmap open plus full xyz checksum vs LAZ decode/crop plus same checksum; OS file cache warm for both.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("cache", "query", "surfaces", "profiles", "edges", "objects", "benchmark"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--tile", default="c_2_3")
        cmd.add_argument("--buffer", type=float, default=0.0)
        cmd.add_argument("--output", type=Path)
        if name in ("surfaces", "edges", "objects"):
            cmd.add_argument("--resolution", type=float, default=0.25)
        if name == "query":
            cmd.add_argument("--classes", type=int, nargs="+")
            cmd.add_argument("--returns", type=int, nargs="+")
        if name == "profiles":
            cmd.add_argument(
                "--line", type=Path, required=True, help="JSON array of engine x/z vertices"
            )
    cmd = sub.add_parser("relief")
    cmd.add_argument("--scope", choices=["office-block", "phase1"], default="phase1")
    cmd.add_argument("--resolution", type=float, default=0.25)
    cmd.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "relief":
        from .relief import run

        run(args.scope, args.resolution, args.output)
        return
    if args.command == "benchmark":
        result = benchmark(args.tile, args.buffer)
    else:
        store = TileStore()
        tile = C.CollisionTile.parse(args.tile)
        cloud = store.tile(tile, args.buffer)
        bbox = tile.bbox.buffered(args.buffer)
        if args.command == "cache":
            result = {
                "tile": args.tile,
                "buffer_m": args.buffer,
                "points": len(cloud),
                "mapped": True,
            }
        elif args.command == "query":
            cloud = cloud.query(classes=args.classes, returns=args.returns)
            if args.output is None:
                parser.error("query requires --output points.npy")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            np.save(args.output, cloud.records, allow_pickle=False)
            print(json.dumps({"points": len(cloud), "output": str(args.output)}))
            return
        elif args.command == "profiles":
            line = LineString(json.loads(args.line.read_text()))
            ground, _ = select_ground(cloud)
            result = [
                {
                    "distance": d,
                    "center_xz": p.tolist(),
                    "normal_xz": n.tolist(),
                    "profile": fit.as_dict() if fit else None,
                }
                for d, p, n, fit in profiles(Surface(ground), line)
            ]
        else:
            raster = surfaces(cloud, bbox, args.resolution)
            if args.command == "surfaces":
                if args.output is None:
                    parser.error("surfaces requires --output surfaces.npz")
                args.output.parent.mkdir(parents=True, exist_ok=True)
                raster.save(args.output)
                print(json.dumps({"output": str(args.output)}))
                return
            if args.command == "objects":
                result = [o.as_dict() for o in small_objects(raster)]
            else:
                ground, _ = select_ground(cloud)
                result = detect_edges(Surface(ground), raster)
    text = json.dumps(result, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
