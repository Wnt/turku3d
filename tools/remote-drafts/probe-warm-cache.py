"""Real OCI first/full warming with online heartbeats and unchanged stage-cache reuse."""
import argparse
import importlib.util
import json
import shutil
import time
from pathlib import Path

from turku3d import config as C
from turku3d.coarse.idle import IdlePrebuild, MIN_FREE
from turku3d.coarse.inputs import gather
from turku3d.coarse.service import Builder
from turku3d.coarse.sources import Sources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--overlay', type=Path)
    args = parser.parse_args()
    root = args.root
    root.mkdir(parents=True, exist_ok=False)
    assets = Path('/srv/turku3d/work/coarse')
    tiles = ['t0_0_0', 't0_0_-2']
    for tile in tiles:
        for name in ('.inputs', '.stages'):
            source = assets / name / tile
            if source.exists():
                shutil.copytree(source, root / name / tile)
    for pattern in ('shared', '.surface-library*', '.compiled-textures'):
        for source in assets.glob(pattern):
            if source.is_dir():
                shutil.copytree(source, root / source.name)
    sources = Sources()
    b = Builder(root, workers=4,
                input_key=lambda t: sources.input_key(C.VisualTile.parse(t)),
                structural_input_key=lambda t: sources.input_key(C.VisualTile.parse(t), first_pass=True))
    blocked = 't0_24_24'
    b.inputs = lambda tile: ['held-probe-raw'] if tile == blocked else []
    plan = lambda: ([(0, t) for t in [blocked, *tiles]], {'route_last_ring': 0})
    idle = IdlePrebuild(b, plan, lambda: True, free=lambda: MIN_FREE + 1)
    before = None
    if args.overlay:
        idle.tick()
        before = dict(queued=len(b.priority), building=len(b.building), state=idle.state)
        assert before['queued'] == before['building'] == 0
        spec = importlib.util.spec_from_file_location('turku3d.coarse._warm_overlay', args.overlay)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        idle = module.IdlePrebuild(b, plan, lambda: True, free=lambda: MIN_FREE + 1)
    started = time.monotonic()
    completed = {}
    maximum_warm = 0
    try:
        while len(completed) < len(tiles) and time.monotonic() - started < 240:
            with b.lock:
                b.demand.update('warm-probe-online', [blocked, *tiles], [], [])
                idle.activity()
            idle.tick()
            maximum_warm = max(maximum_warm, len(idle.tiles - set(b.pending)))
            for tile in tiles:
                if tile in completed or tile in b.building or not b.manifest(tile).exists():
                    continue
                manifest = json.loads(b.manifest(tile).read_text())
                if manifest.get('pass_kind') != 'full':
                    continue
                expected = len(gather(C.VisualTile.parse(tile), root, sources).buildings)
                assert manifest['buildings'] == expected
                assert len(manifest['collision']) == 16
                for entry in [*manifest['collision'].values(), *manifest['visual'].values()]:
                    assert (root / tile / entry['url']).stat().st_size == entry['bytes']
                request_started = time.monotonic()
                assert b.request(tile, 0)['state'] == 'built'
                completed[tile] = dict(ready_seconds=round(time.monotonic()-started, 3),
                                      request_ms=round((time.monotonic()-request_started)*1000, 3),
                                      buildings=expected, collision=16,
                                      stages=json.loads((root/tile/'stage-times.json').read_text()))
            time.sleep(.1)
        assert len(completed) == len(tiles), (completed.keys(), b.status())
        assert maximum_warm == 1, maximum_warm
        assert blocked in b.pending and time.time() - b.requested[blocked] < 2, b.status()
        health = b.build_health.snapshot(True)
        assert health['passes']['first']['built'] == 2, health
        assert health['passes']['full']['built'] == 2, health
        assert not b.failed and not b.cancelled, b.status()
        result = dict(root=str(root), before=before, completed=completed, health=health,
                      maximum_warm=maximum_warm, prebuild=idle.status())
        (root/'measurement.json').write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result), flush=True)
    finally:
        if len(completed) < len(tiles):
            with b.lock:
                for tile in list(b.building):
                    b.cancelled.add(tile)
                    b.run.cancel(tile)
        b.drain()
        b.wait_drained()
        b.stop()


if __name__ == '__main__':
    main()
