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
    profiles = [dict(key='street:0', line=shapely.LineString([(15, 10), (15, 110)]))]
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
