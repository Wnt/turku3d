import unittest
import numpy as np
from turku3d.scene.ground_survey import ground_returns, representative_returns


class GroundSurveyTests(unittest.TestCase):
    def test_overlapping_canopy_is_not_ground(self):
        points = np.array([[0, 3.7, 0], [.2, 3.71, .1], [.1, 8, .1], [8, 3.7, 8]])
        kept = ground_returns(points, np.array([2, 12, 12, 12]))
        np.testing.assert_array_equal(kept, points[:2])

    def test_step_retains_two_real_levels_without_intermediate_height(self):
        low = np.array([[x, 1.2, z] for x in (.05, .15, .25) for z in (.2, .5, .8)])
        high = np.array([[x, 3.7, z] for x in (.65, .75, .85) for z in (.2, .5, .8)])
        samples = representative_returns(np.r_[low, high])
        self.assertEqual(len(samples), 2)
        np.testing.assert_array_equal(np.sort(samples[:, 1]), [1.2, 3.7])
        self.assertTrue(all(any(np.array_equal(row, original) for original in np.r_[low, high]) for row in samples))

    def test_plane_is_preserved_and_order_does_not_change_samples(self):
        points = np.array([[x, 3.7 + .02*x - .01*z, z] for x in np.arange(-2, 2, .2) for z in np.arange(-2, 2, .2)])
        a = representative_returns(points)
        b = representative_returns(points[::-1])
        np.testing.assert_array_equal(a, b)
        np.testing.assert_allclose(a[:, 1], 3.7 + .02*a[:, 0] - .01*a[:, 2], atol=1e-12)
        self.assertLess(len(a), len(points) / 8)


if __name__ == '__main__':
    unittest.main()
