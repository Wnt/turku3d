#!/usr/bin/env python3
"""Verify a served tile's manifest, every referenced payload, hashes and external GLB images."""
import argparse
import hashlib
import json
import re
import struct
from urllib.parse import urljoin
from urllib.request import urlopen


def audit(base, tile):
    manifest_url = urljoin(base.rstrip('/') + '/', tile + '/manifest.json')
    with urlopen(manifest_url, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f'{tile}: HTTP {response.status}, not ready')
        manifest = json.load(response)
    fetched = {}
    def fetch(url, descriptor=None):
        if url in fetched:
            return
        data = urlopen(url, timeout=30).read()
        digest = hashlib.sha256(data).hexdigest()
        expected = (descriptor or {}).get('sha256')
        if expected is None:
            match = re.search(r'[a-f0-9]{64}', url.rsplit('/', 1)[-1])
            expected = match[0] if match else None
        if expected and digest != expected:
            raise RuntimeError(f'hash mismatch: {url}')
        if descriptor and descriptor.get('bytes', len(data)) != len(data):
            raise RuntimeError(f'length mismatch: {url}')
        fetched[url] = len(data)
        if data[:4] == b'glTF':
            size, kind = struct.unpack_from('<II', data, 12)
            if kind != 0x4e4f534a:
                raise RuntimeError('GLB JSON chunk missing')
            document = json.loads(data[20:20 + size])
            for item in [*document.get('images', []), *document.get('buffers', [])]:
                uri = item.get('uri')
                if uri and not uri.startswith('data:'):
                    fetch(urljoin(url, uri))
    def visit(value):
        if isinstance(value, dict):
            if 'url' in value:
                fetch(urljoin(manifest_url, value['url']), value)
            else:
                for child in value.values():
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(manifest)
    return dict(tile=tile, revision=manifest.get('revision'), generation=manifest.get('generation'),
                files=len(fetched), bytes=sum(fetched.values()), pass_kind=manifest.get('pass_kind'),
                stale=manifest.get('stale'), requested_revision=manifest.get('requested_revision'),
                source_kind=manifest.get('source_kind'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base')
    parser.add_argument('tiles', nargs='+')
    args = parser.parse_args()
    for tile in args.tiles:
        print(json.dumps(audit(args.base, tile)), flush=True)
