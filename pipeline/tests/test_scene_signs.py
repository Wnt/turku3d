"""Primary sign plates retain source faces, grounded poles and collision identity."""
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
import numpy as np

from turku3d.col import read_col
from turku3d.col.materials import mat_id
from turku3d.scene.compile import mesh_record, write_products
from turku3d.scene.signs import place_signs, qualified_signs


def prop(identity,code,x,z,orientation='right_hand_traffic'):
    return SimpleNamespace(object_id=identity,source='GIS_Liikennemerkit',code=code,
                           x=x,z=z,yaw=.6,orientation=orientation)


class SignTests(unittest.TestCase):
    def scene(self):
        ground=mesh_record('ground','ground','footway',[[0,1,0],[0,1,10],[10,1,10],[10,1,0]],
                           [[0,1,2],[0,2,3]],support='support')
        return dict(schema='turku-scene/1',release='pending',id='test',origin=[0,0,0],bounds=[0,0,10,10],
            sources=[dict(id='poles',kind='poles',sha256='a'*64,uncertaintyM=.3)],
            materials=[dict(id='footway',color=[.2,.2,.2],roughness=.9,metalness=0)],boundaries=[],
            supports=[dict(id='support',kind='footway',meshId='ground',boundaryIds=[])],meshes=[ground],
            fixtures=[],emitters=[],assets=[],lightOccluders=[],collision={},coverage=dict(mode='replace',bounds=[0,0,10,10],entityIds=[]),
            spawn=dict(position=[1,1.15,1],headingDeg=0,vehicle=dict(position=[2,1.15,2],yaw=0)))

    def test_unresolved_mounts_and_default_yaw_are_not_invented(self):
        rows=[prop('isolated','E1',1,1),prop('pair-a','E1',3,3),prop('pair-b','B5',3.2,3.1),
              prop('default','D3.1',6,6,'on_centerline_default'),prop('supplement','H20',8,8)]
        self.assertEqual([p.object_id for p in qualified_signs(rows)],['isolated'])

    def test_supported_primary_plates_compile_to_real_furniture_collision(self):
        scene=self.scene();rows=[prop('crossing','E1',2,2),prop('yield','B5',5,2),prop('restriction','C38',8,2)]
        place_signs(scene,rows)
        self.assertEqual(len(scene['fixtures']),3)
        self.assertEqual({f['family'] for f in scene['fixtures']},{'fi-sign-E1-v1','fi-sign-B5-v1','fi-sign-C38-v1'})
        for fixture in scene['fixtures']:
            self.assertEqual(fixture['position'][1],1.)
            mesh=next(m for m in scene['meshes'] if m['id']==fixture['meshIds'][0])
            self.assertAlmostEqual(np.asarray(mesh['positions']).reshape(-1,3)[:,1].min(),1.)
        with tempfile.TemporaryDirectory() as output:
            write_products(scene,Path(output))
            record=scene['collision']['c_0_0'];col=read_col((Path(output)/record['url']).read_bytes())
            self.assertIn(mat_id('street_furniture'),col.materials)

    def test_new_plate_without_complete_pole_contact_is_held(self):
        scene=self.scene();place_signs(scene,[prop('edge','E1',9.99,2)])
        self.assertEqual(scene['fixtures'],[])


if __name__=='__main__':unittest.main()
