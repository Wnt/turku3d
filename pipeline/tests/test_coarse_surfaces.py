"""Pure draft surface semantics, projections and register fallbacks."""

import numpy as np
import shapely
from turku3d import config as C
from turku3d.coarse.register import attributes
from turku3d.coarse.surfaces import classify, fallback, slope_fallback, visuals
from turku3d.col.materials import mat_id


def test_ground_sampler_uses_mesh_diagonal_and_exact_outer_edge():
    from turku3d.coarse.surfaces import grid_height

    tile = C.VisualTile(0, 1, -1)
    # A saddle exposes bilinear interpolation: the diagonal is entirely flat.
    heights = np.array([[0.0, 0.0], [0.0, 8.0]])
    x, _, z = tile.origin
    assert grid_height(tile, heights, x + 125, z + 125) == 0
    assert grid_height(tile, heights, x + 187.5, z + 187.5) == 4
    assert grid_height(tile, heights, x + 250, z + 250) == 8
    assert grid_height(tile, heights, x, z) == 0


def test_kerb_sampler_keeps_the_current_adjusted_grid_at_its_outer_edge(tmp_path):
    from turku3d.coarse.kerbs import height_sampler

    class NoNeighbourReads:
        def lidar_paths(self, tile):
            raise AssertionError("An owned boundary point must use the current adjusted grid")

    tile = C.VisualTile(0, 0, 0)
    sample = height_sampler(tile, np.array([[0.0, 0.0], [0.0, 8.0]]), tmp_path, NoNeighbourReads())
    assert sample(250, 250) == 8


def test_kerb_corners_follow_cross_slope_with_identical_collision_faces():
    from turku3d.coarse.kerbs import produce, visuals

    tile = C.VisualTile(0, 0, 0)

    def height(x, z):
        return 2 + z * 0.5

    meshes = produce(tile, shapely.LineString([(10, 10), (14, 10)]), height, [], [], [])
    assert len(meshes) == 1
    mesh = meshes[0]
    visual = visuals(tile, meshes)[0]
    np.testing.assert_allclose(visual.positions, mesh.positions)
    np.testing.assert_array_equal(visual.indices, mesh.indices)
    relative = mesh.positions[:, 1] - (2 + mesh.positions[:, 2] * 0.5)
    assert np.all(np.isclose(relative, 0.12) | np.isclose(relative, -0.02))


def test_kerb_top_follows_terrain_inside_triangles_and_at_tile_clip():
    from turku3d.coarse.inputs import CELLS
    from turku3d.coarse.kerbs import produce

    tile = C.VisualTile(0, 0, 0)
    step = tile.size / CELLS

    def height(x, z):
        return max(0, x + z - step)

    mesh = produce(tile, shapely.LineString([(-0.2, 0.8), (1.6, 0.8)]), height, [], [], [])[0]
    triangles = mesh.positions[mesh.indices]
    area_y = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])[:, 1]
    top = triangles[area_y > 1e-10]
    assert len(top) > 2
    for p in top.mean(axis=1):
        assert abs(p[1] - height(p[0], p[2]) - 0.12) < 1e-12
    assert mesh.positions[:, 0].min() == 0


def test_building_passage_keeps_its_mapped_ground_paving(tmp_path):
    import json

    import pyarrow as pa
    import pyarrow.parquet as pq
    from turku3d.coarse.surfaces import osm_surfaces

    rows = []
    for z, tunnel, layer in (
        (10, "building_passage", "0"),
        (20, "yes", "0"),
        (30, "building_passage", "-1"),
    ):
        rows.append(
            dict(
                tags=json.dumps(
                    dict(highway="service", surface="asphalt", tunnel=tunnel, layer=layer)
                ),
                geometry_local=shapely.LineString([(10, z), (20, z)]).wkb,
            )
        )
    path = tmp_path / "osm.parquet"
    pq.write_table(pa.Table.from_pylist(rows), path)
    regions = osm_surfaces(C.VisualTile(0, 0, 0), path)
    assert len(regions) == 1
    assert regions[0][1] == mat_id("road")
    assert regions[0][0].covers(shapely.Point(15, 10))


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


def test_steep_terrain_uses_rock_without_repainting_mapped_roads():
    positions = np.array([[0.0, 0, 0], [0, 5, 0], [0, 0, 5], [5, 0, 0]])
    indices = np.array([[0, 1, 2], [0, 2, 3]], np.uint32)
    material = np.array([mat_id("paving_slab"), mat_id("paving_slab")], np.uint8)
    assert slope_fallback(positions, indices, material).tolist() == [
        mat_id("rock"),
        mat_id("paving_slab"),
    ]
    material[0] = mat_id("road")
    assert slope_fallback(positions, indices, material).tolist() == [
        mat_id("road"),
        mat_id("paving_slab"),
    ]


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


def test_kerb_collision_keeps_world_coordinates_until_col_encoding():
    from turku3d.coarse.kerbs import collision, produce
    from turku3d.col import ColTile

    tile = C.VisualTile(0, 1, 1)
    cell = C.CollisionTile(4, 4)
    meshes = produce(tile, shapely.LineString([(260, 270), (265, 270)]), lambda x, z: 3, [], [], [])
    p, ix, mat = collision(
        cell, meshes, np.empty((0, 3)), np.empty((0, 3), np.uint32), np.empty(0, np.uint8)
    )
    col = ColTile.for_tile(cell.i, cell.j, p, ix, mat)
    assert np.allclose(col.world_positions(), p)
    assert col.world_positions()[:, 0].min() >= 260
    assert col.world_positions()[:, 2].min() > 269
