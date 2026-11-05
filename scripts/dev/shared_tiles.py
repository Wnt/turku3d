#!/usr/bin/env python3
"""Local Gen 2 tile farm. Immutable source revisions, single-flight jobs and shared stage caches.

Only registered snapshots under ROOT/revisions are executable. Never serves an older tile
as if it belonged to a requested revision. This is a trusted LAN development service.
"""
from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict
from contextlib import contextmanager
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import runpy
import shutil
import signal
import socket
import struct
import subprocess
import tempfile
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit

class TileHTTPServer(ThreadingHTTPServer):
    # Several browsers burst hundreds of independent near/scenery payloads.
    # The stdlib default of five drops connections before a handler can accept.
    request_queue_size = 256


REV = re.compile(r"[a-f0-9]{40,64}\Z")
TILE = re.compile(r"t0_-?\d{1,5}_-?\d{1,5}\Z")
ALIAS = re.compile(r"[a-z][a-z0-9+-]{0,63}\Z")  # base+m<id> names a migrated generation
HASHED = re.compile(r"[a-f0-9]{64}")
BUNDLE_SOURCE = hashlib.sha256(b'turku3d-shared-collision-delivery-v1').hexdigest()
RAW_CACHES = {
    'vegetation': ('vegetation/trees.py',),
    'coarse-roofs': ('buildings/reconstruct.py', 'buildings/inputs.py'),
    'lidar': ('lidar/cache.py',),
}
RAW_PRODUCERS = {
    'vegetation/trees.py': ('reduced_points',),
    'buildings/reconstruct.py': ('class6',),
    'buildings/inputs.py': ('read_points',),
}
BUNDLES_MAX = 8192  # descriptors (~200 B each): a city's resident tiles must not rebuild their bundles in turn
FRAGMENTS_MAX_BYTES = 384 * 1024 * 1024
PUBLIC = ('coarse', 'tile', 'visual', 'collision', 'roads', 'pass_kind', 'vehicles',
          'lights', 'vehicle_models', 'buildingPlacements', 'generation',
          'migrations', 'migration_base')
GIB = 2**30
PLAYER_SECONDS = 45    # a player stands on a tile while its d=0 requests continue (built tiles refresh every <= 30 s)
BUILD_SECONDS = 3600  # one tile build's wall limit: centre spot rebuilds (landmarks, decks) take 25-50 min
                      # under load; at 1800 s they timed out and restarted from scratch. /limits build_seconds.
DEMAND_SECONDS = 120   # game demand without a request for this long has been left behind
FAR_TILES = 8          # 2 km beyond every live player: dropped after half a minute
FINISH_SECONDS = 60    # a new pin lets the player's own tile finish when this close to done
RING_METRES = 400      # a ring tile's latest reported distance (bbox, <= 354 m) confirms it


class RebuildPending(RuntimeError):
    """A spot-rebuild migration needs the fixed generator's build of this tile first."""
    def __init__(self, revision):
        super().__init__(f'rebuild {revision} pending')
        self.revision = revision


def tile_ij(tile):
    _, i, j = tile.split('_')
    return int(i), int(j)


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


# osgallery-dev has 16 threads and 48 GB; a build uses about one core and 1.1 GB.
MAX_WORKERS = 12
# Migrating a finished base tile is light I/O; it runs beside the build workers.
MIGRATION_WORKERS = 2


class Farm:
    def __init__(self, root, runner, *, workers=2, queue_limit=128, floor_gib=10,
                 reserve_gib=1, keep_free_gib=60, grace_seconds=900, execute=None, free=None, stop=None,
                 migrate=None, navigate=None):
        self.root, self.runner = Path(root).resolve(), Path(runner).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.workers, self.queue_limit = workers, queue_limit
        self.floor, self.reserve, self.keep_free = floor_gib * GIB, reserve_gib * GIB, keep_free_gib * GIB
        # Disk housekeeping never holds the farm lock (scans and deletes take minutes on a full disk; under the
        # farm lock every request queued behind them: the 2026-11-05 city.lab outage). It has its own lock.
        self.housekeeping_lock, self.housekeeping_wake, self.housekeeper = threading.Lock(), threading.Event(), None
        self.grace_seconds = grace_seconds
        self.free = free or (lambda: shutil.disk_usage(self.root).free)
        self.execute = execute or self.run_build
        self.migrate = migrate or self.run_migration
        self.navigate = navigate or self.run_navigation
        # Navigation builds by source revision: 'building', or the last failure row.
        self.navigations = {}
        self.lock = threading.RLock()
        # Serialised fragments of built tiles: (revision, tile, manifest stamp) -> (bytes, bundle path or None).
        # A built tile's fragment depends only on its immutable products, so it is rendered once, outside the lock.
        self.fragments, self.fragment_bytes, self.fragments_lock = OrderedDict(), 0, threading.Lock()
        self.adoption_lock = threading.Lock()
        self.adopting = set()
        self.bundles = OrderedDict()
        self.pool = ThreadPoolExecutor(max_workers=MAX_WORKERS + MIGRATION_WORKERS, thread_name_prefix='tile')
        self.jobs, self.active, self.failures = {}, set(), {}
        self.awaiting = {}  # migrated key -> the spot-rebuild build key it waits for
        self.dispatched, self.queue_info, self.demands = set(), {}, {}
        self.build_seconds = BUILD_SECONDS
        self._status_snapshot, self._status_refresh = None, threading.Lock()
        self.turns, self.closing, self.last_saved = {}, False, 0.0
        # Live player positions: client -> {tile the player stands on (d=0): last time}.
        self.players, self.durations = {}, {}
        self.registrations, self.registrations_scanned = {}, float('-inf')
        # The game's version: the generation its proxy requests by distance from a pin client.
        self.pin = read_json(self.root / 'pin.json', {})
        self.stop = stop or self.stop_worker
        self.metrics = dict(requests=0, hits=0, joined=0, builds=0, failed=0,
                            stage_hits=0, stage_misses=0, input_hits=0, input_misses=0, evicted_bytes=0,
                            migrations=0)
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
        if meta.get('kind') == 'migrated':
            if source_version is not None:
                raise ValueError('a migrated revision inherits its base source version')
        elif source_version is None:
            source_version = meta.get('source_version')
            if source_version is None and previous:
                source_version = self.revision(previous).get('source_version')
        if meta.get('source_revision') and meta.get('kind') != 'migrated':
            source_version = source_version or meta.get('source_version')
            revision = meta['source_revision']
            meta = self.revision(revision)
        if source_version is not None and meta.get('kind') != 'migrated':
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
            self.registrations_scanned = float('-inf')
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
        with self.lock:
            return self._retained(tile)

    def _retained(self, tile):
        candidates = []
        for receipt in (self.root / 'products').glob(f'*/{tile}/ready.json'):
            value = read_json(receipt.parent / 'manifest.json')
            ready = read_json(receipt, {})
            if value and ready.get('revision'):
                candidates.append((value.get('built', ''), receipt.stat().st_mtime, value, ready))
        if candidates:
            _, _, manifest, ready = max(candidates, key=lambda r: r[:2])
            root = self.output(ready['generation'])
            os.utime(root, None)  # Lease starts when advertised, before the first payload GET.
            return manifest, f"/v/{ready['revision']}/coarse/{tile}/", dict(
                revision=ready['revision'], generation=ready['generation'], source_kind='retained'), root
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
                revision=None, generation=manifest.get('code', token), source_kind='accepted-fallback'), root
        return None

    def collision_bundle(self, root, tile, manifest):
        """Existing sorted-col-v1 wire format; derived bytes stay in farm-owned storage."""
        cells = sorted(manifest.get('collision', {}).items())
        if len(cells) != 16 or any(not entry.get('sha256') for _, entry in cells):
            return None
        key = tuple((cell, entry['sha256'], entry['bytes']) for cell, entry in cells)
        directory = self.root / 'delivery/shared'
        with self.lock:
            if key in self.bundles:
                descriptor = self.bundles[key]
                path = directory / descriptor['url'].rsplit('/', 1)[-1]
                try:
                    with path.open('rb') as source:
                        if os.fstat(source.fileno()).st_size == descriptor['bytes']:
                            os.utime(source.fileno(), None)
                            self.bundles.move_to_end(key)
                            return descriptor
                except FileNotFoundError:
                    pass
                del self.bundles[key]
            bodies = []
            for _, entry in cells:
                source = (root / tile / entry['url']).resolve()
                if not source.is_relative_to(root.resolve()):
                    raise ValueError('collision bundle source outside tile root')
                body = source.read_bytes()
                if len(body) != entry['bytes'] or hashlib.sha256(body).hexdigest() != entry['sha256']:
                    raise ValueError('collision bundle source mismatch')
                bodies.append(body)
            body = b''.join(bodies)
            digest = hashlib.sha256(body).hexdigest()
            name = f'bundle.{digest}.col'
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / name
            # Replace even an existing invalid derivative; never preserve an empty file
            # under an immutable hash URL after an interrupted older implementation.
            temporary = target.with_suffix(f'.{threading.get_ident()}.tmp')
            temporary.write_bytes(body)
            temporary.replace(target)
            descriptor = dict(url=f'/v/{BUNDLE_SOURCE}/coarse/shared/{name}', sha256=digest,
                              bytes=len(body), format='sorted-col-v1')
            self.bundles[key] = descriptor
            while len(self.bundles) > BUNDLES_MAX:
                self.bundles.popitem(last=False)
            return descriptor

    def cached_fragment(self, key):
        with self.fragments_lock:
            hit = self.fragments.get(key)
            if hit:
                self.fragments.move_to_end(key)
        # The collision bundle is farm-derived and evictable: a hit is good only while its file is there.
        if hit and (hit[1] is None or hit[1].is_file()):
            return hit[0]
        return None

    def remember_fragment(self, key, payload, bundle):
        with self.fragments_lock:
            old = self.fragments.pop(key, None)
            if old:
                self.fragment_bytes -= len(old[0])
            self.fragments[key] = (payload, bundle)
            self.fragment_bytes += len(payload)
            while self.fragment_bytes > FRAGMENTS_MAX_BYTES and self.fragments:
                _, (dropped, _) = self.fragments.popitem(last=False)
                self.fragment_bytes -= len(dropped)

    @contextmanager
    def payload(self, root, rel):
        """Pin an opened inode and renew its lease before allowing concurrent eviction."""
        with self.lock:
            path = (root / Path(*rel)).resolve()
            if not path.is_relative_to(root.resolve()) or any(p.startswith('.') for p in rel):
                raise ValueError('payload not found')
            if not (TILE.fullmatch(rel[0]) or rel[0] == 'shared'):
                raise ValueError('payload not public')
            try:
                source = path.open('rb')
            except (FileNotFoundError, IsADirectoryError):
                raise ValueError('payload not found') from None
            try:
                if root == self.root / 'delivery':
                    os.utime(source.fileno(), None)
                elif root.is_relative_to(self.root / 'products'):
                    os.utime(root, None)
            except BaseException:
                source.close()
                raise
        try:
            yield path, source
        finally:
            source.close()

    def status(self):
        with self.lock:
            return dict(state='ready', api_version=2, daemon_pid=os.getpid(),
                        version=self.version, aliases=self.aliases.copy(),
                        uptime_seconds=round(time.time() - self.started),
                        workers=self.workers, build_seconds=self.build_seconds, queued=len(self.jobs) - len(self.dispatched),
                        pin=dict(self.pin),
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

    def status_cached(self, max_age=5.0):
        """``status()`` as of at most ``max_age`` seconds ago, refreshed off the request thread.

        /health and /status are polled by the share proxies, the Mac proxies, watchers and guards; computing
        status per request (every queued job's priority, under the farm lock) made them queue behind builds
        and evictions until clients gave up (2026-11-05: 10-40 s answers, hundreds of CLOSE-WAIT sockets).
        The first call computes it; later calls return the last snapshot at once and start one refresh when
        it is older than ``max_age``."""
        snapshot = self._status_snapshot
        if snapshot is None:
            return self._refresh_status()
        if time.monotonic() - snapshot[0] > max_age and self._status_refresh.acquire(blocking=False):
            def refresh():
                try:
                    self._refresh_status()
                finally:
                    self._status_refresh.release()
            threading.Thread(target=refresh, name='status-refresh', daemon=True).start()
        return dict(snapshot[1], status_age_s=round(time.monotonic() - snapshot[0], 1))

    def _refresh_status(self):
        value = self.status()
        self._status_snapshot = (time.monotonic(), value)
        return dict(value, status_age_s=0.0)

    def version_status(self, revision):
        """Game polling describes only the requested generator, not other agents' work."""
        generation = self.revision(revision)['generation']
        with self.lock:
            queued = [key for key in self.jobs if key[0] == generation and key not in self.dispatched]
            building = sorted(key[1] for key in self.dispatched if key[0] == generation)
            failures = [value for key, value in self.failures.items() if key[0] == generation]
            return dict(state='ready', api_version=2, version=self.version,
                        revision=revision, generation=generation,
                        queued=len(queued), building=building, failures=failures[-10:])

    def request(self, revision, tile, distance=None, client=None, player=None):
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
            if distance is not None:
                self.observe(revision, generation, client)
                # Boot checks name themselves (?tileClient=canary -> client=canary): never a player.
                if distance < 1 and not str(player or '').startswith('canary'):
                    self.players.setdefault(player or client or 'anonymous', {})[tile] = time.time()
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
            if self.older(generation):
                return 409, dict(state='superseded', revision=revision, generation=generation,
                                 pin=self.pin.get('revision'))
            migrated = meta.get('kind') == 'migrated'
            waiting = self.awaiting.get(key)
            if waiting and waiting in self.jobs:
                return 202, dict(state='rebuild-' + ('building' if waiting in self.active else 'queued'),
                                 revision=revision, generation=generation)
            self.awaiting.pop(key, None)
            if migrated and not (self.output(meta['base_generation']) / tile / 'ready.json').exists():
                # Missing base tiles build at the base revision; the next request migrates them.
                # No client address: base requests never move the game's pin.
                status, value = self.request(meta['base_revision'], tile, distance, None, player)
                if status != 200:
                    return status if status >= 400 else 202, dict(
                        value, state=value.get('state') if status >= 400 else 'base-' + value['state'],
                        revision=revision, generation=generation)
            if len(self.jobs) >= self.queue_limit:
                return 429, dict(state='queue-full')
            # Reservation covers concurrent builders as well as the hard free-space floor.
            if not self.has_room(self.floor + self.reserve * (len(self.active) + 1)):
                return 507, dict(state='disk-pressure', free_gib=self.free() / GIB)
            self.jobs[key] = revision
            self.queue_info[key] = dict(revision=revision, generation=generation, tile=tile,
                                       requested=time.time(), seen=time.time(), distance=distance or 0,
                                       explicit=distance is None, **({'migrated': True} if migrated else {}))
            self.save_queue(force=True)
            self.pump()
            self.event('queued', revision=revision, tile=tile, generation=generation)
            return 202, dict(state='queued', revision=revision, generation=generation)

    def save_queue(self, force=False):
        if force or time.monotonic() - self.last_saved > 2:
            atomic_json(self.root / 'pending.json', list(self.queue_info.values()))
            self.last_saved = time.monotonic()

    def registered(self, generation):
        """Latest registration of any revision that produces this generation."""
        if time.monotonic() - self.registrations_scanned > 10:
            times, derived = {}, {}
            for path in (self.root / 'revisions').glob('*/revision.json'):
                meta = read_json(path, {})
                produced = meta.get('generation')
                if produced and meta.get('kind') == 'migrated':
                    derived[produced] = meta.get('base_generation')
                elif produced:
                    times[produced] = max(times.get(produced, 0), path.stat().st_mtime)
            # Edits of existing tiles are as old as their base: switching the game to them
            # supersedes nothing the base would not, and the base keeps building under them.
            for produced, base in derived.items():
                if base in times:
                    times[produced] = times[base]
            self.registrations, self.registrations_scanned = times, time.monotonic()
        return self.registrations.get(generation)

    def pin_clients(self):
        """Addresses whose game requests carry the pinned version (the game's tile proxy)."""
        return set(read_json(self.root / 'pin-clients.json', []))

    def older(self, generation):
        """Registered before the game's current version: never built or kept queued."""
        pin = self.pin.get('generation')
        if not pin or generation == pin:
            return False
        own, current = self.registered(generation), self.registered(pin)
        return own is not None and current is not None and own < current

    def observe(self, revision, generation, client):
        """Follow the game's version. A newer registration replaces it at once; an earlier one
        only after the current version had no game request for a minute (a deliberate revert)."""
        if client is None or client not in self.pin_clients():
            return
        now = time.time()
        if generation == self.pin.get('generation'):
            self.pin['seen'] = now
            return
        if self.pin.get('generation') and not (
                (self.registered(generation) or 0) > (self.registered(self.pin['generation']) or 0)
                or now - self.pin.get('seen', 0) > 60):
            return
        previous = self.pin.get('generation')
        self.pin = dict(revision=revision, generation=generation, seen=now, since=now)
        atomic_json(self.root / 'pin.json', self.pin)
        self.retarget(previous, revision, generation)

    def retarget(self, previous, revision, generation):
        """A new game version cancels every queued and running build of older versions.

        Completed stages stay in the shared stage and input caches, so the demanded tiles
        restart at the new version and rerun only the stages it changed.
        """
        now = time.time()
        demanded, distances = {}, {}
        for (g, _client), demand in self.demands.items():
            if self.older(g) and now - demand['seen'] < 120:
                for tile, category in demand['tiles'].items():
                    demanded[tile] = min(demanded.get(tile, 9), category)
        cancelled, stopping, kept = [], [], []
        for key in list(self.jobs):
            if not self.older(key[0]):
                continue
            info = self.queue_info[key]
            if not info.get('explicit') and now - info.get('seen', 0) < 180:
                demanded.setdefault(key[1], 3)
                distances[key[1]] = info.get('distance', 0)
            if key in self.dispatched and self.finishing(key, now):
                kept.append(key[1])  # The player sees it within a minute; the new version queues too.
                continue
            cancelled.append(key[1])
            if key in self.dispatched:
                info['cancelled'] = True
                stopping.append((info, self.output(key[0]) / key[1]))
            else:
                self.jobs.pop(key)
                self.queue_info.pop(key)
        for tile, category in demanded.items():
            key = (generation, tile)
            if key in self.jobs or (self.output(generation) / tile / 'ready.json').exists():
                continue
            self.jobs[key] = revision
            self.queue_info[key] = dict(revision=revision, generation=generation, tile=tile,
                                       requested=now, seen=now, distance=distances.get(tile, 0),
                                       explicit=False)
        self.event('cancelled', revision=revision, previous=previous, reason='pin moved',
                   tiles=cancelled, running=len(stopping), kept=kept, requeued=sorted(demanded))
        for info, output in stopping:
            threading.Thread(target=self.cancel_running, args=(info, output), daemon=True).start()
        self.save_queue(force=True)
        self.pump()

    def finishing(self, key, now):
        """A running build of a tile a live player stands on, expected to finish within
        FINISH_SECONDS (from this tile's last build time, else the recent median)."""
        if tile_ij(key[1]) not in self.standing(now):
            return False
        info = self.queue_info[key]
        recent = sorted(self.durations.values())
        expected = self.durations.get(key[1]) or (recent[len(recent) // 2] if recent else None)
        started = info.get('dispatched_at')
        return expected is not None and started is not None and expected - (now - started) < FINISH_SECONDS

    def cancel_running(self, info, output):
        try:
            if info.get('pid'):
                self.stop(info, output)
        except Exception as error:  # A changed process identity is never stopped.
            self.event('cancel-failed', tile=info.get('tile'), error=str(error)[-300:])

    def standing(self, now=None):
        """Tiles live players stand on (a d=0 request within PLAYER_SECONDS), as (i, j)."""
        now = now or time.time()
        here = set()
        for client, tiles in list(self.players.items()):
            live = {t: seen for t, seen in tiles.items() if now - seen < PLAYER_SECONDS}
            if live:
                self.players[client] = live
                here.update(tile_ij(t) for t in live)
            else:
                self.players.pop(client)
        return here

    def priority(self, key):
        """Player first: the tile a live player stands on, then its ring, then agents' explicit
        checks, then the rest of the game's demand nearest-first by its latest distance."""
        info = self.queue_info[key]
        if info.get('pid') and process_alive(info['pid'], info.get('start_ticks')):
            return (-2, 0, 0, info['requested'])  # Adopt existing work before starting anything new.
        distance = info.get('distance', 0)
        category = 1 if info.get('explicit') else 3
        for (generation, _client), demand in self.demands.items():
            if generation == key[0] and time.time() - demand['seen'] < 120 and key[1] in demand['tiles']:
                category = min(category, demand['tiles'][key[1]] if demand['tiles'][key[1]] < 1 else 2)
        here = self.standing()
        if here and distance < RING_METRES:
            i, j = tile_ij(key[1])
            ring = min(max(abs(i - a), abs(j - b)) for a, b in here)
            if ring <= 1:
                category = min(category, ring - 1)  # -1 standing on it, 0 its ring
        if category == 1:
            return (1, max(self.turns.get(key[0], 0), info['requested']), distance, info['requested'])
        return (category, distance, info['requested'], 0)

    def stale(self, key, now):
        """Game demand the player has left: no request for DEMAND_SECONDS, or far from every
        live player for half a minute. Agents' explicit checks never age out."""
        info = self.queue_info[key]
        if info.get('explicit') or key in self.dispatched or info.get('pid'):
            return False
        if self.priority(key)[0] <= 2:
            return False  # Still in a live demand or a player's ring.
        if now - info['seen'] > DEMAND_SECONDS:
            return True
        here = self.standing(now)
        if not here or now - info['seen'] < 30:
            return False
        i, j = tile_ij(key[1])
        return min(max(abs(i - a), abs(j - b)) for a, b in here) > FAR_TILES

    def demand(self, revision, client, tiles, urgent, visible, address=None):
        meta = self.revision(revision)
        generation = meta['generation']
        with self.lock:
            self.observe(revision, generation, address)
            row = dict(seen=time.time(),
                       tiles={tile: -1 if tile in urgent else 0 if tile in visible else 2 for tile in tiles})
            self.demands[(generation, client[:128])] = row
            if meta.get('kind') == 'migrated':
                self.demands[(meta['base_generation'], client[:128])] = row
            self.demands = {k:v for k,v in self.demands.items() if time.time() - v['seen'] < 120}
            self.pump()
        return self.status()

    def pump(self):
        if self.closing:
            return
        for key in list(self.jobs):
            info = self.queue_info[key]
            if key not in self.dispatched and not info.get('pid') and self.older(key[0]):
                self.event('dropped', revision=self.jobs[key], tile=key[1], reason='superseded by the game version')
                self.jobs.pop(key)
                self.queue_info.pop(key)
                continue
            if self.stale(key, time.time()):
                self.event('dropped', revision=self.jobs[key], tile=key[1], reason='player left this demand')
                self.jobs.pop(key)
                self.queue_info.pop(key)
        while True:
            pending = set(self.jobs) - self.dispatched
            light = {k for k in self.dispatched if self.queue_info[k].get('migrated')}
            if len(self.dispatched) - len(light) >= self.workers:
                pending = {k for k in pending if self.queue_info[k].get('migrated')}
            if len(light) >= MIGRATION_WORKERS:
                pending = {k for k in pending if not self.queue_info[k].get('migrated')}
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
        if self.queue_info.get((generation, tile), {}).get('migrated'):
            return self.build_migrated(revision, tile, generation)
        key = (generation, tile)
        started = time.monotonic()
        try:
            # Versions can build concurrently: stage keys lock their own writers and
            # prepared inputs publish atomically. Lock only the final version/tile product.
            with file_lock(self.root / 'locks' / (generation + '-' + tile + '.lock')):
                with self.lock:
                    # A recorded worker already owns its disk reservation. Recover its
                    # monitoring even under pressure; run_build can stop it safely.
                    if not self.queue_info[key].get('pid') and not self.has_room(
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
                    self.durations[tile] = seconds
                    if len(self.durations) > 512:
                        self.durations.pop(next(iter(self.durations)))
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
            if self.queue_info.get(key, {}).get('cancelled'):
                self.event('cancelled-build', revision=revision, tile=tile, generation=generation,
                           seconds=round(time.monotonic() - started, 3))
            else:
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

    def build_migrated(self, revision, tile, generation):
        """Apply a migrated revision's chain to its finished base tile (tile_migrate.py)."""
        key = (generation, tile)
        started = time.monotonic()
        try:
            with self.lock:
                self.active.add(key)
            try:
                result = self.migrate(revision, tile, self.revision(revision))
            except RebuildPending as pending:
                # The fixed generator builds this tile first; the next request migrates it.
                info = self.queue_info.get(key, {})
                with self.lock:
                    status, value = self.request(pending.revision, tile, info.get('distance') if not
                                                 info.get('explicit') else None, None, None)
                    rebuild = self.revision(pending.revision)['generation']
                    if status == 202:
                        self.awaiting[key] = (rebuild, tile)
                self.event('rebuild-queued', revision=revision, tile=tile, rebuild=pending.revision,
                           status=status, state=value.get('state'))
                return
            ready = read_json(self.output(generation) / tile / 'ready.json', {})
            if ready.get('generation') != generation:
                raise RuntimeError('migration did not produce a ready product')
            seconds = round(time.monotonic() - started, 3)
            with self.lock:
                self.metrics['migrations'] += 1
                self.failures.pop(key, None)
                self.version += 1
            self.event('migrated', revision=revision, generation=generation, tile=tile, seconds=seconds,
                       applied=ready.get('applied'), written=ready.get('written'), result=result)
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

    def run_migration(self, revision, tile, meta):
        """The derived revision's own source snapshot migrates the tile, nice'd like builds."""
        repo = self.root / 'revisions' / revision / 'repo'
        work = self.root / 'work' / 'migrate'
        work.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, REPO=str(repo), TURKU3D_WORK=str(work), TURKU3D_THREADS_PER_WORKER='1')
        data_root = self.revision(meta['base_revision']).get('data_root')
        if data_root:
            env['TURKU3D_DATA'] = data_root
        logs = self.root / 'logs'
        logs.mkdir(exist_ok=True)
        with (logs / f'{revision}-{tile}.log').open('a') as log:
            proc = subprocess.run([str(self.runner), 'python', 'scripts/dev/tile_migrate.py', 'apply',
                                   '--farm-root', str(self.root), '--revision', revision, tile],
                                  cwd=repo, env=env, stdout=subprocess.PIPE, stderr=log, text=True,
                                  timeout=600, start_new_session=True)
            log.write(proc.stdout)
        if proc.returncode == 4:
            for line in proc.stdout.splitlines()[::-1]:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get('state') == 'missing-rebuild' and REV.fullmatch(row.get('rebuild_revision', '')):
                    raise RebuildPending(row['rebuild_revision'])
        if proc.returncode:
            raise RuntimeError(f'migration exit {proc.returncode}; see logs/{revision}-{tile}.log')
        return dict(returncode=0)

    def navigation(self, revision):
        """The city road graph and delivery address pool of ``revision`` (turku3d.coarse.navigation, which the
        public coarse service serves as /coarse/navigation.json), or None while it builds. It derives from OSM
        inputs, not tile products, so a migrated revision serves its base's. Built once in the background."""
        meta = self.revision(revision)
        if meta.get('kind') == 'migrated':
            revision = meta['base_revision']
            meta = self.revision(revision)
        target = self.root / 'navigation' / f'{revision}.json'
        if target.is_file():
            return target
        with self.lock:
            state = self.navigations.get(revision)
            if state == 'building' or (state and time.time() - state['t'] < 60):
                return None
            self.navigations[revision] = 'building'
        threading.Thread(target=self._navigation, args=(revision, meta, target), daemon=True,
                         name='navigation').start()
        return None

    def _navigation(self, revision, meta, target):
        started = time.monotonic()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(f'.{threading.get_ident()}.tmp')
            self.navigate(revision, meta, temporary)
            data = json.loads(temporary.read_bytes())
            if (data['roads'].get('format') != 'turku3d-roadgraph'
                    or data['addresses'].get('format') != 'turku3d-addresses'):
                raise RuntimeError('navigation output has an unknown format')
            temporary.replace(target)
            addresses = data['addresses']['addresses']
            with self.lock:
                self.navigations.pop(revision, None)
            self.event('navigation-built', revision=revision, seconds=round(time.monotonic() - started, 1),
                       addresses=len(addresses), landmarks=sum(bool(a.get('landmark')) for a in addresses),
                       nodes=len(data['roads'].get('nodes', [])) // 2, edges=len(data['roads'].get('edges', [])))
        except Exception as error:
            row = dict(t=time.time(), revision=revision, error=str(error)[-1000:])
            with self.lock:
                self.navigations[revision] = row
            self.event('navigation-failed', **row)

    def run_navigation(self, revision, meta, output):
        """The revision's own source snapshot and data root build the navigation metadata into ``output``;
        its content-keyed cache is shared by every revision with the same inputs."""
        repo = self.root / 'revisions' / revision / 'repo'
        work = self.root / 'work' / 'navigation'
        work.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, REPO=str(repo), TURKU3D_WORK=str(work), TURKU3D_THREADS_PER_WORKER='1')
        if meta.get('data_root'):
            env['TURKU3D_DATA'] = meta['data_root']
        code = ('import sys; from pathlib import Path; from turku3d.coarse.navigation import navigation; '
                'Path(sys.argv[2]).write_bytes(navigation(Path(sys.argv[1])))')
        logs = self.root / 'logs'
        logs.mkdir(exist_ok=True)
        with (logs / f'{revision}-navigation.log').open('a') as log:
            proc = subprocess.run([str(self.runner), 'python', '-c', code, str(self.root / 'navigation' / 'content'),
                                   str(output)], cwd=repo, env=env, stdout=log, stderr=subprocess.STDOUT,
                                  timeout=1800, start_new_session=True)
        if proc.returncode:
            raise RuntimeError(f'navigation exit {proc.returncode}; see logs/{revision}-navigation.log')

    def base_fallback(self, meta, tile):
        """While a migrated tile is pending, its own finished base tile stands in for it."""
        if meta.get('kind') != 'migrated':
            return None
        root = self.output(meta['base_generation'])
        ready = read_json(root / tile / 'ready.json')
        manifest = read_json(root / tile / 'manifest.json')
        if not ready or not manifest:
            return None
        os.utime(root, None)
        return manifest, f"/v/{meta['base_revision']}/coarse/{tile}/", dict(
            revision=meta['base_revision'], generation=meta['base_generation'], source_kind='base'), root

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
        for model in manifest.get('buildingPlacements', {}).get('models', []):
            verify(root / tile / model['uri'], model['bytes'], model['id'])
        return len(checked)

    def run_build(self, revision, tile, root):
        info = self.queue_info[(root.name, tile)]
        if info.get('pid'):
            info.setdefault('build_started', process_started(info.get('start_ticks')) or time.time())
            deadline = time.monotonic() + self.build_seconds
            while process_alive(info['pid'], info.get('start_ticks')):
                reason = ('disk floor protection' if self.free() < self.floor + .5 * GIB else
                          'build timeout' if worker_elapsed(info) > self.build_seconds or time.monotonic() > deadline
                          else None)
                if reason:
                    self.stop_worker(info, root / tile)
                    raise RuntimeError(reason)
                time.sleep(1)
            report = read_json(root / tile / 'stage-times.json', {})
            if report.get('_build', {}).get('status') != 'complete':
                raise RuntimeError('adopted generator did not complete; retry will resume cached stages')
            return dict(returncode=0, adopted=True)
        repo, env = self.build_environment(revision, root)
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
            deadline = time.monotonic() + self.build_seconds
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

    def build_environment(self, revision, root):
        """Source snapshot and environment of a generator building ``revision`` into ``root``."""
        repo = self.root / 'revisions' / revision / 'repo'
        self.mount_intermediates(repo, root / 'work')
        # Bulk admission can drop to zero while already dispatched builds launch.
        # Draining workers still share the host; do not inflate their thread budget.
        with self.lock:
            concurrent = max(1, self.workers, len(self.dispatched))
        env = dict(os.environ, REPO=str(repo), TURKU3D_SHARED_CACHE=str(self.root / 'cache'),
                   TURKU3D_WORK=str(root / 'work'),
                   TURKU3D_THREADS_PER_WORKER=str(max(1, (os.cpu_count() or 1) // concurrent)))
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
        return repo, env

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
        selector = base / 'source_code.py'
        select = runpy.run_path(str(selector))['selected_source'] if selector.exists() else None
        for name, sources in RAW_CACHES.items():
            paths = [base / 'config.py', *(base / p for p in sources)]
            # Older immutable revisions still use direct LAZ readers during handoff.
            decoder = base / 'lidar/decoded.py'
            if decoder.exists():
                paths.append(decoder)
            if all(p.exists() for p in paths):
                code = []
                for path in paths:
                    source = path.read_bytes()
                    roots = RAW_PRODUCERS.get(str(path.relative_to(base)))
                    code.append(select(source, roots) if select and roots else source)
                digest = hashlib.sha256(b''.join(code)).hexdigest()
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

    def has_room(self, target):
        """Cheap check (one statvfs) for callers holding the farm lock: never scans or deletes. Short of room,
        it wakes the background housekeeper and answers at once."""
        if self.free() >= target:
            return True
        self.housekeeping_wake.set()
        return False

    def housekeeping(self, interval=60):
        """Service thread: keeps ``keep_free`` bytes free by ``make_room``, off the farm lock."""
        while not getattr(self, 'closing', False):
            self.housekeeping_wake.wait(interval)
            self.housekeeping_wake.clear()
            try:
                if self.free() < self.keep_free:
                    self.make_room(self.keep_free)
            except Exception as error:  # housekeeping must outlive one bad directory
                self.event('housekeeping-error', error=str(error)[-300:])

    def start_housekeeping(self):
        self.housekeeper = threading.Thread(target=self.housekeeping, name='housekeeping', daemon=True)
        self.housekeeper.start()

    def make_room(self, target):
        """Evict unused generations then cache LRU. Never touch another loop's directories.

        Requested tiles pin their intermediates. Other old entries can be reclaimed while
        workers run; their content-key locks protect concurrent readers and writers. Runs under its own
        housekeeping lock; the farm lock is held only to copy the pins (jobs, aliases, adoptions).
        """
        with self.housekeeping_lock:
            with self.lock:
                jobs, adopting, aliases = dict(self.jobs), set(self.adopting), dict(self.aliases)
            return self._make_room(target, jobs, adopting, aliases)

    def _make_room(self, target, jobs, adopting, aliases):
        if self.free() >= target:
            return True
        for path in sorted((self.root / 'delivery/shared').glob('bundle.*.col'), key=lambda p: p.stat().st_mtime):
            if time.time() - path.stat().st_mtime < self.grace_seconds:
                continue
            size = path.stat().st_size
            path.unlink()
            self.metrics['evicted_bytes'] += size
            self.event('delivery-evicted', bytes=size)
            if self.free() >= target:
                return True
        pinned = {g for g, _ in jobs}
        pinned.update(adopting)
        revisions = set(aliases.values()) | set(jobs.values())
        pinned.update(self.revision(rev)['generation'] for rev in revisions)
        for rev in list(revisions):
            meta = self.revision(rev)
            pinned.update(meta.get('parents', []))
            pinned.update(step['rebuild_generation'] for step in meta.get('migrations', [])
                          if isinstance(step, dict) and step.get('rebuild_generation'))
            for source in (meta.get('source_revision'), meta.get('base_revision'), meta.get('code_revision')):
                if source:
                    revisions.add(source)
        for rev in list(revisions):  # The base of a migrated revision has its own source snapshot.
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
        # Decoded arrays are opened under their source lock and memory-mapped.
        # Unlinking a cold entry cannot invalidate an existing reader's inode;
        # do not wait for a city-wide queue to become idle before reclaiming it.
        decoded = []
        for path in (cache / 'raw-derived/decoded').glob('*/*.npy'):
            if not HASHED.fullmatch(path.stem) or not HASHED.fullmatch(path.parent.name):
                continue
            try:
                decoded.append((path.stat().st_mtime, path))
            except FileNotFoundError:
                continue
        for _, path in sorted(decoded):
            try:
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
                    self.event('decoded-cache-evicted', key=path.stem, bytes=stat.st_size)
            except FileNotFoundError:
                continue
            if self.free() >= target:
                return True
        # Map files are hard-linked into private products before the producer lock
        # is released, so old shared entries can be evicted even while builds run.
        map_candidates = []
        for path in (cache / 'surface-maps').glob('*'):
            if not re.fullmatch(r'[a-f0-9]{64}', path.name):
                continue
            try:
                map_candidates.append((path.stat().st_mtime, path))
            except FileNotFoundError:
                continue
        for _, path in sorted(map_candidates):
            with path.with_suffix('.lock').open('a') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                try:
                    modified = path.stat().st_mtime
                except FileNotFoundError:
                    continue
                if not path.is_dir() or time.time() - modified < self.grace_seconds:
                    continue
                size = sum(p.stat().st_size for p in path.iterdir() if p.is_file())
                shutil.rmtree(path)
                self.metrics['evicted_bytes'] += size
                self.event('surface-maps-evicted', key=path.name, bytes=size)
            if self.free() >= target:
                return True
        wanted_tiles = {tile for _, tile in jobs}
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
        if not jobs:
            for path in sorted((cache / 'textures').glob('*'), key=lambda p: p.stat().st_mtime):
                if path.suffix not in ('.ktx2', '.png', '.rgb') or time.time() - path.stat().st_mtime < self.grace_seconds:
                    continue
                self.metrics['evicted_bytes'] += path.stat().st_size
                path.unlink()
                if self.free() >= target:
                    return True
            for path in ([] if adopting else sorted((cache / 'raw-derived').glob('*/*'), key=lambda p: p.stat().st_mtime)):
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
    protocol_version = 'HTTP/1.1'

    def setup(self):
        super().setup()
        self.connection.settimeout(30)
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def log_message(self, *_):
        pass

    def json(self, status, value):
        self.raw(status, json.dumps(value).encode())

    def raw(self, status, payload):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Access-Control-Allow-Origin', '*')
        if self.close_connection:
            self.send_header('Connection', 'close')
        if status in (202, 429):
            self.send_header('Retry-After', '2')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(payload)

    def do_POST(self):
        try:
            if self.path not in ('/register', '/limits', '/cancel', '/seed'):
                self.close_connection = True  # Unread request body cannot become another request.
                return self.json(404, dict(error='unknown endpoint'))
            try:
                size = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                self.close_connection = True
                raise
            if self.headers.get('Transfer-Encoding'):
                self.close_connection = True
                raise ValueError('Content-Length is required; transfer encoding is unsupported')
            if not 0 < size <= 4096:
                self.close_connection = True
                raise ValueError('invalid body size')
            body = json.loads(self.rfile.read(size))
            if self.path == '/seed':
                return self.json(200, dict(linked=self.server.farm.seed_intermediates()))
            if self.path == '/cancel':
                return self.json(200, self.server.farm.cancel(body['revision'], body['tiles']))
            if self.path == '/limits':
                workers, seconds = body.get('workers'), body.get('build_seconds')
                if workers is not None and (not isinstance(workers, int) or not 1 <= workers <= MAX_WORKERS):
                    raise ValueError(f'worker cap must be 1..{MAX_WORKERS}')
                if seconds is not None and (not isinstance(seconds, int) or not 600 <= seconds <= 14400):
                    raise ValueError('build_seconds must be 600..14400')
                if workers is None and seconds is None:
                    raise ValueError('workers or build_seconds required')
                farm = self.server.farm
                with farm.lock:
                    if seconds is not None:
                        farm.build_seconds = seconds
                        farm.event('build-seconds', seconds=seconds)
                    if workers is not None:
                        farm.workers = workers
                        farm.pump()
                        farm.event('worker-cap', workers=workers)
                return self.json(200, dict(farm.status(), http_backlog=self.server.request_queue_size))
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
            return self.json(200, dict(farm.status_cached(), http_backlog=self.server.request_queue_size))
        if parts == ['receipts']:
            # One tile's completion receipts for several versions at once (the share proxy's train chain), the
            # same bytes as each version's coarse/<tile>/ready.json; null while that version has not finished it.
            query = parse_qs(url.query)
            tile = query.get('tile', [''])[0]
            if not TILE.fullmatch(tile):
                raise ValueError('invalid tile')
            receipts = {}
            for name in query.get('v', [''])[0].split(',')[:32]:
                try:
                    generation = farm.revision(farm.resolve(name))['generation']
                except (ValueError, KeyError):
                    receipts[name] = None
                    continue
                receipts[name] = read_json(farm.output(generation) / tile / 'ready.json')
            return self.json(200, receipts)
        if len(parts) >= 4 and parts[0] == 'v' and parts[2] == 'coarse':
            if parts[1] == BUNDLE_SOURCE:
                return self.file(farm.root / 'delivery', parts[3:])
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
                        set(tiles('urgent')), set(tiles('visible')), self.client_address[0])
        if rel[0] in ('status', 'health', 'want', 'demand'):
            return self.json(200, farm.version_status(revision))
        if TILE.fullmatch(rel[0]) and rel[1:] in (['manifest.json'], ['collision.json']):
            query = parse_qs(url.query)
            distance = max(0, min(100000, float(query['d'][0]))) if 'd' in query else None
            # Only the queue decision holds the farm lock; a built tile's products are immutable.
            return self.raw(*self.fragment(farm, revision, meta, rel[0], distance, query.get('client', [None])[0]))
        if rel == ['navigation.json']:
            path = farm.navigation(revision)
            if path is None:
                # The game falls back to its resident street tiles on a 404 (game/src/gameplay/index.ts).
                return self.json(404, dict(error='navigation is being built', state='building'))
            with path.open('rb') as source:
                # Aliases move: never let the browser keep an earlier revision's graph.
                return self.send(path, source, 'no-cache')
        return self.file(farm.output(meta['generation']), rel)

    def fragment(self, farm, revision, meta, tile, distance, player=None):
        """(status, JSON bytes) of a tile's manifest/collision fragment."""
        status, value = farm.request(revision, tile, distance, self.client_address[0], player)
        key = None
        if status == 200:
            try:
                stamp = os.stat(farm.output(meta['generation']) / tile / 'manifest.json')
            except FileNotFoundError:
                stamp = None
            if stamp:
                key = (revision, tile, stamp.st_mtime_ns, stamp.st_size)
                hit = farm.cached_fragment(key)
                if hit is not None:
                    return 200, hit
        status, value = self.render_fragment(farm, revision, meta, tile, distance, status, value)
        payload = json.dumps(value).encode()
        if key and status == 200 and value.get('stale') is False:
            bundle = value.get('collision_bundle')
            farm.remember_fragment(key, payload, farm.root / 'delivery/shared' / bundle['url'].rsplit('/', 1)[-1]
                                   if bundle else None)
        return status, payload

    def render_fragment(self, farm, revision, meta, tile, distance, status, value):
        queue = dict(queue_status=status, queue_state=value.get('state'))
        if distance is None and status >= 400:
            # A CLI check must see its rejection; games keep the retained tile below.
            return status, {**value, **queue}
        stale = status != 200
        provenance = dict(revision=revision, generation=meta['generation'], source_kind='requested')
        prefix = f'/v/{revision}/coarse/{tile}/'
        source_root = farm.output(meta['generation'])
        if stale:
            retained = farm.base_fallback(meta, tile) or farm.retained(tile)
            if retained is None:
                return status, value
            manifest, prefix, provenance, source_root = retained
        else:
            manifest = read_json(farm.output(meta['generation']) / tile / 'manifest.json')
        value = {k: manifest[k] for k in PUBLIC if k in manifest}
        base_tile = base_prefix = None
        if not stale and meta.get('kind') == 'migrated':
            # A content-addressed payload the migration left unchanged keeps its base URL, so a
            # client switching to the edits reloads only what they changed.
            base_tile = farm.output(meta['base_generation']) / tile
            base_prefix = f"/v/{meta['base_revision']}/coarse/{tile}/"
        def located(v):
            if base_tile and HASHED.search(v.rsplit('/', 1)[-1]) and (base_tile / v).is_file():
                return base_prefix + v
            return prefix + v
        def pin(obj):
            if isinstance(obj, dict):
                return {k: (located(v) if k in ('url', 'uri') and isinstance(v, str)
                            else pin(v)) for k, v in obj.items()}
            if isinstance(obj, list):
                return [pin(v) for v in obj]
            return obj
        value = pin(value)
        bundle = farm.collision_bundle(source_root, tile, manifest)
        if bundle:
            value['collision_bundle'] = bundle
        value.update(**provenance, requested_revision=revision, stale=stale, **queue)
        return 200, value

    def file(self, root, rel):
        with self.server.farm.payload(root, rel) as (path, source):
            self.send(path, source, 'public,max-age=31536000,immutable' if HASHED.search(path.name) else 'no-cache')

    def send(self, path, source, cache):
        self.send_response(200)
        self.send_header('Content-Type', mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(os.fstat(source.fileno()).st_size))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', cache)
        self.end_headers()
        if self.command != 'HEAD':
            shutil.copyfileobj(source, self.wfile)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--runner', type=Path, required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=7950)
    parser.add_argument('--workers', type=int, default=2, choices=range(1, MAX_WORKERS + 1))
    args = parser.parse_args()
    import faulthandler
    # `kill -USR1 <pid>` writes every thread's stack to logs/stacks.log (the container blocks ptrace, so py-spy
    # cannot attach): the way to see what holds the farm lock or the CPU while it is slow.
    stacks = (args.root / 'logs').joinpath('stacks.log')
    stacks.parent.mkdir(exist_ok=True)
    faulthandler.register(signal.SIGUSR1, file=stacks.open('a'), all_threads=True)
    with file_lock(args.root / 'service.lock'):
        farm = Farm(args.root, args.runner, workers=args.workers)
        farm.start_housekeeping()
        server = TileHTTPServer((args.host, args.port), Handler)
        server.farm = farm
        (args.root / 'service.pid').write_text(str(os.getpid()) + '\n')
        farm.event('started', port=args.port, workers=args.workers, pid=os.getpid())
        server.serve_forever()


if __name__ == '__main__':
    main()
