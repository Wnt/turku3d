"""Read-only views of existing authored tiles and a separate, disposable render-derivative cache."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError

TILE = re.compile(r'^t0_-?\d+_-?\d+$')
KEY = re.compile(r'^[a-f0-9]{64}$')
VISUAL = re.compile(r'^vis(?:\.[a-f0-9]{64})?\.glb$')


class Scenery:
    def __init__(self, root, cache=None):
        self.root = Path(root)
        self.cache = Path(cache) if cache else self.root / '.scenery'
        self.script = Path(__file__).with_suffix('.mjs')
        self.recipe = hashlib.sha256(self.script.read_bytes()).hexdigest()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='scenery-derivative')
        self.pending = {}
        self.lock = threading.Lock()

    def catalogue(self, tiles):
        """No Builder access, freshness checks, raw-data reads or source writes on this path."""
        rows, missing = [], []
        self.cache.mkdir(parents=True, exist_ok=True)
        for tile in dict.fromkeys(tiles):
            if not TILE.fullmatch(tile):
                continue
            source = None
            for name in ('manifest.json', 'last-full.json'):
                try:
                    fragment = json.loads((self.root / tile / name).read_text())
                    entry = fragment['visual'][tile]
                    if fragment.get('pass_kind', 'full') != 'full' or not VISUAL.fullmatch(entry['url']):
                        continue
                    if (self.root / tile / entry['url']).is_file():
                        source = entry
                        break
                except (OSError, ValueError, KeyError, TypeError):
                    continue
            if source is None:
                missing.append(tile)
                continue
            key = hashlib.sha256(f"{self.recipe}:{tile}:{source['sha256']}".encode()).hexdigest()
            row = dict(tile=tile, key=key, source_sha256=source['sha256'], source_url=source['url'],
                       h=source.get('h', [0, 0]), recipe=self.recipe)
            mapping = self.cache / f'{key}.source.json'
            if not mapping.exists():
                self._write(mapping, json.dumps(row).encode())
            rows.append(dict(tile=tile, key=key, h=row['h'], source_sha256=row['source_sha256']))
        return dict(tiles=rows, missing=missing, recipe=self.recipe)

    @staticmethod
    def _write(path, data):
        temporary = path.with_suffix(f'.tmp-{os.getpid()}-{threading.get_ident()}')
        temporary.write_bytes(data)
        temporary.replace(path)

    def descriptor(self, key):
        if not KEY.fullmatch(key):
            return 404, {'state': 'missing'}
        target = self.cache / f'{key}.json'
        try:
            value = json.loads(target.read_text())
            if (self.cache / f'lod.{key}.glb').is_file():
                return 200, value
        except (OSError, ValueError):
            pass
        if not (self.cache / f'{key}.source.json').is_file():
            return 404, {'state': 'missing'}
        with self.lock:
            self.pending = {k: v for k, v in self.pending.items() if not v.done()}
            future = self.pending.get(key)
            if future is None:
                if len(self.pending) >= 16:
                    return 202, {'state': 'queued'}
                future = self.pool.submit(self._build, key)
                self.pending[key] = future
        try:
            value = future.result(timeout=.8)
            return 200, value
        except TimeoutError:
            return 202, {'state': 'preparing'}
        except (OSError, ValueError, subprocess.SubprocessError):
            return 503, {'state': 'failed'}
        finally:
            if future.done():
                with self.lock:
                    if self.pending.get(key) is future:
                        del self.pending[key]

    def _build(self, key):
        row = json.loads((self.cache / f'{key}.source.json').read_text())
        if not TILE.fullmatch(row['tile']) or not VISUAL.fullmatch(row['source_url']):
            raise ValueError('Invalid derivative source')
        source = self.root / row['tile'] / row['source_url']
        if hashlib.sha256(source.read_bytes()).hexdigest() != row['source_sha256']:
            raise ValueError('Derivative source changed')
        output = self.cache / f'lod.{key}.glb'
        temporary = self.cache / f'lod.{key}.{os.getpid()}.{threading.get_ident()}.glb'
        try:
            result = subprocess.run(['node', str(self.script), str(source), str(temporary)],
                                    check=True, capture_output=True, text=True, timeout=30)
            stats = json.loads(result.stdout.strip().splitlines()[-1])
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)
        value = dict(coarse=True, scenery=True, tile=row['tile'], pass_kind='full',
                     source_sha256=row['source_sha256'], visual={row['tile']: dict(
                         url=f'../scenery/lod.{key}.glb', sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                         bytes=stats['bytes'], tris=stats['triangles'], h=row['h'])})
        self._write(self.cache / f'{key}.json', json.dumps(value).encode())
        return value


def serve(handler, scenery, url, bounds=(-64, -64, 64, 64)):
    """Shared local/public read-only routes; return True after a handled request."""
    from urllib.parse import parse_qs

    if url.path == '/coarse/scenery':
        tiles = parse_qs(url.query).get('tiles', [''])[0].split(',')
        valid = len(tiles) <= 256 and all(TILE.fullmatch(t) for t in tiles)
        if valid:
            valid = all(bounds[0] <= int(t.split('_')[1]) <= bounds[2]
                        and bounds[1] <= int(t.split('_')[2]) <= bounds[3] for t in tiles)
        handler._json(200, scenery.catalogue(tiles)) if valid else handler._json(400, {'error': 'tiles'})
        return True
    if not url.path.startswith('/coarse/scenery/'):
        return False
    name = url.path.removeprefix('/coarse/scenery/')
    if name.endswith('.json') and KEY.fullmatch(name[:-5]):
        handler._json(*scenery.descriptor(name[:-5]))
    elif name.startswith('lod.') and name.endswith('.glb') and KEY.fullmatch(name[4:-4]):
        file = scenery.cache / name
        if file.is_file():
            handler._send(200, file.read_bytes(), 'model/gltf-binary', 'public, max-age=31536000, immutable')
        else:
            handler._json(404, {'state': 'missing'})
    else:
        handler._json(404, {'state': 'missing'})
    return True
