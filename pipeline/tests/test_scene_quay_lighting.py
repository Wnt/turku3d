"""Observed quay light restoration preserves collision, UVs and unrelated receivers."""
import copy
import tempfile
import unittest

import numpy as np
import shapely
from turku3d.scene.compile import mesh_record,write_products
from turku3d.scene.quay_lighting import append,apply_wash


class PreparedQuayLightingTests(unittest.TestCase):
    def scene(self):
        floor=mesh_record('floor','walk','road',[[0,1.2,0],[0,1.2,1],[2,1.2,1],[2,1.2,0]],[[0,1,2],[0,2,3]],support='walk')
        floor['nightIrradiance']=[.02]*len(floor['positions'])
        p=np.array([[0,-.5,1],[2,-.5,1],[2,1.2,1],[0,1.2,1]])
        wall=mesh_record('shore','shore','quay-wall',p,[[0,1,2],[0,2,3]],uv=p[:,[0,1]]*.5+[7,3])
        scene=dict(schema='turku-scene/1',release='fixture',id='fixture',origin=[0,0,0],bounds=[0,0,2,2],
            sources=[dict(id='quay-lighting.json',sha256='a'*64,kind='quay-lighting-corridors',uncertaintyM=.3)],
            materials=[dict(id=k,color=[.4,.4,.4],roughness=.9,metalness=0) for k in ['road','quay-wall']],
            boundaries=[],supports=[dict(id='walk',kind='quay',boundaryIds=[],meshId='floor')],meshes=[floor,wall],
            assets=[],fixtures=[],emitters=[],lightOccluders=[],collision={},
            coverage=dict(mode='replace',bounds=[0,0,2,2],entityIds=['walk','shore']),
            spawn=dict(position=[.5,1.35,.5],headingDeg=0,vehicle=dict(position=[1,1.35,.5],yaw=0)))
        return scene,wall

    def test_observed_wall_wash_retains_collision_uvs_and_road_lighting(self):
        scene,wall=self.scene();before=copy.deepcopy(scene)
        walks=[dict(line=shapely.LineString([[0,.5],[2,.5]]),width=1.)]
        washes=append(scene,[wall],walks,[shapely.box(-1,-1,3,3)])
        self.assertEqual(len(washes),1);self.assertFalse(wall['render'])
        self.assertEqual({k:v for k,v in wall.items() if k!='render'},before['meshes'][1])
        visible=next(m for m in scene['meshes'] if m['id'] in washes)
        p=np.array(visible['positions']).reshape(-1,3)
        np.testing.assert_allclose(np.array(visible['uv']).reshape(-1,2),p[:,[0,1]]*.5+[7,3],atol=1e-12)
        self.assertTrue(all(m['collision']=='passable' for m in scene['meshes'][2:]))
        apply_wash(scene,washes)
        np.testing.assert_array_equal(scene['meshes'][0]['nightIrradiance'],before['meshes'][0]['nightIrradiance'])
        light=np.array(visible['nightIrradiance']).reshape(-1,3)
        np.testing.assert_allclose(light[p[:,1]<=.3],0,atol=1e-12)
        np.testing.assert_allclose(light.max(axis=0),[4,2.96,1.76],atol=1e-12)
        scene['coverage']['entityIds']=sorted({m['entityId'] for m in scene['meshes']})
        with tempfile.TemporaryDirectory() as a,tempfile.TemporaryDirectory() as b:
            old=write_products(before,a);new=write_products(scene,b)
            self.assertEqual(old['collision'],new['collision'])
        self.assertEqual(scene['materials'][-1]['nightEmission'],[8,5.92,3.52])
        self.assertEqual(len(scene['emitters']),0)

    def test_missing_corridor_or_high_crest_does_not_create_lighting(self):
        for corridors,raise_by in [([],0),([shapely.box(-1,-1,3,3)],4)]:
            scene,wall=self.scene()
            wall['positions']=[v+raise_by if i%3==1 else v for i,v in enumerate(wall['positions'])]
            before=copy.deepcopy(scene)
            washes=append(scene,[wall],[dict(line=shapely.LineString([[0,.5],[2,.5]]),width=1.)],corridors)
            self.assertEqual(washes,{})
            self.assertEqual(scene,before)
