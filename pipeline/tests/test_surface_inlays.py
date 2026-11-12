import copy
import unittest
import numpy as np
from shapely.geometry import Polygon,box
from turku3d.scene.surface_inlays import drape

class SurfaceInlayTests(unittest.TestCase):
    def source(self):
        p=[0,1,0, 2,1.2,0, 0,1.4,2]
        mesh=dict(id='ground',entityId='road',materialId='asphalt',positions=p,indices=[0,2,1],collision='solid',supportId='support',normals=[0,1,0]*3,bakedVisibility=[100,120,140,200,220,240,100,120,140],nightIrradiance=[0,0,0,2,4,6,0,0,0])
        return dict(materials=[{'id':'asphalt'},{'id':'paint'}],supports=[dict(id='support',kind='road',meshId='ground')],meshes=[mesh])
    def test_clipped_receiver_keeps_physics_and_interpolates_lighting(self):
        scene=self.source();before=copy.deepcopy(scene)
        rows,receipt=drape(scene,'stripe',box(.2,.2,.6,.6),'paint',reference_height=1.3)
        self.assertEqual(scene,before);self.assertEqual(len(rows),1);row=rows[0]
        self.assertEqual(row['collision'],'passable');self.assertEqual(row['supportId'],'support')
        p=np.array(row['positions']).reshape(-1,3)
        np.testing.assert_allclose(p[:,1],1+.1*p[:,0]+.2*p[:,2]+.002)
        np.testing.assert_allclose(np.array(row['nightIrradiance']).reshape(-1,3),p[:,0,None]*[1,2,3])
        self.assertAlmostEqual(receipt['coveredAreaM2'],.16)
        self.assertTrue((np.array(row['normals']).reshape(-1,3)[:,1]>0).all())
    def test_hole_and_bridge_level_are_respected(self):
        scene=self.source();upper=copy.deepcopy(scene['meshes'][0]);upper['id']='deck';upper['supportId']='deck-support';upper['positions']=[v+5 if i%3==1 else v for i,v in enumerate(upper['positions'])];scene['meshes'].append(upper);scene['supports'].append(dict(id='deck-support',kind='road',meshId='deck'))
        shape=box(.1,.1,.7,.7).difference(box(.3,.3,.5,.5))
        rows,receipt=drape(scene,'mark',shape,'paint',reference_height=1.3)
        self.assertEqual(len(rows),1);self.assertAlmostEqual(receipt['coveredAreaM2'],shape.area)
        p=np.array(rows[0]['positions']).reshape(-1,3)
        for tri in p.reshape(-1,3,3):self.assertTrue(shape.covers(Polygon(tri[:,[0,2]])))

if __name__=='__main__':unittest.main()
