#!/usr/bin/env python3
"""Bulk host, during a window: first+full generator builds of other cities' tiles, ring by ring from each centre,
interleaved across cities.

  window-cities.py --plan plan.json [--lanes N | --lanes-file F] [--follow] [--dry-run]

plan.json: {"cities": [{"name": "espoo", "checkout": "/home/wnt/turku3d-local/cities/repo", "centre": "t0_0_1",
            "out": "/home/wnt/turku3d-local/cities/espoo/tiles", "tiles": ["t0_0_1", ...]}, ...]}
Only the listed tiles are built (the city agents' lists of tiles that build without any network fetch).

Order: ring r of every city before ring r+1 of any, cities in turn within a ring (Chebyshev rings round each city's
own centre tile; the Turku pass uses the same rings round t0_0_0, in its own lanes). The CPU split between the
cities and Turku is the lane counts (window-lanes.sh).

Hot and additive (a window never idles for a change): the plan file is re-read when it changes and only tiles new to
it are appended to the queue, and the not-yet-started tail takes the plan's order (a city with "ordered": true
keeps its list's order, e.g. a priority agent's queue; otherwise rings round its centre); the lane count is re-read from --lanes-file every few seconds; a
failing tile is logged and skipped and its lane takes the next one. --follow keeps running with an empty queue.

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


def key(city, k, tile):
    """Queue position: a city with "ordered": true is built in its list's order (a priority agent's queue), else by
    ring round its centre; cities take turns at each position, so they keep equal priority."""
    return k if city.get('ordered') else ring(tile, city['centre'])


def order(cities):
    jobs = []
    for n, city in enumerate(cities):
        out = Path(city['out'])
        for k, tile in enumerate(city['tiles']):
            if (out / tile / 'manifest.json').is_file():
                continue
            jobs.append(((key(city, k, tile), n, tile), city, tile))
    jobs.sort(key=lambda j: j[0])
    return [(ring(tile, city['centre']), city, tile) for _, city, tile in jobs]


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
    ap.add_argument('--lanes', type=int, default=4, help='lanes when --lanes-file is absent or unreadable')
    ap.add_argument('--lanes-file', help='a file holding the lane count, re-read every few seconds (window-lanes.sh)')
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
    seen = {(c['name'], t) for _, c, t in jobs}
    stamp = a.plan.stat().st_mtime
    lanes_file = Path(a.lanes_file) if a.lanes_file else None

    def lanes():
        try:
            return max(0, int(lanes_file.read_text().split()[0]))
        except (AttributeError, OSError, ValueError, IndexError):
            return a.lanes

    with ThreadPoolExecutor(64) as pool:
        while jobs or running or a.follow:
            try:
                changed = a.plan.stat().st_mtime != stamp
            except OSError:
                changed = False
            if changed:
                # Append-only: tiles new to the plan join the queue (ring order kept); nothing queued, running or
                # done is dropped or redone, and lanes keep running.
                stamp = a.plan.stat().st_mtime
                cities = json.loads(a.plan.read_text())['cities']
                fresh = order(cities)
                new = [j for j in fresh if (j[1]['name'], j[2]) not in seen]
                seen |= {(c['name'], t) for _, c, t in new}
                # The not-yet-started tail takes the plan's new order; nothing running or done is touched.
                waiting = {(j[1]['name'], j[2]) for j in jobs} | {(c['name'], t) for _, c, t in new}
                jobs = [j for j in fresh if (j[1]['name'], j[2]) in waiting]
            while jobs and len(running) < lanes():
                r, city, tile = jobs.pop(0)
                running[pool.submit(build, city, tile)] = (r, city['name'], tile)
            if not running:
                time.sleep(5)
                continue
            finished, _ = wait(list(running), timeout=5, return_when=FIRST_COMPLETED)
            for future in finished:
                r, name, tile = running.pop(future)
                try:
                    row = future.result()
                except Exception as error:  # a failing tile is logged and skipped; the lane moves on
                    row = dict(city=name, tile=tile, state='failed', error=f'{type(error).__name__}: {error}'[-500:])
                row.update(ring=r, at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
                done.append(row)
                city = next(c for c in cities if c['name'] == name)
                with (Path(city['out']).parent / 'window-builds.jsonl').open('a') as log:
                    log.write(json.dumps(row) + '\n')
            summary = dict(updated=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), pid=os.getpid(),
                           minutes=round((time.time() - started) / 60, 1), lanes=lanes(),
                           running=sorted(f'{n}/{t}' for _, n, t in running.values()),
                           left={c['name']: sum(1 for j in jobs if j[1]['name'] == c['name']) for c in cities},
                           built={c['name']: sum(1 for d in done if d['city'] == c['name'] and d['state'] == 'built')
                                  for c in cities},
                           failed=sorted({f"{d['city']}/{d['tile']}" for d in done if d['state'] == 'failed'}))
            tmp = STATUS.with_suffix('.tmp')
            tmp.write_text(json.dumps(summary, indent=1))
            os.replace(tmp, STATUS)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
