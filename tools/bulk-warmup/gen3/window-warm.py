#!/usr/bin/env python3
"""Bulk host, during a window: put the survey blocks of upcoming tiles in place ahead of the pass's lanes.

  window-warm.py --farm-root F --revision REV --supply DIR [--threads 4] [--ahead 400] [--radius R]

The pass (tile_city_pass.py) supplies a tile's missing catalogued survey blocks in its dispatch loop, one copy at a
time; on a cached host each cold block crosses the tunnel (~130 MB, ~2 s at 60 MB/s), so lanes would idle behind it.
This walks the pass's own order (ring, then tile) and calls the pass's own Survey.missing on each tile, which copies
blocks with the same size + SHA-256 check (supply_block, pid-unique .part files, atomic rename), so the pass then
finds them on the local disk. Blocks are logged to migrations/<generation>/supply-warm.jsonl. It stays at most
--ahead tiles in front of the pass's journal so the disk is not filled with blocks of tiles the window never reaches.
Run it from the alias's code snapshot through shared-tiles-run.sh, like the pass.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, 'scripts/dev')
import tile_city_pass as P  # noqa: E402
import tile_migrate as M  # noqa: E402  (the module the pass imports as M)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--farm-root', type=Path, required=True)
    ap.add_argument('--revision', required=True)
    ap.add_argument('--supply', action='append', default=[])
    ap.add_argument('--threads', type=int, default=4)
    ap.add_argument('--ahead', type=int, default=400)
    ap.add_argument('--radius', type=int)
    a = ap.parse_args()
    meta = json.loads((a.farm_root / 'revisions' / a.revision / 'revision.json').read_text())
    M.use_base_data(a.farm_root, meta)  # as the pass does: Survey reads the base's own data root
    logs = a.farm_root / 'migrations' / meta['generation']
    logs.mkdir(parents=True, exist_ok=True)
    tiles = sorted(M.finished_base_tiles(a.farm_root, meta), key=lambda t: (P.ring(t), t))
    if a.radius is not None:
        tiles = [t for t in tiles if P.ring(t) <= a.radius]
    journal = logs / 'pass.jsonl'
    finished = {r['tile'] for r in P.logged(journal) if r['state'] in ('migrated', 'kept')}
    tiles = [t for t in tiles if t not in finished]  # the pass skips these too
    survey = P.Survey(a.supply, logs / 'supply-warm.jsonl')
    base = len(P.logged(journal))

    def passed():
        return len(P.logged(journal)) - base

    from turku3d import config as C
    from turku3d.coarse import wires

    def needed(tile):
        bbox = C.VisualTile.parse(tile).bbox.buffered(wires.SEARCH)
        return [b.name for b in C.laz_tiles_for_bbox(bbox)]

    # One supply per block: tiles share blocks, and supply_block's .part name is per process, so two threads must
    # never copy the same block (the pass, another process, may; its .part differs).
    started, done, supplied = time.monotonic(), 0, set()
    with ThreadPoolExecutor(a.threads) as pool:
        for start in range(0, len(tiles), a.threads * 4):
            while start - passed() > a.ahead:
                time.sleep(5)
            batch = tiles[start:start + a.threads * 4]
            names = sorted({n for t in batch for n in needed(t)
                            if n in survey.catalog and n not in supplied and not (survey.root / f'{n}.laz').is_file()})
            ok = list(pool.map(survey._link, names))
            supplied.update(n for n, good in zip(names, ok) if good)
            done += len(batch)
            print(json.dumps(dict(t=round(time.monotonic() - started), tiles=done, ring=P.ring(batch[-1]),
                                  supplied=len(supplied), not_supplied=sum(1 for g in ok if not g))), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
