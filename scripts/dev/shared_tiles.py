#!/usr/bin/env python3
"""Local Gen 2 tile farm. Immutable source revisions, single-flight jobs and shared stage caches.

Only registered snapshots under ROOT/revisions are executable. Never serves an older tile
as if it belonged to a requested revision. This is a trusted LAN development service.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit

REV = re.compile(r"[a-f0-9]{40,64}\Z")
TILE = re.compile(r"t0_-?\d{1,5}_-?\d{1,5}\Z")
ALIAS = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
HASHED = re.compile(r"[a-f0-9]{64}")
PUBLIC = ('coarse', 'tile', 'visual', 'collision', 'roads', 'pass_kind', 'vehicles',
          'lights', 'vehicle_models', 'generation')
GIB = 2**30


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f'.{threading.get_ident()}.tmp')
    temp.write_text(json.dumps(value, sort_keys=True) + '\n')
    temp.replace(path)


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


@contextmanager
def file_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


class Farm:
    def __init__(self, root, runner, *, workers=2, queue_limit=128, floor_gib=25,
                 reserve_gib=2, execute=None, free=None):
        self.root, self.runner = Path(root).resolve(), Path(runner).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.workers, self.queue_limit = workers, queue_limit
        self.floor, self.reserve = floor_gib * GIB, reserve_gib * GIB
        self.free = free or (lambda: shutil.disk_usage(self.root).free)
        self.execute = execute or self.run_build
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='tile')
        self.jobs, self.active, self.failures = {}, set(), {}
        self.metrics = dict(requests=0, hits=0, joined=0, builds=0, failed=0,
                            stage_hits=0, stage_misses=0, evicted_bytes=0)
        self.version = time.time_ns() // 1000000
        self.aliases = read_json(self.root / 'aliases.json', {})

    def event(self, kind, **row):
        with self.lock:
            with (self.root / 'events.jsonl').open('a') as out:
                out.write(json.dumps({'t': time.time(), 'event': kind, **row}) + '\n')

    def revision(self, revision):
        if not REV.fullmatch(revision):
            raise ValueError('invalid revision')
        meta = read_json(self.root / 'revisions' / revision / 'revision.json')
        if not meta or not REV.fullmatch(meta.get('generation', '')):
            raise KeyError('revision is not registered')
        return meta

    def register(self, revision, alias=None):
        meta = self.revision(revision)
        if alias is not None and not ALIAS.fullmatch(alias):
            raise ValueError('invalid alias')
        with self.lock:
            if alias:
                self.aliases[alias] = revision
                atomic_json(self.root / 'aliases.json', self.aliases)
                self.version += 1
        self.event('register', revision=revision, alias=alias, generation=meta['generation'])
        return dict(revision=revision, generation=meta['generation'], alias=alias)

    def resolve(self, selector):
        with self.lock:
            return self.aliases.get(selector, selector)

    def output(self, generation):
        return self.root / 'products' / generation

    def status(self):
        with self.lock:
            return dict(state='ready', version=self.version, aliases=self.aliases.copy(),
                        workers=self.workers, queued=len(self.jobs) - len(self.active),
                        building=[k[1] for k in self.active], metrics=self.metrics.copy(),
                        free_gib=round(self.free() / GIB, 2), floor_gib=self.floor / GIB,
                        failures=list(self.failures.values())[-10:])

    def request(self, revision, tile):
        if not TILE.fullmatch(tile):
            raise ValueError('invalid tile')
        meta = self.revision(revision)
        generation = meta['generation']
        key = (generation, tile)
        with self.lock:
            self.metrics['requests'] += 1
            root = self.output(generation)
            receipt = root / tile / 'ready.json'
            if receipt.exists():
                receipt.touch()
                root.touch()
                self.metrics['hits'] += 1
                return 200, dict(state='built', revision=revision, generation=generation)
            if key in self.jobs:
                self.metrics['joined'] += 1
                return 202, dict(state='building' if key in self.active else 'queued',
                                 revision=revision, generation=generation)
            failed = self.failures.get(key)
            if failed and time.time() - failed['t'] < 60:
                return 422, failed
            if len(self.jobs) >= self.queue_limit:
                return 429, dict(state='queue-full')
            # Reservation covers concurrent builders as well as the hard free-space floor.
            if not self.make_room(self.floor + self.reserve * (len(self.active) + 1)):
                return 507, dict(state='disk-pressure', free_gib=self.free() / GIB)
            self.jobs[key] = revision
            self.pool.submit(self.build, revision, tile, generation)
            self.event('queued', revision=revision, tile=tile, generation=generation)
            return 202, dict(state='queued', revision=revision, generation=generation)

    def build(self, revision, tile, generation):
        key = (generation, tile)
        started = time.monotonic()
        try:
            # A tile's prepared datasets are shared even between revisions. Serializing its
            # preparation also prevents legacy input writers racing on the same raw-derived file.
            with file_lock(self.root / 'locks' / (tile + '.lock')):
                with self.lock:
                    if not self.make_room(self.floor + self.reserve * (len(self.active) + 1)):
                        raise RuntimeError('disk floor reservation unavailable')
                    self.active.add(key)
                root = self.output(generation)
                root.mkdir(parents=True, exist_ok=True)
                result = self.execute(revision, tile, root)
                manifest = read_json(root / tile / 'manifest.json')
                if not manifest or manifest.get('pass_kind') != 'full':
                    raise RuntimeError('build did not produce a full manifest')
                self.validate(root, tile, manifest)
                stages = read_json(root / tile / 'stage-times.json', {})
                hit = sum(s.get('cached') is True for s in stages.values())
                miss = sum(s.get('cached') is False for s in stages.values())
                seconds = round(time.monotonic() - started, 3)
                atomic_json(root / tile / 'ready.json', dict(revision=revision, generation=generation,
                            seconds=seconds, stage_hits=hit, stage_misses=miss, result=result))
                with self.lock:
                    self.metrics['builds'] += 1
                    self.metrics['stage_hits'] += hit
                    self.metrics['stage_misses'] += miss
                    self.failures.pop(key, None)
                    self.version += 1
                self.event('built', revision=revision, generation=generation, tile=tile,
                           seconds=seconds, stage_hits=hit, stage_misses=miss, stages=stages)
        except Exception as error:
            row = dict(t=time.time(), revision=revision, tile=tile, state='failed', error=str(error)[-1000:])
            with self.lock:
                self.failures[key] = row
                self.metrics['failed'] += 1
            self.event('failed', **row)
        finally:
            with self.lock:
                self.active.discard(key)
                self.jobs.pop(key, None)

    @staticmethod
    def validate(root, tile, manifest):
        entries = [*manifest.get('visual', {}).values(), *manifest.get('collision', {}).values()]
        if manifest.get('roads'):
            entries.append(manifest['roads'])
        if not entries or tile not in manifest.get('visual', {}) or len(manifest.get('collision', {})) != 16:
            raise RuntimeError('incomplete visual/collision pair')
        for entry in entries:
            path = (root / tile / entry['url']).resolve()
            if not path.is_relative_to(root.resolve()) or path.stat().st_size != entry['bytes']:
                raise RuntimeError('invalid payload ' + entry['url'])
            digest = entry.get('sha256')
            if digest and hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise RuntimeError('payload hash mismatch ' + entry['url'])

    def run_build(self, revision, tile, root):
        repo = self.root / 'revisions' / revision / 'repo'
        env = dict(os.environ, REPO=str(repo), TURKU3D_SHARED_CACHE=str(self.root / 'cache'),
                   TURKU3D_WORK=str(root / 'work'))
        logs = self.root / 'logs'
        logs.mkdir(exist_ok=True)
        with (logs / f'{revision}-{tile}.log').open('a') as log:
            proc = subprocess.Popen([str(self.runner), 'python', '-m', 'turku3d.coarse', 'build',
                                     tile, '--out', str(root / tile)], cwd=repo, env=env,
                                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            deadline = time.monotonic() + 1800
            while proc.poll() is None:
                reason = ('disk floor protection' if self.free() < self.floor + .5 * GIB else
                          'build timeout' if time.monotonic() > deadline else None)
                if reason:
                    os.killpg(proc.pid, signal.SIGTERM)  # Only this service's own process group.
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
                    raise RuntimeError(reason)
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        if proc.returncode:
            raise RuntimeError(f'generator exit {proc.returncode}; see logs/{revision}-{tile}.log')
        return dict(returncode=0)

    def make_room(self, target):
        """Evict unused generations then cache LRU. Never touch another loop's directories.

        Active jobs pin all shared intermediates. Cache eviction happens between jobs only,
        so an open stage lock or file cannot be unlinked underneath a builder.
        """
        if self.free() >= target:
            return True
        pinned = {g for g, _ in self.jobs}
        pinned.update(self.revision(rev)['generation'] for rev in self.aliases.values())
        products = self.root / 'products'
        candidates = sorted(products.glob('*'), key=lambda p: p.stat().st_mtime)
        for path in candidates:
            if path.name in pinned:
                continue
            size = sum(p.stat().st_size for p in path.rglob('*') if p.is_file())
            shutil.rmtree(path)
            self.metrics['evicted_bytes'] += size
            self.event('evicted', generation=path.name, bytes=size)
            if self.free() >= target:
                return True
        if not self.jobs:
            files = sorted((self.root / 'cache').rglob('*.pkl'), key=lambda p: p.stat().st_mtime)
            for path in files:
                self.metrics['evicted_bytes'] += path.stat().st_size
                path.unlink()
                if self.free() >= target:
                    return True
        return self.free() >= target


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def json(self, status, value):
        payload = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Access-Control-Allow-Origin', '*')
        if status in (202, 429):
            self.send_header('Retry-After', '2')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(payload)

    def do_POST(self):
        try:
            if self.path != '/register':
                return self.json(404, dict(error='unknown endpoint'))
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 4096:
                raise ValueError('invalid body size')
            body = json.loads(self.rfile.read(size))
            self.json(200, self.server.farm.register(body['revision'], body.get('alias')))
        except (ValueError, KeyError) as error:
            self.json(400, dict(error=str(error)))

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            self.route()
        except (ValueError, KeyError) as error:
            self.json(404, dict(error=str(error)))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            self.server.farm.event('http-error', error=str(error), path=self.path)
            self.json(500, dict(error=str(error)))

    def route(self):
        farm = self.server.farm
        url = urlsplit(self.path)
        parts = unquote(url.path).strip('/').split('/')
        if any(p in ('.', '..') or '\\' in p for p in parts):
            raise ValueError('invalid path')
        if parts == ['health'] or parts == ['status']:
            return self.json(200, farm.status())
        # /v/<commit-or-alias>/coarse/<path> supports the existing game's URL layout.
        if len(parts) < 4 or parts[0] != 'v' or parts[2] != 'coarse':
            return self.json(404, dict(error='use /v/<revision-or-alias>/coarse/'))
        revision = farm.resolve(parts[1])
        meta = farm.revision(revision)
        rel = parts[3:]
        if rel[0] in ('status', 'health', 'want', 'demand'):
            return self.json(200, {**farm.status(), 'revision': revision, 'generation': meta['generation']})
        if TILE.fullmatch(rel[0]) and rel[1:] in (['manifest.json'], ['collision.json']):
            status, value = farm.request(revision, rel[0])
            if status != 200:
                return self.json(status, value)
            manifest = read_json(farm.output(meta['generation']) / rel[0] / 'manifest.json')
            value = {k: manifest[k] for k in PUBLIC if k in manifest}
            # Absolute revision URLs survive alias changes and reach the same shared assets.
            def pin(obj):
                if isinstance(obj, dict):
                    return {k: (f'/v/{revision}/coarse/{rel[0]}/' + v if k == 'url' and isinstance(v, str)
                                else pin(v)) for k, v in obj.items()}
                if isinstance(obj, list):
                    return [pin(v) for v in obj]
                return obj
            value = pin(value)
            value.update(revision=revision, generation=meta['generation'], stale=False)
            return self.json(200, value)
        root = farm.output(meta['generation'])
        path = (root / Path(*rel)).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file() or any(p.startswith('.') for p in rel):
            return self.json(404, dict(error='payload not found'))
        if not (TILE.fullmatch(rel[0]) or rel[0] == 'shared'):
            return self.json(404, dict(error='payload not public'))
        with path.open('rb') as source:
            self.send_response(200)
            self.send_header('Content-Type', mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
            self.send_header('Content-Length', str(os.fstat(source.fileno()).st_size))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'public,max-age=31536000,immutable' if HASHED.search(path.name) else 'no-cache')
            self.end_headers()
            if self.command != 'HEAD':
                shutil.copyfileobj(source, self.wfile)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--runner', type=Path, required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=7950)
    parser.add_argument('--workers', type=int, default=2, choices=range(1, 5))
    args = parser.parse_args()
    with file_lock(args.root / 'service.lock'):
        farm = Farm(args.root, args.runner, workers=args.workers)
        server = ThreadingHTTPServer((args.host, args.port), Handler)
        server.farm = farm
        farm.event('started', port=args.port, workers=args.workers)
        server.serve_forever()


if __name__ == '__main__':
    main()
