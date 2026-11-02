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
import struct
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


def process_ticks(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def process_alive(pid, ticks=None):
    try:
        stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return stat[0] != 'Z' and (ticks is None or stat[19] == ticks)
    except (OSError, IndexError):
        return False


def process_started(ticks):
    """Recover a worker's wall-clock start when adopting it across daemon upgrades."""
    try:
        if ticks and int(ticks) > 0:
            uptime = float(Path('/proc/uptime').read_text().split()[0])
            return time.time() - uptime + int(ticks) / os.sysconf('SC_CLK_TCK')
    except (OSError, ValueError):
        pass
    return None


class Farm:
    def __init__(self, root, runner, *, workers=2, queue_limit=128, floor_gib=25,
                 reserve_gib=2, grace_seconds=900, execute=None, free=None):
        self.root, self.runner = Path(root).resolve(), Path(runner).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.workers, self.queue_limit = workers, queue_limit
        self.floor, self.reserve = floor_gib * GIB, reserve_gib * GIB
        self.grace_seconds = grace_seconds
        self.free = free or (lambda: shutil.disk_usage(self.root).free)
        self.execute = execute or self.run_build
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='tile')
        self.jobs, self.active, self.failures = {}, set(), {}
        self.dispatched, self.queue_info, self.demands = set(), {}, {}
        self.turns, self.closing, self.last_saved = {}, False, 0.0
        self.metrics = dict(requests=0, hits=0, joined=0, builds=0, failed=0,
                            stage_hits=0, stage_misses=0, input_hits=0, input_misses=0, evicted_bytes=0)
        self.version = time.time_ns() // 1000000
        self.started = time.time()
        self.aliases = read_json(self.root / 'aliases.json', {})
        for row in read_json(self.root / 'pending.json', []):
            key = (row['generation'], row['tile'])
            if not (self.output(key[0]) / key[1] / 'ready.json').exists():
                self.jobs[key] = row['revision']
                self.queue_info[key] = row
        with self.lock:
            self.pump()

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

    def register(self, revision, alias=None, source_version=None):
        meta = self.revision(revision)
        if source_version is not None:
            if not re.fullmatch(r"[a-f0-9]{64}", source_version):
                raise ValueError('invalid source version')
            data = self.root / 'sources' / 'versions' / source_version
            provenance = read_json(data / 'source-version.json', {})
            if provenance.get('version') != source_version:
                raise ValueError('source overlay is not published')
            source_revision = revision
            revision = hashlib.sha256((source_revision + ':' + source_version).encode()).hexdigest()
            generation = hashlib.sha256((meta['generation'] + ':' + source_version).encode()).hexdigest()
            destination = self.root / 'revisions' / revision
            with self.lock:
                if not destination.exists():
                    destination.mkdir()
                    (destination / 'repo').symlink_to(self.root / 'revisions' / source_revision / 'repo',
                                                    target_is_directory=True)
                    atomic_json(destination / 'revision.json', {**meta, 'revision': revision,
                                'commit': meta.get('commit', source_revision), 'source_revision': source_revision,
                                'generation': generation, 'source_version': source_version, 'data_root': str(data)})
            meta = self.revision(revision)
        if alias is not None and not ALIAS.fullmatch(alias):
            raise ValueError('invalid alias')
        with self.lock:
            if alias:
                self.aliases[alias] = revision
                atomic_json(self.root / 'aliases.json', self.aliases)
                self.version += 1
        self.event('register', revision=revision, alias=alias, generation=meta['generation'])
        return dict(revision=revision, commit=meta.get('commit', revision),
                    generation=meta['generation'], source_version=meta.get('source_version'), alias=alias)

    def resolve(self, selector):
        with self.lock:
            return self.aliases.get(selector, selector)

    def output(self, generation):
        return self.root / 'products' / generation

    def fallback_roots(self):
        return {hashlib.sha256(str(Path(p).resolve()).encode()).hexdigest(): Path(p).resolve()
                for p in read_json(self.root / 'fallbacks.json', [])}

    def retained(self, tile):
        """A complete old pair stays playable while the requested immutable version builds."""
        candidates = []
        for receipt in (self.root / 'products').glob(f'*/{tile}/ready.json'):
            value = read_json(receipt.parent / 'manifest.json')
            ready = read_json(receipt, {})
            if value and ready.get('revision'):
                candidates.append((value.get('built', ''), receipt.stat().st_mtime, value, ready))
        if candidates:
            _, _, manifest, ready = max(candidates, key=lambda r: r[:2])
            return manifest, f"/v/{ready['revision']}/coarse/{tile}/", dict(
                revision=ready['revision'], generation=ready['generation'], source_kind='retained')
        for token, root in self.fallback_roots().items():
            manifest = read_json(root / tile / 'manifest.json')
            if not manifest or manifest.get('pass_kind') != 'full':
                continue
            # The existing accepted servers already validated these artifacts. Check the
            # complete visual/collision pair and lengths before exposing a fallback manifest.
            entries = [*manifest.get('visual', {}).values(), *manifest.get('collision', {}).values()]
            if tile not in manifest.get('visual', {}) or len(manifest.get('collision', {})) != 16:
                continue
            try:
                if not all((root / tile / e['url']).stat().st_size == e['bytes'] for e in entries):
                    continue
            except (OSError, KeyError):
                continue
            return manifest, f'/v/{token}/coarse/{tile}/', dict(
                revision=None, generation=manifest.get('code', token), source_kind='accepted-fallback')
        return None

    def status(self):
        with self.lock:
            return dict(state='ready', api_version=2, version=self.version, aliases=self.aliases.copy(),
                        uptime_seconds=round(time.time() - self.started),
                        workers=self.workers, queued=len(self.jobs) - len(self.dispatched),
                        building=[k[1] for k in self.dispatched],
                        active=[dict(revision=self.jobs[k], generation=k[0], tile=k[1],
                                     pid=self.queue_info[k].get('pid'),
                                     seconds=round(time.time() - self.queue_info[k].get('build_started', time.time())))
                                for k in sorted(self.dispatched)],
                        queue=[dict(revision=self.jobs[k], tile=k[1], priority=self.priority(k)[:2],
                                    age=round(time.time()-self.queue_info[k]['requested']))
                               for k in sorted(self.jobs, key=self.priority) if k not in self.dispatched], metrics=self.metrics.copy(),
                        free_gib=round(self.free() / GIB, 2), floor_gib=self.floor / GIB,
                        failures=list(self.failures.values())[-10:])

    def request(self, revision, tile, distance=None):
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
                info = self.queue_info[key]
                info.update(seen=time.time(), distance=distance or 0)
                info['explicit'] = info.get('explicit', False) or distance is None
                self.save_queue()
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
            self.queue_info[key] = dict(revision=revision, generation=generation, tile=tile,
                                       requested=time.time(), seen=time.time(), distance=distance or 0,
                                       explicit=distance is None)
            self.save_queue(force=True)
            self.pump()
            self.event('queued', revision=revision, tile=tile, generation=generation)
            return 202, dict(state='queued', revision=revision, generation=generation)

    def save_queue(self, force=False):
        if force or time.monotonic() - self.last_saved > 2:
            atomic_json(self.root / 'pending.json', list(self.queue_info.values()))
            self.last_saved = time.monotonic()

    def priority(self, key):
        info = self.queue_info[key]
        if info.get('pid') and process_alive(info['pid'], info.get('start_ticks')):
            return (-2, 0, 0, info['requested'])  # Adopt existing work before starting anything new.
        category, distance = (1 if info.get('explicit') else 3), info.get('distance', 0)
        for (generation, _client), demand in self.demands.items():
            if generation == key[0] and time.time() - demand['seen'] < 120 and key[1] in demand['tiles']:
                category = min(category, demand['tiles'][key[1]])
        return (category, max(self.turns.get(key[0], 0), info['requested']), distance, info['requested'])

    def demand(self, revision, client, tiles, urgent, visible):
        generation = self.revision(revision)['generation']
        with self.lock:
            self.demands[(generation, client[:128])] = dict(seen=time.time(),
                tiles={tile: -1 if tile in urgent else 0 if tile in visible else 2 for tile in tiles})
            self.demands = {k:v for k,v in self.demands.items() if time.time() - v['seen'] < 120}
            self.pump()
        return self.status()

    def pump(self):
        if self.closing:
            return
        for key in list(self.jobs):
            info = self.queue_info[key]
            if (key not in self.dispatched and not info.get('explicit') and not info.get('pid')
                    and time.time() - info['seen'] > 180 and self.priority(key)[0] == 3):
                self.event('dropped', revision=self.jobs[key], tile=key[1], reason='no recent demand')
                self.jobs.pop(key)
                self.queue_info.pop(key)
        while len(self.dispatched) < self.workers:
            pending = set(self.jobs) - self.dispatched
            if not pending:
                break
            key = min(pending, key=self.priority)
            self.dispatched.add(key)
            self.queue_info[key].setdefault('dispatched_at', time.time())
            if not self.queue_info[key].get('pid'):
                self.turns[key[0]] = time.time()
            self.pool.submit(self.build, self.jobs[key], key[1], key[0])
        self.save_queue(force=True)

    def cancel(self, revision, tiles):
        """Drop queued work only; an already running generator keeps its output."""
        generation = self.revision(self.resolve(revision))['generation']
        if not isinstance(tiles, list) or not all(TILE.fullmatch(t) for t in tiles):
            raise ValueError('invalid tile list')
        removed, running = [], []
        with self.lock:
            for tile in tiles:
                key = (generation, tile)
                if key in self.dispatched:
                    running.append(tile)
                elif key in self.jobs:
                    self.jobs.pop(key)
                    self.queue_info.pop(key)
                    removed.append(tile)
            self.save_queue(force=True)
            self.event('cancelled', revision=revision, tiles=removed, running=running)
        return dict(cancelled=removed, running=running)

    def close(self):
        with self.lock:
            self.closing = True
            self.save_queue(force=True)
        self.pool.shutdown(wait=True)

    def build(self, revision, tile, generation):
        key = (generation, tile)
        started = time.monotonic()
        try:
            # Versions can build concurrently: stage keys lock their own writers and
            # prepared inputs publish atomically. Lock only the final version/tile product.
            with file_lock(self.root / 'locks' / (generation + '-' + tile + '.lock')):
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
                prepared = read_json(root / tile / 'input-cache.json', {})
                input_hits = sum(s.get('cached') is True for s in prepared.values())
                input_misses = sum(s.get('cached') is False for s in prepared.values())
                hit = sum(s.get('cached') is True for s in stages.values())
                miss = sum(s.get('cached') is False for s in stages.values())
                info = self.queue_info[key]
                build_started = info.get('build_started') or process_started(info.get('start_ticks'))
                seconds = round(time.time() - build_started if build_started else time.monotonic() - started, 3)
                queue_seconds = round(max(0, info.get('dispatched_at', info['requested']) - info['requested']), 3)
                atomic_json(root / tile / 'ready.json', dict(revision=revision, generation=generation,
                            seconds=seconds, queue_seconds=queue_seconds,
                            stage_hits=hit, stage_misses=miss, result=result))
                with self.lock:
                    self.metrics['builds'] += 1
                    self.metrics['stage_hits'] += hit
                    self.metrics['stage_misses'] += miss
                    self.metrics['input_hits'] += input_hits
                    self.metrics['input_misses'] += input_misses
                    self.failures.pop(key, None)
                    self.version += 1
                self.event('built', revision=revision, generation=generation, tile=tile,
                           seconds=seconds, queue_seconds=queue_seconds,
                           stage_hits=hit, stage_misses=miss, stages=stages,
                           input_hits=input_hits, input_misses=input_misses, prepared=prepared)
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
                self.queue_info.pop(key, None)
                self.dispatched.discard(key)
                self.pump()

    @staticmethod
    def validate(root, tile, manifest):
        entries = [*manifest.get('visual', {}).values(), *manifest.get('collision', {}).values(),
                   *manifest.get('vehicle_models', {}).values()]
        if manifest.get('roads'):
            entries.append(manifest['roads'])
        _, i, j = tile.split('_')
        cells = {f'c_{int(i)*4+x}_{int(j)*4+z}' for x in range(4) for z in range(4)}
        if (not manifest.get('coarse') or manifest.get('tile') != tile
                or tile not in manifest.get('visual', {}) or set(manifest.get('collision', {})) != cells):
            raise RuntimeError('incomplete visual/collision pair')
        checked = set()
        def verify(path, size=None, digest=None):
            path = path.resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file():
                raise RuntimeError('missing payload ' + str(path))
            if path in checked:
                return
            if size is not None and path.stat().st_size != size:
                raise RuntimeError('invalid payload size ' + str(path))
            data = path.read_bytes()
            named = HASHED.search(path.name)
            digest = digest or (named[0] if named else None)
            if digest and hashlib.sha256(data).hexdigest() != digest:
                raise RuntimeError('payload hash mismatch ' + str(path))
            checked.add(path)
            if data[:4] == b'glTF':
                length, kind = struct.unpack_from('<II', data, 12)
                if kind != 0x4e4f534a:
                    raise RuntimeError('GLB has no JSON chunk')
                document = json.loads(data[20:20 + length])
                for item in [*document.get('images', []), *document.get('buffers', [])]:
                    uri = item.get('uri')
                    if uri and not uri.startswith('data:'):
                        verify(path.parent / uri)
        for entry in entries:
            verify(root / tile / entry['url'], entry.get('bytes'), entry.get('sha256'))
        return len(checked)

    def run_build(self, revision, tile, root):
        info = self.queue_info[(root.name, tile)]
        if info.get('pid'):
            info.setdefault('build_started', process_started(info.get('start_ticks')) or time.time())
            deadline = time.monotonic() + 1800
            while process_alive(info['pid'], info.get('start_ticks')):
                if time.monotonic() > deadline:
                    raise RuntimeError('adopted build exceeded timeout')
                time.sleep(1)
            report = read_json(root / tile / 'stage-times.json', {})
            if report.get('_build', {}).get('status') != 'complete':
                raise RuntimeError('adopted generator did not complete; retry will resume cached stages')
            return dict(returncode=0, adopted=True)
        repo = self.root / 'revisions' / revision / 'repo'
        env = dict(os.environ, REPO=str(repo), TURKU3D_SHARED_CACHE=str(self.root / 'cache'),
                   TURKU3D_WORK=str(root / 'work'))
        # Encoders already lock their content keys; share their results across revisions too.
        textures = self.root / 'cache' / 'textures'
        textures.mkdir(parents=True, exist_ok=True)
        try:
            (root / '.compiled-textures').symlink_to(textures, target_is_directory=True)
        except FileExistsError:
            pass
        meta = self.revision(revision)
        if meta.get('data_root'):
            env['TURKU3D_DATA'] = meta['data_root']
            supplements = Path(meta['data_root']) / 'supplements'
            if supplements.is_dir():
                work = Path(env['TURKU3D_WORK'])
                work.mkdir(parents=True, exist_ok=True)
                for source in supplements.iterdir():
                    destination = work / source.name
                    if not destination.exists():
                        destination.symlink_to(source, target_is_directory=source.is_dir())
        logs = self.root / 'logs'
        logs.mkdir(exist_ok=True)
        with (logs / f'{revision}-{tile}.log').open('a') as log:
            proc = subprocess.Popen([str(self.runner), 'python', '-m', 'turku3d.coarse', 'build',
                                     tile, '--out', str(root / tile)], cwd=repo, env=env,
                                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            with self.lock:
                info.update(pid=proc.pid, start_ticks=process_ticks(proc.pid), build_started=time.time())
                self.save_queue(force=True)
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
        revisions = set(self.aliases.values()) | set(self.jobs.values())
        pinned.update(self.revision(rev)['generation'] for rev in revisions)
        for rev in list(revisions):
            source = self.revision(rev).get('source_revision')
            if source:
                revisions.add(source)
        products = self.root / 'products'
        candidates = sorted(products.glob('*'), key=lambda p: p.stat().st_mtime)
        for path in candidates:
            if path.name in pinned or time.time() - path.stat().st_mtime < self.grace_seconds:
                continue
            size = sum(p.stat().st_size for p in path.rglob('*') if p.is_file())
            shutil.rmtree(path)
            self.metrics['evicted_bytes'] += size
            self.event('evicted', generation=path.name, bytes=size)
            # Source snapshots belong to evicted generations too. Alias/job pins above
            # protect every revision still in use; registration can upload an old one again.
            for snapshot in (self.root / 'revisions').iterdir():
                meta = read_json(snapshot / 'revision.json', {})
                if meta.get('generation') == path.name and snapshot.name not in revisions:
                    shutil.rmtree(snapshot)
            if self.free() >= target:
                return True
        if not self.jobs:
            files = sorted((p for p in (self.root / 'cache').rglob('*')
                            if p.is_file() and p.suffix in ('.pkl', '.ktx2', '.png', '.rgb')),
                           key=lambda p: p.stat().st_mtime)
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
            if self.path not in ('/register', '/limits', '/cancel'):
                return self.json(404, dict(error='unknown endpoint'))
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 4096:
                raise ValueError('invalid body size')
            body = json.loads(self.rfile.read(size))
            if self.path == '/cancel':
                return self.json(200, self.server.farm.cancel(body['revision'], body['tiles']))
            if self.path == '/limits':
                workers = body.get('workers')
                if not isinstance(workers, int) or not 1 <= workers <= 4:
                    raise ValueError('worker cap must be 1..4')
                farm = self.server.farm
                with farm.lock:
                    farm.workers = workers
                    farm.pump()
                    farm.event('worker-cap', workers=workers)
                return self.json(200, farm.status())
            self.json(200, self.server.farm.register(body['revision'], body.get('alias'), body.get('source_version')))
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
        if len(parts) >= 4 and parts[0] == 'v' and parts[2] == 'coarse':
            root = farm.fallback_roots().get(parts[1])
            if root is not None:
                return self.file(root, parts[3:])
        # /v/<commit-or-alias>/coarse/<path> supports the existing game's URL layout.
        if len(parts) < 4 or parts[0] != 'v' or parts[2] != 'coarse':
            return self.json(404, dict(error='use /v/<revision-or-alias>/coarse/'))
        revision = farm.resolve(parts[1])
        meta = farm.revision(revision)
        rel = parts[3:]
        if rel[0] == 'demand':
            query = parse_qs(url.query)
            def tiles(name):
                return [t for t in query.get(name, [''])[0].split(',')[:128] if TILE.fullmatch(t)]
            farm.demand(revision, query.get('client', ['anonymous'])[0], tiles('tiles'),
                        set(tiles('urgent')), set(tiles('visible')))
        if rel[0] in ('status', 'health', 'want', 'demand'):
            return self.json(200, {**farm.status(), 'revision': revision, 'generation': meta['generation']})
        if TILE.fullmatch(rel[0]) and rel[1:] in (['manifest.json'], ['collision.json']):
            query = parse_qs(url.query)
            distance = max(0, min(100000, float(query['d'][0]))) if 'd' in query else None
            status, value = farm.request(revision, rel[0], distance)
            stale = status != 200
            provenance = dict(revision=revision, generation=meta['generation'], source_kind='requested')
            prefix = f'/v/{revision}/coarse/{rel[0]}/'
            if stale:
                retained = farm.retained(rel[0])
                if retained is None:
                    return self.json(status, value)
                manifest, prefix, provenance = retained
            else:
                manifest = read_json(farm.output(meta['generation']) / rel[0] / 'manifest.json')
            value = {k: manifest[k] for k in PUBLIC if k in manifest}
            def pin(obj):
                if isinstance(obj, dict):
                    return {k: (prefix + v if k == 'url' and isinstance(v, str)
                                else pin(v)) for k, v in obj.items()}
                if isinstance(obj, list):
                    return [pin(v) for v in obj]
                return obj
            value = pin(value)
            value.update(**provenance, requested_revision=revision, stale=stale)
            return self.json(200, value)
        return self.file(farm.output(meta['generation']), rel)

    def file(self, root, rel):
        path = (root / Path(*rel)).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file() or any(p.startswith('.') for p in rel):
            return self.json(404, dict(error='payload not found'))
        if not (TILE.fullmatch(rel[0]) or rel[0] == 'shared'):
            return self.json(404, dict(error='payload not public'))
        with path.open('rb') as source:
            if root.is_relative_to(self.server.farm.root / 'products'):
                root.touch()  # Old in-flight payload lease after aliases move.
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
        (args.root / 'service.pid').write_text(str(os.getpid()) + '\n')
        farm.event('started', port=args.port, workers=args.workers, pid=os.getpid())
        server.serve_forever()


if __name__ == '__main__':
    main()
