"""Distant survey geometry must retain roof heights, tile alignment and clear water."""
import numpy as np
import shapely

from turku3d import config as C
from turku3d.coarse.horizon import canopy, shells, terrain
from turku3d.coarse.inputs import Building


def test_shells_retain_survey_ridge_and_courtyard_without_floors():
    footprint = shapely.Polygon([(0, 0), (20, 0), (20, 20), (0, 20)],
                                [[(5, 5), (5, 15), (15, 15), (15, 5)]])
    b = Building('survey', footprint, 3, 15, 'facade_plaster')
    b.roof_parts = [(footprint, np.array([.2, 0, 15]))]
    mesh = shells([b], np.zeros(3), {'survey': '#ffffff'})
    faces = mesh.positions[mesh.indices]
    assert mesh.positions[:, 1].max() == 19
    assert mesh.positions[:, 1].min() == 3
    assert not np.any(np.all(faces[:, :, 1] == 3, axis=1))
    normals = np.cross(faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0])
    roofs = faces[normals[:, 1] > 0]
    assert np.isclose(np.sum(normals[normals[:, 1] > 0, 1]) / 2, footprint.area)
    assert not shapely.contains_xy(shapely.box(5, 5, 15, 15), roofs.mean(1)[:, 0], roofs.mean(1)[:, 2]).any()


def test_terrain_boundaries_share_heights_and_faces_point_up():
    points = np.array([[x, 2 + x * .01 + z * .02, z] for x in (-10, 250, 510) for z in (-10, 260)])
    empty = shapely.Polygon()
    left, _ = terrain(C.VisualTile(0, 0, 0), points, empty, empty, empty)
    right, _ = terrain(C.VisualTile(0, 1, 0), points, empty, empty, empty)
    p = left.positions
    q = right.positions + np.array([250, 0, 0])
    assert np.allclose(np.unique(p[p[:, 0] == 250], axis=0), np.unique(q[q[:, 0] == 250], axis=0))
    faces = p[left.indices]
    up = np.cross(faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0])[:, 1]
    # Surface faces point up; the only other faces are the vertical border skirts (far_ground.skirt).
    skirts = np.isclose(up, 0)
    assert np.all(up[~skirts] > 0) and skirts.any()
    xz = faces[skirts][..., [0, 2]]
    assert np.any(np.isclose(xz, 0) | np.isclose(xz, 250), axis=-1).all()


def test_woodland_canopy_does_not_fill_open_park_or_blocked_water():
    tile = C.VisualTile(0, 0, 0)
    park = [('park', shapely.box(0, 0, 250, 250), 'park', '')]
    assert canopy(tile, park, shapely.Polygon(), lambda x, z: 5) is None
    woodland = [('wood', shapely.box(0, 0, 250, 250), 'wood', '')]
    water = shapely.box(-10, -10, 125, 260)
    mesh = canopy(tile, woodland, water.buffer(5), lambda x, z: 5)
    assert mesh is not None and mesh.positions[:, 0].min() >= 125
    assert np.array_equal(mesh.positions, canopy(tile, woodland, water.buffer(5), lambda x, z: 5).positions)


def test_survey_faces_keep_vertical_walls_and_roof_courtyard_holes():
    from turku3d.coarse.horizon_nodes import face_triangles
    # geometry_local stores x,z,height; engine output must store x,height,z.
    roof = shapely.Polygon([(0, 0, 12), (20, 0, 12), (20, 20, 12), (0, 20, 12)],
                           [[(5, 5, 12), (5, 15, 12), (15, 15, 12), (15, 5, 12)]])
    positions, indices = face_triangles(roof)
    t = positions[indices]
    areas = np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1) / 2
    assert np.isclose(areas.sum(), 300)
    assert not shapely.contains_xy(shapely.box(5, 5, 15, 15), t.mean(1)[:, 0], t.mean(1)[:, 2]).any()
    wall = shapely.Polygon([(0, 0, 2), (20, 0, 2), (20, 0, 12), (0, 0, 12)])
    positions, indices = face_triangles(wall)
    t = positions[indices]
    assert np.isclose(np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1).sum() / 2, 200)
    assert np.all(positions[:, 2] == 0)


def test_independent_ground_halos_share_boundary_samples_at_both_lod_steps():
    from turku3d.coarse.horizon_nodes import sample_heights
    points = np.array([[x, 4 + np.sin(x / 37) + np.cos(z / 51), z]
                       for x in range(-200, 1201, 10) for z in range(-200, 701, 10)])
    left = points[(points[:, 0] >= -100) & (points[:, 0] <= 600)]
    right = points[(points[:, 0] >= 400) & (points[:, 0] <= 1100)]
    fine = sample_heights(left, np.linspace(0, 500, 17), np.linspace(0, 500, 17))
    coarse = sample_heights(right, np.linspace(500, 1000, 9), np.linspace(0, 500, 9))
    assert np.allclose(fine[::2, -1], coarse[:, 0], atol=1e-6)


def test_sparse_ground_key_points_support_the_middle_of_a_flat_survey_block():
    from turku3d.coarse.horizon_nodes import sample_heights
    points = np.array([[x, 3, z] for x in (-500, 0, 500, 1000) for z in (-500, 0, 500, 1000)])
    heights = sample_heights(points, np.linspace(0, 500, 17), np.linspace(0, 500, 17))
    assert np.allclose(heights, 3)


def test_cluster_crowns_stay_clear_of_water_and_share_anchors_across_levels():
    from turku3d.coarse.horizon_nodes import clusters
    block = C.VisualTile(1, 0, 0)
    water = shapely.box(195, 0, 225, 500)
    woods = shapely.box(0, 0, 500, 500).difference(water)
    height = lambda q: np.full(len(q), 5.)
    near = clusters(block, woods, height, False)
    far = clusters(block, woods, height, True)
    assert near is not None and far is not None
    for mesh in (near, far):
        assert shapely.covers(woods, shapely.points(mesh.positions[:, [0, 2]])).all()
        assert not shapely.intersects(water, shapely.polygons(mesh.positions[mesh.indices][:, :, [0, 2]])).any()
    near_tops = {tuple(p) for p in near.positions[::6]}
    assert all(tuple(p) in near_tops for p in far.positions[::6])
    assert len(np.unique(near.positions[::6, 1])) > 20
    assert np.array_equal(near.positions, clusters(block, woods, height, False).positions)


def test_regional_grid_preserves_coast_and_shared_lod_edges(tmp_path):
    from turku3d.coarse.horizon_regional import mesh, covers
    step=15.625
    x,z=np.meshgrid(np.arange(513)*step,np.arange(513)*step)
    height=(8+x*.001+z*.002).astype('float32')
    cover=np.full(height.shape,10,dtype='uint8');cover[x<500]=80
    path=tmp_path/'region.npz'
    np.savez_compressed(path,height=height,cover=cover,xmin=0.,zmin=0.,step=step)
    a=mesh(C.VisualTile(2,3,0),path)
    b=mesh(C.VisualTile(4,1,0),path)
    pa=a.positions+np.array([3000,0,0]);pb=b.positions+np.array([4000,0,0])
    edgea=pa[(pa[:,0]==4000)&(pa[:,2]%62.5==0)]
    edgeb=pb[(pb[:,0]==4000)&(pb[:,2]<=1000)]
    assert np.allclose(edgea,edgeb,atol=1e-6)
    coast=mesh(C.VisualTile(2,0,0),path)
    assert np.all(coast.positions[coast.positions[:,0]<500,1]==0)
    tri=coast.positions[coast.indices]
    assert np.all(np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0])[:,1]>0)
    assert not covers(C.VisualTile(4,-1,0),path)
