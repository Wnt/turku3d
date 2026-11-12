"""Ornaments must not cover the retained window or lose their source attachment."""
import json
import math
from pathlib import Path
import unittest
from turku3d.scene.facade_details import _extrude, timber_window_crown, office_window_details
from turku3d.scene.families import _Geometry
from test_scene_families import volume


class FacadeDetailTests(unittest.TestCase):
    def test_crowns_leave_glazing_open_and_project_outward(self):
        for pediment in (False,True):
            mesh=timber_window_crown((0,0,0),opening_width=1.3,pediment=pediment)[0]
            points=[mesh['positions'][i:i+3] for i in range(0,len(mesh['positions']),3)]
            self.assertGreater(volume(mesh),0)
            self.assertEqual(mesh['collision'],'passable')
            for x,y,z in points:
                self.assertGreaterEqual(z,0)
                self.assertFalse(abs(x)<.65 and y<0,'ornament intrudes into existing glazing')
            self.assertLessEqual(max(p[2] for p in points),.24)
            self.assertLess(max(p[1] for p in points),.7)

    def test_extrusion_winding_survives_reverse_outline(self):
        for shape in ([(-1,0),(1,0),(0,1)],[(0,1),(1,0),(-1,0)]):
            g=_Geometry();_extrude(g,shape,0,.2)
            self.assertAlmostEqual(volume(g.record('a','a','passable',(0,0,0),0)),.2)

    def test_office_is_ten_known_heads_and_unchanged_grey_wing(self):
        meshes=office_window_details()
        self.assertEqual(len(meshes),10)
        self.assertEqual(len({m['id'] for m in meshes}),10)
        self.assertFalse(any('window-42-' in m['id'] for m in meshes))
        self.assertEqual(sum('window-36-' in m['id'] for m in meshes),4)
        self.assertLess(sum(len(m['indices'])//3 for m in meshes),3000)
        for mesh in meshes:
            self.assertTrue(all(math.isfinite(x) for x in mesh['positions']))
            self.assertTrue(all(0<=i<len(mesh['positions'])//3 for i in mesh['indices']))

    def test_reject_unbounded_dimensions(self):
        for width in (0,5,float('nan')):
            with self.assertRaises(ValueError):timber_window_crown((0,0,0),opening_width=width)
