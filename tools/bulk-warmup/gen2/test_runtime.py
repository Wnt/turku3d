import json
from pathlib import Path
import tempfile
import unittest
from runtime import GIB, admission_cap, admits, memory_stalled, ordered, split_exclusions, target_workers, worker_limit

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
    def test_hilbert_order_keeps_each_lidar_unit_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'progress.jsonl'
            path.write_text(json.dumps(dict(event='built',tile='t0_9_9',seconds=300))+'\n')
            tiles=[f't0_{i}_{j}' for i in range(-4,4) for j in range(-4,4)]
            result,timings=ordered(tiles,path)
            self.assertEqual(sorted(result),sorted(tiles))
            self.assertEqual(timings,{'t0_9_9':300})
            units=[(int(t.split('_')[1])//2,int(t.split('_')[2])//2) for t in result]
            # Each unit's four tiles are consecutive, and consecutive units are neighbours.
            groups=[units[k] for k in range(0,len(units),4)]
            self.assertTrue(all(len(set(units[k:k+4]))==1 for k in range(0,len(units),4)))
            self.assertTrue(all(abs(a[0]-b[0])+abs(a[1]-b[1])==1 for a,b in zip(groups,groups[1:])))
    def test_memory_aware_admission_uses_predicted_peaks(self):
        total=256*GIB
        # Light suburb builds fit beyond the flat 3.2 GiB cap of 77 ...
        self.assertTrue(admits(96,85,85*1.9,1.9,total,90*GIB))
        self.assertEqual(admission_cap(96,3.2,total,90*GIB),77)
        # ... while a dense front stops at the plan, and real pressure stops admission.
        self.assertFalse(admits(96,80,80*3.1,3.1,total,90*GIB))
        self.assertFalse(admits(96,10,10*1.9,1.9,total,9*GIB))
        self.assertFalse(admits(85,85,10,1,total,200*GIB))
    def test_tiles_without_ground_returns_are_expected_exclusions(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs=Path(tmp)
            (logs/'r-t0_-31_44.log').write_text('turku3d.coarse.sources.NoCoverage: t0_-31_44: only 0 ground key points\n')
            (logs/'r-t0_1_1.log').write_text('TypeError: boom\n')
            rows=[dict(tile='t0_-31_44',revision='r'),dict(tile='t0_1_1',revision='r'),dict(tile='t0_2_2',result={})]
            excluded,failed=split_exclusions(rows,logs)
            self.assertEqual([r['tile'] for r in excluded],['t0_-31_44'])
            self.assertEqual([r['tile'] for r in failed],['t0_1_1','t0_2_2'])

if __name__=='__main__':unittest.main()
