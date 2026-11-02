import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('shared_tile_identity',
    Path(__file__).parents[1] / 'shared_tile_identity.py')
identity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(identity)


class IdentityTests(unittest.TestCase):
    def test_client_merge_tests_and_docs_reuse_but_stage_and_assets_invalidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.DEVNULL).decode().strip()
            git('init', '-q')
            def tree(path, content):
                file = root / path
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(content)
                git('add', path)
                return identity.generation(git('write-tree'), cwd=root)
            baseline = tree('pipeline/turku3d/coarse/ground_finishes.py', 'runtime')
            for path in ('game/src/main.ts', 'docs/specs/tiles.md', 'pipeline/turku3d/README.md',
                         'textures/tests/test_example.py', 'textures/turkutex/README.md',
                         'pipeline/turku3d/package/example.test.mjs'):
                self.assertEqual(tree(path, 'client or documentation'), baseline, path)
            changed = tree('pipeline/turku3d/coarse/ground_finishes.py', 'new runtime')
            self.assertNotEqual(changed, baseline)
            changed_asset = tree('textures/library/facade-params/example.json', '{}')
            self.assertNotEqual(changed_asset, changed)
            self.assertNotEqual(tree('game/public/basis/basis_transcoder.wasm', 'new encoder'), changed_asset)
