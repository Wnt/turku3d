"""The scenery server answers its own health and never forwards unknown paths."""
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen
import local_horizon
from scenery import Scenery


class LocalHorizonTest(unittest.TestCase):
    def serve(self, root, regional=None):
        local_horizon.scenery = Scenery(root / 'sources', root / 'cache')
        local_horizon.scenery.cache.mkdir()
        local_horizon.activity = local_horizon.Activity()
        local_horizon.args = SimpleNamespace(survey=regional is not None, regional=regional)
        server = local_horizon.Server(('127.0.0.1', 0), local_horizon.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(local_horizon.scenery.pool.shutdown)
        return f'http://127.0.0.1:{server.server_address[1]}'

    def get(self, url):
        try:
            with urlopen(url, timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def test_health_reports_inputs_cache_and_last_served(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'sources').mkdir()
            base = self.serve(root)
            status, state = self.get(base + '/coarse/scenery/health')
            self.assertEqual((status, state['state'], state['served']['count']), (200, 'ready', 0))
            self.assertTrue(state['cache']['writable'])
            self.assertEqual(self.get(base + '/coarse/scenery?tiles=t0_0_0')[0], 200)
            for path in ('/health', '/coarse/health'):
                status, state = self.get(base + path)
                self.assertEqual((status, state['served']['count']), (200, 1))
                self.assertIsNotNone(state['served']['last'])

    def test_missing_input_is_degraded_and_unknown_paths_are_404(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'sources').mkdir()
            base = self.serve(root, regional=str(root / 'moved-away.npz'))
            status, state = self.get(base + '/health')
            self.assertEqual((status, state['state']), (503, 'degraded'))
            self.assertFalse(state['inputs'][str(root / 'moved-away.npz')])
            self.assertEqual(self.get(base + '/'), (404, {'state': 'missing'}))
            self.assertEqual(self.get(base + '/coarse/tiles/t0_0_0/manifest.json')[0], 404)


if __name__ == '__main__':
    unittest.main()
