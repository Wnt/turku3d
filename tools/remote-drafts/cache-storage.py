"""Move only rebuildable draft caches onto the boot disk, preserving byte identities.

Run while producer builders and the prefetcher are stopped; cached serving may continue.
The destination must be an operator-created, user-writable directory on the boot volume.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from turku3d.stage_cache import compress_payload

CACHE_PATHS = ('coarse/.stages', 'coarse/.inputs', 'cache/lidar', 'cache/coarse-roofs', 'cache/coarse-area', 'cache/inputs')


def relocate(work, destination):
    started = time.monotonic()
    destination.mkdir(parents=True, exist_ok=True)
    if work.stat().st_dev == destination.stat().st_dev:
        raise RuntimeError('cache destination must use the boot volume')
    before = {str(p): shutil.disk_usage(p).free for p in (work, destination)}
    for relative in CACHE_PATHS:
        source, target = work / relative, destination / relative
        if source.is_symlink():
            if source.resolve() != target.resolve():
                raise RuntimeError(f'unexpected cache link: {source}')
            continue
        if not source.exists():
            source.mkdir(parents=True)
        size = sum(p.stat().st_size for p in source.rglob('*') if p.is_file())
        if shutil.disk_usage(destination).free - size < 6 * 1024**3:
            raise RuntimeError('relocation would leave less than 6 GiB for the OS')
        target.mkdir(parents=True, exist_ok=True)
        subprocess.run(['rsync', '-a', '--checksum', str(source) + '/', str(target) + '/'], check=True)
        # Verify every copied file before unlinking its source, including cache lock/pointer files.
        for path in source.rglob('*'):
            if path.is_file():
                copy = target / path.relative_to(source)
                with path.open('rb') as original, copy.open('rb') as copied:
                    if hashlib.file_digest(original, 'sha256').digest() != hashlib.file_digest(copied, 'sha256').digest():
                        raise RuntimeError(f'cache copy mismatch: {path}')
        backup = source.with_name(source.name + '.relocated')
        source.rename(backup)
        source.symlink_to(target, target_is_directory=True)
        shutil.rmtree(backup)
    return dict(seconds=round(time.monotonic() - started, 3), before=before,
                after={str(p): shutil.disk_usage(p).free for p in (work, destination)})


def compress_existing(root):
    started = time.monotonic()
    before = after = count = 0
    # Caller has stopped builders; namespace lock files remain in place.
    for path in root.rglob('*.pkl'):
        size = path.stat().st_size
        before += size
        with path.open('rb') as handle:
            handle.seek(32)
            if handle.read(5) == b'T3DZ1':
                after += size
                continue
        data = path.read_bytes()
        if len(data) < 32 or hashlib.sha256(data[32:]).digest() != data[:32]:
            after += len(data)
            continue
        payload = compress_payload(data[32:])
        encoded = hashlib.sha256(payload).digest() + payload
        after += len(encoded)
        if len(encoded) < len(data):
            temporary = path.with_suffix('.compress-tmp')
            temporary.write_bytes(encoded); temporary.replace(path)
            count += 1
    return dict(files=count, before=before, after=after, seconds=round(time.monotonic()-started, 3))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps({'relocation': relocate(args.work, args.destination),
                      'compression': compress_existing(args.destination / 'coarse/.stages')}), flush=True)
