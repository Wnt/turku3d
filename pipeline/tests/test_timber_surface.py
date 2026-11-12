import math
import unittest
from types import SimpleNamespace
import numpy as np
from turku3d.scene.families import promenade_bench,litter_bin
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

    def test_production_furniture_adapter_changes_only_wood_uvs(self):
        from turku3d.scene.compile import mesh_record
        from turku3d.scene.fixtures import place_furniture
        ground=mesh_record('ground','ground','footway',[[170,3.6,250],[190,3.6,250],
                           [190,3.6,270],[170,3.6,270]],[[0,2,1],[0,3,2]],support='walk')
        scene={'meshes':[ground],'supports':[{'id':'walk','meshId':'ground'}],
               'sources':[{'id':'mapped-poles','kind':'poles'}],'fixtures':[]}
        props=[SimpleNamespace(kind='bench',object_id='mapped:bench',x=178.,z=260.,yaw=.1),
               SimpleNamespace(kind='bin',object_id='mapped:bin',x=182.,z=261.,yaw=.7)]
        place_furniture(scene,props)
        rows={m['id']:m for m in scene['meshes']};changed=0
        for fixture,make in zip(scene['fixtures'],(promenade_bench,litter_bin)):
            for piece in make(fixture['position'],fixture['yaw']):
                key=fixture['id']+':'+piece['id'];actual=rows[key]
                prior=mesh_record(key,fixture['id'],piece['materialId'],
                    np.asarray(piece['positions']).reshape(-1,3),np.asarray(piece['indices']).reshape(-1,3),
                    piece['collision'],'walk')
                for field in prior:
                    if field!='uv':self.assertEqual(actual[field],prior[field],(key,field))
                if piece['materialId']=='wood':
                    self.assertNotEqual(actual['uv'],prior['uv']);changed+=1
                    self.assertEqual(len(actual['uv']),len(actual['positions'])//3*2)
                else:self.assertEqual(actual['uv'],prior['uv'])
        self.assertGreater(changed,0)


if __name__=='__main__':unittest.main()
