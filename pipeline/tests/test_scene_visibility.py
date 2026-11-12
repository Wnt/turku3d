"""Prepared visibility responds to real occluders without moving support."""
import copy
from types import SimpleNamespace
import unittest

import numpy as np
import shapely

from turku3d.scene.visibility import bake_scene


class PreparedVisibilityTest(unittest.TestCase):
    def test_roof_occludes_indirect_sky_but_open_ground_stays_open(self):
        ground=dict(id='ground',materialId='road',positions=[-50,0,-50,50,0,-50,50,0,50,-50,0,50],
                    indices=[0,2,1,0,3,2],normals=[0,1,0]*4)
        near=dict(id='near',materialId='road',positions=[2,0,4,2.1,0,4,2,0,4.1],indices=[0,2,1],normals=[0,1,0]*3)
        water=copy.deepcopy(near);water.update(id='water',materialId='water')
        scene=dict(id='test',bounds=[-50,-50,50,50],meshes=[ground,near,water],
                   supports=[dict(meshId='ground')],materials=[],sources=[])
        building=SimpleNamespace(footprint=shapely.box(3,-10,12,10),base=0,top=12,
                                 roof_parts=[(shapely.box(3,-10,12,10),[0,0,12])])
        before=copy.deepcopy([m['positions'] for m in scene['meshes']])
        stats=bake_scene(scene,np.array(ground['positions']).reshape(-1,3),[building])
        open_values=np.array(ground['bakedVisibility']).reshape(-1,3)
        near_values=np.array(near['bakedVisibility']).reshape(-1,3)
        self.assertGreater(open_values[:,1].mean(),250)
        self.assertLess(near_values[:,1].mean(),open_values[:,1].mean()-20)
        self.assertTrue(np.all(near_values[:,2]==255))
        self.assertNotIn('bakedVisibility',water)
        self.assertEqual(before,[m['positions'] for m in scene['meshes']])
        self.assertEqual(stats['vertices'],7)


if __name__=='__main__':unittest.main()
