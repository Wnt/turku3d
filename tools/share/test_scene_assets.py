import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('scene_assets', Path(__file__).with_name('scene_assets.py'))
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)


def fixture(store):
    image = b'fixture image'
    image_name = hashlib.sha256(image).hexdigest()+'.ktx2'
    (store/image_name).write_bytes(image)
    document = json.dumps({'asset': {'version': '2.0'}, 'images': [{'uri': '../shared/'+image_name}]}).encode()
    document += b' '*(-len(document)%4)
    glb = struct.pack('<IIIII', 0x46546c67, 2, 20+len(document), len(document), 0x4e4f534a)+document
    digest = hashlib.sha256(glb).hexdigest()
    (store/(digest+'.glb')).write_bytes(glb)
    return {'release': 'test', 'assets': [{'url': 'shared/'+digest+'.glb', 'sha256': digest}], 'materials': []}, image_name


class SceneAssetTests(unittest.TestCase):
    def test_mount_migration_keeps_old_content_addresses_without_replacing_existing_objects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            previous = root/'releases/old/dist/scenes/shared'
            previous.mkdir(parents=True)
            _, image = fixture(previous)
            store = root/'persistent'
            self.assertEqual(assets.retain_releases(root/'releases', store), 2)
            self.assertEqual(assets.retain_releases(root/'releases', store), 0)
            self.assertEqual((store/image).read_bytes(), b'fixture image')
            (store/image).write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError, 'Corrupt persistent'):
                assets.retain_releases(root/'releases', store)

    def test_glb_closure_and_content_addresses(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp)
            scene, image = fixture(store)
            rows = assets.closure([scene], store)
            self.assertEqual(len(rows), 2)
            self.assertIn('shared/'+image, [r['url'] for r in rows])
            (store/image).write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                assets.closure([scene], store)
            (store/image).unlink()
            with self.assertRaises(FileNotFoundError):
                assets.closure([scene], store)

    def test_external_or_non_content_addressed_references_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            scene, _ = fixture(Path(tmp))
            for url in ('https://other.invalid/a.glb', '../../secret', 'shared/asset.glb'):
                scene['assets'][0]['url'] = url
                with self.assertRaises(ValueError):
                    assets.closure([scene], tmp)


if __name__ == '__main__':
    unittest.main()
