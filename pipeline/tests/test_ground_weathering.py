"""Street details remain grounded, local to hard surfaces and visual-only."""
import numpy as np
import shapely
from turku3d import config as C
from turku3d.col.materials import mat_id
from turku3d.coarse import ground_weathering as wear


def fixture():
    p = np.array([[10, 0, 10], [10, 0, 110], [20, 0, 10], [20, 0, 110]], float)
    p[:, 1] = p[:, 0] * .03 + p[:, 2] * .02
    ix = np.array([[0, 1, 2], [2, 1, 3]], np.uint32)
    profiles = [dict(key='street:0', line=shapely.LineString([(15, 10), (15, 110)]),
                     knots=np.array([0., 100.]), source_stations=np.array([10., 110.]))]
    return p, ix, np.full(2, mat_id('road'), np.uint8), profiles


def test_wear_preserves_terrain_planes_and_area_without_modifying_inputs():
    p, ix, ids, profiles = fixture()
    original = p.copy()
    out, faces, materials, normals, colours, drains = wear.surface(p, ix, ids, profiles)
    np.testing.assert_array_equal(p, original)
    np.testing.assert_allclose(out[:, 1], out[:, 0] * .03 + out[:, 2] * .02, atol=1e-10)
    assert abs(shapely.area(shapely.polygons(out[faces][:, :, [0, 2]])).sum() - 1000) < 1e-7
    assert np.isfinite(normals).all() and np.isfinite(colours).all()
    assert colours.min() >= .7 and colours.min() < .95
    assert drains and wear.ASPHALT_REPAIR in materials


def test_grass_and_stone_never_turn_into_asphalt_repairs():
    p, ix, ids, profiles = fixture()
    for material in (mat_id('grass'), 128):
        result = wear.surface(p, ix, np.full(2, material, np.uint8), profiles)
        assert set(result[2]) == {material}
        if material == mat_id('grass'):
            assert np.all(result[4] == 1)


def test_ground_colours_are_neutral_outside_the_narrow_kerb_band():
    p, ix, ids, profiles = fixture()
    out, _, _, _, colour, _ = wear.surface(p, ix, ids, profiles)
    assert np.all(colour[abs(out[:, 0] - 15) > .36] == 1)
    assert np.all(colour[np.isclose(out[:, 2], 10)] == 1)


def test_drains_follow_road_plane_and_are_rejected_on_grass():
    p, ix, ids, profiles = fixture()
    _, drains = wear.details(profiles)
    tile = C.VisualTile(0, 0, 0)
    meshes = wear.grates(tile, p, ix, ids, drains)
    assert len(meshes) == 1
    mesh = meshes[0]
    world = mesh.positions + tile.origin
    lift = world[:, 1] - world[:, 0] * .03 - world[:, 2] * .02
    assert np.all(np.isclose(lift, .002) | np.isclose(lift, .006))
    assert world[:, 0].min() > 15 and world[:, 0].max() < 15.5
    assert wear.grates(tile, p, ix, np.full(2, mat_id('grass'), np.uint8), drains) == []


def test_visual_mesh_preserves_wear_colours_and_uses_existing_fresh_asphalt():
    from turku3d.coarse.surfaces import visuals
    p, ix, ids, _ = fixture()
    colour = np.full_like(p, .8)
    meshes = visuals(C.VisualTile(0, 0, 0), p, ix, np.full(2, wear.ASPHALT_REPAIR, np.uint8),
                     np.ones(2, bool), colours=colour)
    assert len(meshes) == 1
    assert meshes[0].extras['material_key'] == 'draft_asphalt_fresh'
    assert np.all(meshes[0].colors == .8)


def test_weathering_preserves_incoming_smooth_normals_through_every_cut():
    p, ix, ids, profiles = fixture()
    normal = np.array([.1, 1., .2]); normal /= np.linalg.norm(normal)
    normals = np.tile(normal, (len(p), 1))
    for patches in ([], profiles):
        result = wear.surface(p, ix, ids, patches, normals)
        np.testing.assert_allclose(result[3], np.tile(normal, (len(result[0]), 1)), atol=1e-7)


def test_curved_profile_windows_share_canonical_drain_and_repair_stations():
    xy = np.array([[20, 0], [20, 30], [25, 60], [40, 90], [65, 120]], float)
    source = np.r_[0, np.cumsum(np.linalg.norm(np.diff(xy, axis=0), axis=1))]
    profiles = []
    for start, stop in ((0, 4), (1, 5)):
        part = xy[start:stop]
        knots = source[start:stop] - source[start]
        profiles.append(dict(key=f'curve:{source[start]}', line=shapely.LineString(part),
                             knots=knots, source_stations=source[start:stop]))
    a, b = [wear.details([p]) for p in profiles]
    region = shapely.LineString(xy[1:4]).buffer(.6)
    centres = lambda result: sorted(tuple(np.round(c, 8)) for c, _, _ in result[1]
                                     if region.contains(shapely.Point(c))
                                     and c[1] > 31 and c[1] < 89)
    assert centres(a) and centres(a) == centres(b)
    reversed_profile = dict(profiles[0], line=shapely.LineString(xy[:4][::-1]),
                            knots=profiles[0]['knots'][-1] - profiles[0]['knots'][::-1],
                            source_stations=profiles[0]['source_stations'][::-1])
    # Reversing changes which side is road; centreline event stations stay fixed.
    reversed_drains = wear.details([reversed_profile])[1]
    forward = [c + left * .29 for c, _, left in a[1]]
    backward = [c + left * .29 for c, _, left in reversed_drains]
    np.testing.assert_allclose(sorted(map(tuple, forward)), sorted(map(tuple, backward)), atol=1e-8)


def test_grate_interiors_follow_both_planes_of_a_terrain_diagonal():
    from turku3d.ground.mesh import HeightQuery, Mesh
    p = np.array([[10, 0, 10], [10, 0, 12], [12, 0, 10], [12, .2, 12.]])
    ix = np.array([[0, 1, 2], [2, 1, 3]], np.uint32)
    ids = np.full(2, mat_id('road'), np.uint8)
    drains = [(np.array([11., 11.]), np.array([1., 0.]), np.array([0., 1.]))]
    mesh = wear.grates(C.VisualTile(0, 0, 0), p, ix, ids, drains)[0]
    sample = HeightQuery(Mesh(p, ix, ids))
    centres = mesh.positions[mesh.indices].mean(axis=1)
    lift = centres[:, 1] - sample(centres[:, [0, 2]])
    assert np.all(np.isclose(lift, .002) | np.isclose(lift, .006))


def test_grate_crossing_tile_edge_is_partitioned_without_a_gap():
    p = np.array([[249, 0, 10], [249, 0, 12], [251, 0, 10], [251, 0, 12.]])
    ix = np.array([[0, 1, 2], [2, 1, 3]], np.uint32)
    ids = np.full(2, mat_id('road'), np.uint8)
    drains = [(np.array([250., 11.]), np.array([1., 0.]), np.array([0., 1.]))]
    base_area = 0
    for tile in (C.VisualTile(0, 0, 0), C.VisualTile(0, 1, 0)):
        mesh = wear.grates(tile, p, ix, ids, drains)[0]
        world = mesh.positions + tile.origin
        assert np.all(world[:, 0] >= tile.bbox.xmin - 1e-9)
        assert np.all(world[:, 0] <= tile.bbox.xmax + 1e-9)
        faces = world[mesh.indices]
        base = np.all(np.isclose(faces[:, :, 1], .002), axis=1)
        base_area += shapely.area(shapely.polygons(faces[base][:, :, [0, 2]])).sum()
    assert abs(base_area - .5 * .36) < 1e-9
