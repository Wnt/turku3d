#!/usr/bin/env python3
"""Bulk host, during a window: first+full generator builds of other cities' tiles, ring by ring from each centre,
interleaved across cities, in step with the Turku pass.

  window-cities.py --plan plan.json [--lanes N] [--turku-journal F/migrations/<gen>/pass.jsonl] [--dry-run]

plan.json: {"cities": [{"name": "espoo", "checkout": "/home/wnt/turku3d-local/cities/repo", "centre": "t0_0_1",
            "out": "/home/wnt/turku3d-local/cities/espoo/tiles", "tiles": ["t0_0_1", ...]}, ...]}
Only the listed tiles are built (the city agents' lists of tiles that build without any network fetch). The plan
file is re-read when it changes, so a city whose list lands mid-window joins the ring queue (--follow keeps waiting).

Order: ring r of every city before ring r+1 of any, cities round-robin within a ring (Chebyshev rings round each
city's own centre tile; Turku's pass uses the same rings round t0_0_0). A city ring starts only once the Turku pass
has started that ring too (--turku-journal), so the centres of all four cities come first with equal priority. The
Turku pass caps its lanes by load per core, so these builds take their lanes and Turku fills the rest of the cores.

Each tile builds as `tools/cities/run.sh CITY python -m turku3d.coarse build TILE --out OUT/TILE.building` from the
city's checkout, and is renamed to OUT/TILE only after it exits 0 with a full manifest: a window cut-off loses only
the tiles in flight, and a reader never sees a half-built tile. Tiles already at OUT/TILE are skipped. Progress is one
JSON line per tile in OUT/../window-builds.jsonl and a summary in /home/wnt/turku3d-bulk-gen3/cities.json.
"""
import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

STATUS = Path('/home/wnt/turku3d-bulk-gen3/cities.json')


def ring(tile, centre):
    i, j = (int(v) for v in tile.split('_')[1:])
    ci, cj = (int(v) for v in centre.split('_')[1:])
    return max(abs(i - ci), abs(j - cj))


def turku_ring(journal):
    """The highest ring the Turku pass has started or finished (its journal rows), or None without a journal."""
    if journal is None:
        return None
    best = -1
    try:
        for line in journal.open():
            try:
                tile = json.loads(line)['tile']
            except (ValueError, KeyError):
                continue
            best = max(best, ring(tile, 't0_0_0'))
    except FileNotFoundError:
        pass
    return best


def order(cities):
    jobs = []
    for k, city in enumerate(cities):
        out = Path(city['out'])
        for n, tile in enumerate(sorted(city['tiles'], key=lambda t: (ring(t, city['centre']), t))):
            if (out / tile / 'manifest.json').is_file():
                continue
            jobs.append((ring(tile, city['centre']), n, k, tile))
    jobs.sort()  # ring first, then the n-th tile of each city in turn
    return [(r, cities[k], tile) for r, _, k, tile in jobs]


def build(city, tile):
    out = Path(city['out'])
    # A sibling of the final directory: the build writes shared textures to <out>/../shared, which must be OUT/shared.
    stage = out / f'{tile}.building'
    shutil.rmtree(stage, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    logs = out.parent / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    started, cpu0 = time.time(), os.times()
    with (logs / f'window-build-{tile}.log').open('w') as log:
        proc = subprocess.run([f"{city['checkout']}/tools/cities/run.sh", city['name'], 'python', '-m', 'turku3d.coarse',
                               'build', tile, '--out', str(stage)], cwd=city['checkout'], stdout=log,
                              stderr=subprocess.STDOUT)
    cpu1 = os.times()
    row = dict(city=city['name'], tile=tile, wall=round(time.time() - started, 1),
               cpu=round(cpu1.children_user + cpu1.children_system - cpu0.children_user - cpu0.children_system, 1),
               exit=proc.returncode)
    try:
        manifest = json.loads((stage / 'manifest.json').read_text())
    except (OSError, ValueError):
        manifest = {}
    if proc.returncode == 0 and manifest.get('pass_kind', 'full') == 'full':
        os.replace(stage, out / tile)
        row['state'] = 'built'
    else:
        row['state'] = 'failed'
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--plan', type=Path, required=True)
    ap.add_argument('--lanes', type=int, default=4)
    ap.add_argument('--turku-journal', type=Path)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--follow', action='store_true', help='keep running and pick up tiles added to the plan file')
    a = ap.parse_args()
    cities = json.loads(a.plan.read_text())['cities']
    jobs = order(cities)
    if a.dry_run:
        counts = {}
        for r, city, _ in jobs:
            counts.setdefault(city['name'], {}).setdefault(r, 0)
            counts[city['name']][r] += 1
        print(json.dumps(counts, indent=1))
        return 0
    started, done, running = time.time(), [], {}
    stamp = a.plan.stat().st_mtime
    with ThreadPoolExecutor(a.lanes) as pool:
        while jobs or running or a.follow:
            if a.plan.stat().st_mtime != stamp:
                # Cities or tiles added mid-window (a city's offline-ready list landed): rebuild the queue in ring order,
                # without the tiles built or in flight; a tile that failed (e.g. exit 3, inputs pending) is tried again.
                stamp, cities = a.plan.stat().st_mtime, json.loads(a.plan.read_text())['cities']
                busy = {(n, t) for _, n, t in running.values()} | {(d['city'], d['tile']) for d in done
                                                                      if d['state'] == 'built'}
                jobs = [j for j in order(cities) if (j[1]['name'], j[2]) not in busy]
            limit = turku_ring(a.turku_journal)
            while jobs and len(running) < a.lanes and (limit is None or jobs[0][0] <= max(limit, 0)):
                r, city, tile = jobs.pop(0)
                running[pool.submit(build, city, tile)] = (r, city['name'], tile)
            if not running:
                time.sleep(10)
                continue
            finished, _ = wait(list(running), timeout=10, return_when=FIRST_COMPLETED)
            for future in finished:
                r, name, tile = running.pop(future)
                row = future.result()
                row.update(ring=r, at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
                done.append(row)
                city = next(c for c in cities if c['name'] == name)
                with (Path(city['out']).parent / 'window-builds.jsonl').open('a') as log:
                    log.write(json.dumps(row) + '\n')
            summary = dict(updated=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                           minutes=round((time.time() - started) / 60, 1), turku_ring=limit,
                           running=sorted(f'{n}/{t}' for _, n, t in running.values()), left=len(jobs),
                           built={c['name']: sum(1 for d in done if d['city'] == c['name'] and d['state'] == 'built')
                                  for c in cities},
                           failed=sorted({f"{d['city']}/{d['tile']}" for d in done if d['state'] == 'failed'}))
            tmp = STATUS.with_suffix('.tmp')
            tmp.write_text(json.dumps(summary, indent=1))
            os.replace(tmp, STATUS)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
