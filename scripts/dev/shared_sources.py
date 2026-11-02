#!/usr/bin/env python3
"""Acquire missing survey units into an owned overlay; never write through shared raw symlinks."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid


def merge_tree(base, destination, additions):
    """Clone only changed directory spines; all existing source files stay read-only links."""
    destination.mkdir(parents=True, exist_ok=True)
    groups = {}
    for relative, source in additions.items():
        first, *rest = Path(relative).parts
        groups.setdefault(first, {})[Path(*rest).as_posix() if rest else ''] = source
    if base.is_dir():
        for child in base.iterdir():
            if child.name not in groups:
                (destination / child.name).symlink_to(child.resolve(), target_is_directory=child.is_dir())
    for name, nested in groups.items():
        target = destination / name
        if '' in nested:
            if len(nested) != 1:
                raise ValueError('source file/directory conflict')
            target.symlink_to(nested[''].resolve())
        else:
            merge_tree(base / name, target, nested)


def publish(base, root, downloads):
    base, root = base.resolve(), root.resolve()
    additions = {}
    hashes = {}
    for directory in downloads:
        for source in sorted((directory / 'raw').rglob('*')):
            if (not source.is_file() or source.name.startswith(".")
                    or source.name in ("MANIFEST.jsonl", "SHA256SUMS")):
                continue
            relative = source.relative_to(directory)
            if relative in additions:
                raise ValueError(f'overlapping downloads: {relative}')
            additions[relative] = source
            with source.open('rb') as stream:
                hashes[str(relative)] = hashlib.file_digest(stream, 'sha256').hexdigest()
    # Lidar admission verifies the checksum registry, not only file existence. Merge
    # registry records into an owned immutable file; never append to the shared registry.
    registries = {p.relative_to(directory) for directory in downloads
                  for p in (directory / 'raw').glob('*/MANIFEST.jsonl')}
    for relative in sorted(registries):
        records = {}
        for directory in [base, *downloads]:
            source = directory / relative
            if source.exists():
                for line in source.read_text().splitlines():
                    if line.strip():
                        row = json.loads(line)
                        records[row['path']] = row
        data = ''.join(json.dumps(row, sort_keys=True) + '\n' for _, row in sorted(records.items())).encode()
        digest = hashlib.sha256(data).hexdigest()
        source = root / 'registries' / (digest + '.jsonl')
        source.parent.mkdir(parents=True, exist_ok=True)
        if not source.exists():
            source.write_bytes(data)
        additions[relative] = source
        hashes[str(relative)] = digest
    version = hashlib.sha256(json.dumps({'base': str(base.resolve()), 'files': hashes}, sort_keys=True).encode()).hexdigest()
    directory = root / 'versions' / version
    if not directory.exists():
        temporary = root / 'versions' / ('.' + uuid.uuid4().hex)
        merge_tree(base, temporary, additions)
        (temporary / 'source-version.json').write_text(json.dumps(dict(version=version, files=hashes)) + '\n')
        temporary.rename(directory)
    pointer = root / ('.current-' + uuid.uuid4().hex)
    pointer.symlink_to(directory, target_is_directory=True)
    pointer.replace(root / 'current')
    return directory


def validate(data, blocks):
    os.environ['TURKU3D_DATA'] = str(data)
    from turku3d import config as C
    from turku3d.coarse.sources import Sources, block_tiles, WFS_LAYERS, NoCoverage
    sources = Sources()
    result = {}
    for block in blocks:
        for tile in block_tiles(C.LazTile.parse(block)):
            try:
                paths, missing = sources.lidar_paths(tile)
                absent = [f'lidar/{b.name}' for b in missing]
                if not all(sources.wfs_source(name, tile)[0] for name in WFS_LAYERS[:3]):
                    absent.append('wfs/' + block)
                result[tile.id] = dict(ready=not absent and bool(paths), missing=absent)
            except NoCoverage as error:
                result[tile.id] = dict(ready=False, outside_coverage=str(error))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--runner', type=Path, required=True)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--radius', default='8000')
    args = parser.parse_args()
    root = args.root.resolve()
    if root.is_relative_to(args.base.resolve()):
        raise ValueError('overlay must be outside shared input root')
    root.mkdir(parents=True, exist_ok=True)
    (root / 'logs').mkdir(exist_ok=True)
    units = json.loads(args.inventory.read_text())[args.radius]['missing_units']
    downloads = []
    for unit in units:
        dataset, block = unit.split('/')
        if dataset not in ('lidar', 'wfs') or not block.replace('_', '').isdigit():
            raise ValueError('only explicitly inventoried lidar/WFS units are allowed')
        directory = root / 'downloads' / (dataset + '-' + block)
        directory.mkdir(parents=True, exist_ok=True)
        if not (directory / 'complete.json').exists():
            if shutil.disk_usage(root).free < 29 * 2**30:
                raise RuntimeError('source acquisition disk floor')
            env = dict(os.environ, REPO=str(args.repo), TURKU3D_DATA=str(directory),
                       TURKU3D_WORK=str(root / 'work'))
            started = time.monotonic()
            with (root / 'logs' / (dataset + '-' + block + '.log')).open('a') as log:
                subprocess.run([str(args.runner), 'python', '-m', 'turku3d.coarse', 'fetch', unit],
                               env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
            required = directory / ('raw/laz/maanpinta/' + block + '.laz') if dataset == 'lidar' else directory / ('raw/wfs/coarse-' + block)
            if not required.exists():
                raise RuntimeError(f'fetch returned without {required}')
            receipt = dict(unit=unit, seconds=round(time.monotonic() - started, 3))
            (directory / 'complete.json').write_text(json.dumps(receipt) + '\n')
            print(json.dumps(receipt), flush=True)
        downloads.append(directory)
    data = publish(args.base, root, downloads)
    report = validate(data, sorted({u.split('/')[1] for u in units}))
    (root / 'validation.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(data=str(data), quadrants=len(report), ready=sum(r['ready'] for r in report.values()),
                          missing={k:v for k,v in report.items() if not v['ready']})), flush=True)


if __name__ == '__main__':
    main()
