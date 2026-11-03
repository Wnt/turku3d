#!/usr/bin/env python3
"""Farm side of tile migrations: derive a migrated generation and apply it to finished tiles.

Runs on the farm host from a registered source snapshot, in the pipeline environment
(``shared-tiles-run.sh`` sets REPO, PYTHONPATH and nice 10). The Mac front end is
``scripts/dev/tile-migrate``.

  tile_migrate.py derive --farm-root ROOT --base ALIAS_OR_REVISION --code REVISION
  tile_migrate.py apply  --farm-root ROOT --revision DERIVED TILE...
  tile_migrate.py run    --farm-root ROOT --revision DERIVED [--jobs 6] [--watch SECONDS]
  tile_migrate.py status --farm-root ROOT --revision DERIVED
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared_tiles import REV, TILE, Farm, atomic_json, file_lock, read_json  # noqa: E402

MAX_JOBS = 6


class MissingBase(RuntimeError):
    pass


def revision_meta(root: Path, selector: str) -> dict:
    revision = read_json(root / 'aliases.json', {}).get(selector, selector)
    if not REV.fullmatch(revision):
        raise ValueError(f'unknown alias or revision {selector}')
    meta = read_json(root / 'revisions' / revision / 'revision.json')
    if not meta:
        raise KeyError(f'revision {revision} is not registered')
    return dict(meta, revision=meta.get('revision', revision))


def derive(root: Path, base: str, code: str) -> dict:
    """Write the derived revision of ``base`` with this snapshot's migration chain."""
    from turku3d.migrations import chain, chain_generations

    base_meta = revision_meta(root, base)
    if base_meta.get('kind') == 'migrated':  # Chains always start from a generator product.
        base_meta = revision_meta(root, base_meta['base_revision'])
    if not REV.fullmatch(code) or not (root / 'revisions' / code / 'repo').is_dir():
        raise KeyError(f'code snapshot {code} is not uploaded')
    migrations = chain()
    generations = chain_generations(base_meta['generation'], migrations)
    revision = hashlib.sha256(f"migrated:{base_meta['revision']}:{code}".encode()).hexdigest()
    meta = dict(
        kind='migrated', revision=revision, generation=generations[-1],
        commit=base_meta.get('commit', base_meta['revision']), code_revision=code,
        base_revision=base_meta['revision'], base_generation=base_meta['generation'],
        base_source_version=base_meta.get('source_version'),
        parents=[base_meta['generation'], *generations[:-1]],
        migrations=[dict(name=m.name, version=m.version, code_hash=m.code_hash(), generation=g)
                    for m, g in zip(migrations, generations)])
    destination = root / 'revisions' / revision
    if (destination / 'revision.json').exists():
        existing = read_json(destination / 'revision.json', {})
        if existing.get('generation') != meta['generation']:
            raise RuntimeError('derived revision exists with a different generation')
        return existing
    temporary = destination.with_name(f'{revision}.derive-{os.getpid()}')
    temporary.mkdir(parents=True)
    (temporary / 'repo').symlink_to(root / 'revisions' / code / 'repo', target_is_directory=True)
    atomic_json(temporary / 'revision.json', meta)
    try:
        temporary.rename(destination)
    except OSError:
        shutil.rmtree(temporary)
        if not (destination / 'revision.json').exists():
            raise
    return meta


def products(root: Path, generation: str) -> Path:
    return root / 'products' / generation


def migrate_tile(root: Path, meta: dict, tile: str) -> dict:
    """Migrate one finished base tile into the derived generation. Idempotent and resumable."""
    from turku3d.migrations import chain, core

    if not TILE.fullmatch(tile):
        raise ValueError(f'invalid tile {tile}')
    started = time.monotonic()
    generation = meta['generation']
    base = products(root, meta['base_generation'])
    target_root = products(root, generation)
    if not (base / tile / 'ready.json').exists():
        raise MissingBase(f'{tile} has no finished base product')
    with file_lock(root / 'locks' / f'{generation}-{tile}.lock'):
        base_sha = hashlib.sha256((base / tile / 'manifest.json').read_bytes()).hexdigest()
        ready = read_json(target_root / tile / 'ready.json', {})
        if ready.get('base_manifest_sha256') == base_sha and ready.get('generation') == generation:
            return dict(tile=tile, state='kept')
        migrations = chain()
        keys = [m.key for m in migrations]
        if keys != [f"{s['name']}@{s['version']}" for s in meta['migrations']]:
            raise RuntimeError(f'snapshot chain {keys} differs from the derived revision')
        # Resume from the longest finished prefix generation of the same base product.
        source, done = base, 0
        for index in range(len(migrations) - 1, 0, -1):
            prefix = products(root, meta['migrations'][index - 1]['generation'])
            if read_json(prefix / tile / 'ready.json', {}).get('base_manifest_sha256') == base_sha:
                source, done = prefix, index
                break
        product = core.Product(source / tile, json.loads((source / tile / 'manifest.json').read_bytes()))
        core.run(product, migrations[done:])
        product.manifest['migration_base'] = dict(
            revision=meta['base_revision'], generation=meta['base_generation'], manifest_sha256=base_sha)
        target_root.mkdir(parents=True, exist_ok=True)
        temporary = target_root / f'.{tile}.migrate-{os.getpid()}'
        if temporary.exists():
            shutil.rmtree(temporary)
        try:
            paths = core.materialize(product, temporary)
            final = target_root / tile
            if final.exists():  # An older or interrupted product of this generation.
                old = target_root / f'.{tile}.old-{os.getpid()}'
                final.rename(old)
                shutil.rmtree(old)
            temporary.rename(final)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        try:
            checked = Farm.validate(target_root, tile, product.manifest)
        except Exception:
            shutil.rmtree(target_root / tile, ignore_errors=True)
            raise
        written = sum(1 for p in product.written if p in paths)
        result = dict(revision=meta['revision'], generation=generation, kind='migrated', tile=tile,
                      base_revision=meta['base_revision'], base_generation=meta['base_generation'],
                      base_manifest_sha256=base_sha, source_generation=source.name,
                      migrations=keys, applied=keys[done:], payloads=len(paths), checked=checked,
                      written=written, linked=len(paths) - written,
                      seconds=round(time.monotonic() - started, 3))
        atomic_json(target_root / tile / 'ready.json', result)
        return dict(result, state='migrated')


def _worker(args):
    root, meta, tile = args
    try:
        return migrate_tile(Path(root), meta, tile)
    except MissingBase as error:
        return dict(tile=tile, state='missing-base', error=str(error))
    except Exception as error:  # Reported per tile; the run continues.
        return dict(tile=tile, state='failed', error=f'{type(error).__name__}: {error}'[-1000:])


def finished_base_tiles(root: Path, meta: dict) -> list[str]:
    base = products(root, meta['base_generation'])
    tiles = [p.parent.name for p in base.glob('t0_*/ready.json') if TILE.fullmatch(p.parent.name)]
    # Centre first, like the city build: the tiles players see first migrate first.
    return sorted(tiles, key=lambda t: (max(abs(int(v)) for v in t.split('_')[1:]), t))


def run(root: Path, meta: dict, jobs: int, tiles=None, watch=0, idle_exit=7200) -> dict:
    log_dir = root / 'migrations' / meta['generation']
    log_dir.mkdir(parents=True, exist_ok=True)
    counts, last_new = {}, time.monotonic()
    with ProcessPoolExecutor(max_workers=max(1, min(MAX_JOBS, jobs))) as pool:
        while True:
            todo = tiles or finished_base_tiles(root, meta)
            started = time.monotonic()
            changed = 0
            with (log_dir / 'run.jsonl').open('a') as log:
                for result in pool.map(_worker, [(str(root), meta, t) for t in todo], chunksize=8):
                    counts[result['state']] = counts.get(result['state'], 0) + 1
                    if result['state'] != 'kept':
                        changed += 1
                        log.write(json.dumps(dict(result, t=time.time())) + '\n')
                        log.flush()
            summary = dict(revision=meta['revision'], generation=meta['generation'],
                           base_generation=meta['base_generation'], tiles=len(todo), counts=counts,
                           pass_seconds=round(time.monotonic() - started, 1), updated=time.time(),
                           pid=os.getpid(), watching=bool(watch))
            atomic_json(log_dir / 'status.json', summary)
            if changed:
                last_new = time.monotonic()
            if not watch or tiles or time.monotonic() - last_new > idle_exit:
                return summary
            counts = {}
            time.sleep(watch)


def status(root: Path, meta: dict) -> dict:
    base = finished_base_tiles(root, meta)
    target = products(root, meta['generation'])
    done = sum((target / t / 'ready.json').exists() for t in base)
    return dict(revision=meta['revision'], generation=meta['generation'],
                base_generation=meta['base_generation'], base_tiles=len(base), migrated=done,
                migrations=[f"{s['name']}@{s['version']}" for s in meta['migrations']],
                run=read_json(root / 'migrations' / meta['generation'] / 'status.json'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=('derive', 'apply', 'run', 'status'))
    parser.add_argument('tiles', nargs='*')
    parser.add_argument('--farm-root', type=Path, required=True)
    parser.add_argument('--base')
    parser.add_argument('--code')
    parser.add_argument('--revision')
    parser.add_argument('--jobs', type=int, default=MAX_JOBS)
    parser.add_argument('--watch', type=float, default=0, help='rescan the base every N seconds')
    parser.add_argument('--idle-exit', type=float, default=7200)
    args = parser.parse_args(argv)
    root = args.farm_root.resolve()
    if args.action == 'derive':
        print(json.dumps(derive(root, args.base, args.code)))
        return 0
    meta = revision_meta(root, args.revision)
    if meta.get('kind') != 'migrated':
        raise SystemExit(f'{args.revision} is not a migrated revision')
    if args.action == 'status':
        print(json.dumps(status(root, meta)))
        return 0
    if args.action == 'apply':
        failed = 0
        for tile in args.tiles:
            result = _worker((str(root), meta, tile))
            failed += result['state'] not in ('kept', 'migrated')
            print(json.dumps(result))
        return 1 if failed else 0
    summary = run(root, meta, args.jobs, args.tiles or None, args.watch, args.idle_exit)
    print(json.dumps(summary))
    return 1 if summary['counts'].get('failed') else 0


if __name__ == '__main__':
    sys.exit(main())
