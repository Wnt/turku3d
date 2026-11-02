import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('capture_summary', Path(__file__).with_name('capture_summary.py'))
summary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summary)


class CaptureSummaryTest(unittest.TestCase):
    def test_reference_batches_pause_boundaries_and_gpu_dedup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'meta.json').write_text('{}')
            def write(name, values):
                (root / f'{name}.jsonl').write_text(''.join(json.dumps(v) + '\n' for v in values))
            write('frames', [[[0, 1, 2, 4, 1, 10, 20], [10, 2, 3, 4, 1, 10, 20]],
                             [[30, 3, 4, 8, 3, 10, 20], [50, 4, 5, 8, 3, 10, 20], [60, 5, 5, 8, 3, 10, 20]]])
            write('poses', [dict(t=t, paused=paused, E=t, N=0, H=1) for t, paused in [(0, True), (10, False), (50, False), (60, True)]])
            write('loaf', [dict(t=20, d=120, scripts=[dict(fn='work', url='code.js', pos=1, d=100, forced=2)])])
            result = summary.analyse(root, 1)
            self.assertEqual(result['frames'], 3)
            self.assertEqual(result['metrics']['fps'], 50)
            self.assertEqual(result['gpu_samples'], 2)
            self.assertEqual(result['metrics']['gpu_p50'], 6)
            self.assertEqual(result['scripts']['work @ code.js:1'], [100, 1, 2])

    def test_self_and_inclusive_stack_weights(self):
        traces = [dict(trace=dict(resources=['main.js'], frames=[dict(name='root', resourceId=0), dict(name='leaf', resourceId=0)],
                                 stacks=[dict(frameId=0), dict(frameId=1, parentId=0)],
                                 samples=[dict(timestamp=10, stackId=1), dict(timestamp=20, stackId=0)]))]
        own, inclusive, count = summary.js_functions(traces, 10, 20)
        self.assertEqual(count, 2)
        self.assertEqual(own['leaf @ main.js:?:?'], 10)
        self.assertEqual(inclusive['root @ main.js:?:?'], 20)

    def test_late_beacons_do_not_reverse_or_split_segments(self):
        poses = [dict(t=t, paused=paused) for t, paused in [(40, False), (0, True), (50, False), (30, True), (20, False), (10, False)]]
        self.assertEqual(summary.segments(poses, []), [(10, 20), (40, 50)])
        self.assertEqual([p['t'] for p in poses], [40, 0, 50, 30, 20, 10])

    def test_precise_marks_split_between_pose_samples(self):
        poses = [dict(t=t, paused=False) for t in [10, 20, 30, 40]]
        marks = [dict(k='pause', t=25), dict(k='resume', t=26)]
        self.assertEqual(summary.segments(poses, marks), [(10, 20), (30, 40)])


if __name__ == '__main__':
    unittest.main()
