"""Fetch and convert every input of the tiles in rings around a centre tile, so their builds need no network.

    tools/cities/run.sh amsterdam python -m turku3d.nl.prepare t0_1_0 --rings 3 [--seam-ring 1]

Rings 0..R around the centre get every unit a full build reads (ground, point cloud, ortho, WFS layers, OSM);
the next ``--seam-ring`` rings get their ground blocks too, because seams and tunnel portals sample the
neighbours' ground. Prints the tiles whose inputs are complete (they build offline) as JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from turku3d import config as C


def ring(centre: C.VisualTile, r: int) -> list[C.VisualTile]:
    return [C.VisualTile(0, centre.i + di, centre.j + dj)
            for di in range(-r, r + 1) for dj in range(-r, r + 1) if max(abs(di), abs(dj)) == r]


def main(argv=None) -> int:
    from turku3d.coarse.__main__ import fetch_unit
    from turku3d.coarse.imagery import ImagerySources
    from turku3d.coarse.sources import Unit, block_of, tile_blocks

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("centre")
    ap.add_argument("--rings", type=int, default=3)
    ap.add_argument("--seam-ring", type=int, default=1)
    ap.add_argument("--shard", default="0/1", help="K/N: this process prepares every N-th block from K (run N in parallel)")
    a = ap.parse_args(argv)
    centre = C.VisualTile.parse(a.centre)
    log = lambda *x: print(*x, file=sys.stderr, flush=True)  # noqa: E731
    tiles = [t for r in range(a.rings + 1) for t in ring(centre, r)]
    seams = [t for r in range(a.rings + 1, a.rings + 1 + a.seam_ring) for t in ring(centre, r)]
    k, n = map(int, a.shard.split("/"))
    done, started = set(), time.monotonic()
    for tile in [*tiles, *seams]:
        # Shards split by 500 m block (the unit grid); OSM squares are clipped by shard 0 only.
        sources = ImagerySources()
        if tile in seams:
            units = [Unit("lidar", b) for b in tile_blocks(tile) if not sources.lidar_present(b)]
        else:
            units = sources.missing_for_service(tile)
        for unit in units:
            if unit.key in done or (unit.dataset == "osm" and k != 0):
                continue
            b = unit.block
            if (int(b.xmin // C.LAZ_TILE_SIZE) + int(b.ymax // C.LAZ_TILE_SIZE)) % n != k and unit.dataset != "osm":
                continue
            log(f"prepare {tile.id}: {unit.key}")
            try:
                log(json.dumps(fetch_unit(unit.key)))
            except Exception as error:  # keep going; the tile is reported incomplete below
                log(f"prepare {unit.key}: {type(error).__name__}: {error}")
            done.add(unit.key)
    sources = ImagerySources()
    ready = [t.id for t in tiles if not sources.missing_for_service(t)]
    print(json.dumps({"centre": centre.id, "rings": a.rings, "ready": ready,
                      "incomplete": [t.id for t in tiles if t.id not in ready],
                      "blocks": sorted({block_of(t).name for t in tiles}),
                      "minutes": round((time.monotonic() - started) / 60, 1)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
