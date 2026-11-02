"""Git-content identity of tile producers; client, documentation and tests are excluded."""
import hashlib
from pathlib import PurePosixPath
import subprocess


ROOTS = ('pipeline/turku3d', 'pipeline/env/package-lock.json',
         'textures/turkutex', 'textures/library', 'data-manifests', 'game/public/basis')


def producer(path):
    parts = PurePosixPath(path).parts
    name = parts[-1]
    return (not any(p in ('tests', '__pycache__') for p in parts)
            and not name.endswith(('.md', '.test.mjs', '.test.js', '.pyc')))


def generation(revision, *, cwd=None):
    rows = subprocess.check_output(
        ['git', 'ls-tree', '-rz', revision, '--', *ROOTS], cwd=cwd).split(b'\0')
    sources = []
    for row in rows:
        if not row:
            continue
        metadata, path = row.split(b'\t', 1)
        if producer(path.decode()):
            # File bytes and location matter; an executable bit alone cannot change a tile.
            sources.append(path + b'\0' + metadata.split()[2])
    return hashlib.sha256(b'\n'.join(sources)).hexdigest()
