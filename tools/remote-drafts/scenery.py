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

TILE = re.compile(r'^t[024]_-?\d+_-?\d+$')
KEY = re.compile(r'^[a-f0-9]{64}$')
VISUAL = re.compile(r'^vis(?:\.[a-f0-9]{64})?\.glb$')


class Scenery:
    def __init__(self, root, cache=None, survey=None):
        self.root = Path(root)
        self.cache = Path(cache) if cache else self.root / '.scenery'
        self.script = Path(__file__).with_suffix('.mjs')
        self.tree_script = Path(__file__).with_name('scenery-trees.mjs')
        self.recipe = hashlib.sha256(self.script.read_bytes()).hexdigest()
        self.survey = survey
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='scenery-derivative')
        self.pending = {}
        self.lock = threading.Lock()

    def catalogue(self, tiles):
        """Prefer retained visuals; optional surveys prepare identities, never street tiles or source writes."""
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
                row = self.survey.describe(tile) if self.survey else None
                if row:
                    self._write(self.cache / f"{row['key']}.source.json", json.dumps(row).encode())
                    rows.append({**{k: row[k] for k in ('tile', 'key', 'h', 'source_sha256')},
                                 'coverage': row.get('coverage', 1), 'regional':row.get('regional',False)})
                else:
                    missing.append(tile)
                continue
            key = hashlib.sha256(f"{self.recipe}:{tile}:{source['sha256']}".encode()).hexdigest()
            row = dict(tile=tile, key=key, source_sha256=source['sha256'], source_url=source['url'],
                       h=source.get('h', [0, 0]), recipe=self.recipe)
            mapping = self.cache / f'{key}.source.json'
            if not mapping.exists():
                self._write(mapping, json.dumps(row).encode())
            rows.append(dict(tile=tile, key=key, h=row['h'], source_sha256=row['source_sha256'],
                             trees_url=f'scenery/{key}.trees.json'))
        return dict(tiles=rows, missing=missing, recipe=self.recipe,
                    generation=hashlib.sha256((self.recipe + (self.survey.recipe if self.survey else "")).encode()).hexdigest())

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
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            return 503, {'state': 'failed'}
        finally:
            if future.done():
                with self.lock:
                    if self.pending.get(key) is future:
                        del self.pending[key]

    def _build(self, key):
        row = json.loads((self.cache / f'{key}.source.json').read_text())
        if row.get('kind') == 'survey':
            if not self.survey or not TILE.fullmatch(row['tile']) or row['key'] != key:
                raise ValueError('Invalid survey source')
            output = self.cache / f'lod.{key}.glb'
            temporary = self.cache / f'lod.{key}.{os.getpid()}.{threading.get_ident()}.glb'
            try:
                try:
                    stats = self.survey.build(row, temporary)
                except RuntimeError as error:
                    from turku3d import config as C
                    from turku3d.coarse.sources import NoCoverage
                    if not isinstance(error, NoCoverage):
                        raise
                    fallback = self.survey.regional_row(C.VisualTile.parse(row['tile']))
                    if not fallback:
                        raise
                    # The survey recipe already includes the immutable regional
                    # grid hash. Resolve empty sea cells in the worker, never by
                    # decoding lidar synchronously in the HTTP catalogue.
                    stats = {**self.survey.build(fallback, temporary), 'fallback': 'regional'}
                temporary.replace(output)
            finally:
                temporary.unlink(missing_ok=True)
            return self._descriptor(key, row, output, stats)
        if not TILE.fullmatch(row['tile']) or not VISUAL.fullmatch(row['source_url']):
            raise ValueError('Invalid derivative source')
        # Local source unions use directory symlinks. Resolve before conversion so
        # external ../shared buffers stay relative to the original tile root.
        source = (self.root / row['tile'] / row['source_url']).resolve()
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
        return self._descriptor(key, row, output, stats)

    def _descriptor(self, key, row, output, stats):
        value = dict(coarse=True, scenery=True, tile=row['tile'], pass_kind='full', scenery_stats=stats,
                     source_sha256=row['source_sha256'], visual={row['tile']: dict(
                         url=f'../scenery/lod.{key}.glb', sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                         bytes=stats['bytes'], tris=stats['triangles'], h=row['h'])})
        self._write(self.cache / f'{key}.json', json.dumps(value).encode())
        return value

    def trees(self, key):
        """Decode only mapped instance transforms; source geometry/textures remain untouched."""
        if not KEY.fullmatch(key):
            return 404, {'state': 'missing'}
        recipe = hashlib.sha256(self.tree_script.read_bytes()).hexdigest()
        target = self.cache / f'{key}.trees.{recipe}.json'
        if target.is_file():
            return 200, json.loads(target.read_text())
        job = f'trees:{key}:{recipe}'
        with self.lock:
            self.pending = {k: v for k, v in self.pending.items() if not v.done()}
            future = self.pending.get(job)
            if future is None:
                if len(self.pending) >= 16:
                    return 202, {'state': 'queued'}
                future = self.pool.submit(self._build_trees, key, target)
                self.pending[job] = future
        try:
            return 200, future.result(timeout=.8)
        except TimeoutError:
            return 202, {'state': 'preparing'}
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            return 503, {'state': 'trees unavailable'}

    def _build_trees(self, key, target):
        row = json.loads((self.cache / f'{key}.source.json').read_text())
        if not TILE.fullmatch(row['tile']) or not VISUAL.fullmatch(row['source_url']):
            raise ValueError('Invalid tree source')
        source = (self.root / row['tile'] / row['source_url']).resolve()
        if hashlib.sha256(source.read_bytes()).hexdigest() != row['source_sha256']:
            raise ValueError('Tree source changed')
        temporary = target.with_suffix(f'.{threading.get_ident()}.tmp')
        try:
            subprocess.run(['node', str(self.tree_script), str(source), str(temporary)],
                           check=True, capture_output=True, timeout=30)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return json.loads(target.read_text())


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
    if name.endswith('.trees.json') and KEY.fullmatch(name[:-11]):
        handler._json(*scenery.trees(name[:-11]))
    elif name.endswith('.json') and KEY.fullmatch(name[:-5]):
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
