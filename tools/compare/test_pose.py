"""Synthetic camera tests; no browser, network, tiles or real imagery."""

import math
import unittest

from tools.compare.pose import GooglePose, HeightReference, Pose, from_google, to_google


class PoseTests(unittest.TestCase):
    def test_roundtrip_all_height_references(self):
        for heights in (
            HeightReference("ellipsoid", 19.2),
            HeightReference("ground", 8.5),
            HeightReference("n2000", 0),
        ):
            for yaw in (0, 90, 180, 359.9):
                p = Pose(23459668, 6704254, 84, yaw, -20.06, 35)
                q = from_google(GooglePose.parse(to_google(p, heights).url()), heights)
                for key in ("E", "N", "H", "pitch", "fov"):
                    self.assertAlmostEqual(getattr(p, key), getattr(q, key), delta=0.001)
                self.assertAlmostEqual((p.yaw - q.yaw + 180) % 360 - 180, 0, places=5)

    def test_heading_convergence_and_vertical_datum_sign(self):
        p = Pose(23459500, 6704500, 80, 90, -30, 45)
        g = to_google(p, HeightReference("ellipsoid", 19))
        self.assertAlmostEqual(g.heading, 89.36, delta=0.01)
        self.assertEqual(g.altitude, 99)
        self.assertEqual(g.tilt, 60)
        self.assertEqual(g.fov, 45)
        self.assertEqual(to_google(p, HeightReference("ground", 12)).altitude, 68)

    def test_down_and_horizon(self):
        for pitch, tilt in ((-90, 0), (0, 90)):
            g = to_google(Pose(23459500, 6704500, 100, 0, pitch, 35), HeightReference("n2000", 0))
            self.assertEqual(g.tilt, tilt)

    def test_example_is_camera_location_not_bridge_location(self):
        url = "https://www.google.com/maps/@60.4562084,22.2901796,84a,35y,139.27h,69.94t/data=!3m1!1e3"
        p = from_google(GooglePose.parse(url), HeightReference("n2000", 0))
        self.assertAlmostEqual(p.E, 23460939.122, places=3)
        self.assertAlmostEqual(p.N, 6705112.305, places=3)
        self.assertAlmostEqual(p.pitch, -20.06)

    def test_rejects_bad_or_non_aerial_urls(self):
        for url in (
            "https://evil.test/maps/@60,22,84a,35y,139h,69t/data=!3m1!1e3",
            "https://www.google.com/maps/@60,22,100m/data=!3m1!1e3",
            "https://www.google.com/maps/@60,22,3a,35y,139h,69t/data=!3m1!1e1",
            "https://www.google.com/maps/@100,22,84a,35y,139h,69t/data=!3m1!1e3",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                GooglePose.parse(url)

    def test_invalid_camera_and_height(self):
        for cam in ("1,2,3,4,5", "1,2,nan,4,-30,35", "1,2,3,4,30,35", "1,2,3,4,-30,180"):
            with self.subTest(cam=cam), self.assertRaises(ValueError):
                Pose.parse(cam)
        for datum, offset in (("msl", 0), ("n2000", 1), ("ground", math.nan)):
            with self.subTest(datum=datum), self.assertRaises(ValueError):
                HeightReference(datum, offset)


if __name__ == "__main__":
    unittest.main()
