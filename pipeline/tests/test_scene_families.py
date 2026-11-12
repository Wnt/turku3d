"""Furniture contact, winding and transform regressions, no renderer required."""
import math
import unittest
from turku3d.scene.families import _Geometry, HERITAGE_LANTERN, heritage_lantern, promenade_bench, litter_bin, flat_canopy_lamp, crook_lamp


def volume(mesh):
    p=[mesh['positions'][i:i+3] for i in range(0,len(mesh['positions']),3)]
    total=0
    for i in range(0,len(mesh['indices']),3):
        a,b,c=[p[j] for j in mesh['indices'][i:i+3]]
        total+=(a[0]*(b[1]*c[2]-b[2]*c[1])+a[1]*(b[2]*c[0]-b[0]*c[2])+a[2]*(b[0]*c[1]-b[1]*c[0]))/6
    return total


class LanternFamilyTests(unittest.TestCase):
    def test_primitives_face_outwards(self):
        for kind in ('box','taper','beam'):
            g=_Geometry()
            if kind=='box':g.box((0,0,0),(2,3,4))
            if kind=='taper':g.taper(0,2,1,0.5)
            if kind=='beam':g.beam((0,0,0),(1,2,3),0.2)
            self.assertGreater(volume(g.record('test','iron','solid',(0,0,0),0)),0,kind)

    def test_support_contact_and_bounded_geometry(self):
        meshes=heritage_lantern((0,0,0))
        ys=[y for m in meshes for y in m['positions'][1::3]]
        self.assertAlmostEqual(min(ys),0)
        self.assertAlmostEqual(max(ys),HERITAGE_LANTERN['dimensions'][1])
        self.assertEqual([m['id'] for m in meshes if m['collision']=='solid'],['post'])
        self.assertGreater(volume(meshes[0]),0)
        self.assertLess(sum(len(m['indices'])//3 for m in meshes),1000)
        for m in meshes:
            self.assertTrue(all(math.isfinite(x) for x in m['positions']))
            self.assertTrue(all(0<=i<len(m['positions'])//3 for i in m['indices']))
            for j in range(0,len(m['indices']),3):
                points=[m['positions'][i*3:i*3+3] for i in m['indices'][j:j+3]]
                a,b,c=points;u=[b[k]-a[k] for k in range(3)];v=[c[k]-a[k] for k in range(3)]
                area=sum(x*x for x in (u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]))
                self.assertGreater(area,1e-15)

    def test_yaw_and_translation_preserve_fixture(self):
        a=heritage_lantern((0,0,0));b=heritage_lantern((10,20,30),math.pi/2)
        for ma,mb in zip(a,b):
            self.assertEqual(ma['indices'],mb['indices'])
            for i in range(0,len(ma['positions']),3):
                x,y,z=ma['positions'][i:i+3]
                for actual,want in zip(mb['positions'][i:i+3],(10+z,20+y,30-x)):
                    self.assertAlmostEqual(actual,want)

    def test_bench_and_bin_contact_and_extent(self):
        for meshes,height in ((promenade_bench((0,0,0)),0.875),
                              (promenade_bench((0,0,0),backrest=False),0.45),
                              (litter_bin((0,0,0)),0.95)):
            ys=[y for m in meshes for y in m['positions'][1::3]]
            self.assertAlmostEqual(min(ys),0,places=7)
            self.assertAlmostEqual(max(ys),height,places=2)
            self.assertLess(sum(len(m['indices'])//3 for m in meshes),1500)
            for m in meshes:
                self.assertTrue(all(math.isfinite(v) for v in m['positions']))
                if m['collision']=='solid':self.assertGreater(volume(m),0)

    def test_distinct_office_lamp_families(self):
        for family,height in ((flat_canopy_lamp,5.6),(crook_lamp,10.60)):
            meshes=family((0,0,0))
            ys=[y for m in meshes for y in m['positions'][1::3]]
            self.assertAlmostEqual(min(ys),0)
            self.assertLess(abs(max(ys)-height),0.02)
            self.assertLess(sum(len(m['indices'])//3 for m in meshes),1600)
            self.assertEqual(len([m for m in meshes if m['collision']=='solid']),1)
            with self.assertRaises(ValueError):family((0,math.inf,0))

    def test_reject_nonfinite_attachment(self):
        for p,yaw in (((0,math.nan,0),0),((0,0,0),math.inf)):
            with self.assertRaises(ValueError):heritage_lantern(p,yaw)


if __name__=='__main__':unittest.main()
