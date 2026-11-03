#!/usr/bin/env python3
"""Host, before the window: every walk tile's generator inputs are in the frozen source version.

Uses the pinned generator's own source resolution (coarse.sources / coarse.inputs) for each of
the walk's buildable tiles: no missing lidar, ortho or WFS unit (every layer, street furniture
included), OSM present, every prepared dataset's raw files on disk. Then the run-wide datasets:
the playground overlay and the landmark/facade descriptions, which must match the LAN overlay
byte for byte when --expect gives its hashes. Fails loudly (exit 1) and names every gap.

  validate-inputs.py ROOT [--repo PINNED_REPO] [--expect overlay-hashes.json] [--limit N]
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root', type=Path)
    p.add_argument('--expect', type=Path, help='{relative path: sha256} of the LAN source overlay')
    p.add_argument('--limit', type=int)
    p.add_argument('--repo', type=Path, help='generator checkout (default: ROOT/repo, the pinned revision)')
    p.add_argument('--report', type=Path)
    a = p.parse_args()
    root = a.root.resolve()
    state = root / 'state'
    sources_meta = json.loads((state / 'sources.json').read_text())
    data = root / 'farm/sources/versions' / sources_meta['version']
    os.environ['TURKU3D_DATA'] = str(data)
    os.environ.setdefault('TURKU3D_WORK', str(state / 'validate-work'))
    sys.path.insert(0, str((a.repo or root / 'repo').resolve() / 'pipeline'))
    from turku3d import config as C
    from turku3d.coarse.inputs import _sources
    from turku3d.coarse.sources import WFS_LAYERS, NoCoverage, Sources
    from turku3d.playgrounds import DATASET as PLAYGROUNDS

    walk = json.loads((state / 'walk.json').read_text())
    unavailable = set(json.loads((state / 'PREFETCH_COMPLETE.json').read_text()).get('unavailable_tiles', []))
    tiles = [t for t in walk if t not in unavailable][:a.limit]
    sources = Sources()
    failures, excluded, started = {}, [], time.monotonic()
    for tile_id in tiles:
        tile = C.VisualTile.parse(tile_id)
        gaps = []
        try:
            gaps += [f'missing unit {u.key}' for u in sources.missing(tile)]
        except NoCoverage:
            excluded.append(tile_id)  # Open water without lidar: an expected exclusion.
            continue
        if sources.osm_source(tile) is None:
            gaps.append('missing unit osm')
        gaps += [f'WFS {name} not found' for name in WFS_LAYERS if not sources.wfs_source(name, tile)[0]]
        for dataset, paths in _sources(tile, sources).items():
            if paths is None:
                gaps.append(f'{dataset}: raw inputs not on disk')
                continue
            gaps += [f'{dataset}: {Path(f).relative_to(data) if Path(f).is_relative_to(data) else f} absent'
                     for f in paths if f is not None and not Path(f).exists()]
        if gaps:
            failures[tile_id] = sorted(set(gaps))
    overlay = {}
    playgrounds = data / PLAYGROUNDS
    overlay['derived/playgrounds/playgrounds.json'] = playgrounds.is_file() and playgrounds.stat().st_size > 0
    descriptions = sorted((data / 'landmark-descriptions').glob('*.json'))
    overlay['landmark-descriptions'] = len(descriptions)
    mismatched = []
    if a.expect:
        for relative, digest in json.loads(a.expect.read_text()).items():
            path = data / relative
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                mismatched.append(relative)
    run_wide = []
    if not overlay['derived/playgrounds/playgrounds.json']:
        run_wide.append('derived/playgrounds/playgrounds.json absent')
    if not descriptions:
        run_wide.append('landmark-descriptions absent')
    run_wide += [f'{r} differs from the LAN overlay' for r in mismatched]
    report = dict(source_version=sources_meta['version'], tiles=len(tiles), expected_exclusions=len(excluded),
                  failing_tiles=len(failures), run_wide_failures=run_wide, overlay=overlay,
                  gap_kinds=dict(sorted(_kinds(failures).items(), key=lambda kv: -kv[1])),
                  failures=dict(list(failures.items())[:200]), seconds=round(time.monotonic() - started, 1),
                  ok=not failures and not run_wide)
    out = a.report or state / 'input-validation.json'
    out.write_text(json.dumps(report, indent=1) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'failures'}), flush=True)
    return 0 if report['ok'] else 1


def _kinds(failures):
    kinds = {}
    for gaps in failures.values():
        for gap in gaps:
            kind = gap.split(':')[0] if ':' in gap else ' '.join(gap.split()[:3])
            kinds[kind] = kinds.get(kind, 0) + 1
    return kinds


if __name__ == '__main__':
    raise SystemExit(main())
