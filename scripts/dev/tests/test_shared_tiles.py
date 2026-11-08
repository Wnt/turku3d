"""The farm's version, concurrency, publication and eviction contracts, without city data."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from urllib.request import urlopen

SPEC = importlib.util.spec_from_file_location('shared_tiles', Path(__file__).parents[1] / 'shared_tiles.py')
farm_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(farm_module)

REV = 'a' * 40
REV2 = 'b' * 40
GEN = 'c' * 64
GEN2 = 'd' * 64


def fake_build(_revision, tile, root):
    directory = root / tile
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'mesh.glb').write_bytes(b'mesh')
    (directory / 'ground.col').write_bytes(b'col')
    m = dict(coarse=True, tile=tile, pass_kind='full',
             visual={tile: dict(url='mesh.glb', bytes=4, sha256=hashlib.sha256(b'mesh').hexdigest())},
             collision={f'c_{int(tile.split("_")[1])*4+x}_{int(tile.split("_")[2])*4+z}':
                        dict(url='ground.col', bytes=3, sha256=hashlib.sha256(b'col').hexdigest())
                        for x in range(4) for z in range(4)})
    farm_module.atomic_json(directory / 'manifest.json', m)
    farm_module.atomic_json(directory / 'stage-times.json', dict(ground={'cached': True}, walls={'cached': False}))
    return {'returncode': 0}


class FarmTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.farms = []
        for rev, gen in ((REV, GEN), (REV2, GEN2)):
            farm_module.atomic_json(self.root / 'revisions' / rev / 'revision.json',
                                    dict(revision=rev, generation=gen))

    def tearDown(self):
        for farm in self.farms:
            farm.close()
        self.temp.cleanup()

    def farm(self, **kw):
        farm = farm_module.Farm(self.root, self.root / 'run.sh', execute=kw.pop('execute', fake_build),
                                free=kw.pop('free', lambda: 100 * farm_module.GIB),
                                grace_seconds=kw.pop('grace_seconds', 0), **kw)
        self.farms.append(farm)
        return farm

    def wait(self, farm):
        deadline = time.monotonic() + 5
        while farm.jobs and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(farm.jobs)

    def test_http_listener_admits_a_browser_asset_burst_before_accepting(self):
        import socket
        from concurrent.futures import ThreadPoolExecutor

        server = farm_module.TileHTTPServer(('127.0.0.1', 0), farm_module.Handler)
        clients = []
        try:
            # Hold the accept loop: all handshakes must fit in the real socket
            # backlog, rather than winning a race with fast request handlers.
            def connect(_):
                return socket.create_connection(server.server_address, timeout=.3)
            with ThreadPoolExecutor(max_workers=32) as pool:
                clients = list(pool.map(connect, range(32)))
            self.assertEqual(len(clients), 32)
        finally:
            for client in clients:
                client.close()
            server.server_close()

    def test_a_payload_failing_after_its_headers_is_cut_not_padded_with_an_error_answer(self):
        """A 500 written after a payload's headers would arrive as body bytes inside its Content-Length."""
        import socket

        class Failing(farm_module.Handler):
            def route(self):
                self.send_response(200)
                self.send_header('Content-Length', '1000')
                self.send_header('Cache-Control', 'public,max-age=31536000,immutable')
                self.end_headers()
                self.wfile.write(b'x' * 10)
                raise RuntimeError('payload read failed')

        server = farm_module.TileHTTPServer(('127.0.0.1', 0), Failing)
        server.farm = type('Farm', (), {'event': lambda *_a, **_k: None})()
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        try:
            with socket.create_connection(server.server_address, timeout=5) as client:
                client.sendall(b'GET /v/x/coarse/shared/a.glb HTTP/1.1\r\nHost: x\r\n\r\n')
                received = b''
                while chunk := client.recv(65536):
                    received += chunk
            head, _, body = received.partition(b'\r\n\r\n')
            self.assertIn(b'Content-Length: 1000', head)
            self.assertEqual(body, b'x' * 10)
        finally:
            thread.join(5)
            server.server_close()

    def test_same_tile_version_single_flight_and_renderer_revision_reuse(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def build(rev, tile, root):
            calls.append((rev, tile))
            entered.set()
            release.wait(2)
            return fake_build(rev, tile, root)
        farm = self.farm(execute=build)
        self.assertEqual(farm.request(REV, 't0_0_0')[0], 202)
        self.assertTrue(entered.wait(2))
        self.assertEqual(farm.request(REV, 't0_0_0')[0], 202)
        self.assertEqual(farm.metrics['joined'], 1)
        release.set()
        self.wait(farm)
        self.assertEqual(farm.request(REV, 't0_0_0')[0], 200)
        farm_module.atomic_json(self.root / 'revisions' / REV2 / 'revision.json',
                                dict(revision=REV2, generation=GEN))
        self.assertEqual(farm.request(REV2, 't0_0_0')[0], 200)
        self.assertEqual(len(calls), 1)
        self.assertEqual(farm.metrics['stage_hits'], 1)
        self.assertEqual(farm.metrics['stage_misses'], 1)

    def test_distinct_versions_of_same_tile_use_both_workers(self):
        entered, release = threading.Barrier(3), threading.Event()
        def build(rev, tile, root):
            entered.wait(timeout=2)
            release.wait(2)
            return fake_build(rev, tile, root)
        farm = self.farm(execute=build, workers=2)
        farm.request(REV, 't0_0_0')
        farm.request(REV2, 't0_0_0')
        try:
            entered.wait(timeout=2)
        finally:
            release.set()
        self.wait(farm)
        self.assertEqual(farm.metrics['builds'], 2)

    def test_cancel_drops_only_queued_work(self):
        entered, release = threading.Event(), threading.Event()
        def build(rev, tile, root):
            entered.set()
            release.wait(2)
            return fake_build(rev, tile, root)
        farm = self.farm(execute=build, workers=1)
        farm.request(REV, 't0_0_0')
        self.assertTrue(entered.wait(2))
        farm.request(REV, 't0_0_1')
        result = farm.cancel(REV, ['t0_0_0', 't0_0_1'])
        self.assertEqual(result, dict(cancelled=['t0_0_1'], running=['t0_0_0']))
        release.set()
        self.wait(farm)
        self.assertEqual(farm.metrics['builds'], 1)

    def test_changed_generation_never_serves_old_tile(self):
        farm = self.farm()
        farm.request(REV, 't0_0_0')
        self.wait(farm)
        self.assertEqual(farm.request(REV2, 't0_0_0')[0], 202)
        self.wait(farm)
        self.assertEqual(farm.metrics['builds'], 2)
        self.assertTrue((farm.output(GEN) / 't0_0_0/ready.json').exists())
        self.assertTrue((farm.output(GEN2) / 't0_0_0/ready.json').exists())

    def test_failed_build_is_not_published(self):
        def broken(rev, tile, root):
            fake_build(rev, tile, root)
            (root / tile / 'ground.col').unlink()
        farm = self.farm(execute=broken)
        farm.request(REV, 't0_0_0')
        self.wait(farm)
        self.assertEqual(farm.request(REV, 't0_0_0')[0], 422)
        self.assertFalse((farm.output(GEN) / 't0_0_0/ready.json').exists())
        self.assertEqual(farm.metrics['failed'], 1)

    def test_retired_generator_is_not_requeued_by_old_clients(self):
        farm = self.farm()
        farm_module.atomic_json(self.root / 'retired.json', {REV: 'manifest serialization fixed in next commit'})
        status, result = farm.request(REV, 't0_0_0')
        self.assertEqual(status, 422)
        self.assertEqual(result['state'], 'revision-retired')
        self.assertFalse(farm.jobs)

    def test_disk_eviction_preserves_alias_and_running_generations(self):
        free = [100 * farm_module.GIB]
        farm = self.farm(free=lambda: free[0])
        farm.register(REV, 'integration')
        for gen in (GEN, GEN2):
            fake_build(REV, 't0_0_0', farm.output(gen))
        free[0] = 5 * farm_module.GIB  # under the 10 GiB floor
        self.assertFalse(farm.make_room(27 * farm_module.GIB))
        self.assertTrue(farm.output(GEN).exists())
        self.assertFalse(farm.output(GEN2).exists())
        self.assertEqual(farm.request(REV, 't0_1_0')[0], 507)
        self.assertFalse(farm.jobs)

    def test_disk_eviction_skips_generations_and_snapshots_moved_to_another_filesystem(self):
        free = [5 * farm_module.GIB]
        farm = self.farm(free=lambda: free[0])
        moved = self.root.parent / f'{self.root.name}-bulk'
        self.addCleanup(shutil.rmtree, moved, True)
        fake_build(REV, 't0_0_0', moved / ('e' * 64))
        (self.root / 'products').mkdir(parents=True, exist_ok=True)
        (self.root / 'products' / ('e' * 64)).symlink_to(moved / ('e' * 64))
        os.utime(self.root / 'products' / ('e' * 64), (1, 1), follow_symlinks=False)  # oldest: met first
        fake_build(REV2, 't0_0_0', farm.output(GEN2))
        snapshot = self.root / 'revisions' / REV2
        shutil.move(snapshot, moved / REV2)
        snapshot.symlink_to(moved / REV2)
        self.assertFalse(farm.make_room(27 * farm_module.GIB))  # completes the round instead of raising
        self.assertFalse(farm.output(GEN2).exists())
        self.assertTrue((self.root / 'products' / ('e' * 64) / 't0_0_0/manifest.json').exists())
        self.assertTrue((snapshot / 'revision.json').exists())
        events = [json.loads(line) for line in (self.root / 'events.jsonl').read_text().splitlines()]
        skipped = [(e['what'], e['name']) for e in events if e['event'] == 'eviction-skipped-link']
        self.assertEqual(skipped, [('generation', 'e' * 64), ('revision', REV2)])
        farm.make_room(27 * farm_module.GIB)
        self.assertEqual(sum(e['event'] == 'eviction-skipped-link' for e in map(
            json.loads, (self.root / 'events.jsonl').read_text().splitlines())), 2)  # logged once

    def test_disk_pressure_evicts_cold_area_cache_while_another_area_is_building(self):
        import fcntl
        entered, release = threading.Event(), threading.Event()
        def build(rev, tile, root):
            entered.set()
            release.wait(3)
            return fake_build(rev, tile, root)
        farm = self.farm(workers=1, execute=build)
        farm.request(REV, 't0_0_0')
        self.assertTrue(entered.wait(2))
        active = self.root / 'cache/inputs/t0_0_0/ground.key.pkl'
        cold = self.root / 'cache/stages/t0_5_5/ground/key.pkl'
        locked = self.root / 'cache/stages/t0_6_6/ground/key.pkl'
        for p in (active, cold, locked):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'cache')
        try:
            with locked.with_suffix('.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                farm.free = lambda: farm.floor  # under the floor plus its half-GiB margin
                with farm.lock:
                    self.assertFalse(farm.make_room(27 * farm_module.GIB))
                self.assertTrue(active.exists())
                self.assertTrue(locked.exists())
                self.assertFalse(cold.exists())
        finally:
            farm.free = lambda: 100 * farm_module.GIB
            release.set()
        self.wait(farm)

    def test_surface_map_eviction_preserves_locked_builds_and_private_hardlinks(self):
        import fcntl
        import os
        farm = self.farm()
        shared = self.root / 'cache/surface-maps'
        first, busy = shared / ('a' * 64), shared / ('b' * 64)
        for root in (first, busy):
            root.mkdir(parents=True)
            (root / 'albedo.png').write_bytes(b'pixels')
        private = self.root / 'private.png'
        os.link(first / 'albedo.png', private)
        with busy.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            farm.free = lambda: 0
            farm.make_room(farm_module.GIB)
        self.assertFalse(first.exists())
        self.assertTrue(busy.exists())
        self.assertEqual(private.read_bytes(), b'pixels')

    def test_surface_map_eviction_ignores_disappearing_publication_temporaries(self):
        from unittest.mock import patch
        farm = self.farm()
        temporary = self.root / 'cache/surface-maps/.maps-publishing'
        temporary.mkdir(parents=True)
        original = Path.stat
        def raced_stat(path, *args, **kwargs):
            if path == temporary:
                raise FileNotFoundError('producer removed temporary after atomic publication')
            return original(path, *args, **kwargs)
        farm.free = lambda: 0
        with patch.object(Path, 'stat', raced_stat):
            self.assertFalse(farm.make_room(farm_module.GIB))

    def test_explicit_candidate_precedes_background_from_another_revision(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def build(revision, tile, root):
            calls.append((revision, tile))
            if len(calls) == 1:
                entered.set()
                release.wait(3)
            return fake_build(revision, tile, root)
        farm = self.farm(workers=1, execute=build)
        farm.request(REV, 't0_0_0', 0)
        self.assertTrue(entered.wait(2))
        farm.request(REV, 't0_1_0', 500)
        farm.request(REV2, 't0_2_0')
        release.set()
        self.wait(farm)
        self.assertEqual(calls, [(REV, 't0_0_0'), (REV2, 't0_2_0'), (REV, 't0_1_0')])

    def test_pending_requests_resume_after_restart(self):
        row = dict(revision=REV, generation=GEN, tile='t0_0_0', requested=time.time(),
                   seen=time.time(), distance=0, explicit=True)
        farm_module.atomic_json(self.root / 'pending.json', [row])
        farm = self.farm()
        self.wait(farm)
        self.assertEqual(farm.request(REV, 't0_0_0')[0], 200)
        self.assertEqual(farm.metrics['builds'], 1)

    def test_explicit_profiled_revision_takes_precedence_over_alias_profile(self):
        farm = self.farm()
        for source in ('e' * 64, 'f' * 64):
            farm_module.atomic_json(self.root / 'sources/versions' / source / 'source-version.json',
                                    dict(version=source, files={}))
        requested = farm.register(REV, source_version='e' * 64)
        farm.register(REV, 'integration', 'f' * 64)
        result = farm.register(requested['revision'], 'integration')
        self.assertEqual(result['revision'], requested['revision'])
        self.assertEqual(result['source_version'], 'e' * 64)

    def test_parallel_supplement_initialization_preserves_exact_profile(self):
        from concurrent.futures import ThreadPoolExecutor
        farm = self.farm()
        source = self.root / 'sources/profile/supplements/lidar-supplement'
        source.mkdir(parents=True)
        work = self.root / 'work'
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: farm.mount_supplements(source.parents[1], work), range(12)))
        self.assertEqual((work / source.name).resolve(), source)
        other = self.root / 'sources/other/supplements/lidar-supplement'
        other.mkdir(parents=True)
        with self.assertRaisesRegex(RuntimeError, 'pinned source'):
            farm.mount_supplements(other.parents[1], work)

    def test_raw_adoption_pins_sources_and_destination_against_disk_eviction(self):
        from unittest.mock import patch
        farm = self.farm()
        repo = self.root / 'revisions' / REV / 'repo'
        base = repo / 'pipeline/turku3d'
        for name in ('config.py', 'vegetation/trees.py'):
            p = base / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('producer')
        source = farm.output(GEN) / 'work/cache/vegetation/complete.npz'
        source.parent.mkdir(parents=True)
        source.write_bytes(b'complete')
        original = farm_module.os.link
        def evict_then_link(src, dst):
            farm.free = lambda: 0
            with farm.lock:
                farm.make_room(27 * farm_module.GIB)
            self.assertTrue(source.exists())
            self.assertTrue(dst.parent.exists())
            original(src, dst)
        with patch.object(farm_module.os, 'link', side_effect=evict_then_link):
            self.assertEqual(farm.seed_intermediates(), 1)
        self.assertFalse(farm.adopting)

    def test_lidar_adoption_requires_every_declared_complete_shard(self):
        import struct
        directory = self.root / 'decoded'
        directory.mkdir()
        farm_module.atomic_json(directory / 'MANIFEST.json', dict(counts={'c_0_0': 2}))
        with self.assertRaises(FileNotFoundError):
            farm_module.Farm.validate_shards(directory)
        dtype = [('xyz', '<f8', (3,)), ('classification', '|u1'), ('return_number', '|u1'),
                 ('number_of_returns', '|u1'), ('intensity', '<u2')]
        header = repr(dict(descr=dtype, fortran_order=False, shape=(2,))).encode()
        header += b' ' * (-(10 + len(header) + 1) % 64) + b'\n'
        data = b'\x93NUMPY\x01\x00' + struct.pack('<H', len(header)) + header + bytes(58)
        shard = directory / 'c_0_0.npy'
        shard.write_bytes(data)
        self.assertEqual(len(farm_module.Farm.validate_shards(directory)), 2)
        shard.write_bytes(data[:-1])
        with self.assertRaisesRegex(RuntimeError, 'incomplete'):
            farm_module.Farm.validate_shards(directory)

    def test_collision_delivery_bundles_preserve_bytes_and_do_not_write_accepted_sources(self):
        import hashlib
        farm = self.farm()
        root = self.root / 'accepted'
        tile = 't0_0_0'
        directory = root / tile / 'col'
        directory.mkdir(parents=True)
        cells = {}
        for x in range(4):
            for z in range(4):
                cell = f'c_{x}_{z}'
                data = (cell + '-bytes').encode()
                path = directory / (cell + '.col')
                path.write_bytes(data)
                cells[cell] = dict(url='col/' + path.name, bytes=len(data),
                                   sha256=hashlib.sha256(data).hexdigest())
        before = {p: p.stat().st_mtime_ns for p in directory.iterdir()}
        manifest = dict(collision=cells)
        descriptor = farm.collision_bundle(root, tile, manifest)
        name = descriptor['url'].rsplit('/', 1)[-1]
        body = (self.root / 'delivery/shared' / name).read_bytes()
        self.assertEqual(body, b''.join((root / tile / cells[c]['url']).read_bytes() for c in sorted(cells)))
        self.assertEqual(hashlib.sha256(body).hexdigest(), descriptor['sha256'])
        self.assertEqual(before, {p: p.stat().st_mtime_ns for p in directory.iterdir()})
        self.assertIs(farm.collision_bundle(root, tile, manifest), descriptor)
        target = self.root / 'delivery/shared' / name
        target.write_bytes(b'')
        repaired = farm.collision_bundle(root, tile, manifest)
        self.assertEqual(repaired, descriptor)
        self.assertEqual(target.read_bytes(), body)
        farm.bundles.clear()
        target.write_bytes(b'bad persisted derivative')
        self.assertEqual(farm.collision_bundle(root, tile, manifest), descriptor)
        self.assertEqual(target.read_bytes(), body)
        (directory / 'c_0_0.col').write_bytes(b'bad')
        farm.bundles.clear()
        with self.assertRaisesRegex(ValueError, 'source mismatch'):
            farm.collision_bundle(root, tile, manifest)

    def test_retained_bundle_uses_the_selected_generations_root(self):
        farm = self.farm()
        for generation, revision, built in ((GEN, REV, '2026-11-03'), (GEN2, REV2, '2026-11-02')):
            root = farm.output(generation)
            fake_build(revision, 't0_0_0', root)
            manifest = farm_module.read_json(root / 't0_0_0/manifest.json')
            manifest['built'] = built
            farm_module.atomic_json(root / 't0_0_0/manifest.json', manifest)
            farm_module.atomic_json(root / 't0_0_0/ready.json', dict(revision=revision, generation=generation))
        manifest, prefix, provenance, root = farm.retained('t0_0_0')
        self.assertEqual(provenance['revision'], REV)
        self.assertEqual(root, farm.output(GEN))

    def test_retained_manifest_renews_lease_before_payload_request(self):
        import os
        farm = self.farm(grace_seconds=900)
        root = farm.output(GEN)
        fake_build(REV, 't0_0_0', root)
        farm_module.atomic_json(root / 't0_0_0/ready.json', dict(revision=REV, generation=GEN))
        os.utime(root, (time.time() - 1800,) * 2)
        self.assertIsNotNone(farm.retained('t0_0_0'))
        farm.free = lambda: 0
        self.assertFalse(farm.make_room(farm_module.GIB))
        self.assertTrue((root / 't0_0_0/mesh.glb').exists())
        self.assertLess(time.time() - root.stat().st_mtime, 2)

    def test_bundle_lookup_and_payload_open_are_safe_during_eviction(self):
        from unittest.mock import patch
        import os
        farm = self.farm(grace_seconds=900)
        root, tile = self.root / 'accepted', 't0_0_0'
        fake_build(REV, tile, root)
        manifest = farm_module.read_json(root / tile / 'manifest.json')
        descriptor = farm.collision_bundle(root, tile, manifest)
        path = self.root / 'delivery/shared' / descriptor['url'].rsplit('/', 1)[-1]
        os.utime(path, (time.time() - 1800,) * 2)
        entered, release, eviction_started, evicted = (threading.Event() for _ in range(4))
        original = Path.open
        def gated_open(p, *args, **kwargs):
            if p == path:
                entered.set()
                self.assertTrue(release.wait(2))
            return original(p, *args, **kwargs)
        def evict():
            eviction_started.set()
            farm.make_room(farm_module.GIB)
            evicted.set()
        farm.free = lambda: 0
        with patch.object(Path, 'open', gated_open):
            reader = threading.Thread(target=lambda: farm.collision_bundle(root, tile, manifest))
            reader.start()
            self.assertTrue(entered.wait(2))
            collector = threading.Thread(target=evict)
            collector.start()
            self.assertTrue(eviction_started.wait(2))
            try:
                self.assertFalse(evicted.wait(.05))
            finally:
                release.set()
                reader.join(2)
                collector.join(2)
        self.assertTrue(evicted.is_set())
        self.assertEqual(path.read_bytes(), b'col' * 16)
        # Once opened, streaming can finish even if a zero-grace pressure collector
        # unlinks the pathname. Neither lease update nor streaming recreates it.
        with farm.payload(self.root / 'delivery', ['shared', path.name]) as (_, stream):
            farm.grace_seconds = 0
            farm.make_room(farm_module.GIB)
            self.assertFalse(path.exists())
            self.assertEqual(stream.read(), b'col' * 16)
        self.assertFalse(path.exists())

    @unittest.skipUnless(Path('/proc/self/maps').exists(), 'open and mapped files are read from /proc')
    def test_raw_entries_of_finished_builds_go_oldest_first_while_a_build_runs(self):
        import fcntl
        import subprocess
        import sys

        farm = self.farm(workers=0, free=lambda: 0)
        farm.jobs[(GEN, 't0_0_0')] = REV
        farm.queue_info[(GEN, 't0_0_0')] = dict(pid=os.getpid(), build_started=time.time() - 3600)
        raw, digest, old = self.root / 'cache/raw-derived', 'd' * 64, time.time() - 7200

        def entry(path, age):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'entry')
            os.utime(path, (age, age))
            return path

        crown = entry(raw / 'crown-canopy' / ('6' * 64 + '.npz'), old - 300)
        crop = entry(raw / 'lidar' / digest / 'crops' / ('5' * 64 + '.npy'), old - 200)
        vegetation = entry(raw / 'vegetation' / digest / ('1' * 64 + '.npz'), old - 100)
        during = entry(raw / 'vegetation' / digest / ('2' * 64 + '.npz'), time.time() - 1800)  # running build's
        roof = entry(raw / 'coarse-roofs' / digest / ('3' * 64 + '.class6.npy'), old)
        source = raw / 'lidar' / digest / 'sources' / ('4' * 64)
        entry(source / 'index.npy', old)
        os.utime(source, (old, old))
        build = subprocess.Popen([sys.executable, '-c', 'import mmap, sys\n'
                                  'stream = open(sys.argv[1], "rb")\n'
                                  'data = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)\n'
                                  'print("mapped", flush=True)\n'
                                  'sys.stdin.read()', str(roof)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.addCleanup(build.kill)
        self.assertEqual(build.stdout.readline().strip(), 'mapped')
        with source.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)  # a build decoding or reading this source
            self.assertFalse(farm.make_room(farm_module.GIB))
        self.assertFalse(crown.exists() or crop.exists() or vegetation.exists())
        self.assertTrue(during.exists() and roof.exists() and source.exists())
        events = [json.loads(line) for line in (self.root / 'events.jsonl').read_text().splitlines()]
        self.assertEqual([e['cache'] for e in events if e['event'] == 'raw-entry-evicted'],
                         ['lidar-crop', 'vegetation', 'crown-canopy'])  # by kind, then age
        build.stdin.close()
        build.wait(5)
        farm.make_room(farm_module.GIB)
        self.assertFalse(roof.exists() or source.exists())
        self.assertTrue(during.exists())

    def test_raw_eviction_keeps_lidar_sources_that_queued_tiles_will_read(self):
        farm = self.farm(workers=0, free=lambda: 0)
        farm.jobs[(GEN, 't0_0_0')] = REV  # queued: it reads the sources whose spans reach it
        raw, old = self.root / 'cache/raw-derived', time.time() - 7200
        sources = {}
        for name, cell in (('7', 'c_1_1'), ('8', 'c_40_40')):
            source = raw / 'lidar' / ('d' * 64) / 'sources' / (name * 64)
            source.mkdir(parents=True)
            farm_module.atomic_json(source / 'MANIFEST.json', dict(spans={cell: [0, 1]}))
            os.utime(source, (old, old))
            sources[cell] = source
        self.assertFalse(farm.make_room(farm_module.GIB))
        self.assertTrue(sources['c_1_1'].exists())
        self.assertFalse(sources['c_40_40'].exists())

    def test_eviction_takes_lidar_sources_then_roofs_then_vegetation_and_decoded_arrays_last(self):
        free = [50 * farm_module.GIB]
        farm = self.farm(workers=0, free=lambda: free[0])
        cache, digest, now = self.root / 'cache', 'd' * 64, time.time()

        def entry(path, age, data=b'entry'):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            os.utime(path, (now - age, now - age))
            return path

        decoded_old = entry(cache / 'raw-derived/decoded' / digest / ('f' * 64 + '.npy'), 9000)
        decoded_recent = entry(cache / 'raw-derived/decoded' / digest / ('a' * 64 + '.npy'), 1800)  # used 30 min ago
        roof = entry(cache / 'raw-derived/coarse-roofs' / digest / ('3' * 64 + '.class6.npy'), 8000)
        vegetation = entry(cache / 'raw-derived/vegetation' / digest / ('1' * 64 + '.npz'), 8000)
        source = cache / 'raw-derived/lidar' / digest / 'sources' / ('4' * 64)
        entry(source / 'index.npy', 7000)
        os.utime(source, (now - 7000, now - 7000))  # the youngest of the old entries, yet first

        self.assertTrue(farm.make_room(free[0] + 1))  # a one-byte deficit
        self.assertFalse(source.exists())
        self.assertTrue(roof.exists() and vegetation.exists() and decoded_old.exists())

        free[0] = 0  # now everything eligible goes, in order
        self.assertFalse(farm.make_room(farm_module.GIB))
        events = [json.loads(line) for line in (self.root / 'events.jsonl').read_text().splitlines()]
        order = [e.get('cache', 'decoded') for e in events if e['event'] in ('raw-entry-evicted', 'decoded-cache-evicted')]
        self.assertEqual(order, ['lidar-source', 'roof', 'vegetation', 'decoded'])
        self.assertFalse(decoded_old.exists())
        self.assertTrue(decoded_recent.exists())  # used within the hour: the next tiles read it again

    def test_a_round_stops_on_what_it_evicted_while_free_space_lags(self):
        stale = 50 * farm_module.GIB  # statvfs that has not caught up with the unlinks yet
        farm = self.farm(workers=0, free=lambda: stale)
        root = self.root / 'cache/raw-derived/decoded' / ('e' * 64)
        root.mkdir(parents=True)
        old = time.time() - 7200
        for i, name in enumerate('fa'):
            path = root / (name * 64 + '.npy')
            path.write_bytes(b'decoded columns')
            os.utime(path, (old + i, old + i))
        self.assertTrue(farm.make_room(stale + 1))  # a one-byte deficit
        self.assertEqual([p.name[0] for p in root.glob('*.npy')], ['a'])  # only the older array went

    def test_raw_eviction_stops_on_released_bytes_while_free_space_lags(self):
        stale = 50 * farm_module.GIB  # statvfs that has not caught up with the unlinks yet
        farm = self.farm(workers=0, free=lambda: stale)
        raw, old = self.root / 'cache/raw-derived', time.time() - 7200
        for i, name in enumerate('123'):
            path = raw / 'vegetation' / ('d' * 64) / (name * 64 + '.npz')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'entry')
            os.utime(path, (old + i, old + i))
        self.assertTrue(farm.evict_raw_entries(raw, stale + 1, None))  # a one-byte deficit
        left = sorted(p.name[0] for p in (raw / 'vegetation' / ('d' * 64)).glob('*.npz'))
        self.assertEqual(left, ['2', '3'])  # only the oldest went

    def test_seed_copies_from_a_generation_moved_to_another_filesystem_while_room_allows(self):
        import errno
        from unittest import mock

        free = [100 * farm_module.GIB]
        farm = self.farm(free=lambda: free[0])
        repo = self.root / 'revisions' / REV / 'repo'
        for name in ('config.py', 'vegetation/trees.py', 'buildings/reconstruct.py',
                     'buildings/inputs.py', 'lidar/cache.py'):
            p = repo / 'pipeline/turku3d' / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('producer')
        local = farm.output(GEN) / 'work/cache/vegetation'
        local.mkdir(parents=True)
        (local / 'content.npz').write_bytes(b'complete')
        link = os.link

        def across(source, target, *args, **kw):  # products/<GEN> lives on another filesystem
            if str(farm.output(GEN)) in str(source):
                raise OSError(errno.EXDEV, 'Invalid cross-device link')
            return link(source, target, *args, **kw)

        with mock.patch.object(farm_module.os, 'link', across):
            free[0] = farm.keep_free - farm_module.GIB  # under keep_free: a copy would deepen the shortage
            self.assertEqual(farm.seed_intermediates(), 0)
            shared = farm.intermediate_roots(repo)['vegetation'] / 'content.npz'
            self.assertFalse(shared.exists())
            free[0] = 100 * farm_module.GIB
            self.assertEqual(farm.seed_intermediates(), 1)
        self.assertEqual(shared.read_bytes(), b'complete')
        self.assertNotEqual(shared.stat().st_ino, (local / 'content.npz').stat().st_ino)
        self.assertEqual([p.name for p in shared.parent.iterdir()], ['content.npz'])  # no temporary left
        events = [json.loads(line) for line in (self.root / 'events.jsonl').read_text().splitlines()]
        adopted = [(e['files'], e['copied'], e['skipped']) for e in events if e['event'] == 'intermediates-adopted']
        self.assertEqual(adopted, [(0, 0, 1), (1, 1, 0)])

    def test_raw_intermediates_share_by_producer_and_adopt_complete_private_files(self):
        farm = self.farm()
        repo = self.root / 'revisions' / REV / 'repo'
        base = repo / 'pipeline/turku3d'
        for name in ('config.py', 'vegetation/trees.py', 'buildings/reconstruct.py',
                     'buildings/inputs.py', 'lidar/cache.py'):
            p = base / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('producer')
        local = farm.output(GEN) / 'work/cache/vegetation'
        local.mkdir(parents=True)
        old = local / 'content.npz'
        old.write_bytes(b'complete')
        (local / '.content.tmp.npz').write_bytes(b'partial')
        self.assertEqual(farm.seed_intermediates(), 1)
        other = farm.output(GEN2) / 'work'
        farm.mount_intermediates(repo, other)
        linked = other / 'cache/vegetation/content.npz'
        self.assertTrue(linked.exists())
        self.assertEqual(linked.stat().st_ino, old.stat().st_ino)
        self.assertFalse((linked.parent / '.content.tmp.npz').exists())
        self.assertFalse(local.is_symlink())
        old_roots = farm.intermediate_roots(repo)
        (base / 'buildings/inputs.py').write_text('new roof producer')
        roots = farm.intermediate_roots(repo)
        self.assertEqual(roots['vegetation'], old_roots['vegetation'])
        self.assertNotEqual(roots['coarse-roofs'], old_roots['coarse-roofs'])
        (base / 'lidar/decoded.py').write_text('shared decoder')
        decoded_roots = farm.intermediate_roots(repo)
        self.assertEqual(set(decoded_roots), set(roots))
        for name in roots:
            self.assertNotEqual(decoded_roots[name], roots[name])

    @unittest.skipUnless(Path('/proc/self/maps').exists(), 'open and mapped files are read from /proc')
    def test_decoded_eviction_during_build_preserves_locked_and_mapped_sources(self):
        import fcntl
        import subprocess
        import sys

        farm = self.farm(workers=0, free=lambda: 0)
        farm.jobs[(GEN, 't0_0_0')] = REV
        root = self.root / 'cache/raw-derived/decoded' / ('e' * 64)
        root.mkdir(parents=True)
        cold, mapped, locked = (root / (c * 64 + '.npy') for c in 'fba')
        cold.write_bytes(b'exact source columns')
        mapped.write_bytes(b'columns a build reads')
        locked.write_bytes(b'decoding')
        for path in (cold, mapped, locked):  # last used two hours ago: past the decoded keep time
            os.utime(path, (time.time() - 7200, time.time() - 7200))
        # A running build: another process that memory-maps one array and keeps it.
        build = subprocess.Popen([sys.executable, '-c', 'import mmap, sys\n'
                                  'stream = open(sys.argv[1], "rb")\n'
                                  'data = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)\n'
                                  'stream.close()\n'
                                  'print("mapped", flush=True)\n'
                                  'sys.stdin.read()\n'
                                  'print(data[:].decode(), flush=True)', str(mapped)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.addCleanup(build.kill)
        self.assertEqual(build.stdout.readline().strip(), 'mapped')
        with locked.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertFalse(farm.make_room(farm_module.GIB))
            self.assertTrue(locked.exists())
        self.assertFalse(cold.exists())
        self.assertTrue(mapped.exists())  # unlinking it would free nothing while the build maps it
        self.assertTrue(cold.with_suffix('.lock').exists())
        self.assertEqual(farm.metrics['evicted_bytes'], len(b'exact source columns'))
        build.stdin.close()
        self.assertEqual(build.stdout.read().strip(), 'columns a build reads')
        build.wait(5)
        farm.make_room(farm_module.GIB)  # once the build lets go, the entry is cold like any other
        self.assertFalse(mapped.exists())

    def test_raw_reducers_ignore_unrelated_geometry_but_track_local_helpers(self):
        farm = self.farm()
        repo = self.root / 'revisions' / REV / 'repo'
        base = repo / 'pipeline/turku3d'
        for name, text in {
            'config.py': 'ORIGIN = 0',
            'source_code.py': (Path(__file__).parents[3] / 'pipeline/turku3d/source_code.py').read_text(),
            'vegetation/trees.py': 'CELL = .5\ndef reduced_points(): return helper()\ndef helper(): return CELL\ndef placement(): return 1',
            'buildings/reconstruct.py': 'def class6(): return 1\ndef unrelated(): return 1',
            'buildings/inputs.py': 'def read_points(): return 1',
            'lidar/cache.py': 'cache',
        }.items():
            path = base / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        before = farm.intermediate_roots(repo)
        path = base / 'vegetation/trees.py'
        path.write_text(path.read_text().replace('placement(): return 1', 'placement(): return 2'))
        self.assertEqual(farm.intermediate_roots(repo), before)
        path.write_text(path.read_text().replace('CELL = .5', 'CELL = .25'))
        after = farm.intermediate_roots(repo)
        self.assertNotEqual(after['vegetation'], before['vegetation'])
        self.assertEqual(after['coarse-roofs'], before['coarse-roofs'])

    def test_game_polling_counts_only_its_requested_generation(self):
        entered, release = threading.Event(), threading.Event()
        def build(revision, tile, root):
            entered.set()
            release.wait(3)
            return fake_build(revision, tile, root)
        farm = self.farm(workers=1, execute=build)
        farm.request(REV, 't0_0_0')
        self.assertTrue(entered.wait(2))
        farm.request(REV2, 't0_1_0')
        first, second = farm.version_status(REV), farm.version_status(REV2)
        self.assertEqual((first['queued'], first['building']), (0, ['t0_0_0']))
        self.assertEqual((second['queued'], second['building']), (1, []))
        release.set()
        self.wait(farm)

    def test_live_camera_demand_precedes_old_explicit_requests(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def build(revision, tile, root):
            calls.append(tile)
            if len(calls) == 1:
                entered.set()
                release.wait(3)
            return fake_build(revision, tile, root)
        farm = self.farm(workers=1, execute=build)
        farm.request(REV, 't0_0_0')
        self.assertTrue(entered.wait(2))
        farm.request(REV, 't0_1_0')
        farm.request(REV2, 't0_2_0', 300)
        farm.request(REV2, 't0_3_0', 10)
        farm.demand(REV2, 'camera', ['t0_2_0', 't0_3_0'], {'t0_3_0'}, {'t0_2_0'})
        release.set()
        self.wait(farm)
        self.assertEqual(calls, ['t0_0_0', 't0_3_0', 't0_2_0', 't0_1_0'])

    def test_player_tile_and_ring_precede_explicit_checks_and_older_demand(self):
        entered, release, calls = threading.Event(), threading.Event(), []
        def build(revision, tile, root):
            calls.append(tile)
            if len(calls) == 1:
                entered.set()
                release.wait(3)
            return fake_build(revision, tile, root)
        farm = self.farm(workers=1, execute=build)
        farm.request(REV, 't0_0_0', 0, 'game')
        self.assertTrue(entered.wait(2))
        farm.request(REV, 't0_8_8', 1400, 'game')   # older demand, far behind the player now
        farm.request(REV, 't0_3_0', 600, 'game')
        farm.request(REV, 't0_9_9')                 # an agent's explicit check
        farm.request(REV, 't0_6_1', 120, 'game')    # the player's ring
        farm.request(REV, 't0_5_0', 0, 'game')      # the tile the player now stands on
        farm.request(REV, 't0_5_1', 80, 'game')
        farm.players['game'].pop('t0_0_0')
        release.set()
        self.wait(farm)
        self.assertEqual(calls, ['t0_0_0', 't0_5_0', 't0_5_1', 't0_6_1', 't0_9_9', 't0_3_0', 't0_8_8'])

    def test_a_boot_check_client_is_never_taken_for_a_player(self):
        farm = self.farm(workers=0)
        farm.request(REV, 't0_0_0', 0, 'proxy', 'canary')
        farm.request(REV, 't0_6_-14', 0, 'proxy')
        self.assertEqual(farm.standing(), {(6, -14)})
        self.assertLess(farm.priority((GEN, 't0_6_-14')), farm.priority((GEN, 't0_0_0')))

    def test_demand_the_player_left_ages_out_but_explicit_checks_stay(self):
        farm = self.farm(workers=0)
        farm.request(REV, 't0_20_0', 0, 'game')
        farm.request(REV, 't0_1_0', 300, 'game')
        farm.request(REV, 't0_2_0', 300, 'game')
        farm.request(REV, 't0_3_0')
        farm.queue_info[(GEN, 't0_1_0')]['seen'] -= 40    # 19 tiles from the player now
        farm.queue_info[(GEN, 't0_2_0')]['seen'] -= 200   # no request for minutes
        farm.queue_info[(GEN, 't0_3_0')]['seen'] -= 500
        farm.pump()
        self.assertEqual({k[1] for k in farm.jobs}, {'t0_20_0', 't0_3_0'})

    def test_new_version_lets_the_players_nearly_finished_tile_complete(self):
        farm, entered, release, calls, stopped = self.pinned_farm()
        farm.durations['t0_0_0'] = 20.0
        farm.request(REV, 't0_0_0', 0, 'proxy')
        self.assertTrue(entered.wait(2))
        farm.queue_info[(GEN, 't0_0_0')]['pid'] = 1
        farm.request(REV2, 't0_0_0', 0, 'proxy')
        self.assertEqual(farm.pin['generation'], GEN2)
        self.assertFalse(farm.queue_info[(GEN, 't0_0_0')].get('cancelled'))
        self.assertIn((GEN2, 't0_0_0'), farm.jobs)
        time.sleep(.1)
        self.assertEqual(stopped, [])
        farm.queue_info[(GEN, 't0_0_0')].pop('pid')
        release.set()
        self.wait(farm)

    def pinned_farm(self):
        entered, release = threading.Event(), threading.Event()
        calls, stopped = [], []
        def build(revision, tile, root):
            calls.append((revision, tile))
            if len(calls) == 1:
                entered.set()
                release.wait(3)
            return fake_build(revision, tile, root)
        now = time.time()
        os.utime(self.root / 'revisions' / REV / 'revision.json', (now - 600, now - 600))
        farm_module.atomic_json(self.root / 'pin-clients.json', ['proxy'])
        farm = self.farm(workers=1, execute=build, stop=lambda info, output: stopped.append(output.name))
        return farm, entered, release, calls, stopped

    def test_new_game_version_cancels_older_builds_and_requeues_demanded_tiles(self):
        farm, entered, release, calls, stopped = self.pinned_farm()
        farm.request(REV, 't0_0_0', 0, 'proxy')
        self.assertTrue(entered.wait(2))
        farm.queue_info[(GEN, 't0_0_0')]['pid'] = 1  # A running generator to stop.
        farm.request(REV, 't0_1_0', 50, 'proxy')
        farm.request(REV, 't0_2_0')
        self.assertEqual(farm.pin['generation'], GEN)
        farm.request(REV2, 't0_5_0', 0, 'proxy')
        self.assertEqual(farm.pin['generation'], GEN2)
        self.assertTrue(farm.queue_info[(GEN, 't0_0_0')]['cancelled'])
        deadline = time.monotonic() + 2
        while not stopped and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(stopped, ['t0_0_0'])
        queued = {k for k in farm.jobs if k not in farm.dispatched}
        self.assertEqual(queued, {(GEN2, 't0_0_0'), (GEN2, 't0_1_0'), (GEN2, 't0_5_0')})
        farm.queue_info[(GEN, 't0_0_0')].pop('pid')
        release.set()
        self.wait(farm)
        self.assertNotIn((REV, 't0_2_0'), calls)
        self.assertEqual(farm.request(REV, 't0_3_0'), (409, dict(
            state='superseded', revision=REV, generation=GEN, pin=REV2)))

    def test_a_lagging_game_request_does_not_revert_the_version(self):
        farm, entered, release, calls, stopped = self.pinned_farm()
        farm.request(REV2, 't0_0_0', 0, 'proxy')
        self.assertTrue(entered.wait(2))
        self.assertEqual(farm.request(REV, 't0_1_0', 0, 'proxy')[0], 409)
        self.assertEqual(farm.pin['generation'], GEN2)
        self.assertEqual(farm.request(REV, 't0_1_0', 0, 'other-browser')[0], 409)
        release.set()
        self.wait(farm)
        self.assertEqual(stopped, [])

    def test_explicit_checks_of_newer_registrations_build_without_moving_the_version(self):
        farm, entered, release, calls, stopped = self.pinned_farm()
        farm.request(REV, 't0_0_0', 0, 'proxy')
        self.assertTrue(entered.wait(2))
        self.assertEqual(farm.request(REV2, 't0_1_0')[0], 202)
        self.assertEqual(farm.pin['generation'], GEN)
        release.set()
        self.wait(farm)
        self.assertEqual([tile for _, tile in calls], ['t0_0_0', 't0_1_0'])

    def test_completed_orphan_is_adopted_without_running_a_second_generator(self):
        fake_build(REV, 't0_0_0', self.root / 'products' / GEN)
        farm_module.atomic_json(self.root / 'products' / GEN / 't0_0_0/stage-times.json',
                                {'_build': {'status': 'complete'}})
        farm_module.atomic_json(self.root / 'pending.json', [dict(revision=REV, generation=GEN,
            tile='t0_0_0', requested=time.time(), seen=time.time(), explicit=True, distance=0,
            pid=2147483647, start_ticks='0')])
        farm = self.farm(execute=None, free=lambda: 24 * farm_module.GIB)
        self.wait(farm)
        receipt = farm_module.read_json(farm.output(GEN) / 't0_0_0/ready.json')
        self.assertTrue(receipt['result']['adopted'])
        self.assertEqual(farm.metrics['builds'], 1)

    def test_health_answers_from_a_snapshot_refreshed_off_the_request(self):
        farm = self.farm(execute=None)
        calls = []
        real = farm.status
        farm.status = lambda: calls.append(1) or real()
        first = farm.status_cached(max_age=60)
        self.assertEqual(len(calls), 1)
        self.assertEqual(first['status_age_s'], 0.0)
        for _ in range(20):  # within max_age: no recomputation at all
            farm.status_cached(max_age=60)
        self.assertEqual(len(calls), 1)
        stale = farm.status_cached(max_age=0)  # old: answers at once from the snapshot, refreshes in the background
        self.assertIn('status_age_s', stale)
        deadline = time.monotonic() + 5
        while len(calls) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(calls), 2)

    def test_requests_never_wait_for_disk_housekeeping(self):
        farm = self.farm(free=lambda: 0, execute=None)
        with farm.housekeeping_lock:  # a long eviction in progress elsewhere
            started = time.monotonic()
            status, value = farm.request(REV, 't0_0_0', distance=0)
            self.assertLess(time.monotonic() - started, 1)
        self.assertEqual((status, value['state']), (507, 'disk-pressure'))
        self.assertTrue(farm.housekeeping_wake.is_set())  # the housekeeper is asked to reclaim, off the lock
        with farm.housekeeping_lock:
            self.assertTrue(farm.lock.acquire(timeout=1))  # make_room does not hold the farm lock while it waits
            farm.lock.release()

    def test_registration_times_under_the_lock_read_no_revision_files(self):
        # 2026-11-08: 419 of 434 snapshots were symlinks to a busy HDD; rereading every revision.json every 10 s under
        # the farm lock took minutes and every tile request waited. The scanner reads each once, off the lock.
        from unittest.mock import patch
        farm = self.farm(execute=None)
        farm.start_revision_scanner()
        farm.registrations_scanned = float('-inf')
        self.assertIsNotNone(farm.registered(GEN))
        farm.registrations_scanned = float('-inf')
        with patch.object(farm_module, 'read_json', side_effect=AssertionError('read under the lock')):
            self.assertIsNotNone(farm.registered(GEN2))
            self.assertEqual(farm.revision(REV)['generation'], GEN)
        rev3, gen3 = 'e' * 40, 'f' * 64
        farm_module.atomic_json(self.root / 'revisions' / rev3 / 'revision.json', dict(revision=rev3, generation=gen3))
        farm.register(rev3, 'newer')  # the revision register() writes or names counts at once
        self.assertIsNotNone(farm.registered(gen3))

    def test_a_registered_alias_shows_in_health_at_once(self):
        farm = self.farm(execute=None)
        farm.status_cached(max_age=60)
        farm.register(REV, 'integration')
        self.assertEqual(farm.status_cached(max_age=60)['aliases'].get('integration'), REV)

    def test_adopted_workers_keep_disk_and_total_runtime_limits(self):
        from unittest.mock import patch
        farm = self.farm(execute=None)
        root = farm.output(GEN)
        tile = 't0_0_0'
        info = dict(pid=2147483647, start_ticks='0', started_monotonic=time.monotonic())
        farm.queue_info[(GEN, tile)] = info
        with patch.object(farm_module, 'process_alive', return_value=True), patch.object(farm, 'stop_worker') as stop:
            farm.free = lambda: farm.floor  # under the floor plus its half-GiB margin
            with self.assertRaisesRegex(RuntimeError, 'disk floor'):
                farm.run_build(REV, tile, root)
            stop.assert_called_once_with(info, root / tile)
            stop.reset_mock()
            farm.free = lambda: 100 * farm_module.GIB
            info['started_monotonic'] = time.monotonic() - farm.build_seconds - 1
            with self.assertRaisesRegex(RuntimeError, 'build timeout'):
                farm.run_build(REV, tile, root)
            stop.assert_called_once_with(info, root / tile)
        farm.queue_info.clear()

    def test_launch_keeps_thread_budget_when_bulk_admission_drains(self):
        from unittest.mock import Mock, patch
        farm = self.farm(execute=None)
        root = farm.output(GEN)
        root.mkdir(parents=True)
        tile = 't0_0_0'
        farm.queue_info[(GEN, tile)] = {}
        farm.dispatched = {(GEN, f't0_{i}_0') for i in range(4)}
        farm.workers = 0  # Memory pressure after pump(), before this worker launches.
        process = Mock(pid=2147483647, returncode=0)
        process.poll.return_value = 0
        with patch.object(farm_module.os, 'cpu_count', return_value=16), \
                patch.object(farm_module.subprocess, 'Popen', return_value=process) as launch:
            farm.run_build(REV, tile, root)
        self.assertEqual(launch.call_args.kwargs['env']['TURKU3D_THREADS_PER_WORKER'], '4')
        farm.queue_info.clear()
        farm.dispatched.clear()

    def test_recent_unpinned_payloads_get_a_grace_lease(self):
        farm = self.farm(grace_seconds=900)
        fake_build(REV, 't0_0_0', farm.output(GEN))
        farm.free = lambda: 24 * farm_module.GIB
        self.assertFalse(farm.make_room(27 * farm_module.GIB))
        self.assertTrue(farm.output(GEN).exists())
        import os
        os.utime(farm.output(GEN), (time.time() - 901, time.time() - 901))
        farm.make_room(27 * farm_module.GIB)
        self.assertFalse(farm.output(GEN).exists())

    def test_source_overlay_registration_pins_data_without_retargeting_old_revision(self):
        farm = self.farm()
        source = 'e' * 64
        farm_module.atomic_json(self.root / 'sources/versions' / source / 'source-version.json',
                                dict(version=source, files={}))
        farm.register(REV, 'integration')
        result = farm.register(REV, 'integration', source)
        self.assertNotEqual(result['revision'], REV)
        self.assertNotEqual(result['generation'], GEN)
        self.assertEqual(result['commit'], REV)
        self.assertEqual(farm.revision(REV)['generation'], GEN)
        self.assertEqual(farm.resolve('integration'), result['revision'])
        self.assertEqual(farm.revision(result['revision'])['data_root'],
                         str(self.root / 'sources/versions' / source))
        self.assertEqual(farm.register(REV, 'integration', source), result)
        fake_build(REV, 't0_0_0', farm.output(GEN))
        farm.free = lambda: 24 * farm_module.GIB
        farm.make_room(27 * farm_module.GIB)
        self.assertFalse(farm.output(GEN).exists())
        self.assertTrue((self.root / 'revisions' / REV / 'revision.json').exists())

    def test_client_revision_retains_source_overlay_and_finished_product(self):
        farm = self.farm()
        source = 'e' * 64
        farm_module.atomic_json(self.root / 'sources/versions' / source / 'source-version.json',
                                dict(version=source, files={}))
        first = farm.register(REV, 'integration', source)
        farm.request(first['revision'], 't0_0_0')
        self.wait(farm)
        farm_module.atomic_json(self.root / 'revisions' / REV2 / 'revision.json',
                                dict(revision=REV2, generation=GEN))
        advanced = farm.register(REV2, 'integration')
        self.assertEqual(advanced['source_version'], source)
        self.assertEqual(advanced['generation'], first['generation'])
        self.assertEqual(farm.request(advanced['revision'], 't0_0_0')[0], 200)
        self.assertEqual(farm.metrics['builds'], 1)
        self.assertEqual(farm.register(advanced['revision'], 'integration'), advanced)

    def test_http_reuses_connection_for_manifest_head_payload_and_rejects_unread_body(self):
        from http.client import HTTPConnection
        farm = self.farm()
        farm.request(REV, 't0_0_0')
        self.wait(farm)
        server = farm_module.ThreadingHTTPServer(('127.0.0.1', 0), farm_module.Handler)
        server.farm = farm
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection('127.0.0.1', server.server_port, timeout=2)
        try:
            connection.request('GET', f'/v/{REV}/coarse/t0_0_0/manifest.json')
            manifest = json.loads(connection.getresponse().read())
            sock = connection.sock
            self.assertIsNotNone(sock)
            url = manifest['visual']['t0_0_0']['url']
            connection.request('HEAD', url)
            response = connection.getresponse()
            self.assertEqual(response.getheader('Content-Length'), '4')
            self.assertEqual(response.read(), b'')
            connection.request('GET', url)
            self.assertEqual(connection.getresponse().read(), b'mesh')
            self.assertIs(connection.sock, sock)
            connection.request('POST', '/unknown', body=b'GET /health HTTP/1.1\r\n\r\n')
            response = connection.getresponse()
            self.assertEqual(response.status, 404)
            self.assertEqual(response.getheader('Connection'), 'close')
            response.read()
            self.assertIsNone(connection.sock)
            connection.request('POST', '/limits', body=b'x', headers={'Content-Length': 'invalid'})
            response = connection.getresponse()
            self.assertEqual(response.status, 400)
            response.read()
            self.assertIsNone(connection.sock)
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join()

    def test_http_built_fragment_is_rendered_once_and_byte_identical(self):
        farm = self.farm()
        farm.register(REV, 'train1')
        farm.request(REV, 't0_0_0'); self.wait(farm)
        server = farm_module.ThreadingHTTPServer(('127.0.0.1', 0), farm_module.Handler)
        server.farm = farm
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_port}'
        url = base + '/v/train1/coarse/t0_0_0/manifest.json?d=40'
        try:
            first = urlopen(url).read()
            self.assertEqual(len(farm.fragments), 1)
            self.assertEqual(urlopen(url).read(), first)
            self.assertEqual(urlopen(url.replace('manifest', 'collision')).read(), first)
            farm.fragments.clear()
            self.assertEqual(urlopen(url).read(), first, 'a cached answer is the rendered answer')
            # A rewritten product renders again; an evicted bundle is derived again.
            manifest = farm_module.read_json(farm.output(GEN) / 't0_0_0/manifest.json')
            manifest['pass_kind'] = 'draft'
            farm_module.atomic_json(farm.output(GEN) / 't0_0_0/manifest.json', manifest)
            self.assertEqual(json.loads(urlopen(url).read())['pass_kind'], 'draft')
            bundle = json.loads(urlopen(url).read())['collision_bundle']
            (self.root / 'delivery/shared' / bundle['url'].rsplit('/', 1)[-1]).unlink()
            farm.bundles.clear()
            self.assertEqual(urlopen(base + json.loads(urlopen(url).read())['collision_bundle']['url']).read(), b'col' * 16)
            receipts = json.load(urlopen(base + f'/receipts?tile=t0_0_0&v=train1,{REV2},missing'))
            self.assertEqual(receipts['train1'], json.loads(urlopen(base + '/v/train1/coarse/t0_0_0/ready.json').read()))
            self.assertIsNone(receipts[REV2]); self.assertIsNone(receipts['missing'])
        finally:
            server.shutdown()
            server.server_close()

    def test_http_cold_tile_serves_complete_accepted_fallback_while_building(self):
        release = threading.Event()
        def build(revision, tile, root):
            release.wait(3)
            return fake_build(revision, tile, root)
        accepted = self.root / 'accepted'
        fake_build(REV, 't0_0_0', accepted)
        farm_module.atomic_json(self.root / 'fallbacks.json', [str(accepted)])
        before = (accepted / 't0_0_0/mesh.glb').stat().st_mtime_ns
        farm = self.farm(execute=build)
        server = farm_module.ThreadingHTTPServer(('127.0.0.1', 0), farm_module.Handler)
        server.farm = farm
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            manifest = json.load(urlopen(base + f'/v/{REV}/coarse/t0_0_0/manifest.json'))
            self.assertTrue(manifest['stale'])
            self.assertEqual(manifest['requested_revision'], REV)
            self.assertIsNone(manifest['revision'])
            self.assertEqual(manifest['source_kind'], 'accepted-fallback')
            self.assertEqual(urlopen(base + manifest['visual']['t0_0_0']['url']).read(), b'mesh')
            bundle = manifest['collision_bundle']
            self.assertEqual(bundle['format'], 'sorted-col-v1')
            self.assertEqual(urlopen(base + bundle['url']).read(), b'col' * 16)
            self.assertEqual((accepted / 't0_0_0/mesh.glb').stat().st_mtime_ns, before)
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join()

    def test_building_models_are_validated_and_pinned_with_their_manifest(self):
        from urllib.parse import urljoin
        digest = hashlib.sha256(b'building').hexdigest()
        def build(revision, tile, root):
            result = fake_build(revision, tile, root)
            (root / 'shared').mkdir(exist_ok=True)
            (root / 'shared' / (digest + '.glb')).write_bytes(b'building')
            manifest = farm_module.read_json(root / tile / 'manifest.json')
            manifest['buildingPlacements'] = dict(version=1,
                models=[dict(id=digest, uri='../shared/' + digest + '.glb', bytes=8)],
                placements=[dict(id='building', model=digest, position=[250,0,100], viewDistance=500)])
            farm_module.atomic_json(root / tile / 'manifest.json', manifest)
            return result
        farm = self.farm(execute=build)
        farm.register(REV, 'integration')
        farm.request(REV, 't0_0_0'); self.wait(farm)
        server = farm_module.ThreadingHTTPServer(('127.0.0.1', 0), farm_module.Handler)
        server.farm = farm
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            manifest = json.load(urlopen(base + '/v/integration/coarse/t0_0_0/manifest.json'))
            model = manifest['buildingPlacements']['models'][0]
            self.assertTrue(model['uri'].startswith(f'/v/{REV}/coarse/'))
            farm.register(REV2, 'integration')
            self.assertEqual(urlopen(urljoin(base, model['uri'])).read(), b'building')
            original = farm_module.read_json(farm.output(GEN) / 't0_0_0/manifest.json')
            (farm.output(GEN) / 'shared' / (digest + '.glb')).write_bytes(b'corrupt!')
            with self.assertRaisesRegex(RuntimeError, 'hash mismatch'):
                farm.validate(farm.output(GEN), 't0_0_0', original)
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_http_pins_manifest_urls_and_serves_both_revisions(self):
        farm = self.farm()
        farm.register(REV, 'integration')
        farm.request(REV, 't0_0_0')
        self.wait(farm)
        server = farm_module.ThreadingHTTPServer(('127.0.0.1', 0), farm_module.Handler)
        server.farm = farm
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            m = json.load(urlopen(base + '/v/integration/coarse/t0_0_0/manifest.json'))
            url = m['visual']['t0_0_0']['url']
            self.assertEqual(url, f'/v/{REV}/coarse/t0_0_0/mesh.glb')
            farm.register(REV2, 'integration')
            self.assertEqual(urlopen(base + url).read(), b'mesh')
            stale = json.load(urlopen(base + '/v/integration/coarse/t0_0_0/manifest.json'))
            self.assertTrue(stale['stale'])
            self.assertEqual(stale['requested_revision'], REV2)
            self.assertEqual(stale['revision'], REV)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


DREV = 'e' * 64
DGEN = 'f' * 64


def register_migrated(root):
    farm_module.atomic_json(root / 'revisions' / DREV / 'revision.json', dict(
        kind='migrated', revision=DREV, generation=DGEN, commit=REV, code_revision=REV2,
        base_revision=REV, base_generation=GEN, parents=[GEN],
        migrations=[dict(name='chain-record', version=1, generation=DGEN)]))


def fake_migrate(revision, tile, meta, farm):
    """Hard links of the base tile plus the chain record, like tile_migrate.py."""
    base, target = farm.output(meta['base_generation']) / tile, farm.output(meta['generation']) / tile
    target.mkdir(parents=True)
    for name in ('mesh.glb', 'ground.col'):
        os.link(base / name, target / name)
    manifest = farm_module.read_json(base / 'manifest.json')
    manifest.update(migrations=[dict(name='chain-record', version=1)],
                    migration_base=dict(revision=REV, generation=GEN))
    farm_module.atomic_json(target / 'manifest.json', manifest)
    farm_module.atomic_json(target / 'ready.json', dict(revision=revision, generation=meta['generation'],
                                                        applied=['chain-record@1'], written=0))
    return {'returncode': 0}


class MigratedRevisionTests(unittest.TestCase):
    setUp, tearDown, farm, wait, pinned_farm = (
        FarmTests.setUp, FarmTests.tearDown, FarmTests.farm, FarmTests.wait, FarmTests.pinned_farm)

    def migrated_farm(self, **kw):
        register_migrated(self.root)
        holder = {}
        farm = self.farm(migrate=lambda r, t, m: fake_migrate(r, t, m, holder['farm']), **kw)
        holder['farm'] = farm
        return farm

    def test_missing_base_builds_at_the_base_revision_then_migrates(self):
        farm = self.migrated_farm()
        farm.register(DREV, 'bulk+mffffffff')
        status, value = farm.request(DREV, 't0_0_0')
        self.assertEqual((status, value['state']), (202, 'base-queued'))
        self.assertIn((GEN, 't0_0_0'), farm.jobs)
        self.wait(farm)
        self.assertTrue((farm.output(GEN) / 't0_0_0/ready.json').exists())
        self.assertEqual(farm.request(DREV, 't0_0_0')[0], 202)
        self.wait(farm)
        self.assertEqual(farm.request(DREV, 't0_0_0')[0], 200)
        self.assertEqual(farm.metrics['migrations'], 1)
        migrated = farm.output(DGEN) / 't0_0_0/mesh.glb'
        self.assertEqual(migrated.stat().st_ino, (farm.output(GEN) / 't0_0_0/mesh.glb').stat().st_ino)

    def test_migrations_run_beside_busy_build_workers(self):
        release = threading.Event()
        def build(revision, tile, root):
            if tile != 't0_0_0':
                release.wait(3)
            return fake_build(revision, tile, root)
        farm = self.migrated_farm(workers=1, execute=build)
        farm.request(REV, 't0_0_0'); self.wait(farm)
        farm.request(REV, 't0_1_0')  # Holds the only build worker.
        farm.request(DREV, 't0_0_0')
        deadline = time.monotonic() + 2
        while not (farm.output(DGEN) / 't0_0_0/ready.json').exists() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue((farm.output(DGEN) / 't0_0_0/ready.json').exists())
        release.set()
        self.wait(farm)

    def test_pending_migrated_tile_serves_its_own_base_with_the_chain_after(self):
        gate = threading.Event()
        def migrate(revision, tile, meta):
            gate.wait(3)
            return fake_migrate(revision, tile, meta, farm)
        register_migrated(self.root)
        farm = self.farm(migrate=migrate)
        farm.register(DREV, 'bulk+mffffffff')
        farm.request(REV, 't0_0_0'); self.wait(farm)
        fake_build(REV2, 't0_0_0', farm.output(GEN2))  # A newer unrelated product.
        farm_module.atomic_json(farm.output(GEN2) / 't0_0_0/ready.json', dict(revision=REV2, generation=GEN2))
        server = farm_module.ThreadingHTTPServer(('127.0.0.1', 0), farm_module.Handler)
        server.farm = farm
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            stale = json.load(urlopen(base + '/v/bulk+mffffffff/coarse/t0_0_0/manifest.json'))
            self.assertTrue(stale['stale'])
            self.assertEqual((stale['source_kind'], stale['revision']), ('base', REV))
            self.assertEqual(urlopen(base + stale['visual']['t0_0_0']['url']).read(), b'mesh')
            gate.set()
            self.wait(farm)
            fresh = json.load(urlopen(base + '/v/bulk+mffffffff/coarse/t0_0_0/manifest.json'))
            self.assertFalse(fresh['stale'])
            self.assertEqual(fresh['revision'], DREV)
            self.assertEqual(fresh['migrations'], [dict(name='chain-record', version=1)])
            self.assertEqual(fresh['migration_base']['generation'], GEN)
            self.assertEqual(fresh['visual']['t0_0_0']['url'], f'/v/{DREV}/coarse/t0_0_0/mesh.glb')
            self.assertEqual(urlopen(base + fresh['visual']['t0_0_0']['url']).read(), b'mesh')
        finally:
            gate.set()
            server.shutdown(); server.server_close(); thread.join()

    def test_migrated_generation_has_its_base_vintage(self):
        farm, entered, release, calls, stopped = self.pinned_farm()
        register_migrated(self.root)
        farm.registrations_scanned = float('-inf')
        farm.request(REV, 't0_0_0', 0, 'proxy')
        self.assertTrue(entered.wait(2))
        release.set()
        self.wait(farm)
        # A test alias registered after the base stays newer than a switch to the base's edits.
        self.assertFalse(farm.older(DGEN))
        farm.pin.update(revision=DREV, generation=DGEN)
        self.assertFalse(farm.older(GEN))
        self.assertFalse(farm.older(GEN2))
        self.assertEqual(farm.request(REV, 't0_1_0')[0], 202)
        self.wait(farm)

    def test_migrated_alias_pins_its_base_and_refuses_a_source_overlay(self):
        free = [100 * farm_module.GIB]
        farm = self.migrated_farm(free=lambda: free[0])
        farm.register(DREV, 'bulk+mffffffff')
        with self.assertRaisesRegex(ValueError, 'inherits'):
            farm.register(DREV, 'bulk+mffffffff', 'a' * 64)
        for gen in (GEN, GEN2, DGEN):
            fake_build(REV, 't0_0_0', farm.output(gen))
        free[0] = 24 * farm_module.GIB
        farm.make_room(27 * farm_module.GIB)
        self.assertTrue(farm.output(GEN).exists())
        self.assertTrue(farm.output(DGEN).exists())
        self.assertFalse(farm.output(GEN2).exists())


    def test_unchanged_payloads_keep_their_base_urls_after_a_switch(self):
        body, edited = b'hashed mesh', b'edited cell'
        visual, cell = f'vis.{hashlib.sha256(body).hexdigest()}.glb', f'c.{hashlib.sha256(edited).hexdigest()}.col'
        def build(revision, tile, root):
            fake_build(revision, tile, root)
            (root / tile / visual).write_bytes(body)
            manifest = farm_module.read_json(root / tile / 'manifest.json')
            manifest['visual'][tile] = dict(url=visual, bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
            farm_module.atomic_json(root / tile / 'manifest.json', manifest)
            return {'returncode': 0}
        def migrate(revision, tile, meta):
            fake_migrate(revision, tile, meta, farm)
            target = farm.output(meta['generation']) / tile
            os.link(farm.output(GEN) / tile / visual, target / visual)
            (target / cell).write_bytes(edited)
            manifest = farm_module.read_json(target / 'manifest.json')
            manifest['visual'][tile] = dict(url=visual, bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
            manifest['collision']['c_0_0'] = dict(url=cell, bytes=len(edited), sha256=hashlib.sha256(edited).hexdigest())
            farm_module.atomic_json(target / 'manifest.json', manifest)
            return {'returncode': 0}
        register_migrated(self.root)
        farm = self.farm(execute=build, migrate=migrate)
        farm.request(REV, 't0_0_0'); self.wait(farm)
        farm.request(DREV, 't0_0_0'); self.wait(farm)
        server = farm_module.ThreadingHTTPServer(('127.0.0.1', 0), farm_module.Handler)
        server.farm = farm
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            m = json.load(urlopen(base + f'/v/{DREV}/coarse/t0_0_0/manifest.json'))
            self.assertFalse(m['stale'])
            self.assertEqual(m['visual']['t0_0_0']['url'], f'/v/{REV}/coarse/t0_0_0/{visual}')
            self.assertEqual(m['collision']['c_0_0']['url'], f'/v/{DREV}/coarse/t0_0_0/{cell}')
            self.assertEqual(m['collision']['c_0_1']['url'], f'/v/{DREV}/coarse/t0_0_0/ground.col')
            self.assertEqual(urlopen(base + m['visual']['t0_0_0']['url']).read(), body)
            self.assertEqual(urlopen(base + m['collision']['c_0_0']['url']).read(), edited)
        finally:
            server.shutdown(); server.server_close(); thread.join()


    def test_spot_rebuild_builds_the_fixed_tile_then_migrates(self):
        builds, gate = [], threading.Event()
        def build(revision, tile, root):
            builds.append(revision)
            if revision == REV2:
                gate.wait(3)
            return fake_build(revision, tile, root)
        def migrate(revision, tile, meta):
            if not (farm.output(GEN2) / tile / 'ready.json').exists():
                raise farm_module.RebuildPending(REV2)
            return fake_migrate(revision, tile, meta, farm)
        register_migrated(self.root)
        farm = self.farm(execute=build, migrate=migrate)
        farm.request(REV, 't0_0_0'); self.wait(farm)
        self.assertEqual(farm.request(DREV, 't0_0_0')[0], 202)
        deadline = time.monotonic() + 2
        while (DGEN, 't0_0_0') in farm.jobs and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertIn((GEN2, 't0_0_0'), farm.jobs)  # The fixed generator's build is queued.
        status, value = farm.request(DREV, 't0_0_0')
        self.assertEqual((status, value['state']), (202, 'rebuild-building'))
        self.assertNotIn((DGEN, 't0_0_0'), farm.jobs)
        gate.set()
        self.wait(farm)
        self.assertEqual(farm.request(DREV, 't0_0_0')[0], 202)
        self.wait(farm)
        self.assertEqual(farm.request(DREV, 't0_0_0')[0], 200)
        self.assertEqual(builds, [REV, REV2])
        self.assertFalse(farm.failures)


if __name__ == '__main__':
    unittest.main()


class NavigationTests(unittest.TestCase):
    setUp, tearDown, farm = FarmTests.setUp, FarmTests.tearDown, FarmTests.farm
    # Flat [x, z] node coordinates and edge rows, as turku3d-roadgraph v1 stores them.
    NAVIGATION = dict(roads=dict(format='turku3d-roadgraph', nodes=[0, 0, 5, 0], edges=[[0, 1, 3]]),
                      addresses=dict(format='turku3d-addresses', addresses=[dict(id='a'), dict(id='m', landmark=True)]))

    def serve(self, farm):
        server = farm_module.ThreadingHTTPServer(('127.0.0.1', 0), farm_module.Handler)
        server.farm = farm
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join()))
        return f'http://127.0.0.1:{server.server_port}'

    def get(self, url):
        from urllib.error import HTTPError
        try:
            with urlopen(url) as reply:
                return reply.status, reply.headers.get('Cache-Control'), reply.read()
        except HTTPError as error:
            with error:
                return error.code, None, error.read()

    def test_builds_once_per_source_revision_and_a_migrated_revision_serves_its_base(self):
        calls, gate = [], threading.Event()
        def navigate(revision, meta, output):
            calls.append(revision)
            gate.wait(3)
            output.write_text(json.dumps(self.NAVIGATION))
        register_migrated(self.root)
        farm = self.farm(navigate=navigate)
        farm.register(DREV, 'bulk+mffffffff')
        base = self.serve(farm)
        status, _, body = self.get(base + '/v/bulk+mffffffff/coarse/navigation.json')
        self.assertEqual((status, json.loads(body)['state']), (404, 'building'))
        self.assertEqual(self.get(base + f'/v/{REV}/coarse/navigation.json')[0], 404)  # Joins the running build.
        gate.set()
        deadline = time.monotonic() + 5
        while not (self.root / 'navigation' / f'{REV}.json').exists() and time.monotonic() < deadline:
            time.sleep(.01)
        for url in ('/v/bulk+mffffffff/coarse/navigation.json', f'/v/{REV}/coarse/navigation.json'):
            status, cache, body = self.get(base + url)
            self.assertEqual((status, cache, json.loads(body)), (200, 'no-cache', self.NAVIGATION))
        self.assertEqual(calls, [REV])
        built = [json.loads(l) for l in (self.root / 'events.jsonl').read_text().splitlines()]
        self.assertIn(dict(addresses=2, landmarks=1, nodes=2, edges=1),  # Counts, never the graph itself.
                      [{k: e[k] for k in ('addresses', 'landmarks', 'nodes', 'edges')} for e in built
                       if e['event'] == 'navigation-built'])

    def test_a_failed_build_is_not_retried_within_a_minute(self):
        calls = []
        def navigate(revision, meta, output):
            calls.append(revision)
            output.write_text('{"roads": {"format": "other"}, "addresses": {}}')
        farm = self.farm(navigate=navigate)
        base = self.serve(farm)
        self.assertEqual(self.get(base + f'/v/{REV}/coarse/navigation.json')[0], 404)
        deadline = time.monotonic() + 5
        while not isinstance(farm.navigations.get(REV), dict) and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(self.get(base + f'/v/{REV}/coarse/navigation.json')[0], 404)
        self.assertEqual(calls, [REV])
        self.assertFalse((self.root / 'navigation' / f'{REV}.json').exists())
        self.assertIn('unknown format', farm.navigations[REV]['error'])
