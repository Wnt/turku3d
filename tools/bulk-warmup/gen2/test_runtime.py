import json
from pathlib import Path
import tempfile
import unittest
from runtime import GIB, admission_cap, memory_stalled, ordered, target_workers, worker_limit

class Runtime(unittest.TestCase):
    def test_memory_pressure_drains_without_stopping_workers(self):
        self.assertEqual(worker_limit(64),96)
        self.assertEqual(admission_cap(96,3,256*GIB,240*GIB),82)
        self.assertEqual(admission_cap(80,3,256*GIB,9*GIB),0)
        self.assertEqual(admission_cap(1,3,4*GIB,3.6*GIB),1)
    def test_idle_memory_pressure_is_resumable_not_an_endless_wait(self):
        self.assertTrue(memory_stalled(0,0,['t0_1_0']))
        self.assertFalse(memory_stalled(0,1,['t0_1_0']))
        self.assertFalse(memory_stalled(1,0,['t0_1_0']))
        self.assertFalse(memory_stalled(0,0,[]))
    def test_live_cap_rejects_partial_and_out_of_bounds_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'workers.json'
            for value in ('{','{"workers":97}','{"workers":true}'):
                path.write_text(value);target,error=target_workers(path,80,96)
                self.assertEqual(target,80);self.assertTrue(error)
            path.write_text('{"workers":88}')
            self.assertEqual(target_workers(path,80,96),(88,None))
    def test_longest_first_uses_measurements_before_density_prior(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'progress.jsonl'
            path.write_text(json.dumps(dict(event='built',tile='t0_50_50',seconds=300))+'\n')
            result,_=ordered(['t0_30_30','t0_0_0','t0_50_50'],path)
            self.assertEqual(result,['t0_50_50','t0_0_0','t0_30_30'])
            classes={'t0_30_30':{'kind':'dense'},'t0_0_0':{'kind':'empty'}}
            result,_=ordered(['t0_0_0','t0_30_30'],Path(tmp)/'absent',classes,{'dense':140,'empty':9})
            self.assertEqual(result,['t0_30_30','t0_0_0'])

if __name__=='__main__':unittest.main()
