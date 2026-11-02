#!/usr/bin/env python3
"""Local Gen 2 tile farm. Immutable source revisions, single-flight jobs and shared stage caches.

Only registered snapshots under ROOT/revisions are executable. Never serves an older tile
as if it belonged to a requested revision. This is a trusted LAN development service.
"""
from __future__ import annotations

import argparse
import ast
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
import tempfile
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit

REV = re.compile(r"[a-f0-9]{40,64}\Z")
TILE = re.compile(r"t0_-?\d{1,5}_-?\d{1,5}\Z")
ALIAS = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
HASHED = re.compile(r"[a-f0-9]{64}")
RAW_CACHES = {
    'vegetation': ('vegetation/trees.py',),
    'coarse-roofs': ('buildings/reconstruct.py', 'buildings/inputs.py'),
    'lidar': ('lidar/cache.py',),
}
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
            return time.time() - time.monotonic() + int(ticks) / os.sysconf('SC_CLK_TCK')
    except (OSError, ValueError):
        pass
    return None


def worker_elapsed(info):
    started = info.get('started_monotonic')
    if started is None and info.get('start_ticks') and int(info['start_ticks']) > 0:
        started = int(info['start_ticks']) / os.sysconf('SC_CLK_TCK')
    return max(0, time.monotonic() - started) if started is not None else 0


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
        self.adoption_lock = threading.Lock()
        self.adopting = set()
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
        if alias is not None and not ALIAS.fullmatch(alias):
            raise ValueError('invalid alias')
        # Advancing a branch must not silently discard its surveyed source profile.
        previous = self.aliases.get(alias)
        if source_version is None:
            source_version = meta.get('source_version')
            if source_version is None and previous:
                source_version = self.revision(previous).get('source_version')
        if meta.get('source_revision'):
            source_version = source_version or meta.get('source_version')
            revision = meta['source_revision']
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
            return dict(state='ready', api_version=2, daemon_pid=os.getpid(),
                        version=self.version, aliases=self.aliases.copy(),
                        uptime_seconds=round(time.time() - self.started),
                        workers=self.workers, queued=len(self.jobs) - len(self.dispatched),
                        building=[k[1] for k in self.dispatched],
                        active=[dict(revision=self.jobs[k], generation=k[0], tile=k[1],
                                     pid=self.queue_info[k].get('pid'),
                                     seconds=round(worker_elapsed(self.queue_info[k])))
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
        retired = read_json(self.root / 'retired.json', {})
        reason = retired.get(meta.get('commit', revision)) or retired.get(revision)
        if reason:
            return 422, dict(state='revision-retired', revision=revision, error=reason)
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
                    # A recorded worker already owns its disk reservation. Recover its
                    # monitoring even under pressure; run_build can stop it safely.
                    if not self.queue_info[key].get('pid') and not self.make_room(
                            self.floor + self.reserve * (len(self.active) + 1)):
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
                seconds = round(worker_elapsed(info) or time.monotonic() - started, 3)
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
            info = self.queue_info.get(key, {})
            # Never release product/cache pins while a preserved worker can still write.
            while info.get('pid') and process_alive(info['pid'], info.get('start_ticks')):
                time.sleep(.1)
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
                reason = ('disk floor protection' if self.free() < self.floor + .5 * GIB else
                          'build timeout' if worker_elapsed(info) > 1800 or time.monotonic() > deadline else None)
                if reason:
                    self.stop_worker(info, root / tile)
                    raise RuntimeError(reason)
                time.sleep(1)
            report = read_json(root / tile / 'stage-times.json', {})
            if report.get('_build', {}).get('status') != 'complete':
                raise RuntimeError('adopted generator did not complete; retry will resume cached stages')
            return dict(returncode=0, adopted=True)
        repo = self.root / 'revisions' / revision / 'repo'
        self.mount_intermediates(repo, root / 'work')
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
            self.mount_supplements(Path(meta['data_root']), Path(env['TURKU3D_WORK']))
        logs = self.root / 'logs'
        logs.mkdir(exist_ok=True)
        with (logs / f'{revision}-{tile}.log').open('a') as log:
            proc = subprocess.Popen([str(self.runner), 'python', '-m', 'turku3d.coarse', 'build',
                                     tile, '--out', str(root / tile)], cwd=repo, env=env,
                                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            with self.lock:
                info.update(pid=proc.pid, start_ticks=process_ticks(proc.pid), build_started=time.time(),
                            started_monotonic=time.monotonic())
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

    def stop_worker(self, info, output):
        """Only stop a recorded farm session whose PID identity and output still match."""
        pid = info['pid']
        if not process_alive(pid, info.get('start_ticks')):
            return
        argv = Path(f'/proc/{pid}/cmdline').read_bytes().decode().strip('\0').split('\0')
        if ('turku3d.coarse' not in argv or '--out' not in argv or os.getpgid(pid) != pid
                or Path(argv[argv.index('--out') + 1]) != output):
            raise RuntimeError('refusing to stop a process outside this recorded farm worker')
        os.killpg(pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while process_alive(pid, info.get('start_ticks')) and time.monotonic() < deadline:
            time.sleep(.05)
        if process_alive(pid, info.get('start_ticks')):
            os.killpg(pid, signal.SIGKILL)
        while process_alive(pid, info.get('start_ticks')):
            time.sleep(.05)

    def mount_supplements(self, data, work):
        supplements = data / 'supplements'
        if not supplements.is_dir():
            return
        with self.lock:
            work.mkdir(parents=True, exist_ok=True)
            for source in supplements.iterdir():
                destination = work / source.name
                if destination.exists() or destination.is_symlink():
                    if destination.resolve() != source.resolve():
                        raise RuntimeError('work supplement does not match the pinned source profile')
                else:
                    destination.symlink_to(source, target_is_directory=source.is_dir())

    def intermediate_roots(self, repo):
        """Whole-source decoding has its own producer identity, independent of tile geometry."""
        base = repo / 'pipeline/turku3d'
        result = {}
        for name, sources in RAW_CACHES.items():
            paths = [base / 'config.py', *(base / p for p in sources)]
            if all(p.exists() for p in paths):
                digest = hashlib.sha256(b''.join(p.read_bytes() for p in paths)).hexdigest()
                result[name] = self.root / 'cache/raw-derived' / name / digest
        return result

    def mount_intermediates(self, repo, work):
        cache = work / 'cache'
        cache.mkdir(parents=True, exist_ok=True)
        with self.lock:
            for name, shared in self.intermediate_roots(repo).items():
                shared.mkdir(parents=True, exist_ok=True)
                shared.touch()
                local = cache / name
                if not local.exists():
                    local.symlink_to(shared, target_is_directory=True)
                # Existing workers can still read a private cache. Keep their directory
                # stable; new product generations use the shared immutable entries.

    def seed_intermediates(self):
        """Adopt complete private results using hard links, without touching existing workers."""
        with self.adoption_lock:
            with self.lock:
                generations = {}
                for snapshot in (self.root / 'revisions').glob('*/revision.json'):
                    meta = read_json(snapshot, {})
                    generations.setdefault(meta.get('generation'), snapshot.parent / 'repo')
                self.adopting = set(generations) - {None}
            try:
                return self._seed_intermediates(generations)
            finally:
                with self.lock:
                    self.adopting.clear()

    @staticmethod
    def validate_shards(directory):
        manifest = read_json(directory / 'MANIFEST.json')
        if not manifest or not isinstance(manifest.get('counts'), dict):
            raise RuntimeError('invalid decoded lidar manifest')
        expected = [('xyz', '<f8', (3,)), ('classification', '|u1'), ('return_number', '|u1'),
                    ('number_of_returns', '|u1'), ('intensity', '<u2')]
        files = [directory / 'MANIFEST.json']
        for cell, count in manifest['counts'].items():
            if not re.fullmatch(r'c_-?\d+_-?\d+', cell) or not isinstance(count, int) or count < 0:
                raise RuntimeError('invalid decoded lidar cell')
            path = directory / (cell + '.npy')
            with path.open('rb') as stream:
                if stream.read(6) != b'\x93NUMPY':
                    raise RuntimeError('invalid decoded lidar array')
                version = stream.read(2)
                size = 2 if version == b'\x01\x00' else 4 if version in (b'\x02\x00', b'\x03\x00') else 0
                if not size:
                    raise RuntimeError('unknown decoded lidar array format')
                length = int.from_bytes(stream.read(size), 'little')
                if length > 65536:
                    raise RuntimeError('oversized decoded lidar header')
                header = ast.literal_eval(stream.read(length).decode())
                if (header.get('shape') != (count,) or header.get('descr') != expected
                        or header.get('fortran_order') is not False
                        or path.stat().st_size != stream.tell() + count * 29):
                    raise RuntimeError('incomplete decoded lidar shard')
            files.append(path)
        return files

    def _seed_intermediates(self, generations):
        linked = 0
        for generation, repo in generations.items():
            if not generation:
                continue
            for name, shared in self.intermediate_roots(repo).items():
                local = self.output(generation) / 'work/cache' / name
                if not local.exists() or local.is_symlink():
                    continue
                if name == 'lidar':
                    # A source directory is published atomically only after its manifest.
                    for manifest in (local / 'sources').glob('*/MANIFEST.json'):
                        destination = shared / 'sources' / manifest.parent.name
                        with file_lock(destination.with_suffix('.lock')):
                            if (destination / 'MANIFEST.json').exists():
                                continue
                            temporary = Path(tempfile.mkdtemp(prefix='.seed-', dir=destination.parent))
                            try:
                                for source in self.validate_shards(manifest.parent):
                                    os.link(source, temporary / source.name)
                                self.validate_shards(temporary)
                                temporary.rename(destination)
                                linked += len(list(destination.iterdir()))
                            finally:
                                if temporary.exists():
                                    shutil.rmtree(temporary)
                    files = list((local / 'crops').glob('*.npy'))
                else:
                    files = list(local.glob('*.npz' if name == 'vegetation' else '*.class6.npy'))
                for source in files:
                    if source.name.startswith('.') or '.tmp.' in source.name:
                        continue
                    target = shared / source.relative_to(local)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        os.link(source, target)
                        linked += 1
                    except FileExistsError:
                        pass
        self.event('intermediates-adopted', files=linked)
        return linked

    def make_room(self, target):
        """Evict unused generations then cache LRU. Never touch another loop's directories.

        Requested tiles pin their intermediates. Other old entries can be reclaimed while
        workers run; their content-key locks protect concurrent readers and writers.
        """
        if self.free() >= target:
            return True
        pinned = {g for g, _ in self.jobs}
        pinned.update(self.adopting)
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
        cache = self.root / 'cache'
        wanted_tiles = {tile for _, tile in self.jobs}
        files = sorted((p for kind in ('inputs', 'stages') for p in (cache / kind).rglob('*.pkl')
                        if p.relative_to(cache).parts[1] not in wanted_tiles),
                       key=lambda p: p.stat().st_mtime)
        for path in files:
            try:
                if time.time() - path.stat().st_mtime < self.grace_seconds:
                    continue
                with path.with_suffix('.lock').open('a') as lock:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        continue
                    stat = path.stat()
                    if time.time() - stat.st_mtime < self.grace_seconds:
                        continue
                    path.unlink()
                    self.metrics['evicted_bytes'] += stat.st_size
                    self.event('cache-evicted', path=str(path.relative_to(cache)), bytes=stat.st_size)
            except FileNotFoundError:
                continue
            if self.free() >= target:
                return True
        # Texture encoders also maintain their own bounded cache. Reclaim additional
        # old encodes only when no farm package can still be using the returned paths.
        if not self.jobs:
            for path in sorted((cache / 'textures').glob('*'), key=lambda p: p.stat().st_mtime):
                if path.suffix not in ('.ktx2', '.png', '.rgb') or time.time() - path.stat().st_mtime < self.grace_seconds:
                    continue
                self.metrics['evicted_bytes'] += path.stat().st_size
                path.unlink()
                if self.free() >= target:
                    return True
            for path in ([] if self.adopting else sorted((cache / 'raw-derived').glob('*/*'), key=lambda p: p.stat().st_mtime)):
                if time.time() - path.stat().st_mtime < self.grace_seconds:
                    continue
                size = sum(p.stat().st_size for p in path.rglob('*') if p.is_file())
                shutil.rmtree(path)
                self.metrics['evicted_bytes'] += size
                self.event('raw-cache-evicted', path=str(path.relative_to(cache)), bytes=size)
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
            if self.path not in ('/register', '/limits', '/cancel', '/seed'):
                return self.json(404, dict(error='unknown endpoint'))
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 4096:
                raise ValueError('invalid body size')
            body = json.loads(self.rfile.read(size))
            if self.path == '/seed':
                return self.json(200, dict(linked=self.server.farm.seed_intermediates()))
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
