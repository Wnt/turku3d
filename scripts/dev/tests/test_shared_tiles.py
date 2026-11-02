"""The farm's version, concurrency, publication and eviction contracts, without city data."""
import importlib.util
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
             visual={tile: dict(url='mesh.glb', bytes=4)},
             collision={f'c_{int(tile.split("_")[1])*4+x}_{int(tile.split("_")[2])*4+z}':
                        dict(url='ground.col', bytes=3) for x in range(4) for z in range(4)})
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
        farm = self.farm(execute=None)
        self.wait(farm)
        receipt = farm_module.read_json(farm.output(GEN) / 't0_0_0/ready.json')
        self.assertTrue(receipt['result']['adopted'])
        self.assertEqual(farm.metrics['builds'], 1)

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
