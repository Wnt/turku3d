"""Separate source levels survive prepared support and collision compilation."""
import tempfile
import unittest
from types import SimpleNamespace
import numpy as np
import shapely
from turku3d.coarse.bridges import slab
from turku3d.scene.structures import append,grade_approach
from turku3d.scene.fixtures import support_query
from turku3d.scene.retaining import RetainingField


class SceneStructureTests(unittest.TestCase):
    def test_crowned_deck_and_lower_walk_remain_independent_supports(self):
        line=shapely.LineString([(0,0),(10,0)]);outline=line.buffer(2,cap_style='flat')
        def height(xz):return np.interp(xz[:,0],[0,5,10],[5.9,6.02,5.9])
        meshes=slab('measured-deck',outline,line,[0,5,10],[5.9,6.02,5.9],'road',height,.85)
        lower=slab('lower-walk',outline,line,[0,10],[1.2,1.2],'sidewalk',lambda xz:np.full(len(xz),1.2),.1)
        scene=dict(materials=[],meshes=[],supports=[],boundaries=[])
        append(scene,[SimpleNamespace(way={'id':'source'},meshes=meshes+lower)])
        self.assertEqual(len(scene['supports']),2)
        query=support_query(scene)
        self.assertAlmostEqual(query(5,0,reference_y=6)[1],6.02)
        self.assertAlmostEqual(query(5,0,reference_y=1.3)[1],1.2)
        supported={s['meshId'] for s in scene['supports']}
        self.assertNotIn('measured-deck:body',supported)
        self.assertTrue(all(s['boundaryIds'] for s in scene['supports']))

    def test_mapped_approach_closure_requires_exact_declared_boundary(self):
        field=RetainingField([],lambda x:np.zeros(len(x)),shapely.box(0,0,10,10))
        boundaries=[dict(id='lower',positions=[[0,1,0],[1,1,0]]),dict(id='upper',positions=[[0,5,0],[1,5,0]])]
        result=field.wall_meshes(boundaries,[('bridge:source',shapely.LineString([(0,0),(1,0)]))])
        self.assertEqual(len(result),1);self.assertEqual(result[0]['boundaryIds'],['lower','upper'])
        self.assertEqual(result[0]['entityId'],'bridge:source')
        with self.assertRaises(ValueError):field.wall_meshes(boundaries,[('wrong',shapely.LineString([(0,1),(1,1)]))])

    def test_vault_explicit_top_support_reuses_solid_enclosure_faces(self):
        from turku3d.coarse.underpasses import enclosure
        mesh=enclosure('mapped',np.array([[0.,0.],[10.,0.]]),np.array([[0.,1.],[0.,1.]]),np.array([1.2,1.2]),2.4,2.7,lambda x,z:5.9)
        top=mesh.metadata['support_top'];vertices=np.asarray(top['positions'])
        self.assertTrue(np.allclose(vertices[:,1],5.9))
        for point in vertices:self.assertLess(np.min(np.linalg.norm(mesh.positions-point,axis=1)),1e-12)
        self.assertGreater(len(top['indices']),0)

    def test_lower_corridor_stays_excluded_from_approach_grading(self):
        line=shapely.LineString([(0,0),(10,0)]);lower=shapely.box(3,-2,5,2)
        patch=dict(line=line,knots=np.array([0,10]),heights=np.array([6,3]),width=4,lateral=2,feather=2,blocked=lower,area=line.buffer(4).difference(lower))
        points=np.array([[2,0],[4,0],[8,0]])
        result=grade_approach(points,np.ones(3),[patch])
        self.assertAlmostEqual(result[1],1);self.assertGreater(result[0],5);self.assertGreater(result[2],3)
