import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('shared_tile_report',
    Path(__file__).parents[1] / 'shared_tile_report.py')
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


class ReportTests(unittest.TestCase):
    def test_distinguishes_invalidated_stages_from_evicted_unchanged_results(self):
        def row(key, cached):
            return dict(key=key, cached=cached, seconds=2)
        before = dict(tile='t0_0_0', revision='a', ready=dict(generation='old'),
                      inputs={'ground': row('g', False)},
                      stages={'ground': row('g', False), 'walls': row('w', False),
                              'old_stage': row('o', False), 'measure_only': {'seconds': 10}})
        after = dict(tile='t0_0_0', revision='b', ready=dict(generation='new', seconds=20),
                     inputs={'ground': row('g', True)},
                     stages={'ground': row('g', False), 'walls': row('w2', False),
                             'new_stage': row('n', True), 'measure_only': {'seconds': 3}})
        result = report.compare(before, after)
        self.assertFalse(result['same_product'])
        self.assertEqual(result['inputs']['changed'], [])
        self.assertEqual(result['inputs']['hit_rate'], 1)
        self.assertEqual(result['stages']['changed'], ['walls'])
        self.assertEqual(result['stages']['unchanged_misses'], ['ground'])
        self.assertEqual(result['stages']['added'], ['new_stage'])
        self.assertEqual(result['stages']['removed'], ['old_stage'])
        self.assertEqual(result['stages']['hit_rate'], .3333)
        with self.assertRaisesRegex(ValueError, 'same area'):
            report.compare(before, {**after, 'tile': 't0_1_0'})
