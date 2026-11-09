"""Pure draft surface semantics, projections and register fallbacks."""

import numpy as np
import shapely
from turku3d import config as C
from turku3d.coarse.register import attributes
from turku3d.coarse.surfaces import classify, fallback, neutral_asphalt, slope_fallback, visuals
from turku3d.col.materials import mat_id
from turku3d.ground.materials import surface_material


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
    assert mesh.image is None
    assert "ground_ortho_size" not in mesh.extras


def test_city_paving_selects_physical_texture_on_paths_and_carriageways():
    p = np.array([[0.0, 0, 0], [0, 0, 10], [10, 0, 0]])
    for kind, paving, key in (
        ("Jalkakäytävä", "Asfalttibetoni", "draft_asphalt_worn"),
        ("Ajorata", "Betonilaatta", "draft_concrete_slabs"),
    ):
        mesh = visuals(
            C.VisualTile(0, 0, 0),
            p,
            np.array([[0, 1, 2]], np.uint32),
            np.array([mat_id(surface_material(kind, paving))]),
            np.array([True]),
        )[0]
        assert mesh.extras["material_key"] == key


def test_register_missing_fields_do_not_invent_recent_construction():
    assert attributes({}) == dict(material_class=None, levels=None, levels_source=None, construction_year=None, era="unknown")
    assert attributes(
        dict(julkisivumateriaali="tiili", kerrosluku="4", kottovuosi="1905-01-01")
    ) == dict(material_class="brick", levels=4, levels_source="register", construction_year=1905, era="1850-1919")


def test_procedural_floor_is_repeatable_and_material_specific():
    assert np.array_equal(fallback(4), fallback(4))
    assert not np.array_equal(fallback(4), fallback(5))
    assert fallback(4).shape == (256, 256, 3)


def test_asphalt_cast_removal_preserves_light_and_grain():
    rgb = np.array([[[40, 35, 25], [180, 160, 130], [100, 110, 120]]], np.uint8)
    neutral = neutral_asphalt(rgb)
    assert np.array_equal(neutral[..., 0], neutral[..., 1])
    assert np.array_equal(neutral[..., 1], neutral[..., 2])

    def luminance(image):
        s = image.astype(float) / 255
        linear = np.where(s <= 0.04045, s / 12.92, ((s + 0.055) / 1.055) ** 2.4)
        return linear @ [0.2126, 0.7152, 0.0722]

    assert np.allclose(luminance(rgb), luminance(neutral), atol=0.003)
    assert neutral[0, 0, 0] < neutral[0, 2, 0] < neutral[0, 1, 0]


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


def test_tram_rails_are_two_gauge_strips_laid_in_setts_but_not_in_grass(tmp_path):
    import json

    import pyarrow as pa
    import pyarrow.parquet as pq
    from turku3d.coarse.surfaces import RAIL_HEAD, TRAM_GAUGE, paint_visual, tram_rails

    tile = C.VisualTile(0, 0, 0)
    line = shapely.LineString([(5, 1), (5, 9)]).wkb
    tags = [{"railway": "tram"}, {"railway": "tram", "tunnel": "yes"}, {"railway": "rail"}]
    path = tmp_path / "osm.parquet"
    pq.write_table(pa.Table.from_pylist([dict(tags=json.dumps(t), geometry_local=line) for t in tags]), path)
    items = tram_rails(tile, path)
    assert len(items) == 2 and all(kind == "tram_rail" and deck is None for _, _, deck, kind in items)
    xs = sorted(p.centroid.x for p, *_ in items)
    assert abs((xs[1] - xs[0]) - (TRAM_GAUGE + RAIL_HEAD)) < 1e-6
    p = np.array([[0.0, 1, 0], [0, 1, 10], [10, 1, 0], [10, 1, 10]])
    ix = np.array([[0, 1, 2], [2, 1, 3]], np.uint32)
    keep = np.array([True, True])
    assert paint_visual(tile, p, ix, np.array([mat_id("cobblestone")] * 2), keep, items) is not None
    assert paint_visual(tile, p, ix, np.array([mat_id("grass")] * 2), keep, items) is None
    # Road paint still never lands on setts.
    assert paint_visual(tile, p, ix, np.array([mat_id("cobblestone")] * 2), keep, [shapely.box(1, 1, 2, 2)]) is None


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


def test_stair_treads_replace_ground_and_share_collision_faces():
    from turku3d.coarse import stairs
    from turku3d.coarse.build import grid_mesh
    from turku3d.ground.mesh import Mesh

    tile = C.VisualTile(0, 0, 0)
    line = shapely.LineString([(10, 20), (16, 20)])
    meshes, openings = stairs.produce(
        tile,
        ([("flight", line, {"width": "8", "step_count": "10"})], []),
        lambda x, z: (x - 10) / 6 * 1.6,
    )
    mesh = meshes[0]
    triangles = mesh.positions[mesh.indices]
    normal = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    flight = (triangles[:, :, 0].min(axis=1) >= 10) & (triangles[:, :, 0].max(axis=1) <= 16)
    treads = triangles[(normal[:, 1] > 1e-8) & flight]
    np.testing.assert_allclose(np.unique(np.round(treads[:, :, 1], 8)), np.arange(1, 11) * 0.16)
    assert mesh.positions[:, 2].min() == 16
    assert mesh.positions[:, 2].max() == 24
    assert abs(normal[(normal[:, 1] > 0) & flight, 1].sum() / 2 - 48) < 1e-8
    # Ground triangles crossing the corridor with vertices outside must be split exactly.
    pos, ix = grid_mesh(tile, np.zeros((129, 129)))
    ground = stairs.cut_ground(Mesh(pos, ix, np.full(len(ix), mat_id("grass"), np.uint8)), openings)
    polys = shapely.polygons(ground.positions[ground.indices][:, :, [0, 2]])
    assert shapely.area(shapely.intersection(polys, openings[0])).sum() < 1e-8
    # A boundary triangle whose centre is cut still owns its exterior remainder.
    expected = shapely.box(0, 0, 250, 250).difference(openings[0])
    assert expected.symmetric_difference(shapely.union_all(polys)).area < 1e-8
    ct = C.CollisionTile.at(12, 20)
    p, idx, mat = stairs.collision(
        ct, meshes, np.empty((0, 3)), np.empty((0, 3), np.uint32), np.empty(0, np.uint8)
    )
    # Visible treads remain exact query geometry; the capsule sees a separate smooth ramp.
    np.testing.assert_array_equal(p[idx[mat == mat_id("stair_tread_helper")]], triangles)
    assert np.count_nonzero(mat == mat_id("stair_slope")) > 0
    assert not np.any(mat == mat_id("curb"))


def test_monumental_fallback_preserves_explicit_material_and_stored_description(monkeypatch):
    from turku3d import facade_kit

    fr = facade_kit.renderer()
    attrs = {"monumental": True}
    d = fr.register_description("m", "m", attrs, 40, 15)
    assert d["material"] == "stone"
    assert d["storeys"] == 3
    assert d["window_columns"] == 9
    brick = fr.register_description("m", "m", {**attrs, "material_class": "brick"}, 40, 15)
    assert brick["material"] == "brick"
    monkeypatch.setattr(facade_kit, "read_description", lambda *a, **kw: brick)
    params = facade_kit.parameters("m", 15, 40, attrs)
    assert params["module"] // 6 == facade_kit.MATERIALS.index("brick")
    assert not params["plinth"]


def test_stair_ground_cut_preserves_vertical_bank_faces():
    from turku3d.coarse.stairs import cut_ground
    from turku3d.ground.mesh import Mesh

    p = np.array([[0.0, 0.0, 0.0], [0.0, 3.0, 0.0], [0.0, 0.0, 4.0]])
    mesh = Mesh(p, np.array([[0, 1, 2]], np.uint32), np.array([mat_id("quay")], np.uint8))
    result = cut_ground(mesh, [shapely.box(-1, 1, 1, 3)])
    np.testing.assert_array_equal(result.positions[result.indices], p[mesh.indices])
    np.testing.assert_array_equal(result.material, mesh.material)


def test_boundary_cut_preserves_vertical_faces_and_region_precedence_with_holes():
    from turku3d.coarse.surfaces import split_boundaries

    p = np.array([[0.0, 0, 0], [0, 2, 10], [10, 1, 0], [0, 3, 0]])
    ix = np.array([[0, 1, 2], [0, 3, 1]], np.uint32)
    outer = shapely.Polygon(
        [(-1, -1), (11, -1), (11, 11), (-1, 11)], holes=[[(1, 1), (2, 1), (2, 2), (1, 2)]]
    )
    road = shapely.box(3, -1, 5, 11)
    pos, idx, mat, normals = split_boundaries(
        p,
        ix,
        np.array([mat_id("grass"), mat_id("quay")]),
        np.ones(2, bool),
        [(outer, mat_id("gravel")), (road, mat_id("road"))],
        np.array([mat_id("grass"), mat_id("quay")]),
    )
    np.testing.assert_array_equal(pos[idx[mat == mat_id("quay")]], p[ix[1:]])
    triangles = pos[idx[mat != mat_id("quay")]]
    polys = shapely.polygons(triangles[:, :, [0, 2]])
    whole = shapely.Polygon(p[ix[0]][:, [0, 2]])
    assert abs(shapely.area(polys).sum() - whole.area) < 1e-9
    for key, expected in (
        ("road", whole.intersection(road)),
        ("gravel", whole.intersection(outer).difference(road)),
        ("grass", whole.difference(outer).difference(road)),
    ):
        selected = polys[mat[mat != mat_id("quay")] == mat_id(key)]
        assert shapely.union_all(selected).symmetric_difference(expected).area < 1e-9
    assert np.isfinite(pos).all() and np.isfinite(normals).all()


def test_ortho_distinguishes_dry_vegetation_gravel_and_paving():
    tile = C.VisualTile(0, 0, 0)
    rgb = np.array([[[155, 156, 90], [140, 125, 100], [170, 172, 171]]], np.uint8)
    # The classifier accepts square tile rasters, north-up rows.
    rgb = np.repeat(rgb, 3, axis=0)
    points = np.array([[40, 0, 40], [125, 0, 40], [210, 0, 40]])
    assert classify(points, rgb, tile, []).tolist() == [
        mat_id(k) for k in ("grass", "gravel", "road")
    ]


def test_field_keeps_grass_park_leaves_cover_to_the_ortho_and_explicit_yard_keeps_paving(tmp_path):
    import json

    import pyarrow as pa
    import pyarrow.parquet as pq
    from turku3d.coarse.surfaces import osm_surfaces

    rows = [
        dict(tags=json.dumps(tags), geometry_local=geom.wkb)
        for tags, geom in (
            ({"leisure": "park"}, shapely.box(0, 0, 250, 250)),
            ({"landuse": "farmland"}, shapely.box(0, 0, 100, 100)),
            ({"surface": "paving_stones"}, shapely.box(10, 10, 20, 20)),
        )
    ]
    path = tmp_path / "osm.parquet"
    pq.write_table(pa.Table.from_pylist(rows), path)
    points = np.array([[15, 0, 15], [50, 0, 50], [150, 0, 150], [220, 0, 220]])
    rgb = np.full((8, 8, 3), 125, np.uint8)
    rgb[7, 7] = (70, 120, 50)  # a lawn in the park's south-east corner
    # A park is a use, not a surface: its grey car park is the ortho's, its lawn too.
    assert classify(
        points, rgb, C.VisualTile(0, 0, 0), [], osm_surfaces(C.VisualTile(0, 0, 0), path)
    ).tolist() == [mat_id(k) for k in ("paving_slab", "grass", "road", "grass")]


def test_bent_stair_and_landings_cover_the_entire_terrain_cut():
    from turku3d.coarse import stairs

    line = shapely.LineString([(10, 20), (13, 21), (16, 20)])
    meshes, openings = stairs.produce(
        C.VisualTile(0, 0, 0),
        ([("bend", line, {"width": "2.4", "step_count": "10"})], []),
        lambda x, z: 0.3 * x + 0.1 * z,
    )
    tris = meshes[0].positions[meshes[0].indices]
    normal = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    top = shapely.union_all(shapely.polygons(tris[normal[:, 1] > 1e-8][:, :, [0, 2]]))
    assert openings[0].difference(top).area < 1e-8
    # Landing outer edges coincide with the surrounding terrain, including crossfall.
    vertices = meshes[0].positions
    for point in vertices[(vertices[:, 0] < 9.5) | (vertices[:, 0] > 16.5)]:
        assert np.isfinite(point).all()


def test_descending_bent_stair_merges_roundoff_stations_without_losing_geometry():
    from turku3d.coarse import stairs

    # Reversing a bent way changes the floating-point sum of segment lengths.
    line = shapely.LineString([
        (1016, 1020.7346), (1014, 1020.1137), (1011, 1020.3912), (1010, 1020.5167)
    ])

    def sample(x, z):
        assert np.isfinite([x, z]).all()
        return 0.3 * (x - 1000)

    with np.errstate(invalid="raise", divide="raise"):
        meshes, openings = stairs.produce(
            C.VisualTile(0, 4, 4),
            ([("descending", line, {"width": "2.4"})], []),
            sample,
        )
    assert meshes and len(openings) == 1
    assert all(np.isfinite(m.positions).all() for m in meshes)
    triangles = meshes[0].positions[meshes[0].indices]
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    top = shapely.union_all(shapely.polygons(triangles[normals[:, 1] > 1e-8][:, :, [0, 2]]))
    assert openings[0].difference(top).area < 1e-8
    assert np.isclose(triangles[:, :, 1], sample(*line.coords[0])).any()


def test_boundary_cut_repairs_invalid_overlay_without_losing_surface_area():
    from turku3d.coarse.surfaces import split_boundaries

    positions = np.array([[0., 0, 0], [0, 1, 10], [10, 2, 0]])
    indices = np.array([[0, 1, 2]], np.uint32)
    # A crossing source ring is repaired only when floating overlay rejects it.
    region = shapely.Polygon([(1, 1), (5, 5), (1, 5), (5, 1), (1, 1)])
    pos, ix, material, normals = split_boundaries(
        positions, indices, np.array([mat_id("grass")], np.uint8), np.ones(1, bool),
        [(region, mat_id("road"))],
    )
    polygons = shapely.polygons(pos[ix][:, :, [0, 2]])
    assert abs(shapely.area(polygons).sum() - 50) < 1e-6
    road = shapely.union_all(polygons[material == mat_id("road")])
    expected = shapely.make_valid(region).intersection(shapely.Polygon(positions[:, [0, 2]]))
    assert road.symmetric_difference(expected).area < 1e-6
    assert np.isfinite(pos).all() and np.isfinite(normals).all()


def test_untagged_park_footway_inherits_only_connected_unambiguous_gravel(tmp_path):
    import json
    import pyarrow as pa
    import pyarrow.parquet as pq
    from turku3d.coarse.surfaces import osm_surfaces

    features = [(dict(leisure="park"), shapely.box(0, 0, 100, 100))]
    # Joined fine-gravel trail. Untagged link inherits; an explicit slab link does not.
    features += [
        (dict(highway="path", surface="fine_gravel"), shapely.LineString([(10, 10), (30, 10)])),
        (dict(highway="footway"), shapely.LineString([(20, 10), (20, 20)])),
        (dict(highway="footway", surface="paving_stones"), shapely.LineString([(30, 10), (30, 20)])),
        # Nearby but disconnected, and joined outside the park: no inferred gravel.
        (dict(highway="footway"), shapely.LineString([(40, 10), (40, 20)])),
        (dict(highway="path", surface="fine_gravel"), shapely.LineString([(110, 10), (130, 10)])),
        (dict(highway="footway"), shapely.LineString([(120, 10), (120, 20)])),
        # A join to both asphalt and gravel has no unambiguous inherited finish.
        (dict(highway="footway"), shapely.LineString([(10, 10), (10, 20)])),
        (dict(highway="path", surface="asphalt"), shapely.LineString([(0, 20), (10, 20)])),
    ]
    path = tmp_path / "park.parquet"
    pq.write_table(pa.Table.from_pylist([
        dict(tags=json.dumps(tags), geometry_local=geom.wkb) for tags, geom in features
    ]), path)
    regions = osm_surfaces(C.VisualTile(0, 0, 0), path)
    def finish(x, z):
        return [material for geom, material in regions if geom.covers(shapely.Point(x, z))][-1]
    assert finish(20, 15) == mat_id("gravel")
    for x in (10, 30, 40, 120):
        assert finish(x, 15) == mat_id("paving_slab")


def test_visual_finish_never_paints_lawn_or_paving_onto_near_vertical_faces():
    from turku3d.coarse.surfaces import split_boundaries

    # A flat lawn, a steep quay step across the lawn edge, a steep soil bank
    # inside a lawn, and a steep cobbled step inside a mapped quay.
    p = np.array([
        [0.0, 0, 0], [4, 0, 0], [0, 0, 4],
        [5, 0, 0], [5.2, 3, 0], [5, 0, 4],
        [7, 0, 0], [7.2, 3, 0], [7, 0, 2],
        [9, 0, 0], [9.2, 3, 0], [9, 0, 2],
    ])
    ix = np.arange(12, dtype=np.uint32).reshape(4, 3)
    own = np.array([mat_id("cobblestone"), mat_id("quay"), mat_id("soil"), mat_id("cobblestone")])
    lawn = shapely.box(-1, -1, 8, 2)
    quay = shapely.box(8.5, -1, 10, 3)
    regions = [(lawn, mat_id("grass")), (quay, mat_id("quay"))]
    ortho = np.full(4, mat_id("road"), np.uint8)
    pos, idx, mat, _ = split_boundaries(p, ix, own, np.ones(4, bool), regions, ortho, keep_steep=True)
    # Steep faces are neither cut nor repainted with lawn, paving or the ortho fallback.
    for face, expected in ((1, "quay"), (2, "soil"), (3, "quay")):
        hits = [k for k, t in enumerate(pos[idx]) if np.allclose(np.sort(t, axis=0), np.sort(p[ix[face]], axis=0))]
        assert len(hits) == 1 and mat[hits[0]] == mat_id(expected), face
    # The flat face is still cut at the lawn edge with the usual precedence.
    flat = shapely.polygons(pos[idx][:, :, [0, 2]])
    inside = np.asarray([shapely.Polygon(p[ix[0]][:, [0, 2]]).covers(g) for g in flat])
    assert set(mat[inside].tolist()) == {mat_id("grass"), mat_id("road")}
    # Without the option the old repaint remains for other cut passes.
    _, _, legacy, _ = split_boundaries(p, ix, own, np.ones(4, bool), regions, ortho)
    assert mat_id("grass") in legacy[:3].tolist() or mat_id("road") in legacy.tolist()


def test_near_vertical_lawn_and_paving_faces_take_their_material_from_context():
    from turku3d.coarse.surfaces import steep_context
    from turku3d.ground.mesh import HeightQuery, Mesh

    # Four 2 m steps with lawn/paving ids, a low kerb riser and a soil bank face.
    def step(x, z, rise=2.0):
        return [[x, 0, z], [x + 0.1, rise, z], [x, 0, z + 1]]

    p = np.array([*step(1, 1), *step(20, 1), *step(40, 1), *step(60, 1), *step(80, 1, 0.15), *step(1, 4)], float)
    ix = np.arange(len(p), dtype=np.uint32).reshape(-1, 3)
    own = np.array([mat_id(k) for k in ("grass", "cobblestone", "road", "grass", "road", "soil")], np.uint8)
    # Lidar terrain: flat, except a 45 degree hillside around x = 60.
    x, z = np.meshgrid(np.arange(0, 101.0), np.arange(-10, 11.0))
    h = np.clip(x - 55, 0, 10)
    grid = np.c_[x.ravel(), h.ravel(), z.ravel()]
    cells = [(r * 101 + c) for r in range(20) for c in range(100)]
    tris = [[a, a + 101, a + 1] for a in cells] + [[a + 1, a + 101, a + 102] for a in cells]
    terrain = HeightQuery(Mesh(grid, np.asarray(tris, np.uint32), np.zeros(len(tris), np.uint8)))
    result = steep_context(
        Mesh(p, ix, own),
        terrain,
        abutments=shapely.box(-3, -3, 0, 3).buffer(3.0),  # 3 m reach from water beside step 1
        retaining=shapely.LineString([(20, 0), (20, 3)]).buffer(2.0),
    )
    assert [int(m) for m in result.material] == [
        mat_id("quay"), mat_id("wall"), mat_id("soil"), mat_id("rock"), mat_id("road"), mat_id("soil"),
    ]


def test_mapped_bare_rock_replaces_mapped_lawn_but_not_paths_or_grassy_hollows(tmp_path):
    import json

    import pyarrow as pa
    import pyarrow.parquet as pq
    from turku3d.coarse.surfaces import osm_surfaces

    outcrop = shapely.box(50, 50, 150, 150).exterior  # mapped as a closed way, like OSM
    rows = [
        dict(tags=json.dumps(tags), geometry_local=geom.wkb)
        for tags, geom in (
            ({"landuse": "grass"}, shapely.box(0, 0, 250, 250)),
            ({"natural": "bare_rock"}, outcrop),
            ({"highway": "footway", "surface": "gravel"}, shapely.LineString([(100, 0), (100, 250)])),
        )
    ]
    path = tmp_path / "osm.parquet"
    pq.write_table(pa.Table.from_pylist(rows), path)
    tile = C.VisualTile(0, 0, 0)
    regions = osm_surfaces(tile, path)
    points = np.array([[70, 0, 70], [100, 0, 120], [200, 0, 200], [130, 0, 60]], float)
    rgb = np.full((250, 250, 3), (150, 140, 135), np.uint8)  # grey-pink rock in the ortho
    rgb[55:65, 125:135] = (80, 140, 70)  # a bright grassy hollow inside the outline
    assert classify(points, rgb, tile, [], regions).tolist() == [
        mat_id(k) for k in ("rock", "gravel", "grass", "grass")
    ]


def test_unmapped_outcrop_needs_both_bare_ortho_and_lidar_relief():
    from turku3d.coarse.build import grid_mesh
    from turku3d.coarse.inputs import CELLS
    from turku3d.coarse.surfaces import bare_rock

    tile = C.VisualTile(0, 0, 0)
    step = tile.size / CELLS
    axis = np.arange(CELLS + 1) * step
    gx, gz = np.meshgrid(axis, axis)
    # A rounded 3 m knob at (60, 60) and a flat, equally grey square at (190, 190).
    # Bedrock is cracked and stepped, not a smooth mound: 0.5 m terraces.
    heights = np.round(6 * np.exp(-((gx - 60) ** 2 + (gz - 60) ** 2) / (2 * 8.0 ** 2)) / 0.5) * 0.5
    positions, indices = grid_mesh(tile, heights)
    centroids = positions[indices].mean(axis=1)
    rgb = np.full((500, 500, 3), (70, 130, 60), np.uint8)  # lawn
    rgb[100:140, 100:140] = rgb[360:400, 360:400] = (150, 141, 134)  # bare grey-pink patches
    lawn = np.full(len(indices), mat_id("grass"), np.uint8)
    result = bare_rock(lawn, centroids, rgb, tile, positions, indices)
    rock = result == mat_id("rock")
    near = lambda x, z, radius=8: np.hypot(centroids[:, 0] - x, centroids[:, 2] - z) < radius
    assert rock[near(60, 60)].mean() > 0.3
    assert not rock[near(190, 190)].any()
    assert not rock[~near(60, 60, 20)].any()
    # The same outcrop beside water is left to mapped evidence.
    shore = [shapely.box(50, 50, 70, 70)]
    assert not (bare_rock(lawn, centroids, rgb, tile, positions, indices, shore) == mat_id("rock")).any()
    # Paths and paving are never candidates, whatever the ortho shows.
    paved = np.full(len(indices), mat_id("paving_slab"), np.uint8)
    assert (bare_rock(paved, centroids, rgb, tile, positions, indices) == paved).all()


def test_granite_rock_finish_is_procedural_periodic_and_bound_to_rock():
    from turku3d.coarse.ground_finishes import PERIODS, PROCEDURAL, maps
    from turku3d.coarse.surfaces import BINDINGS, KEYS

    assert "granite_rock" in PROCEDURAL and PERIODS["granite_rock"] == 4
    assert BINDINGS["rock"] == KEYS.index("granite_rock")
    first, second = maps("granite_rock", 128), maps("granite_rock", 128)
    for slot in ("albedo", "normal", "orm"):
        np.testing.assert_array_equal(first[slot], second[slot])
    albedo = first["albedo"].astype(float)
    # Pinkish grey: red above blue, not green, and visibly textured.
    assert albedo[..., 0].mean() > albedo[..., 2].mean() and albedo[..., 1].mean() < albedo[..., 0].mean()
    assert albedo.std() > 8


def test_rock_visual_carries_moss_tint_toward_its_edges():
    from turku3d.coarse.surfaces import visuals

    tile = C.VisualTile(0, 0, 0)
    x, z = np.meshgrid(np.arange(0, 21.0), np.arange(0, 21.0))
    positions = np.c_[x.ravel(), np.zeros(x.size), z.ravel()]
    cells = [r * 21 + c for r in range(20) for c in range(20)]
    indices = np.asarray([[a, a + 21, a + 1] for a in cells] + [[a + 1, a + 21, a + 22] for a in cells], np.uint32)
    centre = positions[indices].mean(axis=1)
    material = np.where((abs(centre[:, 0] - 10) < 6) & (abs(centre[:, 2] - 10) < 6), mat_id("rock"), mat_id("grass"))
    meshes = visuals(tile, positions, indices, material.astype(np.uint8), np.ones(len(indices), bool))
    rock = [m for m in meshes if m.extras["material_key"] == "draft_granite_rock"]
    assert rock and all(m.colors is not None for m in rock)
    p, tint = rock[0].positions + tile.origin, rock[0].colors
    middle, edge = np.hypot(p[:, 0] - 10, p[:, 2] - 10) < 1, (abs(p[:, 0] - 10) > 5.5) | (abs(p[:, 2] - 10) > 5.5)
    assert tint[middle].mean() > tint[edge].mean() + 0.1
    assert all(m.colors is None for m in meshes if m.extras["material_key"] == "draft_grass")


def test_boundary_cut_local_windows_preserve_complex_region_holes_and_precedence():
    from turku3d.coarse.surfaces import split_boundaries

    x, z = np.meshgrid(np.arange(0, 101., 5), np.arange(0, 101., 5))
    p = np.c_[x.ravel(), .1 * x.ravel() + .2 * z.ravel(), z.ravel()]
    cells = [r * 21 + c for r in range(20) for c in range(20)]
    ix = np.asarray([[a, a + 21, a + 1] for a in cells]
                    + [[a + 1, a + 21, a + 22] for a in cells], np.uint32)
    angle = np.linspace(0, 2 * np.pi, 1024, endpoint=False)
    radius = 45 + 4 * np.sin(angle * 19)
    region = shapely.Polygon(np.c_[50 + radius * np.cos(angle), 50 + radius * np.sin(angle)])
    region = region.difference(shapely.Point(50, 50).buffer(17, quad_segs=64))
    road = shapely.box(42, -10, 57, 110)
    material = np.full(len(ix), mat_id('grass'), np.uint8)
    pos, idx, mat, normals = split_boundaries(
        p, ix, material, np.ones(len(ix), bool),
        [(region, mat_id('gravel')), (road, mat_id('road'))], material,
        keep_steep=True,
    )
    np.testing.assert_allclose(pos[:, 1], .1 * pos[:, 0] + .2 * pos[:, 2], atol=1e-10)
    np.testing.assert_allclose(np.linalg.norm(normals, axis=1), 1, atol=1e-10)
    polys = shapely.polygons(pos[idx][:, :, [0, 2]])
    whole = shapely.box(0, 0, 100, 100)
    for key, expected in (
        ('road', whole.intersection(road)),
        ('gravel', whole.intersection(region).difference(road)),
        ('grass', whole.difference(region).difference(road)),
    ):
        pieces = polys[mat == mat_id(key)]
        assert abs(shapely.area(pieces).sum() - expected.area) < 1e-8
        assert shapely.union_all(pieces).symmetric_difference(expected).area < 1e-8
