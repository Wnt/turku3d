"""Connected, measured kerb geometry keeps source slope, holes and collision surface."""
from types import SimpleNamespace

import numpy as np
import shapely

from turku3d.coarse import street_profiles as profiles, street_survey as survey
from turku3d.col.materials import mat_id
from turku3d.ground.mesh import Mesh, HeightQuery


def patch():
    return dict(key='street', line=shapely.LineString([(0, 0), (0, 20)]),
                knots=np.array([0, 20]), heights=np.array([[0, .12], [.2, .32]]))


def test_shared_city_edges_exclude_building_side_and_are_stably_ordered():
    rows=[(shapely.box(0,0,8,20),SimpleNamespace(Katuosalaji='Ajorata')),
          (shapely.box(-2,0,0,20),SimpleNamespace(Katuosalaji='Jalkakäytävä'))]
    window=shapely.box(-3,-1,10,21)
    a=survey.shared_lines(rows,window);b=survey.shared_lines(rows[::-1],window)
    assert a==b and len(a)==1
    assert np.isclose(a[0][1].length,20)
    assert all(x==0 for x,z in a[0][1].coords)


def test_connected_profile_preserves_road_slope_and_unchanged_outer_join():
    x,z=np.meshgrid(np.arange(-3,4),np.arange(21))
    p=np.c_[x.ravel(),z.ravel()*.01+.06,z.ravel()]
    indices=[]
    for j in range(20):
        for i in range(6):
            a=j*7+i;indices.extend([[a,a+7,a+1],[a+1,a+7,a+8]])
    ix=np.array(indices,np.uint32);mats=np.full(len(ix),mat_id('road'),np.uint8)
    mesh=Mesh(p,ix,mats);surface=profiles.constrain(mesh,[patch()]);out=surface.mesh;sample=HeightQuery(out)
    low,high=sample([[.015,10],[-.025,10]])
    assert abs(low-.1)<1e-8 and abs(high-.22)<1e-8
    np.testing.assert_allclose(sample([[-1.5,10],[1.5,10]]),[.16,.16],atol=1e-8)
    tri=out.positions[out.indices]
    area=abs(np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0])[:,1]).sum()/2
    assert np.isclose(area,120)
    assert np.any(out.material==mat_id('curb'))
    np.testing.assert_array_equal(mesh.positions,p)
    assert np.all(mats==mat_id('road'))
    height=profiles.sampler(lambda x,z:99,surface)
    assert abs(height(-.025,10)-.22)<1e-8
    assert height(20,10)==99


def test_fitter_rejects_missing_support_instead_of_bridging_gap():
    x,z=np.meshgrid(np.arange(-1.5,1.51,.08),np.arange(-1,21.01,.08))
    points=np.c_[x.ravel(),.01*z.ravel()+np.where(x.ravel()<0,.12,0),z.ravel()]
    points=points[(points[:,2]<8)|(points[:,2]>12)]
    result=survey.measure([('survey',patch()['line'])],points,shapely.box(-3,-2,3,22))
    assert len(result)==2
    for p in result:
        assert np.allclose(p['heights'][:,1]-p['heights'][:,0],.12,atol=.01)
        assert not p['line'].intersects(shapely.box(-1,8.5,1,11.5))
    assert survey.measure([('survey',patch()['line'])],np.empty((0,3)),shapely.box(-2,0,2,20))==[]


def test_nearby_water_and_structure_faces_are_not_graded_or_marked_as_kerb():
    p=np.array([[-.1,.16,9],[-.1,.16,11],[.1,.16,9],
                [0,.16,9],[0,.16,11],[0,1.16,9]])
    ix=np.array([[0,1,2],[3,4,5]],np.uint32)
    ids=np.array([mat_id('water'),mat_id('quay')],np.uint8)
    out=profiles.constrain(Mesh(p,ix,ids),[patch()]).mesh
    assert set(out.material)==set(ids)
    water=out.positions[out.indices[out.material==mat_id('water')]]
    assert np.allclose(water[:,:,1],.16)
    wall=out.positions[out.indices[out.material==mat_id('quay')]]
    assert np.allclose(wall[:,:,0],0)
    u=wall[:,:,1]-.16;v=(wall[:,:,2]-9)/2
    assert np.all(u>=-1e-8) and np.all(v>=-1e-8) and np.all(u+v<=1+1e-8)
    area=np.linalg.norm(np.cross(wall[:,1]-wall[:,0],wall[:,2]-wall[:,0]),axis=1).sum()/2
    assert np.isclose(area,1)


def test_overlapping_profiles_do_not_accumulate_height_corrections():
    a=patch();b=patch()
    b['line']=shapely.LineString([(.5,0),(.5,20)])
    p=np.array([[-.025,.16,10],[.475,.16,10]])
    np.testing.assert_allclose(profiles.grade(p,[a,b]),profiles.grade(p,[b,a]),atol=1e-8)


def test_zero_area_clipping_sliver_cannot_pin_one_copy_of_a_kerb_vertex():
    p=np.array([[-.025,.16,10],[-.025,.16,11],[-1,.16,10]])
    mesh=Mesh(p,np.array([[0,1,2],[0,0,0]],np.uint32),
              np.full(2,mat_id('sidewalk'),np.uint8))
    out=profiles.constrain(mesh,[patch()]).mesh
    same=np.all(abs(out.positions[:,[0,2]]-p[0,[0,2]])<1e-8,axis=1)
    assert same.sum()>0
    np.testing.assert_allclose(out.positions[same,1],.22,atol=1e-8)


def test_bent_kerb_has_no_hanging_internal_edges_after_grading():
    x,z=np.meshgrid(np.arange(-3,4),np.arange(7))
    p=np.c_[x.ravel(),np.full(x.size,.06),z.ravel()]
    ix=[]
    for j in range(6):
        for i in range(6):
            a=j*7+i;ix.extend([[a,a+7,a+1],[a+1,a+7,a+8]])
    mesh=Mesh(p,np.array(ix,np.uint32),np.full(len(ix),mat_id('road'),np.uint8))
    line=shapely.LineString([(0,.3),(0,3),(.4,5.7)])
    patch=dict(key='bent',line=line,knots=np.array([0,2.7,line.length]),
               heights=np.tile([0,.12],(3,1)))
    out=profiles.constrain(mesh,[patch]).mesh
    edges=np.sort(out.indices[:,[[0,1],[1,2],[2,0]]].reshape(-1,2),axis=1)
    edges,count=np.unique(edges,axis=0,return_counts=True)
    centre=out.positions[edges[count==1]].mean(axis=1)
    internal=(abs(centre[:,0])<3-1e-7)&(centre[:,2]>1e-7)&(centre[:,2]<6-1e-7)
    assert not internal.any()


def test_endpoint_transition_has_explicit_full_height_breakline():
    p=np.array([[-2,.06,0],[-2,.06,20],[2,.06,0],[2,.06,20]])
    mesh=Mesh(p,np.array([[0,1,2],[2,1,3]],np.uint32),np.full(2,mat_id('road'),np.uint8))
    a=patch();a['heights']=np.tile([0,.12],(2,1))
    out=profiles.constrain(mesh,[a]).mesh;sample=HeightQuery(out)
    np.testing.assert_allclose(sample([[.015,.5],[-.025,.5]]),[0,.12],atol=1e-7)


def test_equal_distance_overlap_uses_stable_profile_identity():
    a=patch();b=patch();a['key']='a';b['key']='b';b['heights']=a['heights']+.08
    p=np.array([[-.025,.16,10]])
    np.testing.assert_allclose(profiles.grade(p,[a,b]),profiles.grade(p,[b,a]))


def test_sampler_covers_deformed_approach_when_the_kerb_is_outside_tile():
    p=np.array([[.4,.16,8],[.4,.16,12],[1.2,.16,8],[1.2,.16,12]])
    mesh=Mesh(p,np.array([[0,1,2],[2,1,3]],np.uint32),np.full(2,mat_id('road'),np.uint8))
    surface=profiles.constrain(mesh,[patch()])
    assert profiles.material_regions(surface.mesh)==[]
    query=HeightQuery(surface.mesh)
    expected=query([[.6,10]])[0]
    assert abs(expected-.16)>.01
    height=profiles.sampler(lambda x,z:99,surface)
    assert abs(height(.6,10)-expected)<1e-10
    assert height(10,10)==99


def test_profile_surface_cache_identity_tracks_sampler_support_and_geometry():
    from turku3d.stage_cache import content_key
    p=np.array([[.4,.16,8],[.4,.16,12],[1.2,.16,8],[1.2,.16,12]])
    mesh=Mesh(p,np.array([[0,1,2],[2,1,3]],np.uint32),np.full(2,mat_id('road'),np.uint8))
    surface=profiles.constrain(mesh,[patch()])
    before=content_key(surface)
    assert content_key(profiles.constrain(mesh,[patch()]))==before
    assert content_key(profiles.ProfiledSurface(surface.mesh,shapely.GeometryCollection()))!=before


def test_adjacent_tiles_query_identical_kerb_heights_at_their_shared_edge():
    surfaces=[]
    for low,high in [(0,10),(10,20)]:
        p=np.array([[-2,.16,low],[-2,.16,high],[2,.16,low],[2,.16,high]])
        mesh=Mesh(p,np.array([[0,1,2],[2,1,3]],np.uint32),np.full(2,mat_id('road'),np.uint8))
        surfaces.append(profiles.constrain(mesh,[patch()]))
    points=np.c_[[-1.5,-.18,-.025,-.01,0,.015,.18,.6,1.5],np.full(9,10)]
    np.testing.assert_allclose(HeightQuery(surfaces[0].mesh)(points),
                               HeightQuery(surfaces[1].mesh)(points),atol=1e-10)
