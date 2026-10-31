"""Source preservation, immutable identities and admission for distant derivatives."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scenery import Scenery


class SceneryTest(unittest.TestCase):
    def test_catalogue_preserves_sources_and_uses_retained_full_without_regeneration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / 't0_-6_5'
            folder.mkdir()
            raw = b'precious source bytes'
            digest = hashlib.sha256(raw).hexdigest()
            name = f'vis.{digest}.glb'
            (folder / name).write_bytes(raw)
            full = dict(tile=folder.name, pass_kind='full', visual={folder.name: dict(url=name, sha256=digest, h=[0, 107])})
            (folder / 'last-full.json').write_text(json.dumps(full))
            (folder / 'manifest.json').write_text(json.dumps({**full, 'pass_kind': 'first'}))
            before = {p.name: p.read_bytes() for p in folder.iterdir()}
            cache = Scenery(root)
            first = cache.catalogue([folder.name, 't0_0_0'])
            self.assertEqual(first['missing'], ['t0_0_0'])
            self.assertEqual(len(first['tiles']), 1)
            self.assertEqual(cache.catalogue([folder.name])['tiles'], first['tiles'])
            key = first['tiles'][0]['key']
            self.assertTrue((cache.cache / f'{key}.source.json').is_file())
            cache.recipe = 'new derivative recipe'
            self.assertNotEqual(cache.catalogue([folder.name])['tiles'][0]['key'], key)
            self.assertEqual(before, {p.name: p.read_bytes() for p in folder.iterdir()})
            cache.pool.shutdown()

    def test_bad_source_hash_is_rejected_before_conversion(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); cache = Scenery(root); cache.cache.mkdir()
            folder = root / 't0_0_0'; folder.mkdir()
            name = 'vis.' + 'a' * 64 + '.glb'; (folder / name).write_bytes(b'changed')
            key = 'b' * 64
            (cache.cache / f'{key}.source.json').write_text(json.dumps(dict(tile=folder.name, source_url=name, source_sha256='a' * 64)))
            with patch('scenery.subprocess.run') as run:
                self.assertEqual(cache.descriptor(key)[0], 503)
                run.assert_not_called()
            self.assertEqual(cache.descriptor('../bad')[0], 404)
            cache.pool.shutdown()
