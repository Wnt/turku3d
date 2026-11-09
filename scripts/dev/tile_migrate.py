#!/usr/bin/env python3
"""Farm side of tile migrations: derive a migrated generation and apply it to finished tiles.

Runs on the farm host from a registered source snapshot, in the pipeline environment
(``shared-tiles-run.sh`` sets REPO, PYTHONPATH and nice 10). The Mac front end is
``scripts/dev/tile-migrate``.

  tile_migrate.py derive --farm-root ROOT --base ALIAS_OR_REVISION --code REVISION
                         [--rebuilds-from DERIVED] [--rebuild-revision REVISION]
  tile_migrate.py apply  --farm-root ROOT --revision DERIVED TILE...
  tile_migrate.py run    --farm-root ROOT --revision DERIVED [--jobs 6] [--watch SECONDS]
  tile_migrate.py status --farm-root ROOT --revision DERIVED
  tile_migrate.py parity --farm-root ROOT --revision DERIVED --generated REVISION TILE...
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
from urllib.request import urlopen

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


def use_base_data(root: Path, meta: dict) -> None:
    """Source reads see the base's own source version (its ``data_root``), as on the farm daemon's on-request
    migrations; the runner's default TURKU3D_DATA is the combined working data, which can differ."""
    data = revision_meta(root, meta['base_revision']).get('data_root')
    if data:
        os.environ['TURKU3D_DATA'] = data


def rebuild_revision(root: Path, code: str, source_version) -> dict:
    """The generator of the code snapshot with the base's source version (farm overlay ids)."""
    code_meta = revision_meta(root, code)
    if source_version:
        revision = hashlib.sha256(f'{code}:{source_version}'.encode()).hexdigest()
        generation = hashlib.sha256(f"{code_meta['generation']}:{source_version}".encode()).hexdigest()
    else:
        revision, generation = code, code_meta['generation']
    meta = read_json(root / 'revisions' / revision / 'revision.json')
    if not meta or meta.get('generation') != generation:
        raise KeyError(f'rebuild revision {revision} is not registered (register the code with the '
                       f'base source version first)')
    return dict(revision=revision, generation=generation)


def inherited_rebuilds(root: Path, selector: str | None, migrations) -> dict:
    """Spot-rebuild generators of an earlier derived revision whose step this chain repeats.

    A rebuild step's tiles are the products of the generator that introduced it. A later chain
    that keeps the step unchanged (same key and code hash) reuses those products, so extending the
    chain resumes from its finished prefix instead of rebuilding the class with every new commit."""
    if not selector:
        return {}
    meta = revision_meta(root, selector)
    if meta.get('kind') != 'migrated':
        raise ValueError(f'{selector} is not a migrated revision')
    steps = {(s['name'], s['version'], s['code_hash']): s for s in meta['migrations']}
    out = {}
    for m in migrations:
        step = steps.get((m.name, m.version, m.code_hash()))
        if step and step.get('rebuild_revision'):
            out[m.name] = dict(revision=step['rebuild_revision'], generation=step['rebuild_generation'])
    return out


def registered_rebuild(root: Path, selector: str) -> dict:
    """A registered generator revision (with its source version) whose finished products a new spot-rebuild step
    adopts instead of the code snapshot's own: products of a generator that already has the step's fix need no
    new builds. They record that generator's chain, which ends before the new step (``run_chain``)."""
    meta = revision_meta(root, selector)
    if meta.get('kind') == 'migrated' or not REV.fullmatch(meta.get('generation', '')):
        raise ValueError(f'{selector} is not a registered generator revision')
    return dict(revision=meta['revision'], generation=meta['generation'])


def derive(root: Path, base: str, code: str, rebuilds_from: str | None = None,
           rebuild_from: str | None = None) -> dict:
    """Write the derived revision of ``base`` with this snapshot's migration chain. ``rebuilds_from``: inherit the
    unchanged spot rebuilds of that derived revision; ``rebuild_from``: the generator revision whose products the
    other spot-rebuild steps adopt (default: this snapshot's generator with the base's source version)."""
    from turku3d.migrations import Rebuild, chain, chain_generations

    base_meta = revision_meta(root, base)
    if base_meta.get('kind') == 'migrated':  # Chains always start from a generator product.
        base_meta = revision_meta(root, base_meta['base_revision'])
    if not REV.fullmatch(code) or not (root / 'revisions' / code / 'repo').is_dir():
        raise KeyError(f'code snapshot {code} is not uploaded')
    migrations = chain()
    inherited = inherited_rebuilds(root, rebuilds_from, migrations)
    adopted = registered_rebuild(root, rebuild_from) if rebuild_from else None
    rebuilds = {m.name: inherited.get(m.name) or adopted
                or rebuild_revision(root, code, base_meta.get('source_version'))
                for m in migrations if isinstance(m, Rebuild)}
    generations = chain_generations(base_meta['generation'], migrations,
                                    {name: r['generation'] for name, r in rebuilds.items()})
    # Inherited or adopted rebuilds give the same code another chain generation, hence another revision.
    suffix = f":{generations[-1]}" if inherited or adopted else ""
    revision = hashlib.sha256(f"migrated:{base_meta['revision']}:{code}{suffix}".encode()).hexdigest()
    meta = dict(
        kind='migrated', revision=revision, generation=generations[-1],
        commit=base_meta.get('commit', base_meta['revision']), code_revision=code,
        base_revision=base_meta['revision'], base_generation=base_meta['generation'],
        base_source_version=base_meta.get('source_version'),
        parents=[base_meta['generation'], *generations[:-1]],
        migrations=[dict(name=m.name, version=m.version, code_hash=m.code_hash(), generation=g,
                         **({f'rebuild_{k}': v for k, v in rebuilds[m.name].items()}
                            if m.name in rebuilds else {}))
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


def checkpoint(meta: dict, key: str) -> dict:
    """The derived revision cut after step ``key``: migrating a tile to it stores that prefix generation's product,
    which a later chain with other steps after ``key`` resumes from (migrate_tile's longest finished prefix)."""
    keys = [f"{s['name']}@{s['version']}" for s in meta['migrations']]
    if key not in keys:
        raise ValueError(f'{key} is not a step of {keys}')
    steps = meta['migrations'][:keys.index(key) + 1]
    return dict(meta, generation=steps[-1]['generation'], migrations=steps, checkpoint_of=meta['generation'])


def products(root: Path, generation: str) -> Path:
    return root / 'products' / generation


_VERIFIED = {}


def verified_chain(meta: dict):
    """This snapshot's chain, refused unless its keys and code hashes are the derived revision's."""
    from turku3d.migrations import chain

    theirs = tuple((f"{s['name']}@{s['version']}", s['code_hash']) for s in meta['migrations'])
    if theirs not in _VERIFIED:
        migrations = chain()
        mine = tuple((m.key, m.code_hash()) for m in migrations)
        if meta.get('checkpoint_of'):  # A checkpoint: the first steps of the derived revision's chain.
            mine, migrations = mine[:len(theirs)], migrations[:len(theirs)]
        if mine != theirs:
            raise RuntimeError(f'snapshot chain {mine} differs from the derived revision {theirs}')
        _VERIFIED[theirs] = migrations
    return _VERIFIED[theirs]


def run_chain(core, product, migrations, done: int) -> None:
    """``core.run`` from step ``done``, segment by segment up to each spot-rebuild step.

    A rebuild built by this chain's commit records the whole chain, and ``core.run`` stops there. A rebuild
    inherited from an earlier, shorter chain (``--rebuilds-from``, same step code) records that chain only, a prefix
    of this one through the rebuild step: the steps after that prefix are then applied to the adopted product, as
    on every other tile, instead of failing on the shorter record. A rebuild adopted from the generator before a new
    step (``--rebuild-revision``) records the chain up to that step only; the step is a no-op in a generator that
    already has its fix, so the adopted product also records the step itself."""
    keys = [dict(name=m.name, version=m.version) for m in migrations]
    while done < len(migrations):
        end = next((i + 1 for i in range(done, len(migrations)) if isinstance(migrations[i], core.Rebuild)),
                   len(migrations))
        full, before = migrations, False
        step = migrations[end - 1]
        if isinstance(step, core.Rebuild) and product.rebuilds and step.name in product.rebuilds:
            path = product.rebuilds[step.name] / product.tile / 'manifest.json'
            recorded = read_json(path, {}).get('migrations') if path.exists() else None
            if recorded and end - 1 <= len(recorded) < len(keys) and recorded == keys[:len(recorded)]:
                full, before = migrations[:len(recorded)], len(recorded) == end - 1
        core.run(product, migrations[done:end], full=full)
        if before and product.manifest.get('migrations') == keys[:end - 1]:
            product.manifest['migrations'] = keys[:end]
        reached = len(product.manifest.get('migrations') or [])
        if product.manifest.get('migrations') != keys[:reached] or reached < end:
            raise RuntimeError(f"product records {product.manifest.get('migrations')}, not a prefix of {keys}")
        done = reached


def migrate_tile(root: Path, meta: dict, tile: str) -> dict:
    """Migrate one finished base tile into the derived generation. Idempotent and resumable."""
    from turku3d.migrations import core

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
        migrations = verified_chain(meta)
        keys = [m.key for m in migrations]
        # Resume from the longest finished prefix generation of the same base product.
        source, done = base, 0
        for index in range(len(migrations) - 1, 0, -1):
            prefix = products(root, meta['migrations'][index - 1]['generation'])
            if read_json(prefix / tile / 'ready.json', {}).get('base_manifest_sha256') == base_sha:
                source, done = prefix, index
                break
        product = core.Product(source / tile, json.loads((source / tile / 'manifest.json').read_bytes()))
        product.rebuilds = {step['name']: products(root, step['rebuild_generation'])
                            for step in meta['migrations'] if step.get('rebuild_generation')}
        run_chain(core, product, migrations, done)
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
        rebuilt = product.directory.parent.name not in (source.name, base.name)
        result = dict(revision=meta['revision'], generation=generation, kind='migrated', tile=tile,
                      base_revision=meta['base_revision'], base_generation=meta['base_generation'],
                      base_manifest_sha256=base_sha, source_generation=source.name,
                      migrations=keys, applied=keys[done:], payloads=len(paths), checked=checked,
                      written=written, linked=len(paths) - written,
                      rebuilt_from=product.directory.parent.name if rebuilt else None,
                      seconds=round(time.monotonic() - started, 3))
        atomic_json(target_root / tile / 'ready.json', result)
        return dict(result, state='migrated')


def _worker(args):
    from turku3d.migrations import MissingRebuild

    root, meta, tile = args
    try:
        return migrate_tile(Path(root), meta, tile)
    except MissingRebuild as error:
        step = next(s for s in meta['migrations'] if s['name'] == error.name)
        return dict(tile=tile, state='missing-rebuild', rebuild_revision=step['rebuild_revision'],
                    rebuild_generation=step['rebuild_generation'], error=str(error))
    except MissingBase as error:
        return dict(tile=tile, state='missing-base', error=str(error))
    except Exception as error:  # Reported per tile; the run continues.
        return dict(tile=tile, state='failed', error=f'{type(error).__name__}: {error}'[-1000:])


def finished_base_tiles(root: Path, meta: dict) -> list[str]:
    base = products(root, meta['base_generation'])
    tiles = [p.parent.name for p in base.glob('t0_*/ready.json') if TILE.fullmatch(p.parent.name)]
    # Centre first, like the city build: the tiles players see first migrate first.
    return sorted(tiles, key=lambda t: (max(abs(int(v)) for v in t.split('_')[1:]), t))


def request_rebuild(farm_url, result):
    """An explicit farm build of a spot-rebuild tile (no distance: never moves the game's pin)."""
    if not farm_url:
        return
    try:
        urlopen(f"{farm_url}/v/{result['rebuild_revision']}/coarse/{result['tile']}/manifest.json",
                timeout=30).read()
    except OSError:
        pass  # Queue full or busy: the next pass asks again.


def run(root: Path, meta: dict, jobs: int, tiles=None, watch=0, idle_exit=7200, farm_url=None) -> dict:
    log_dir = root / 'migrations' / meta['generation']
    log_dir.mkdir(parents=True, exist_ok=True)
    counts, last_new, finished = {}, time.monotonic(), set()
    with ProcessPoolExecutor(max_workers=max(1, min(MAX_JOBS, jobs))) as pool:
        while True:
            # Base products are immutable: a watching pass checks only tiles it has not finished.
            todo = tiles or [t for t in finished_base_tiles(root, meta) if t not in finished]
            started = time.monotonic()
            changed = 0
            with (log_dir / 'run.jsonl').open('a') as log:
                for result in pool.map(_worker, [(str(root), meta, t) for t in todo], chunksize=8):
                    counts[result['state']] = counts.get(result['state'], 0) + 1
                    if result['state'] in ('kept', 'migrated'):
                        finished.add(result['tile'])
                    elif result['state'] == 'missing-rebuild':
                        request_rebuild(farm_url, result)
                    if result['state'] != 'kept':
                        changed += 1
                        log.write(json.dumps(dict(result, t=time.time())) + '\n')
                        log.flush()
            summary = dict(revision=meta['revision'], generation=meta['generation'],
                           base_generation=meta['base_generation'], tiles=len(todo), counts=counts,
                           finished=len(finished),
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


# Build provenance: when, by which producer code and from which input paths (snapshot paths
# differ per revision; ``inputs`` keys the content), not what the tile contains.
PROVENANCE = {'built', 'seconds', 'code', 'collision_code', 'migration_base', 'sources'}


def _content(value):
    """A manifest value without nested stage timings."""
    if isinstance(value, dict):
        return {k: _content(v) for k, v in value.items() if k != 'seconds'}
    if isinstance(value, list):
        return [_content(v) for v in value]
    return value


def parity(root: Path, meta: dict, generated: str, tile: str) -> dict:
    """A fresh generator build at the migration's commit versus the migrated base product."""
    from turku3d.migrations import core

    sides = {}
    for name, generation in (('migrated', meta['generation']),
                             ('generated', revision_meta(root, generated)['generation'])):
        directory = products(root, generation) / tile
        if not (directory / 'ready.json').exists():
            raise MissingBase(f'{tile} is not finished in {name} generation {generation}')
        manifest = json.loads((directory / 'manifest.json').read_bytes())
        product = core.Product(directory, manifest)
        sides[name] = (manifest, {p: hashlib.sha256((directory / p).read_bytes()).hexdigest()
                                  for p in core.references(product)})
    (left, a), (right, b) = sides['migrated'], sides['generated']
    payloads = sorted(set(a) ^ set(b) | {p for p in set(a) & set(b) if a[p] != b[p]})
    keys = sorted(k for k in set(left) | set(right) if _content(left.get(k)) != _content(right.get(k)))
    content = [k for k in keys if k not in PROVENANCE]
    return dict(tile=tile, identical=not payloads and not content, payloads=len(a),
                differing_payloads=payloads[:20], differing_content_keys=content,
                differing_provenance_keys=[k for k in keys if k in PROVENANCE])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=('derive', 'apply', 'run', 'status', 'parity'))
    parser.add_argument('tiles', nargs='*')
    parser.add_argument('--farm-root', type=Path, required=True)
    parser.add_argument('--base')
    parser.add_argument('--code')
    parser.add_argument('--rebuilds-from', help='derive: reuse the spot rebuilds of this derived revision')
    parser.add_argument('--rebuild-revision', help='derive: new spot-rebuild steps adopt the finished products of '
                        'this registered generator revision (it already has their fix) instead of new builds')
    parser.add_argument('--revision')
    parser.add_argument('--generated', help='parity: generator alias or revision of the same tile')
    parser.add_argument('--jobs', type=int, default=MAX_JOBS)
    parser.add_argument('--watch', type=float, default=0, help='rescan the base every N seconds')
    parser.add_argument('--idle-exit', type=float, default=7200)
    parser.add_argument('--farm-url', default='http://192.168.1.114:7950',
                        help='run: ask the farm to build missing spot-rebuild tiles')
    args = parser.parse_args(argv)
    root = args.farm_root.resolve()
    if args.action == 'derive':
        print(json.dumps(derive(root, args.base, args.code, args.rebuilds_from, args.rebuild_revision)))
        return 0
    meta = revision_meta(root, args.revision)
    if meta.get('kind') != 'migrated':
        raise SystemExit(f'{args.revision} is not a migrated revision')
    if args.action == 'status':
        print(json.dumps(status(root, meta)))
        return 0
    use_base_data(root, meta)
    if args.action == 'parity':
        results = [parity(root, meta, args.generated, tile) for tile in args.tiles]
        for result in results:
            print(json.dumps(result))
        return 0 if all(r['identical'] for r in results) else 1
    if args.action == 'apply':
        states = []
        for tile in args.tiles:
            result = _worker((str(root), meta, tile))
            states.append(result['state'])
            print(json.dumps(result), flush=True)
        if any(state not in ('kept', 'migrated', 'missing-rebuild') for state in states):
            return 1
        return 4 if 'missing-rebuild' in states else 0  # 4: the farm builds the rebuild first
    summary = run(root, meta, args.jobs, args.tiles or None, args.watch, args.idle_exit, args.farm_url)
    print(json.dumps(summary))
    return 1 if summary['counts'].get('failed') else 0


if __name__ == '__main__':
    sys.exit(main())
