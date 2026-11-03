#!/usr/bin/env python3
"""Receiver: finish a city generation on a shared host, paging raw sources chunk by chunk.

  resume.py ROOT --queue queue.json --tile-raw tile-raw.json --raw-from HOST:DATA_ROOT
            [--chunk 150] [--workers 10] [--link-dest LAN_SOURCE_VERSION]

ROOT is a private bulk root (state/, repo, ops/, farm/) whose farm/products/<generation> is the
LAN farm's own generation directory, so finished tiles land next to the pulled ones and the game
serves them at once. The queue is built in its given (Hilbert) order in chunks: each chunk's raw
files (`tile-raw.json`, from the pinned generator's own source resolution) are rsynced in while
the previous chunk builds, and deleted once no later chunk needs them, so the raw window stays a
few chunks wide however large the generation. Each chunk runs `bulk.py --walk` (transient
caches, memory-aware admission, the producing revision's validation). Resumable: built tiles
are skipped; a crash is restarted by the supervisor (systemd Restart=on-failure).
"""
import argparse
import concurrent.futures
import json
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def log(path, **row):
    row = dict(t=round(time.time(), 1), **row)
    with path.open('a') as out:
        out.write(json.dumps(row) + '\n')
    print(json.dumps(row), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('root', type=Path)
    p.add_argument('--queue', type=Path, required=True)
    p.add_argument('--tile-raw', type=Path, required=True)
    p.add_argument('--raw-from', required=True, help='host:path of the full source version')
    p.add_argument('--link-dest', type=Path, help='local source version whose identical raw files are hard-linked')
    p.add_argument('--chunk', type=int, default=75)
    p.add_argument('--workers', type=int, default=10)
    p.add_argument('--worker-memory-gib', type=float, default=3.3)
    p.add_argument('--keep-free-gib', type=float, default=45,
                   help='prefetch the next chunk only if this much stays free after it')
    a = p.parse_args()
    root = a.root.resolve(); state = root / 'state'
    version = json.loads((state / 'sources.json').read_text())['version']
    data = root / 'farm/sources/versions' / version
    generation = json.loads((state / 'revision.json').read_text())
    products = (root / 'farm/products').resolve()
    queue = json.loads(a.queue.read_text()); needs = json.loads(a.tile_raw.read_text())
    chunks = [queue[k:k + a.chunk] for k in range(0, len(queue), a.chunk)]
    events = state / 'resume.jsonl'
    host, remote = a.raw_from.split(':', 1)

    manifest = json.loads((data / 'source-version.json').read_text())['files']

    def paged(rel):
        """Large per-unit raw files, paged with the chunks; everything else stays (about 9 GB)."""
        return rel.startswith('raw/laz/pistepilvi/') or (
            rel.startswith('raw/ortho/') and rel.endswith(('.tif', '.tiff', '.jp2', '.jpg')))

    def rsync(files, label):
        files = sorted(f for f in files if not (data / f).exists())
        if not files:
            return 0
        with tempfile.NamedTemporaryFile('w', delete=False) as listing:
            listing.write('\n'.join(files) + '\n')
        command = ['rsync', '-a', '--files-from=' + listing.name]
        if a.link_dest:
            command.append('--link-dest=' + str(a.link_dest.resolve()))
        command += [f'{host}:{remote}/', str(data) + '/']
        started = time.monotonic()
        subprocess.run(command, check=True)
        Path(listing.name).unlink()
        log(events, event='raw-fetched', what=label, files=len(files),
            gb=round(sum(manifest[f]['bytes'] for f in files if f in manifest) / 1e9, 2),
            seconds=round(time.monotonic() - started, 1))
        return len(files)

    # Registries, WFS, OSM, ground lidar and ortho wrappers: small, read by presence checks of
    # every tile, so they are fetched once and kept.
    rsync([k for k in manifest if k.startswith('raw/') and not paged(k)], 'base')

    def built(tile):
        return any((g / tile / 'ready.json').exists() for g in products.iterdir() if g.is_dir())

    def fetch(index):
        return rsync({f for t in chunks[index] for f in needs.get(t, []) if paged(f)}, f'chunk-{index}')

    def release(index):
        later = {f for c in chunks[index + 1:] for t in c for f in needs.get(t, [])}
        removed = 0
        for f in {f for t in chunks[index] for f in needs.get(t, [])} - later:
            path = data / f
            if path.is_file() and paged(f):
                path.unlink(); removed += 1
        log(events, event='raw-released', chunk=index, files=removed)

    with concurrent.futures.ThreadPoolExecutor(1) as prefetch:
        ahead = None
        for index, chunk in enumerate(chunks):
            todo = [t for t in chunk if not built(t)]
            if not todo:
                release(index); continue
            if ahead is not None and ahead[0] == index:
                ahead[1].result()
            else:
                fetch(index)
            if index + 1 < len(chunks):
                upcoming = {f for t in chunks[index + 1] for f in needs.get(t, []) if paged(f) and not (data / f).exists()}
                size = sum(manifest[f]['bytes'] for f in upcoming if f in manifest)
                if shutil.disk_usage(data).free - size > a.keep_free_gib * 2**30:
                    ahead = (index + 1, prefetch.submit(fetch, index + 1))
                else:
                    ahead = None  # Fetched after this chunk releases its raw: the shared host stays clear.
                    log(events, event='prefetch-deferred', chunk=index + 1, gb=round(size / 1e9, 1))
            walk = state / f'resume-chunk-{index}.json'
            walk.write_text(json.dumps(todo))
            started = time.monotonic()
            log(events, event='chunk-start', chunk=index, tiles=len(todo), of=len(chunks))
            while True:
                result = subprocess.run([str(root / 'run.sh'), 'python', str(root / 'ops/bulk.py'), str(root),
                                         '--walk', str(walk), '--workers', str(a.workers),
                                         '--worker-memory-gib', str(a.worker_memory_gib), '--reserve-gib', '.2'],
                                        cwd=root)
                if result.returncode != 75:
                    break
                time.sleep(15)  # Disk or memory pressure: let the shared host recover, then resume.
            left = [t for t in todo if not built(t)]
            log(events, event='chunk-done', chunk=index, exit=result.returncode, built=len(todo) - len(left),
                failed=len(left), seconds=round(time.monotonic() - started, 1),
                tiles_per_h=round((len(todo) - len(left)) / max(1, time.monotonic() - started) * 3600))
            release(index)
    log(events, event='done', generation=generation.get('generation'))


if __name__ == '__main__':
    main()
