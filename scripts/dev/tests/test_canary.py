"""Real child worker protocol verifies first/full publication and the full wall deadline."""
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('canary', Path(__file__).resolve().parents[3] / 'tools/remote-drafts/canary.py')
canary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(canary)
SERIAL = True

WORKER = '''import json,sys,time
from pathlib import Path
for line in sys.stdin:
 job=json.loads(line); out=Path(job['out']); first=job['first_pass']
 if not first: time.sleep(DELAY)
 (out/'vis.glb').write_bytes(b'visual')
 (out/'col.col').write_bytes(b'collision')
 m=dict(pass_kind='first' if first else 'full',visual={'t0_0_0':{'url':'vis.glb'}},collision={'c_0_0':{'url':'col.col'}})
 (out/'collision.json').write_text(json.dumps(m))
 if not first: (out/'manifest.json').write_text(json.dumps(m))
 print(json.dumps({'code':CODE,'stdout':'{}','stderr':'RuntimeError: injected'}),flush=True)
'''


class CanaryTests(unittest.TestCase):
    def run_worker(self, root, *, delay=0, code=0):
        original = subprocess.Popen
        script = WORKER.replace('DELAY', repr(delay)).replace('CODE', repr(code))
        def spawn(args, **kwargs):
            return original([canary.sys.executable, '-u', '-c', script], **kwargs)
        with patch.object(canary.subprocess, 'Popen', side_effect=spawn):
            return canary.run(root)

    def test_first_collision_then_full_manifest_and_recorded_median(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = self.run_worker(root)
            self.assertIn('first_seconds', result)
            self.assertIn('full_seconds', result)
            self.assertEqual(json.loads((root / '.canary-history.json').read_text()), [result])

    def test_failure_and_timeout_do_not_record_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(RuntimeError, 'first canary failed'):
                self.run_worker(root, code=1)
            self.assertFalse((root / '.canary-history.json').exists())
            history = [{'full_seconds': .02}]
            (root / '.canary-history.json').write_text(json.dumps(history))
            with self.assertRaisesRegex(RuntimeError, 'full canary exceeded'):
                self.run_worker(root, delay=.2)
            self.assertEqual(json.loads((root / '.canary-history.json').read_text()), history)
