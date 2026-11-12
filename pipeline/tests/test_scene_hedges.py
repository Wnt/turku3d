"""Ground support, fixed survey crowns and deliberate path gaps survive generation."""
import unittest
import numpy as np
import shapely
from turku3d.scene.hedges import segments, segment_geometry


class HedgeTests(unittest.TestCase):
    def test_four_contacts_follow_slope_without_raising_measured_top(self):
        def support(x, z, reference_y=None):
            return dict(id='ground'), 3.7 + .02*x + .03*z
        p, ix, uv, sid = segment_geometry(np.array([0., 0.]), np.array([2., 0.]), .65, 4.38, 1.4, support)
        feet = p[p[:, 1] < 4.38]
        np.testing.assert_allclose(feet[:, 1], 3.7 + .02*feet[:, 0] + .03*feet[:, 2])
        self.assertEqual(float(p[:, 1].max()), 4.38)
        self.assertEqual(sid, 'ground')
        self.assertEqual(ix.shape, (12, 3))
        self.assertEqual(uv.shape, (24, 2))
        normals = np.cross(p[ix[:, 1]]-p[ix[:, 0]], p[ix[:, 2]]-p[ix[:, 0]])
        centre = p.mean(axis=0)
        self.assertTrue(np.all(np.einsum('ij,ij->i', normals, p[ix].mean(axis=1)-centre) > 0))

    def test_crossing_gap_is_not_bridged_and_subdivision_is_bounded(self):
        row = dict(line=shapely.to_geojson(shapely.LineString([(0, 0), (10, 0)])),
                   openings=shapely.to_geojson(shapely.box(4, -1, 6, 1)))
        pieces = list(segments(row, [1, -2, 9, 2]))
        self.assertEqual(len(pieces), 6)
        self.assertTrue(all(np.linalg.norm(b-a) <= 1. for a, b in pieces))
        self.assertTrue(all(not 4 < ((a+b)/2)[0] < 6 for a, b in pieces))

    def test_missing_support_or_buried_crown_does_not_invent_geometry(self):
        a, b = np.array([0., 0.]), np.array([1., 0.])
        self.assertIsNone(segment_geometry(a, b, .65, 4.38, 1.4, lambda *args, **kw: None))
        self.assertIsNone(segment_geometry(a, b, .65, 4.38, 1.4, lambda *args, **kw: (dict(id='ground'), 4.3)))

    def test_sloped_measured_crown_can_retain_height_above_support(self):
        p, _, _, _ = segment_geometry(np.array([0., 0.]), np.array([1., 0.]), .65, None, .8,
                                      lambda x, z, **kw: (dict(id='ground'), 3+x*.3))
        self.assertAlmostEqual(float(p[:, 1].max()), 4.1)


if __name__ == '__main__':
    unittest.main()
