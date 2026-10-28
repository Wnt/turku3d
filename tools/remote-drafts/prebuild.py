"""Warm only startup collision coverage, yielding every build slot to player demand."""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from turku3d import config as C


def spawn_tiles(points=None, radius=200):
    """Match startupDraftTiles: default office/nearby car search and multiplayer starts.

    Saved player/car locations belong to the client's startup want-list; the server
    never receives or stores save games. No replacement-parent or background ring.
    """
    if points is None:
        x, _, z = C.en_to_local(C.OFFICE_E, C.OFFICE_N, 0)
        points = [(x, z)]
        spawns = C.REPO_ROOT / 'server/spawns.json'
        points.extend((p['E'], -p['N']) for p in json.loads(spawns.read_text())['points'])
    tiles = set()
    size = C.VISUAL_TILE_SIZE
    for x, z in points:
        for i in range(math.floor((x-radius)/size), math.floor((x+radius)/size)+1):
            for j in range(math.floor((z-radius)/size), math.floor((z+radius)/size)+1):
                if math.hypot(max(i*size-x, 0, x-(i+1)*size), max(j*size-z, 0, z-(j+1)*size)) <= radius:
                    tiles.add(f't0_{i}_{j}')
    return sorted(tiles)


def main():
    root = C.work_root()
    results = []
    def report(state, error=None):
        (root/'prebuild.json').write_text(json.dumps(
            {'scope': 'startup', 'state': state, 'results': results, 'error': error}, indent=2)+'\n')

    def fail(tile, reason):
        message = f'startup tile failed: {tile}: {reason}'
        results.append({'tile': tile, 'state': 'failed', 'error': reason})
        report('failed', message)
        raise RuntimeError(message)

    report('running')
    # Use the stable proxy; either producer slot can be active after a deployment.
    for tile in spawn_tiles():
        started = time.monotonic()
        while time.monotonic() - started < 600:
            try:
                state = json.loads((root/'coarse/.scheduler.json').read_text())
            except (OSError, ValueError):
                state = {}
            if state.get('foreground', 0):
                time.sleep(2)
                continue
            try:
                with urlopen(f'http://127.0.0.1:7764/coarse/{tile}/manifest.json', timeout=5) as r:
                    m = json.load(r)
                    if m.get('stale') or m.get('state') == 'failed':
                        fail(tile, 'build failed; last good output retained')
                    if r.status == 200 and not m.get('stale') and m.get('pass_kind') == 'full':
                        break
            except HTTPError as error:
                if error.code == 404:
                    fail(tile, f'HTTP {error.code}')
            except (URLError, TimeoutError):
                pass
            time.sleep(2)
        else:
            fail(tile, 'timed out')
        results.append({'tile': tile, 'state': 'done', 'seconds': round(time.monotonic()-started, 2)})
        report('running')
        print(json.dumps(results[-1]), flush=True)
    report('done')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
