import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('shared_sources', Path(__file__).parents[1] / 'shared_sources.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class OverlayTests(unittest.TestCase):
    def test_publish_adds_sources_without_writing_base_and_keeps_old_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base, overlay, first, second = (root / name for name in ('base', 'overlay', 'first', 'second'))
            for path in (base / 'raw/laz', first / 'raw/laz', second / 'raw/wfs', overlay):
                path.mkdir(parents=True)
            (base / 'raw/laz/old.laz').write_bytes(b'old')
            (base / 'library.json').write_bytes(b'library')
            (first / 'raw/laz/new.laz').write_bytes(b'new')
            (base / 'raw/laz/MANIFEST.jsonl').write_text('{"path":"old.laz","bytes":3}\n')
            (first / 'raw/laz/MANIFEST.jsonl').write_text('{"path":"new.laz","bytes":3}\n')
            a = module.publish(base, overlay, [first])
            self.assertEqual((a / 'raw/laz/old.laz').read_bytes(), b'old')
            self.assertEqual((a / 'raw/laz/new.laz').read_bytes(), b'new')
            self.assertFalse((base / 'raw/laz/new.laz').exists())
            self.assertEqual(len((a / 'raw/laz/MANIFEST.jsonl').read_text().splitlines()), 2)
            self.assertEqual(len((base / 'raw/laz/MANIFEST.jsonl').read_text().splitlines()), 1)
            self.assertTrue((a / 'library.json').is_symlink())
            (second / 'raw/wfs/building.json').write_bytes(b'building')
            b = module.publish(base, overlay, [first, second])
            self.assertNotEqual(a, b)
            self.assertEqual((overlay / 'current').resolve(), b)
            self.assertFalse((a / 'raw/wfs/building.json').exists())
            self.assertEqual((base / 'raw/laz/old.laz').read_bytes(), b'old')
            self.assertEqual(module.publish(base, overlay, [first, second]), b)


if __name__ == '__main__':
    unittest.main()
