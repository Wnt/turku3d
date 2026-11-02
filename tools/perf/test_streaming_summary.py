import unittest
from streaming_summary import resource, summarize, timestamp


class StreamingSummaryTests(unittest.TestCase):
    def test_playwright_window_and_generation_identity(self):
        road = 'roads.' + 'a' * 64 + '.json'
        rows = [dict(k='res', t='21:00:00.000', p='/coarse/_v/one/t0_2_-1/' + road, s=200),
                dict(k='res', t='21:00:20.000', p='/coarse/_v/two/t0_2_-1/' + road, s=200),
                dict(k='res', t='21:00:30.000', p='/coarse/t0_2_-1/manifest.json?d=20', s=304),
                dict(k='fail', t='21:00:25.000', p='/coarse/t0_2_-1/' + road),
                dict(k='res', t='21:01:01.000', p='/coarse/t0_2_-1/' + road, s=200)]
        result = summarize(rows, timestamp('21:00:00'), timestamp('21:00:30'))
        by_kind = {row['kind']: row for row in result['resources']}
        self.assertEqual(by_kind['roads']['tile'], 't0_2_-1')
        self.assertEqual(by_kind['roads']['responses'], 2)
        self.assertEqual(by_kind['roads']['unique_urls'], 1)
        self.assertEqual(by_kind['roads']['repeated_responses'], 1)
        self.assertEqual(by_kind['roads']['per_minute'], 4)
        self.assertEqual(by_kind['manifest']['statuses'], {'304': 1})

    def test_capture_rows_and_shared_assets_do_not_invent_tile_ownership(self):
        rows = [dict(t=1000, url='http://localhost:5890/coarse/scenery/lod.hash.glb', status=200),
                dict(t=2000, url='http://localhost:5890/coarse/shared/texture.ktx2', status=200),
                dict(t=3000, url='http://localhost:5890/coarse/t0_-1_0/col/c_-4_0.hash.col', status=200),
                dict(t=4000, url='http://localhost:5890/coarse/want?x=5', status=200)]
        result = summarize(rows, 1000, 61000)
        self.assertEqual(len(result['resources']), 3)
        by_kind = {row['kind']: row for row in result['resources']}
        self.assertIsNone(by_kind['texture']['tile'])
        self.assertIsNone(by_kind['scenery_glb']['tile'])
        self.assertEqual(by_kind['collision_payload']['tile'], 't0_-1_0')
        self.assertEqual(by_kind['collision_payload']['per_minute'], 1)
        self.assertEqual(resource('/other/file.glb'), None)

    def test_requires_a_positive_explicit_observation_window(self):
        with self.assertRaises(ValueError):
            summarize([], 1, 1)


if __name__ == '__main__':
    unittest.main()
