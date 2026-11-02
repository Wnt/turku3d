"""The farm's version, concurrency, publication and eviction contracts, without city data."""
import importlib.util
import hashlib
import json
from pathlib import Path
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
        free[0] = 24 * farm_module.GIB
        self.assertFalse(farm.make_room(27 * farm_module.GIB))
        self.assertTrue(farm.output(GEN).exists())
        self.assertFalse(farm.output(GEN2).exists())
        self.assertEqual(farm.request(REV, 't0_1_0')[0], 507)
        self.assertFalse(farm.jobs)

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
                farm.free = lambda: 24 * farm_module.GIB
                with farm.lock:
                    self.assertFalse(farm.make_room(27 * farm_module.GIB))
                self.assertTrue(active.exists())
                self.assertTrue(locked.exists())
                self.assertFalse(cold.exists())
        finally:
            farm.free = lambda: 100 * farm_module.GIB
            release.set()
        self.wait(farm)

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

    def test_adopted_workers_keep_disk_and_total_runtime_limits(self):
        from unittest.mock import patch
        farm = self.farm(execute=None)
        root = farm.output(GEN)
        tile = 't0_0_0'
        info = dict(pid=2147483647, start_ticks='0', started_monotonic=time.monotonic())
        farm.queue_info[(GEN, tile)] = info
        with patch.object(farm_module, 'process_alive', return_value=True), patch.object(farm, 'stop_worker') as stop:
            farm.free = lambda: 24 * farm_module.GIB
            with self.assertRaisesRegex(RuntimeError, 'disk floor'):
                farm.run_build(REV, tile, root)
            stop.assert_called_once_with(info, root / tile)
            stop.reset_mock()
            farm.free = lambda: 100 * farm_module.GIB
            info['started_monotonic'] = time.monotonic() - 1801
            with self.assertRaisesRegex(RuntimeError, 'build timeout'):
                farm.run_build(REV, tile, root)
            stop.assert_called_once_with(info, root / tile)
        farm.queue_info.clear()

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


if __name__ == '__main__':
    unittest.main()
