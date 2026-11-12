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
                    boundaries=[],supports=[],meshes=[],fixtures=[],emitters=[],collision={},assets=[],lightOccluders=[],
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

    def test_night_irradiance_is_explicit_hdr_rgb_per_vertex(self):
        s=self.compiled();m=s['meshes'][0];m['nightIrradiance']=[2.5]*len(m['positions']);validate_scene(s)
        for values in ([-1.]*len(m['positions']),[float('nan')]*len(m['positions']),[0.]):
            m['nightIrradiance']=values
            with self.assertRaises(ValueError):validate_scene(s)

    def test_night_emission_is_nonnegative_finite_linear_rgb(self):
        s=self.compiled();material=s['materials'][0]
        material['nightEmission']=[8.,5.92,3.52];validate_scene(s)
        for values in ([-1.,0.,0.],[float('inf'),0.,0.],[1.,1.],[True,0.,0.]):
            material['nightEmission']=values
            with self.assertRaises(ValueError):validate_scene(s)

    def test_hole_is_not_triangulated(self):
        s=self.scene();p=np.array([[x,0,z] for x in range(-1,12) for z in range(-1,12)])
        building=type('Building',(),{'footprint':shapely.box(3,3,7,7)})()
        ground_scene(s,p,[building],[],[])
        m=s['meshes'][0];p=np.array(m['positions']).reshape(-1,3);ix=np.array(m['indices']).reshape(-1,3)
        tris=p[ix];area=np.abs(np.cross(tris[:,1]-tris[:,0],tris[:,2]-tris[:,0])[:,1]).sum()/2
        self.assertAlmostEqual(area,84)

    def test_measured_lower_walk_remains_separate_from_upper_ground(self):
        s=self.scene()
        p=np.array([[x,3.6 if z<5 else 1.2,z] for x in range(-1,12) for z in range(-1,12)])
        walk=dict(key='survey',line=shapely.LineString([[0,7],[10,7]]),width=2.,
                  knots=np.array([0.,10.]),cross=np.array([-7.,-4.,-1.,0.,1.,4.,7.]),
                  heights=np.tile([3.6,3.6,1.2,1.2,1.2,np.nan,np.nan],(2,1)))
        s['materials'].append(dict(id='quay',color=[.3,.3,.3],roughness=.9,metalness=0))
        ground_scene(s,p,[],[],[],[walk])
        quays=[r for r in s['supports'] if r['kind']=='quay']
        self.assertTrue(quays)
        for support in quays:
            m=next(m for m in s['meshes'] if m['id']==support['meshId'])
            pos=np.array(m['positions']).reshape(-1,3)
            np.testing.assert_allclose(pos[:,1],1.2,atol=1e-8)
        validate_scene(s)

    def test_retaining_face_reuses_both_support_edges(self):
        s=self.scene()
        s['materials'].append(dict(id='quay-wall',color=[.3,.3,.3],roughness=.9,metalness=0))
        points=np.array([[x,2.4,z] for x in range(-3,14) for z in range(-1,12)])
        centres=np.array([[-2.,5.],[2.,5.],[6.,5.],[10.,5.],[12.,5.]])
        profile=dict(centres=centres,left=centres+[0,2.5],right=centres-[0,2.5],heights=np.tile([1.2,3.6],(5,1)))
        records=[(shapely.LineString(centres),dict(source='survey-wall',_survey=profile),False)]
        ground_scene(s,points,[],[],[],retaining=records)
        validate_scene(s)
        walls=[m for m in s['meshes'] if m['entityId']=='survey-wall']
        self.assertTrue(walls)
        boundaries={b['id']:b for b in s['boundaries']}
        supports={b for support in s['supports'] for b in support['boundaryIds']}
        for wall in walls:
            self.assertEqual(wall['collision'],'solid')
            self.assertIsNone(wall['supportId'])
            self.assertEqual(len(wall['boundaryIds']),2)
            self.assertTrue(set(wall['boundaryIds'])<=supports)
            vertices=np.array(wall['positions']).reshape(-1,3)
            for key in wall['boundaryIds']:
                for point in boundaries[key]['positions']:
                    self.assertLess(np.linalg.norm(vertices-point,axis=1).min(),1e-8)
        levels={round(p[1],4) for m in walls for p in np.array(m['positions']).reshape(-1,3) if 2<=p[0]<=10}
        self.assertEqual(levels,{1.2,3.6})
        broken=copy.deepcopy(s);broken['meshes'][0]['boundaryIds']=['missing']
        with self.assertRaises(ValueError):validate_scene(broken)

    def test_upper_connector_does_not_punch_through_lower_quay(self):
        from turku3d.scene.fixtures import support_query
        s=self.scene()
        for material in ('quay','quay-wall','water'):
            s['materials'].append(dict(id=material,color=[.3,.3,.3],roughness=.9,metalness=0))
        points=np.array([[x,3.6,z] for x in range(-1,12) for z in range(-1,12)])
        lower=dict(key='lower',line=shapely.LineString([[0,7],[10,7]]),width=2.,knots=np.array([0.,10.]),
                   cross=np.array([-7.,-4.,-1.,0.,1.,4.,7.]),heights=np.full((2,7),1.2))
        connector=dict(lower,key='connector',line=shapely.LineString([[5,3],[5,7]]),knots=np.array([0.,4.]),heights=np.full((2,7),3.6))
        ground_scene(s,points,[],[],[shapely.box(0,8,10,10)],[lower,connector])
        query=support_query(s)
        for z in (6.1,6.5,6.9,7.5):self.assertAlmostEqual(query(5,z)[1],1.2,6)

    def test_upper_connector_cannot_spike_wide_lower_terrace(self):
        from turku3d.scene.fixtures import support_query
        s=self.scene()
        for material in ('quay','quay-wall','water'):
            s['materials'].append(dict(id=material,color=[.3,.3,.3],roughness=.9,metalness=0))
        points=np.array([[x,1.2,z] for x in range(-1,12) for z in range(-1,12)])
        def walk(key,z,width,height):
            half=width/2
            return dict(key=key,line=shapely.LineString([[0,z],[10,z]]),width=width,knots=np.array([0.,10.]),
                        cross=np.array([-half-6,-half-3,-half,0,half,half+3,half+6]),heights=np.full((2,7),height))
        terrace=walk('wide-terrace',5.,6.,1.2)
        shore=walk('shore',7.,2.,1.2)
        upper=walk('connector',3.,2.,3.6)
        ground_scene(s,points,[],[],[shapely.box(0,8,10,10)],[terrace,shore,upper])
        self.assertAlmostEqual(support_query(s)(5,3)[1],1.2,6)

    def test_missing_quay_knot_requires_independent_wall_support(self):
        from turku3d.scene.retaining import shore_walks
        centres=np.array([[0.,5.],[5.,5.],[10.,5.]])
        heights=np.full((3,7),1.2);heights[1,2:5]=np.nan
        walk=dict(key='quay',line=shapely.LineString(centres+[0,1]),width=2.,knots=np.array([0.,5.,10.]),heights=heights)
        wall=(shapely.LineString(centres),dict(_survey=dict(centres=centres,heights=np.tile([1.25,3.6],(3,1)))),False)
        water=shapely.box(-1,7,11,12)
        self.assertTrue(np.isnan(shore_walks([walk],water,[])[0]['heights'][1,3]))
        self.assertEqual(shore_walks([walk],water,[wall])[0]['heights'][1,3],1.25)
        self.assertTrue(np.isnan(walk['heights'][1,3]))

    def test_mapped_lamps_have_rigid_heads_and_exact_sloping_foot_contact(self):
        from types import SimpleNamespace
        from turku3d.scene.fixtures import place_lamps
        s=self.scene()
        for material in ('iron','glass'):
            s['materials'].append(dict(id=material,color=[.3,.3,.3],roughness=.9,metalness=0))
        points=np.array([[x,.3*x,z] for x in range(-1,12) for z in range(-1,12)])
        ground_scene(s,points,[],[],[])
        props=[SimpleNamespace(object_id='frn_CityFurniture_LOD2:CityFurniture_'+key,kind='lamp',x=x,z=5)
               for key,x in [('56200764',2.),('19460502',7.),('unassociated',4.)]]
        from pathlib import Path
        import turku3d.scene.fixtures as fixtures
        place_lamps(s,props,Path(fixtures.__file__).with_name('office_fixture_bindings.json'));validate_scene(s)
        self.assertEqual(len(s['fixtures']),2);self.assertEqual(len(s['emitters']),2)
        for fixture in s['fixtures']:
            self.assertAlmostEqual(fixture['position'][1],.3*fixture['position'][0])
            post=next(m for m in s['meshes'] if m['id'] in fixture['meshIds'] and m['collision']=='solid')
            p=np.array(post['positions']).reshape(-1,3)
            foot=p[p[:,1]<fixture['position'][1]+.04]
            np.testing.assert_allclose(foot[:,1],.3*foot[:,0],atol=1e-8)
            light=next(e for e in s['emitters'] if e['id']==fixture['emitterIds'][0])
            if 'crook' in fixture['family']:
                self.assertAlmostEqual(light['position'][1]-fixture['position'][1],9.18)
                self.assertEqual(light['intensity'],90.)
                self.assertEqual(light['range'],42.)
                self.assertGreater(light['position'][0],fixture['position'][0])
                self.assertGreater(light['position'][2],fixture['position'][2])
        canopy=next(e for e in s['emitters'] if 'aperture' in e)
        self.assertEqual(canopy['range'],27.)
        self.assertEqual(canopy['intensity'],45.)
        fixture=next(f for f in s['fixtures'] if f['id']==canopy['fixtureId'])
        self.assertAlmostEqual(canopy['aperture']['position'][1]-fixture['position'][1],5.555)
        self.assertAlmostEqual(canopy['position'][1]-fixture['position'][1],4.85)
        self.assertEqual(canopy['aperture']['normal'],[0.,-1.,0.])
        for change in (dict(axisU=[0,1,0]),dict(size=[0,.84]),dict(cutoutRadius=.5)):
            broken=copy.deepcopy(s);next(e for e in broken['emitters'] if 'aperture' in e)['aperture'].update(change)
            with self.assertRaises(ValueError):validate_scene(broken)
        s['emitters'][0]['category']='inferred-from-triangles'
        with self.assertRaises(ValueError):validate_scene(s)

    def test_c1_register_row_is_a_sign_even_when_old_adapter_called_it_post(self):
        from types import SimpleNamespace
        from turku3d.scene.signs import place_signs,prototype
        s=self.scene();points=np.array([[x,0,z] for x in range(-1,12) for z in range(-1,12)])
        ground_scene(s,points,[],[],[])
        prop=SimpleNamespace(source='GIS_Liikennemerkit',object_id='registered-C1',kind='post',code='C1',x=5.,z=5.,yaw=.8)
        place_signs(s,[prop]);validate_scene(s)
        self.assertEqual(s['fixtures'][0]['family'],'fi-sign-C1-v1')
        self.assertEqual(s['meshes'][-1]['collision'],'solid')
        self.assertAlmostEqual(s['fixtures'][0]['dimensions'][0],.64)
        from PIL import Image
        import io
        image=Image.open(io.BytesIO(prototype('C1').image))
        self.assertEqual(image.getpixel((192,64)),(255,218,54))
        self.assertEqual(image.getpixel((245,64)),(217,32,44))
        from turku3d.scene.compile import write_products
        from turku3d.col import read_col
        from turku3d.col.materials import mat_id
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            write_products(s,directory)
            record=next(iter(s['collision'].values()))
            collision=read_col((Path(directory)/record['url']).read_bytes())
            self.assertIn(mat_id('street_furniture'),collision.materials)

    def test_light_blocker_height_is_explicit_and_nonempty(self):
        s=self.compiled();s['lightOccluders']=[dict(id='wall',positions=[[1,1],[2,1],[2,2],[1,1]],minY=0.,maxY=8.)]
        validate_scene(s)
        s['lightOccluders'][0]['maxY']=0
        with self.assertRaises(ValueError):validate_scene(s)



class ContextClipTests(unittest.TestCase):
    def test_context_excludes_playable_ground_and_interpolates_baked_color(self):
        from turku3d.scene.context import outside_mesh
        p=np.array([[-5,0,-5],[15,0,-5],[15,0,15],[-5,0,15]],float)
        c=np.c_[(p[:,0]+5)/20,(p[:,2]+5)/20,np.ones(4)]
        row=dict(name='ground',layer='ground',positions=p.tolist(),colors=c.tolist(),indices=[0,2,1,0,3,2])
        mesh=outside_mesh(row,[0,0,10,10]);tri=mesh.positions[mesh.indices]
        area=np.linalg.norm(np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]),axis=1).sum()/2
        self.assertAlmostEqual(area,300)
        polygons=shapely.polygons(tri[:,:,[0,2]])
        self.assertAlmostEqual(shapely.union_all(polygons).intersection(shapely.box(0,0,10,10)).area,0)
        np.testing.assert_allclose(mesh.colors[:,0],(mesh.positions[:,0]+5)/20)
        np.testing.assert_allclose(mesh.colors[:,1],(mesh.positions[:,2]+5)/20)


if __name__=='__main__':unittest.main()
