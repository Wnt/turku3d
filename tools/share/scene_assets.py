#!/usr/bin/env python3
"""Package the committed scene's exact immutable asset closure; no generation or tests.

Large products live in TURKU3D_PREPARED_ASSETS (the shared/ directory), never in
the source archive. Verify their content addresses before any release upload.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import struct
import subprocess
from urllib.parse import urlsplit

NAME = re.compile(r'([a-f0-9]{64})\.[a-z0-9]+')


def closure(scenes, store):
    store = Path(store)
    required, pending = {}, []

    def add(url, base='', declared=None):
        parsed = urlsplit(url)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or url.startswith('/'):
            raise ValueError(f'Prepared asset must use a relative immutable URL: {url}')
        path = posixpath.normpath(posixpath.join(base, parsed.path))
        parts = PurePosixPath(path).parts
        if len(parts) != 2 or parts[0] != 'shared' or not (match := NAME.fullmatch(parts[1])):
            raise ValueError(f'Prepared asset must resolve inside shared/: {url}')
        digest = match[1]
        if declared is not None and digest != declared:
            raise ValueError(f'Asset name/hash mismatch: {url}')
        if path not in required:
            required[path] = digest
            pending.append(path)

    for scene in scenes:
        for asset in scene['assets']:
            add(asset['url'], declared=asset['sha256'])
        for material in scene['materials']:
            for key in ('baseColorTexture', 'normalTexture', 'ormTexture'):
                if key in material:
                    texture = material[key]
                    add(texture['url'], declared=texture['sha256'])
    rows = []
    while pending:
        path = pending.pop()
        source = store / PurePosixPath(path).name
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != required[path]:
            raise ValueError(f'Prepared asset hash mismatch: {source}')
        rows.append(dict(url=path, sha256=required[path], bytes=len(data)))
        if path.endswith('.glb'):
            if len(data) < 20 or struct.unpack_from('<III', data) != (0x46546c67, 2, len(data)):
                raise ValueError(f'Invalid GLB container: {source}')
            size, kind = struct.unpack_from('<II', data, 12)
            if kind != 0x4e4f534a or 20+size > len(data):
                raise ValueError(f'Invalid GLB JSON chunk: {source}')
            document = json.loads(data[20:20+size])
            for entry in document.get('images', []) + document.get('buffers', []):
                uri = entry.get('uri')
                if uri and not uri.startswith('data:'):
                    add(uri, posixpath.dirname(path))
    return sorted(rows, key=lambda row: row['url'])


def package(repo, revision, store, output):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args])
    paths = git('ls-tree', '-r', '--name-only', revision, '--', 'game/public/scenes').decode().splitlines()
    scenes = {path: json.loads(git('show', f'{revision}:{path}')) for path in paths if path.endswith('.scene.json')}
    if not scenes:
        raise ValueError('The committed release has no prepared scene')
    rows = closure(scenes.values(), store)
    output = Path(output)
    (output / 'shared').mkdir(parents=True, exist_ok=True)
    for row in rows:
        target = output / row['url']
        shutil.copyfile(Path(store) / target.name, target)
        # Validate the packaged bytes too, before transport; concurrent store edits cannot change this release.
        if hashlib.sha256(target.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError(f'Asset changed during packaging: {target.name}')
    (output / 'required.sha256').write_text(''.join(f"{r['sha256']}  {r['url']}\n" for r in rows))
    (output / 'files.txt').write_text(''.join(PurePosixPath(r['url']).name+'\n' for r in rows))
    inventory = dict(schema='turku-scene-assets/1', scenes={p: s['release'] for p,s in scenes.items()}, files=rows)
    (output / 'inventory.json').write_text(json.dumps(inventory, separators=(',', ':'))+'\n')
    return inventory


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('repo', 'revision', 'store', 'output'):
        parser.add_argument('--'+key, required=True)
    args = parser.parse_args()
    result = package(args.repo, args.revision, args.store, args.output)
    print(f"Prepared asset package: {len(result['files'])} verified files, {sum(r['bytes'] for r in result['files'])} bytes")
