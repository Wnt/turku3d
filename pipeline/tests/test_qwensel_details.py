"""Reviewed Qwensel frontage topology, anchors and generated map checks."""
import math
import unittest
from turku3d.scene.qwensel_details import QWENSEL_FRONTAGE as D, frontage_anchor, frontage_length, qwensel_window_details, timber_shutter_pair, qwensel_entrance_details, opposed_timber_stair
from turku3d.scene.qwensel_maps import frontage_maps


class QwenselDetailsTests(unittest.TestCase):
    def test_reference_topology_and_pane_patterns(self):
        windows=D['windows']
        self.assertEqual(len(windows),6)
        self.assertEqual([w['id'] for w in windows if w['shutters']],['window-1','window-2','window-3','window-4'])
        self.assertEqual([w['columns']*w['rows'] for w in windows],[16,16,16,16,6,6])
        self.assertLess(windows[4]['along'],D['door']['along'])
        self.assertLess(D['door']['along'],windows[5]['along'])
        gaps=[b['along']-a['along'] for a,b in zip(windows,windows[1:])]
        self.assertGreater(max(gaps)-min(gaps),1)

    def test_anchors_remain_on_piecewise_authored_wall(self):
        path=D['frontLocalPath'];base=D['buildingPosition']
        for along,expected in [(0,path[0]),(math.dist(*path[:2]),path[1]),(frontage_length(),path[-1])]:
            point,yaw=frontage_anchor(along,7)
            self.assertAlmostEqual(point[0],expected[0]+base[0])
            self.assertAlmostEqual(point[2],expected[1]+base[2])
            self.assertEqual(point[1],7)
            self.assertTrue(.49<yaw<.52)
        with self.assertRaises(ValueError):frontage_anchor(frontage_length()+.1,7)

    def test_detail_meshes_are_bounded_passable_and_finite(self):
        meshes=qwensel_window_details()
        self.assertEqual(len(meshes),18)
        self.assertEqual(len(set(m['id'] for m in meshes)),18)
        self.assertLess(sum(len(m['indices'])//3 for m in meshes),5000)
        for m in meshes:
            self.assertEqual(m['collision'],'passable')
            self.assertTrue(all(math.isfinite(x) for x in m['positions']))
            self.assertTrue(all(0<=i<len(m['positions'])//3 for i in m['indices']))
            self.assertGreater(min(m['positions'][1::3]),5.9)
            self.assertLess(max(m['positions'][1::3]),8)
            for i in range(0,len(m['indices']),3):
                a,b,c=[m['positions'][3*j:3*j+3] for j in m['indices'][i:i+3]]
                u=[b[k]-a[k] for k in range(3)];v=[c[k]-a[k] for k in range(3)]
                self.assertGreater(sum(x*x for x in [u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]]),1e-15)

    def test_shutter_clearance_keeps_glass_uncovered(self):
        meshes=timber_shutter_pair((0,0,0),width=1.1,height=1.4)
        for m in meshes:
            self.assertTrue(all(abs(x)>.54 for x in m['positions'][::3]))
        with self.assertRaises(ValueError):timber_shutter_pair((0,0,0),width=math.nan,height=1.4)

    def test_entrance_has_measured_threshold_and_bounded_steps(self):
        entry=D['entrance'];meshes=qwensel_entrance_details()
        self.assertEqual(len(meshes),4)
        self.assertEqual(len([m for m in meshes if m['collision']=='solid']),3)
        tread=meshes[0];ys=tread['positions'][1::3]
        self.assertAlmostEqual(max(ys),entry['landingHeight'])
        for ground in entry['footGroundHeights']:
            self.assertLessEqual((entry['landingHeight']-ground)/entry['stepsPerFlight'],.22)
        self.assertGreaterEqual(entry['flightRun']/(entry['stepsPerFlight']-1),.20)
        self.assertLess(sum(len(m['indices'])//3 for m in meshes),1200)
        self.assertAlmostEqual(D['baseHeight']+D['door']['sill'],entry['landingHeight'],places=6)
        for m in meshes:
            self.assertTrue(all(math.isfinite(x) for x in m['positions']))
            self.assertGreaterEqual(min(m['positions'][1::3]),3.79)
        with self.assertRaises(ValueError):
            opposed_timber_stair((0,5.2,0),landing_width=1.2,flight_run=1.6,depth=.9,back_offset=.15,steps=7,foot_ground_heights=[0,0],post_ground_heights=[0,0])

    def test_generated_maps_are_finite_and_same_size(self):
        images=frontage_maps()
        self.assertEqual(len(set(i.size for i in images)),1)
        self.assertGreater(images[0].width,850)
        self.assertGreater(images[0].height,200)
        self.assertEqual(images[0].mode,'RGB')


if __name__=='__main__':unittest.main()
