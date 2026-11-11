"""Contract failures and shared-boundary contact in the new semantic compiler."""
import copy
import unittest
import numpy as np
import shapely
from turku3d.scene.compile import ground_scene, mesh_record
from turku3d.scene.schema import validate_scene


class SemanticSceneTests(unittest.TestCase):
    def scene(self):
        return dict(schema="turku-scene/1", release="fixture", id="fixture", origin=[0,0,0], bounds=[0,0,10,10], sources=[],
                    materials=[dict(id=k,color=[.2,.2,.2],roughness=.8,metalness=0) for k in ("ground","road","footway")],
                    boundaries=[],supports=[],meshes=[],fixtures=[],emitters=[],collision={},
                    coverage=dict(mode="replace",bounds=[0,0,10,10],entityIds=[]),
                    spawn=dict(position=[2,1,2],headingDeg=0,vehicle=dict(position=[3,1,3],yaw=0)))

    def compiled(self):
        s=self.scene()
        p=np.array([[x,.01*x+.02*z,z] for x in range(-1,12) for z in range(-1,12)])
        ground_scene(s,p,[],[(shapely.box(0,0,5,10),1),(shapely.box(5,0,10,10),2)],[])
        return s

    def test_adjacent_supports_share_identical_edges_and_height(self):
        s=self.compiled(); validate_scene(s)
        a,b=s['supports']
        common=set(a['boundaryIds'])&set(b['boundaryIds'])
        self.assertEqual(len(common),10)
        edges={x['id']:x['positions'] for x in s['boundaries']}
        for key in common:
            for x,y,z in edges[key]:
                self.assertEqual(x,5)
                self.assertAlmostEqual(y,.01*x+.02*z,10)
        for m in s['meshes']:
            p=np.array(m['positions']).reshape(-1,3)
            np.testing.assert_allclose(p[:,1],.01*p[:,0]+.02*p[:,2],atol=1e-10)

    def test_reject_unknown_fields_nonfinite_and_dangling_support(self):
        for mutate in (lambda s:s.update(legacy=True),
                       lambda s:s['meshes'][0]['positions'].__setitem__(0,float('nan')),
                       lambda s:s['meshes'][0].update(supportId='missing'),
                       lambda s:s['meshes'][0]['indices'].__setitem__(0,999999)):
            s=self.compiled();mutate(s)
            with self.assertRaises(ValueError):validate_scene(s)

    def test_hole_is_not_triangulated(self):
        s=self.scene();p=np.array([[x,0,z] for x in range(-1,12) for z in range(-1,12)])
        building=type('Building',(),{'footprint':shapely.box(3,3,7,7)})()
        ground_scene(s,p,[building],[],[])
        m=s['meshes'][0];p=np.array(m['positions']).reshape(-1,3);ix=np.array(m['indices']).reshape(-1,3)
        tris=p[ix];area=np.abs(np.cross(tris[:,1]-tris[:,0],tris[:,2]-tris[:,0])[:,1]).sum()/2
        self.assertAlmostEqual(area,84)


if __name__=='__main__':unittest.main()
