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


def _region(tmp_path, cover_at):
    step = 15.625
    x, z = np.meshgrid(np.arange(513) * step, np.arange(513) * step)
    height = (8 + x * .001 + z * .002).astype('float32')
    path = tmp_path / 'region.npz'
    np.savez_compressed(path, height=height, cover=cover_at(x, z).astype('uint8'), xmin=0., zmin=0., step=step)
    return path


def test_far_canopy_covers_tree_cells_only_and_faces_out(tmp_path):
    from turku3d.coarse.horizon_regional import canopy
    # Trees east of x = 1250 m, water west of 1100 m, grass between.
    path = _region(tmp_path, lambda x, z: np.where(x >= 1250, 10, np.where(x < 1100, 80, 30)))
    block = C.VisualTile(1, 2, 0)  # x 1000..1500
    shell = canopy(block, path, 15.625)
    p = shell.positions + np.asarray(block.origin)
    assert p[:, 0].min() >= 1250 - 15.625 - 1e-6  # within one cell of the 15.625 m cover samples
    tri = p[shell.indices]
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    up = normal[:, 1] > 1e-6
    # Mounds face up; the stand's west edge has skirts facing west (towards the open grass).
    assert up.sum() in (4 * 16 * 32, 4 * 17 * 32)
    side = ~up
    assert side.any() and np.all(normal[side, 0] < 0)
    assert canopy(C.VisualTile(1, 0, 0), path, 15.625) is None
    tops = p[:, 1] - (8 + p[:, 0] * .001 + p[:, 2] * .002)
    assert 4 <= tops.max() <= 9 + 1e-6 and tops.min() >= -2.1  # feet: 2 m under the lower side
    # Skirts run from the crowns' edge colour to the open grass's land-cover colour at their feet: none is darker
    # than the darkest crown vertex.
    skirt = np.unique(shell.indices[side])
    feet = skirt[tops[skirt] < 0]
    assert len(feet) and np.allclose(shell.colors[feet], (.24, .29, .16))
    crown = np.unique(shell.indices[up])
    assert shell.colors[skirt].sum(1).min() >= shell.colors[crown].sum(1).min() - 1e-6


def _chm(tmp_path, height_at, ground=10.0, step=4.0, size=2000.0, name='chm.npz'):
    """A merged lidar canopy model (coarse/chm.py format) over x, z 0..size with flat ground."""
    n = int(size / step)
    x, z = np.meshgrid((np.arange(n) + .5) * step, (np.arange(n) + .5) * step)
    path = tmp_path / name
    np.savez_compressed(path, chm=np.round(height_at(x, z) * 10).astype('int16'),
                        dtm=np.full((n, n), int(ground * 10), 'int16'), seen=np.ones((n, n), bool),
                        xmin=0., zmin=0., step=step)
    return path


def test_far_canopy_follows_the_lidar_canopy_height_model(tmp_path):
    from turku3d.coarse.horizon_regional import canopy
    # Land cover says grass everywhere; the lidar finds 15 m canopy east of x = 1250 m and a 60 m clearing in it.
    region = _region(tmp_path, lambda x, z: np.full(x.shape, 30))
    trees = lambda x, z: np.where((x >= 1250) & ~((abs(x - 1400) < 30) & (abs(z - 750) < 30)), 15.0, 0.0)
    chm = _chm(tmp_path, trees)
    block = C.VisualTile(1, 2, 1)  # x 1000..1500, z 500..1000: the model also holds the ring of cells around it
    assert canopy(block, region, 31.25) is None  # the land cover alone has no canopy here
    shell = canopy(block, region, 31.25, chm=chm)
    p = shell.positions + np.asarray(block.origin)
    assert p[:, 0].min() >= 1250 - 31.25 - 1e-6
    tops = p[:, 1] - 10.0  # above the lidar ground, not the surface model
    assert 14 <= tops.max() <= 15 + 1e-6 and tops.min() >= -2 - 1e-6
    # The clearing stays open: no shell vertex stands high above its middle.
    middle = (abs(p[:, 0] - 1400) < 12) & (abs(p[:, 2] - 750) < 12)
    assert not np.any(middle & (tops > 5))
    # A block the model does not cover keeps the land-cover shell.
    assert canopy(C.VisualTile(1, 6, 0), region, 31.25, chm=chm) is None
    # On a survey ground the skirts fade into the colour the caller draws there.
    drawn = canopy(block, region, 31.25, chm=chm, ground_colour=(.2, .3, .1))
    feet = (drawn.positions + np.asarray(block.origin))[:, 1] < 10.0
    assert feet.any() and np.allclose(drawn.colors[feet], (.2, .3, .1))


def test_a_growing_canopy_model_only_changes_the_blocks_it_newly_covers(tmp_path):
    from turku3d.coarse.horizon_regional import chm_covers, chm_digest, chm_grid
    trees = lambda x, z: np.where((x // 100 + z // 100) % 2 == 0, 12.0, 0.0)
    small = _chm(tmp_path, trees, size=2000.0, name='small.npz')
    grown = _chm(tmp_path, trees, size=3000.0, name='grown.npz')
    inside, edge, new = C.VisualTile(1, 1, 1), C.VisualTile(1, 3, 1), C.VisualTile(1, 4, 1)  # x 500.., 1500.., 2000..
    assert chm_covers(inside, small) and chm_covers(inside, grown)
    assert chm_digest(inside, small) == chm_digest(inside, grown)
    assert not chm_covers(edge, small) and chm_covers(edge, grown)  # its ring of shell cells reaches past 2000 m
    assert not chm_covers(new, small) and chm_covers(new, grown)
    changed = _chm(tmp_path, lambda x, z: trees(x, z) + (abs(x - 700) < 20) * 3.0, size=3000.0, name='changed.npz')
    assert chm_digest(inside, changed) != chm_digest(inside, grown)
    chm_grid.cache_clear()


def test_chm_merge_orients_blocks_and_fills_ground(tmp_path, monkeypatch):
    from turku3d.coarse import chm as M
    monkeypatch.setattr(M.C, 'ORIGIN_E', 1000.0, raising=False)
    monkeypatch.setattr(M.C, 'ORIGIN_N', 2000.0, raising=False)
    n = int(M.BLOCK / M.CELL)
    rows = np.arange(n)[:, None] * np.ones((1, n))  # block rows run south to north
    for east, north, base in ((1000, 2000, 0), (1500, 2000, 100)):
        dtm = np.full((n, n), 50 + base, 'int16'); dtm[0, 5] = M.MISSING  # one gap inside each block
        np.savez_compressed(tmp_path / f'{east}_{north}.npz', chm=(rows * 2).astype('int16'), dtm=dtm,
                            x0=float(east), y0=float(north - 500), cell=M.CELL)
    out = tmp_path / 'chm.npz'
    info = M.merge(tmp_path, out)
    with np.load(out) as d:
        assert d['chm'].shape == (n, 2 * n) and float(d['xmin']) == 0.0 and float(d['zmin']) == 0.0
        assert d['chm'][0, 0] == 2 * (n - 1) and d['chm'][-1, 0] == 0  # north row first: the block's top row
        assert d['dtm'].min() == 50 and d['dtm'][-1, 5] == 50 and d['dtm'][-1, n + 5] == 150  # gaps filled
        assert d['seen'].all()
    assert info['blocks'] == 2 and info['holes'] == 2


def test_far_canopy_corners_meet_across_tiles_and_levels(tmp_path):
    from turku3d.coarse.horizon_regional import build, canopy
    path = _region(tmp_path, lambda x, z: np.where((x // 300 + z // 300) % 2 == 0, 10, 30))
    a, b = C.VisualTile(1, 2, 2), C.VisualTile(1, 3, 2)
    pa = canopy(a, path, 15.625).positions + np.asarray(a.origin)
    pb = canopy(b, path, 15.625).positions + np.asarray(b.origin)
    edge = 1500.0

    def surface(p):  # the canopy surface meets; skirt feet sit under the ground, at each side's open neighbour
        return {tuple(np.round(q, 4)) for q in p[np.isclose(p[:, 0], edge) & (p[:, 1] > 8 + p[:, 0] * .001 + p[:, 2] * .002 - 1)]}
    ea, eb = surface(pa), surface(pb)
    assert ea and ea == eb
    meshes, stats = build(C.VisualTile(4, 0, 0), path)
    assert [m.extras['layer'] for m in meshes] == ['regional', 'canopy-regional']
    assert stats['draws'] == 2
