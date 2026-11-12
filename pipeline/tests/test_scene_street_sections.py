"""Measured street rails keep source finish, shared support and collider geometry."""
import pickle
import tempfile
import unittest
from pathlib import Path
import numpy as np
import shapely
from turku3d.scene import street_sections as sections
from turku3d.scene.compile import ground_scene
from turku3d.scene.fixtures import support_query


class StreetSectionTests(unittest.TestCase):
    def section(self):
        return dict(bounds=[0,0,8,6],profiles=[dict(key='measured',line=shapely.LineString([(1,3),(7,3)]),
            knots=np.array([0.,2.,4.,6.]),heights=np.tile([2.,2.12],(4,1)))],finishes=[
            dict(geometry=shapely.box(0,0,8,3),kind='Ajorata',finish='Asfalttibetoni'),
            dict(geometry=shapely.box(0,3,8,6),kind='Jalkakäytävä',finish='Betonilaatta')])

    def test_registered_finish_is_distinct_from_walkable_semantics(self):
        rows=sections.regions(self.section(),shapely.box(0,0,8,6))
        self.assertEqual([(k,m) for _,k,m in rows],[('road','road'),('footway','concrete-pavers'),('footway','curb')])
        self.assertIsNone(sections.finish('Unknown'))
        self.assertEqual(sections.finish('Betonikivi'),'brick-paving')

    def test_curb_grade_and_shared_boundaries_come_from_same_measured_rails(self):
        scene=dict(bounds=[0,0,8,6],meshes=[],supports=[],boundaries=[])
        x,z=np.meshgrid(np.arange(9.),np.arange(7.));points=np.c_[x.ravel(),np.full(x.size,2.06),z.ravel()]
        ground_scene(scene,points,[],[],[],street_sections=self.section())
        query=support_query(scene)
        self.assertAlmostEqual(query(4,2.985)[1],2.,places=5)
        self.assertAlmostEqual(query(4,3.025)[1],2.12,places=5)
        self.assertAlmostEqual(query(4,3.18)[1],2.12,places=5)
        self.assertEqual({m['materialId'] for m in scene['meshes']},{'road','concrete-pavers','curb'})
        boundary_ids={b['id'] for b in scene['boundaries']}
        self.assertTrue(all(set(s['boundaryIds'])<=boundary_ids for s in scene['supports']))
        for mesh in scene['meshes']:self.assertEqual(mesh['collision'],'solid')

    def test_stale_extent_and_unordered_heights_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'street-survey.pkl';value=self.section();path.write_bytes(pickle.dumps(value))
            with self.assertRaises(ValueError):sections.load(tmp,[0,0,9,6])
            value['profiles'][0]['heights'][:,1]=1.9;path.write_bytes(pickle.dumps(value))
            with self.assertRaises(ValueError):sections.load(tmp,value['bounds'])

    def test_retaining_outer_rail_follows_the_declared_planar_quantization(self):
        from turku3d.scene.retaining import RetainingField
        from turku3d.scene.compile import PLANAR_GRID_M
        centres=np.array([[0.,0.],[4.,0.],[8.,0.]])
        profile=dict(centres=centres,left=centres+[0,1],right=centres-[0,1],heights=np.tile([3.,1.],(3,1)))
        records=[(shapely.LineString(centres),dict(source='measured-wall',_survey=profile),None)]
        field=RetainingField(records,lambda x:np.full(len(x),2.),shapely.box(-1,-3,9,3),coordinate_tolerance=PLANAR_GRID_M*2)
        sampled=field.height(np.array([[4.,2.+PLANAR_GRID_M*.4]]),np.array([0.]))
        self.assertAlmostEqual(sampled[0],2.,places=4)
