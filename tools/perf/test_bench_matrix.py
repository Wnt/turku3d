import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('bench_matrix', Path(__file__).with_name('bench_matrix.py'))
matrix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matrix)


def result(label, fps, cpu95, gpu50, minor=10):
    d = lambda p50, p95=None, p99=None, mx=None: {'p50': p50, 'p95': p95, 'p99': p99, 'max': mx}
    return {'label': label, 'mode': 'static',
            'summary': {'fps': fps, 'cpuMs': d(8, cpu95, 40), 'gpuMs': d(gpu50, 12, 14), 'intervalMs': d(16.7, 30, 60, 120),
                        'longFrames': {'over50': 3}, 'draws': d(900), 'tris': d(4_200_000)},
            'trace': {'windows': {'static': {'gc': {'minor': minor, 'major': 1, 'ms': 30}, 'mainThread': {'busyMs': 7000}}}}}


class BenchMatrixTest(unittest.TestCase):
    def test_variants_put_the_base_first_and_one_per_ablation(self):
        self.assertEqual(matrix.variants('?bench=static', ['shadows', 'post'], ['quality=high', 'off=shadows']),
                         ['bench=static', 'bench=static&off=shadows', 'bench=static&off=post', 'bench=static&quality=high'])

    def test_metrics_medians_and_deltas_against_the_first_row(self):
        base = matrix.median_row([result('static/medium', 30, 20, 10), result('static/medium', 32, 22, 10, minor=14)])
        self.assertEqual(base['fps'], 31)
        self.assertEqual(base['gcMin'], 12)
        self.assertAlmostEqual(base['Mtris'], 4.2)
        off = matrix.median_row([result('static/medium off=shadows', 46.5, 11, 7)])
        text = matrix.table([('static/medium', 2, base), ('static/medium off=shadows', 1, off)])
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith('variant'))
        self.assertIn('+50%', lines[2])  # fps 31 -> 46.5
        self.assertIn('-30%', lines[2])  # gpu p50 10 -> 7
        self.assertTrue(lines[1].rstrip().endswith('-'))
        self.assertTrue(matrix.table([('a', 1, base)], markdown=True).startswith('| variant | n | fps'))

    def test_results_mode_reads_result_files_and_capture_folders(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'a.json').write_text(json.dumps(result('static/medium', 30, 20, 10)))
            capture = root / '20261103-120000-abcdef'
            capture.mkdir()
            (capture / 'meta.json').write_text(json.dumps({'bench': {'mode': 'static', 'result': result('static/medium off=post', 40, 15, 6)}}))
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(matrix.main(['--results', str(root / 'a.json'), str(capture)]), 0)
            self.assertIn('static/medium off=post', out.getvalue())
            (root / 'empty').mkdir()
            (root / 'empty' / 'meta.json').write_text('{}')
            with self.assertRaises(ValueError):
                matrix.load(root / 'empty')


if __name__ == '__main__':
    unittest.main()
