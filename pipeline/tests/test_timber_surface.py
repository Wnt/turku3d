import math
import unittest
import numpy as np
from turku3d.scene.families import promenade_bench
from turku3d.scene.timber_surface import timber_maps,bench_uvs,SIZE


class TimberSurfaceTest(unittest.TestCase):
    def test_gpu_extent_original_brown_and_nonmetal_roughness(self):
        maps=timber_maps()
        self.assertEqual({im.size for im in maps.values()},{SIZE})
        self.assertTrue(all(n%4==0 for n in SIZE))
        rgb=np.asarray(maps['albedo'],float)
        self.assertTrue(np.all(np.abs(rgb.mean(axis=(0,1))-[117.65,84.62,52.52])<2))
        orm=np.asarray(maps['orm'])
        self.assertEqual(orm[:,:,0].min(),255);self.assertEqual(orm[:,:,2].max(),0)
        self.assertGreater(orm[:,:,1].min(),160);self.assertLess(orm[:,:,1].max(),235)
        # Fibres vary across the grain more than along it; no broad image noise.
        self.assertGreater(np.abs(np.diff(rgb,axis=0)).mean(),np.abs(np.diff(rgb,axis=1)).mean()*3)

    def test_metre_uvs_follow_fixture_rotation_without_geometry_change(self):
        def wood(position,yaw):return next(m for m in promenade_bench(position,yaw) if m['materialId']=='wood')
        origin=wood((0,0,0),0);moved=wood((178,3.6,260),.45)
        before=list(moved['positions']);a=bench_uvs(origin,(0,0,0),0);b=bench_uvs(moved,(178,3.6,260),.45)
        self.assertTrue(np.allclose(a,b,atol=1e-10));self.assertEqual(moved['positions'],before)
        self.assertTrue(all(math.isfinite(v) for v in b));self.assertEqual(len(b),len(moved['positions'])//3*2)
        self.assertAlmostEqual(max(a[::2])-min(a[::2]),.9)


if __name__=='__main__':unittest.main()
