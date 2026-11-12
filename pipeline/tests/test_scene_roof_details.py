"""Placement and bounded shape checks for the additive office roof families."""
import math
import unittest
from turku3d.scene.roof_details import timber_eave, brick_chimney, timber_dormer, office_roof_details
from test_scene_families import volume


class RoofDetailTests(unittest.TestCase):
    def test_eave_winding_and_window_clearance(self):
        mesh=timber_eave((0,10.67,0),0,width=7.2)[0]
        self.assertGreater(volume(mesh),0)
        points=list(zip(*[iter(mesh['positions'])]*3))
        self.assertGreater(min(p[1] for p in points),9.7)
        self.assertGreaterEqual(min(p[2] for p in points),0)
        self.assertLessEqual(max(p[2] for p in points),.46)

    def test_chimney_matches_explicit_top_and_open_rain_hood(self):
        meshes=brick_chimney((0,13.2,0),0,height=2.5)
        self.assertGreater(volume(meshes[0]),0)
        self.assertAlmostEqual(max(v for m in meshes for v in m['positions'][1::3]),15.7)
        self.assertEqual({m['collision'] for m in meshes},{'passable'})
        cap=meshes[1];self.assertGreater(volume(cap),0)

    def test_dormer_glass_faces_outward_and_sits_in_front_of_backing(self):
        meshes=timber_dormer((0,0,0),0,apex_height=1.6)
        glass=meshes[-1];p=glass['positions'];a,b,c=[p[i*3:i*3+3] for i in glass['indices'][:3]]
        normal_z=(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
        self.assertGreater(normal_z,0)
        self.assertTrue(all(abs(z-.018)<1e-9 for z in p[2::3]))
        self.assertLess(max(p[1::3]),1.6)

    def test_office_roof_is_finite_passable_and_bounded(self):
        meshes=office_roof_details()
        self.assertEqual(len({m['id'] for m in meshes}),len(meshes))
        self.assertEqual(sum('cornice-corbels' in m['id'] for m in meshes),3)
        self.assertEqual(sum('flashing-cap' in m['id'] for m in meshes),6)
        self.assertEqual(sum('dormer-' in m['id'] and 'glazing' in m['id'] for m in meshes),2)
        self.assertLess(sum(len(m['indices'])//3 for m in meshes),10000)
        for m in meshes:
            self.assertEqual(m['collision'],'passable')
            self.assertTrue(all(math.isfinite(v) for v in m['positions']))
            self.assertTrue(all(0<=i<len(m['positions'])//3 for i in m['indices']))
            self.assertGreater(min(m['positions'][1::3]),9.5)
            self.assertLess(max(m['positions'][1::3]),16)

    def test_dimensions_are_bounded(self):
        for height in (0,10,float('nan')):
            with self.assertRaises(ValueError):brick_chimney((0,0,0),0,height=height)
        with self.assertRaises(ValueError):timber_eave((0,0,0),0,width=100)
