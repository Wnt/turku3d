"""Scorer admission must keep the exact client lease and release it after freezing."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from gmaps_oci import freeze_current


class LeaseTest(unittest.TestCase):
    def test_full_snapshot_uses_nonurgent_visible_lease_and_matching_manifest_client(self):
        tile = 't0_1_0'
        payload = b'owned immutable fixture'
        sha = hashlib.sha256(payload).hexdigest()
        calls = []
        manifest = dict(pass_kind='full', visual={tile: dict(url=f'vis.{sha}.glb', sha256=sha)})

        class Response:
            status_code = 200
            headers = {}
            content = payload
            url = 'https://fixture/coarse/manifest'

            def json(self):
                return manifest

            def raise_for_status(self):
                pass

        def get(url, params=None, timeout=None):
            calls.append((url, dict(params or {})))
            return Response()

        storage = types.SimpleNamespace(atomic_json=lambda path, value: path.write_text(json.dumps(value)))
        oci = types.SimpleNamespace(BASE='https://fixture/coarse/', textures=lambda *args: None)
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, TURKU3D_SANDBOX='fixture'), \
                patch.dict('sys.modules', storage=storage, round2_oci=oci), patch('gmaps_oci.requests.get', side_effect=get):
            result = freeze_current(tile, Path(temporary))
            self.assertEqual(Path(result['path']).read_bytes(), payload)
        demands = [params for url, params in calls if url.endswith('/demand')]
        polls = [params for url, params in calls if url.endswith('/manifest.json')]
        self.assertTrue(demands)
        self.assertEqual(demands[0], dict(client=result['demand_client'], tiles=tile, urgent='', visible=tile))
        self.assertEqual(polls[0]['client'], result['demand_client'])
        self.assertEqual(demands[-1], dict(client=result['demand_client'], tiles='', urgent='', visible=''))


if __name__ == '__main__':
    unittest.main()
