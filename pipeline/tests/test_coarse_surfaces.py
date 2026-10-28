"""Pure draft surface semantics, projections and register fallbacks."""

import numpy as np
import shapely
from turku3d import config as C
from turku3d.coarse.register import attributes
from turku3d.coarse.surfaces import classify, fallback, visuals
from turku3d.col.materials import mat_id


def test_vectors_override_baked_blue_and_white_and_vegetation():
    tile = C.VisualTile(0, 0, 0)
    points = np.array([[10, 1, 10], [20, 1, 10], [30, 1, 10]])
    rgb = np.full((8, 8, 3), [80, 150, 70], np.uint8)
    regions = [(shapely.box(0, 0, 25, 25), mat_id("gravel"))]
    streets = [(shapely.box(5, 5, 15, 15), mat_id("road"))]
    assert classify(points, rgb, tile, streets, regions).tolist() == [
        mat_id(k) for k in ("road", "gravel", "grass")
    ]
    for colour in ([70, 130, 240], [250, 250, 250], [10, 10, 10]):
        rgb[:] = colour
        assert classify(points, rgb, tile, streets, regions)[:2].tolist() == [
            mat_id("road"),
            mat_id("gravel"),
        ]


def test_steep_bank_uses_height_uvs_without_changing_geometry():
    tile = C.VisualTile(0, 0, 0)
    positions = np.array([[0.0, 0, 0], [0, 5, 0], [0, 0, 5]])
    indices = np.array([[0, 1, 2]], np.uint32)
    meshes = visuals(tile, positions, indices, np.array([mat_id("quay")]), np.array([True]))
    assert len(meshes) == 1
    m = meshes[0]
    assert m.extras["material_key"] == "draft_quay_stone"
    assert np.allclose(m.positions[m.indices], positions[indices])
    assert np.linalg.matrix_rank(m.uvs[1:] - m.uvs[0]) == 2
    assert m.image is None


def test_metric_uvs_do_not_restart_on_tile_edges():
    p = np.array([[250.0, 0, 0], [250, 0, 10], [260, 0, 0]])
    args = (p, np.array([[0, 1, 2]], np.uint32), np.array([mat_id("road")]), np.array([True]))
    a = visuals(C.VisualTile(0, 0, 0), *args)[0]
    b = visuals(C.VisualTile(0, 1, 0), *args)[0]
    assert np.array_equal(a.uvs, b.uvs)


def test_mapped_setts_keep_their_stone_material():
    p = np.array([[0.0, 0, 0], [0, 0, 10], [10, 0, 0]])
    mesh = visuals(
        C.VisualTile(0, 0, 0),
        p,
        np.array([[0, 1, 2]], np.uint32),
        np.array([mat_id("cobblestone")]),
        np.array([True]),
    )[0]
    assert mesh.extras["material_key"] == "draft_cobblestone"


def test_register_missing_fields_do_not_invent_recent_construction():
    assert attributes({}) == dict(material_class=None, levels=None, era="unknown")
    assert attributes(
        dict(julkisivumateriaali="tiili", kerrosluku="4", kottovuosi="1905-01-01")
    ) == dict(material_class="brick", levels=4, era="1850-1919")


def test_procedural_floor_is_repeatable_and_material_specific():
    assert np.array_equal(fallback(4), fallback(4))
    assert not np.array_equal(fallback(4), fallback(5))
    assert fallback(4).shape == (256, 256, 3)


def test_paint_is_draped_on_road_plane_and_excludes_grass_and_water():
    from turku3d.coarse.surfaces import paint_visual

    tile = C.VisualTile(0, 0, 0)
    p = np.array([[0.0, 0, 0], [0, 2, 10], [10, 1, 0]])
    ix = np.array([[0, 1, 2]], np.uint32)
    paint = [shapely.box(1, 1, 2, 2)]
    m = paint_visual(tile, p, ix, np.array([mat_id("road")]), np.array([True]), paint)
    assert np.allclose(m.positions[:, 1], m.positions[:, 0] * 0.1 + m.positions[:, 2] * 0.2 + 0.008)
    assert m.extras["layer"] == "markings"
    for material in ("grass", "water"):
        assert (
            paint_visual(tile, p, ix, np.array([mat_id(material)]), np.array([True]), paint) is None
        )
    assert paint_visual(tile, p, ix, np.array([mat_id("road")]), np.array([False]), paint) is None


def test_boundary_cut_preserves_area_and_height_but_respects_exact_road_edge():
    from turku3d.coarse.surfaces import split_boundaries

    p = np.array([[0.0, 0, 0], [0, 2, 10], [10, 1, 0]])
    ix = np.array([[0, 1, 2]], np.uint32)
    road = shapely.box(-1, -1, 3, 11)
    positions, indices, material, normals = split_boundaries(
        p,
        ix,
        np.array([mat_id("road")]),
        np.array([True]),
        [(road, mat_id("road"))],
        np.array([mat_id("grass")]),
    )
    triangles = positions[indices]
    assert np.allclose(positions[:, 1], positions[:, 0] * 0.1 + positions[:, 2] * 0.2)
    area = (
        np.abs(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])[:, 1])
        / 2
    )
    assert np.isclose(area.sum(), 50)
    assert np.isclose(area[material == mat_id("road")].sum(), 25.5)
    assert np.allclose(np.linalg.norm(normals, axis=1), 1)
    assert (triangles[material == mat_id("road"), :, 0] <= 3).all()


def test_osm_building_match_preserves_register_and_does_not_match_neighbour():
    from turku3d.coarse.inputs import Building
    from turku3d.coarse.register import enrich

    building = Building("x", shapely.box(0, 0, 10, 10), 0, 12, "facade_plaster")
    building.attributes = dict(levels=4, era="unknown")
    osm = dict(
        buildings=[
            (
                shapely.box(0, 0, 10, 10),
                dict(material_class="wood", wall_colour="#123456", levels=2),
            ),
            (shapely.box(11, 0, 20, 10), dict(wall_colour="#ffffff")),
        ],
        shops=[shapely.Point(2, 2)],
    )
    enrich(building, {"x": dict(material_class="brick")}, osm)
    assert building.attributes == dict(
        levels=4, era="unknown", material_class="brick", wall_colour="#123456", ground_floor="shops"
    )


def test_mapped_kerbs_leave_water_buildings_and_paint_clear():
    from turku3d.coarse.inputs import Building
    from turku3d.coarse.kerbs import produce

    tile = C.VisualTile(0, 0, 0)
    line = shapely.LineString([(1, 5), (20, 5)])
    water = [shapely.box(5, 0, 10, 10)]
    building = Building("b", shapely.box(15, 0, 20, 10), 0, 10, "facade_plaster")
    meshes = produce(tile, line, lambda x, z: 2, water, [building], [])
    assert len(meshes) == 1
    mesh = meshes[0]
    centers = mesh.positions[mesh.indices].mean(axis=1)
    assert not shapely.contains_xy(water[0], centers[:, 0], centers[:, 2]).any()
    assert not shapely.contains_xy(building.footprint, centers[:, 0], centers[:, 2]).any()
    assert np.isclose(mesh.positions[:, 1].max(), 2.12)


def test_kerb_width_and_profile_continue_across_visual_tile_edge():
    from turku3d.coarse.kerbs import produce

    line = shapely.LineString([(240, 30), (260, 43)])
    edges = []
    for i in (0, 1):
        mesh = produce(C.VisualTile(0, i, 0), line, lambda x, z: x * 0.02 + z * 0.03, [], [], [])[0]
        edges.append(np.unique(mesh.positions[mesh.positions[:, 0] == 250], axis=0))
    assert np.array_equal(*edges)
    assert np.ptp(edges[0][:, 2]) > 0.18
