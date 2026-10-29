"""Real child worker protocol verifies first/full publication and the full wall deadline."""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'pipeline'))

spec = importlib.util.spec_from_file_location('probe', Path(__file__).resolve().parents[3] / 'tools/remote-drafts/probe-builds.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
SERIAL = True

WORKER = '''import json,sys,time
from pathlib import Path
for line in sys.stdin:
 job=json.loads(line); out=Path(job['out']); first=job['first_pass']
 if not first: time.sleep(DELAY)
 (out/'vis.glb').write_bytes(b'visual')
 (out/'col.col').write_bytes(b'collision')
 m=dict(buildings=2,pass_kind='first' if first else 'full',visual={'t0_0_0':{'url':'vis.glb'}},collision={'c_0_0':{'url':'col.col'}})
 (out/'collision.json').write_text(json.dumps(m))
 if not first: (out/'manifest.json').write_text(json.dumps(m))
 print(json.dumps({'code':CODE,'stdout':'{}','stderr':'RuntimeError: injected'}),flush=True)
'''


class ProbeTests(unittest.TestCase):
    def run_worker(self, root, *, delay=0, code=0):
        original = subprocess.Popen
        script = WORKER.replace('DELAY', repr(delay)).replace('CODE', repr(code))
        def spawn(args, **kwargs):
            return original([probe.sys.executable, '-u', '-c', script], **kwargs)
        with (patch.object(probe.subprocess, 'Popen', side_effect=spawn),
              patch.object(probe, 'source_counts', return_value={'buildings': 2, 'collision': 1})):
            return probe.run(root, tile_id='t0_0_0', first_budget=2, full_budget=.04 if delay else 2)

    def test_first_collision_then_full_manifest_and_source_counts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = self.run_worker(root)
            self.assertEqual(result['passes']['full']['counts'], {'buildings': 2, 'collision': 1})
            health = [json.loads(line) for line in (root / '.build-health.jsonl').read_text().splitlines()]
            self.assertEqual([(r['pass'], r['outcome']) for r in health], [('first', 'built'), ('full', 'built')])

    def test_source_count_mismatch_fails_probe(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(probe, 'source_counts', return_value={'buildings': 99, 'collision': 1}):
                # Exercise a source mismatch with the same actual child protocol.
                original = subprocess.Popen
                script = WORKER.replace('DELAY', '0').replace('CODE', '0')
                def spawn(args, **kwargs):
                    return original([probe.sys.executable, '-u', '-c', script], **kwargs)
                with patch.object(probe.subprocess, 'Popen', side_effect=spawn):
                    with self.assertRaisesRegex(RuntimeError, 'differ from source'):
                        probe.run(Path(temp), tile_id='t0_0_0', first_budget=2, full_budget=2)

    def test_failure_and_timeout_do_not_record_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(RuntimeError, 'first probe failed'):
                self.run_worker(root, code=1)
            self.assertFalse((root / '.build-probe/result.json').exists())
            with self.assertRaisesRegex(TimeoutError, 'full probe exceeded'):
                self.run_worker(root, delay=.2)
            health = [json.loads(line) for line in (root / '.build-health.jsonl').read_text().splitlines()]
            self.assertEqual(health[-1]['error_kind'], 'TimeoutError')
