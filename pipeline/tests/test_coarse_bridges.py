"""Physical crossing identity, true underpasses and tile ownership on tiny synthetic inputs."""

import numpy as np
import shapely
from turku3d import config as C
from turku3d.coarse.bridges import merge_crossings, passes_under, slab
from turku3d.coarse.fences import place
from turku3d.coarse.geometry import clip_mesh
from turku3d.structures.geometry import profile_wall


def way(key, coords, *, layer=1, bridge=True, width=3):
    return dict(
        id=key,
        line=shapely.LineString(coords),
        layer=layer,
        bridge=bridge,
        width=width,
        tags={"highway": "footway"},
    )


def test_outline_unifies_carriageway_and_sidewalk_without_merging_nearby_crossing():
    road = way("road", [(0, 0), (80, 0)], width=8)
    walk = way("walk", [(0, 5), (80, 5)])
    next_bridge = way("next", [(0, 40), (80, 40)])
    outline = shapely.box(-1, -5, 81, 7)
    decks = merge_crossings(
        [road, walk, next_bridge], [("outline", outline, {"man_made": "bridge"})]
    )
    assert len(decks) == 2
    merged = next(d for d in decks if d["id"] == "outline")
    assert set(merged["members"]) == {"road", "walk"}
    assert merged["outline"].equals(outline)
    assert merged["width"] == 12


def test_parallel_fallback_keeps_transverse_and_separate_level_bridges():
    a = way("a", [(0, 0), (80, 0)])
    b = way("b", [(0, 3), (80, 3)])
    cross = way("cross", [(40, -40), (40, 40)])
    upper = way("upper", [(0, 0), (80, 0)], layer=2)
    decks = merge_crossings([a, b, cross, upper], [])
    assert len(decks) == 3
    assert any(set(d["members"]) == {"a", "b"} for d in decks)


def test_clearance_requires_interior_transverse_crossing_below():
    deck = way("bridge", [(0, 0), (80, 0)])
    assert passes_under(deck, way("road", [(40, -20), (40, 20)], layer=0, bridge=False))
    assert not passes_under(deck, way("approach", [(-20, 0), (0, 0)], layer=0, bridge=False))
    assert not passes_under(deck, way("bank", [(0, -20), (0, 20)], layer=0, bridge=False))
    assert not passes_under(deck, way("parallel", [(0, 1), (80, 1)], layer=0, bridge=False))
    assert not passes_under(deck, way("sidewalk", [(0, 0), (80, 0)], layer=1))
    assert not passes_under(deck, way("upper", [(40, -20), (40, 20)], layer=2))


def test_clipped_neighbour_decks_share_boundary_and_never_emit_full_duplicate():
    mesh = profile_wall("bridge", [[240, 10], [260, 10]], [4, 5], [4.4, 5.4], thickness=3)
    left = clip_mesh(mesh, C.VisualTile(0, 0, 0).bbox)
    right = clip_mesh(mesh, C.VisualTile(0, 1, 0).bbox)
    assert left.positions[:, 0].max() == 250
    assert right.positions[:, 0].min() == 250

    def boundary(m):
        return set(map(tuple, m.positions[m.positions[:, 0] == 250]))

    assert boundary(left) == boundary(right)


def test_deck_outline_leaves_ship_footprint_empty():
    ship = shapely.box(8, -2, 12, 2)
    polygon = shapely.box(0, -4, 20, 4).difference(ship.buffer(0.15))
    axis = shapely.LineString([(0, 0), (20, 0)])
    mesh = slab("landing", polygon, axis, np.array([0, 20]), np.array([2, 2]), "sidewalk")[0]
    for tri in mesh.indices:
        footprint = shapely.Polygon(mesh.positions[tri][:, [0, 2]])
        assert footprint.intersection(ship).area == 0


def test_bank_retaining_edge_is_not_invented_above_ground_and_water_clips_fence():
    tile = C.VisualTile(0, 0, 0)
    line = shapely.LineString([(10, 20), (90, 20)])
    assert place(tile, [(line, {"barrier": "retaining_wall"}, False)], [], lambda x, z: 3) == []
    parapet = place(
        tile, [(line, {"barrier": "retaining_wall", "wall": "parapet"}, False)], [], lambda x, z: 3
    )
    assert all(s.height == 0.5 for s in parapet)
    water = shapely.box(40, 0, 60, 40)
    fence = place(tile, [(line, {"barrier": "fence"}, False)], [], lambda x, z: 3, [water])
    assert fence and all(not 39.65 < s.x < 60.35 for s in fence)


def test_branch_outline_axis_follows_carriageway_not_long_ramp():
    road = way("road", [(0, 0), (80, 0)], width=10)
    ramp = way("ramp", [(40, 0), (45, 90)], width=3)
    outline = shapely.box(-1, -6, 81, 6).union(ramp["line"].buffer(2, cap_style="flat"))
    deck = merge_crossings([road, ramp], [("outline", outline, {"layer": "1"})])[0]
    coords = np.asarray(deck["line"].coords)
    assert abs(coords[-1, 1] - coords[0, 1]) < 0.01
    assert deck["width"] == 12
    assert deck["line"].length == 82
    assert set(deck["members"]) == {"road", "ramp"}


def test_passage_cut_removes_collinear_submillimetre_sliver():
    from turku3d.coarse.build import extrude
    from turku3d.coarse.inputs import Building
    from turku3d.coarse.tunnels import passage_footprint

    polygon = shapely.Polygon([(0, 0), (5, 0), (5, 5), (3.0002, 5.000001), (3, 5), (0, 5)])
    building = Building("passage", passage_footprint(polygon), 0, 5, "facade_plaster")
    roof, indices, *_ = extrude(building)
    triangles = roof.astype(np.float32)[indices]
    area = (
        np.linalg.norm(
            np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1
        )
        / 2
    )
    assert (area >= 1e-6).all()
    assert abs(area.sum() - polygon.area) < 1e-4


def test_underpass_height_does_not_sample_upper_deck_as_railway_ground():
    from turku3d.coarse.bridges import under_height

    deck = way("bridge", [(0, 0), (80, 0)], width=12)
    rail = way("rail", [(40, -40), (40, 40)], layer=0, bridge=False)

    def sample(x, z):
        return 25 if abs(z) < 10 else 10 + z / 100

    assert under_height(deck, rail, shapely.Point(40, 0), sample) == 10


def test_bridge_style_uses_source_structure_material_and_surface():
    from turku3d.coarse.bridge_details import style

    road = way("road", [(0, 0), (80, 0)], width=16)
    road["tags"] = {
        "highway": "secondary",
        "bridge:structure": "arch",
        "material": "stone",
        "surface": "asphalt",
        "bridge:parapet": "stone",
    }
    spec = style(road)
    assert spec["body"] == spec["railing"] == "stone"
    assert spec["surface"] == "asphalt"
    assert spec["thickness"] > 0.4
    road["tags"] = {"railway": "rail", "gauge": "1524"}
    assert style(road)["surface"] == "ballast"


def test_lidar_profile_preserves_crown_and_rejects_sparse_or_steep_support():
    from turku3d.coarse.bridge_survey import measure

    deck = way("deck", [(0, 0), (60, 0)], width=6)
    x, z = np.meshgrid(np.linspace(0, 60, 121), np.linspace(-3, 3, 13))
    h = 5 + 0.6 * np.sin(x * np.pi / 60)
    points = np.c_[x.ravel(), h.ravel(), z.ravel()]
    report = measure(deck, points, np.full(len(points), 12))
    assert report["coverage"] == 1
    assert max(report["heights"]) > 5.55
    assert report["median_error_m"] < 0.03
    assert report["heldout_count"] > 100
    assert "heights" not in measure(deck, points[:5], np.full(5, 12))
    points[:, 1] = points[:, 0]
    assert "heights" not in measure(deck, points, np.full(len(points), 12))


def test_named_crossing_profiles_separate_steel_decks_from_stone_supports():
    from turku3d.coarse.bridge_details import furniture, style

    deck = way("auransilta", [(0, 0), (46, 0)], width=12.6)
    deck["tags"] = {
        "highway": "secondary",
        "bridge:structure": "beam",
        "wikidata": "Q11853512",
    }
    deck["survey"] = {"pier_stations": [23], "pier_bases": [0]}
    spec = style(deck)
    assert spec["body"] == "steel"
    assert spec["support"] == "stone"
    rails = shapely.MultiLineString([[(0, -6.3), (46, -6.3)], [(0, 6.3), (46, 6.3)]])
    meshes = furniture(
        deck,
        rails,
        lambda p: np.full(len(p), 6.0),
        lambda p: np.zeros(len(p)),
        [],
        shapely.box(0, -6.3, 46, 6.3),
    )
    assert {m.metadata["surface"] for m in meshes} == {"stone", "steel", "paint"}
    stone = [m for m in meshes if m.metadata["surface"] == "stone"]
    assert any(np.min(m.positions[:, 1]) < 0 for m in stone)
    # Structural detail cannot protrude into the surveyed driving surface.
    steel = [m for m in meshes if m.metadata["surface"] == "steel"]
    assert all(m.positions[:, 1].max() <= 6.14 + 1e-9 for m in steel)
    deck["tags"] = {"highway": "secondary", "bridge:structure": "arch", "wikidata": "Q11898665"}
    assert style(deck)["arch_edge"]
    assert style(deck)["body"] == "concrete"
    for identity in ("Q11871663", None):
        deck["tags"]["wikidata"] = identity
        assert not style(deck)["arch_edge"]
        assert style(deck)["support"] == "concrete"


def test_railing_is_open_geometry_with_blocking_balusters_and_batched_materials():
    from turku3d.coarse.bridge_details import furniture

    deck = way("deck", [(0, 0), (20, 0)], width=6)
    rails = shapely.MultiLineString([[(0, -3), (20, -3)], [(0, 3), (20, 3)]])
    meshes = furniture(
        deck,
        rails,
        lambda p: np.full(len(p), 5.0),
        lambda p: np.zeros(len(p)),
        [],
        shapely.box(0, -3, 20, 3),
    )
    assert len(meshes) <= 3
    metal = next(m for m in meshes if m.metadata["surface"] == "steel")
    assert metal.metadata["collision"]
    assert metal.positions[:, 1].max() > 6.2
    # No 1.2 m solid wall masquerading as an open metal railing.
    triangles = metal.positions[metal.indices]
    assert np.max(np.ptp(triangles[:, :, 0], axis=1) * np.ptp(triangles[:, :, 1], axis=1)) < 0.2


def test_bridge_slab_separates_surface_from_structural_thickness():
    mesh = slab(
        "deck",
        shapely.box(0, -3, 20, 3),
        shapely.LineString([(0, 0), (20, 0)]),
        np.array([0, 20]),
        np.array([5, 5]),
        "road",
        thickness=0.85,
    )
    assert len(mesh) == 2
    assert np.all(mesh[0].positions[:, 1] == 5)
    assert mesh[1].positions[:, 1].min() == 4.15
    assert mesh[1].material == "wall"


def test_crown_station_cuts_do_not_add_paired_internal_bulkheads():
    _, body = slab(
        "deck",
        shapely.box(0, -3, 20, 3),
        shapely.LineString([(0, 0), (20, 0)]),
        np.array([0, 5, 10, 15, 20]),
        np.array([5, 5.3, 5.5, 5.3, 5]),
        "road",
    )
    triangles = body.positions[body.indices]
    vertical = np.ptp(triangles[:, :, 1], axis=1) > 0.1
    # Only the two physical ends may span the width of this straight slab.
    across = vertical & (np.ptp(triangles[:, :, 0], axis=1) == 0)
    assert set(triangles[across, :, 0].ravel()) == {0, 20}
    # Crown stations still break the longitudinal outer walls at their measured heights.
    assert any(np.allclose(p, [10, 5.5, 3]) for p in body.positions)


def test_crown_cuts_keep_vertical_walls_around_physical_openings():
    _, body = slab(
        "deck",
        shapely.box(0, -3, 20, 3).difference(shapely.box(8, -1, 12, 1)),
        shapely.LineString([(0, 0), (20, 0)]),
        np.array([0, 5, 10, 15, 20]),
        np.full(5, 5.0),
        "road",
    )
    triangles = body.positions[body.indices]
    across = (np.ptp(triangles[:, :, 1], axis=1) > 0.1) & (np.ptp(triangles[:, :, 0], axis=1) == 0)
    assert set(triangles[across, :, 0].ravel()) == {0, 8, 12, 20}


def test_slab_ignores_line_remainders_from_outline_clipping():
    polygon = shapely.GeometryCollection(
        [shapely.box(0, -3, 20, 3), shapely.LineString([(0, -3), (-2, -3)])]
    )
    top, _ = slab(
        "deck",
        polygon,
        shapely.LineString([(0, 0), (20, 0)]),
        np.array([0, 10, 20]),
        np.full(3, 5.0),
        "road",
    )
    triangles = top.positions[top.indices]
    area = (
        np.linalg.norm(
            np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1
        ).sum()
        / 2
    )
    assert np.isclose(area, 120)
    assert top.positions[:, 0].min() == 0


def test_spot_anchors_exclude_old_points_lower_routes_and_parapets():
    from turku3d.coarse.bridge_survey import height_anchors

    deck = way("new", [(0, 0), (100, 0)], width=8)
    deck["tags"]["construction_year"] = "2024"
    deck["under"] = [way("lower", [(30, -20), (30, 20)], layer=0, bridge=False)]
    points = [
        (shapely.Point(50, 0), 9, 2025),
        (shapely.Point(60, 0), 8, 2021),
        (shapely.Point(30, 0), 3, 2025),
        (shapely.Point(70, 3.8), 10, 2025),
        (shapely.Point(5, 0), 7, 2025),
    ]
    assert height_anchors(deck, points) == [(50, 9)]
    assert height_anchors(deck, [*points, (shapely.Point(50, 0), 12, 2025)]) == []


def test_spot_profile_preserves_approaches_and_rejects_impossible_grades():
    from turku3d.coarse.bridge_survey import anchored_profile

    stations = np.array([0, 25, 50, 75, 100])
    profile = anchored_profile(stations, np.array([8, 8, 8, 8, 8]), [(50, 9)])
    assert np.allclose(profile, [8, 8.5, 9, 8.5, 8])
    assert anchored_profile(stations, np.full(5, 8), [(50, 30)]) is None
    assert anchored_profile(stations, np.full(5, 8), []) is None


def test_height_points_on_water_below_a_bridge_are_not_deck_anchors():
    from turku3d.coarse.bridge_survey import above_ground_anchors

    axis = shapely.LineString([(0, 0), (100, 0)])
    assert above_ground_anchors(axis, [(30, 0.19), (60, 9.1)], lambda x, z: 0.2) == [(60, 9.1)]


def test_approach_join_reaches_mapped_road_level_on_each_bank_independently():
    from turku3d.coarse.bridges import approach_lengths, extended

    deck = way("deck", [(0, 0), (20, 0)], width=8)
    deck["approaches"] = [
        way("left", [(-40, 0), (0, 0)], bridge=False, width=8),
        way("right", [(20, 0), (60, 0)], bridge=False, width=8),
    ]

    def ground(x, z):
        return -1 if -10 <= x < 0 else 1

    distances = approach_lengths(deck, [1, 1], ground, shapely.GeometryCollection())
    assert distances == (12, 2)
    line = extended(deck["line"], distances)
    assert line.coords[0] == (-12, 0)
    assert line.coords[-1] == (22, 0)
    # Unmapped land and a barrier cannot become a speculative long approach.
    deck["approaches"] = []
    assert approach_lengths(deck, [1, 1], ground, shapely.GeometryCollection()) == (2, 2)
    deck["approaches"] = [way("left", [(-40, 0), (0, 0)], bridge=False, width=8)]
    assert approach_lengths(deck, [1, 1], ground, shapely.box(-40, -5, -6, 5)) == (2, 2)
    assert approach_lengths(deck, [1, 1], lambda x, z: -20, shapely.GeometryCollection()) == (2, 2)


def test_join_sampler_matches_ground_tin_across_visual_tile_seam():
    from turku3d.coarse.bridge_join import terrain_sampler
    from turku3d.coarse.build import grid_height, grid_mesh
    from turku3d.coarse.inputs import grid_axes, grid_heights

    # A sparse bank keypoint is high while the interpolated road triangle is low.
    points = np.array([[230, 1, -20], [270, 7, -20], [230, 3, 40], [270, 4, 40], [252, -1, 8]])
    sample = terrain_sampler(points)
    for tile in (C.VisualTile(0, 0, 0), C.VisualTile(0, 1, 0)):
        pos, _ = grid_mesh(tile, grid_heights(points, *grid_axes(tile)))
        for x in (249.5, 250, 250.5):
            if tile.bbox.xmin <= x <= tile.bbox.xmax:
                assert abs(sample(x, 7) - grid_height(tile, pos, x, 7)) < 1e-10


def test_join_does_not_end_on_a_bank_lip_before_a_depression():
    from turku3d.coarse.bridges import approach_lengths

    deck = way("deck", [(0, 0), (40, 0)], width=8)
    deck["approaches"] = [way("road", [(40, 0), (70, 0)], bridge=False, width=8)]

    def ground(x, z):
        return 6 if x < 45 or x >= 52 else 4

    assert approach_lengths(deck, [6, 6], ground, shapely.GeometryCollection())[1] == 12


def test_cathedral_arch_has_open_spandrels_between_rib_and_deck():
    from turku3d.coarse.bridge_details import furniture

    deck = way("cathedral", [(0, 0), (48, 0)], width=25.9)
    deck["tags"] = {"highway": "secondary", "bridge:structure": "arch", "wikidata": "Q11898665"}
    meshes = furniture(
        deck,
        shapely.GeometryCollection(),
        lambda p: np.full(len(p), 7.2),
        lambda p: np.zeros(len(p)),
        [],
        shapely.box(0, -13, 48, 13),
    )
    concrete = next(m for m in meshes if m.metadata["surface"] == "concrete")
    # At x=11 (between posts), a transverse ray through the spandrel is clear.
    # Project each structural triangle onto X/Y and test the ray's section point.
    triangles = concrete.positions[concrete.indices]
    point = shapely.Point(11, 6.1)
    assert not any(shapely.Polygon(t[:, :2]).buffer(1e-8).contains(point) for t in triangles)
    assert concrete.positions[:, 1].min() < 3.5


def test_skew_abutment_fills_wedge_on_mapped_continuation(monkeypatch):
    from types import SimpleNamespace

    from turku3d.coarse.bridges import produce

    monkeypatch.setattr("turku3d.coarse.tunnels.ground_sampler", lambda fallback: lambda x, z: 6)
    deck = way("skew", [(20, 20), (60, 20)], width=10)
    deck.update(
        outline=shapely.Polygon([(18, 15), (58, 15), (62, 25), (22, 25)]),
        approaches=[way("east", [(58, 17), (80, 17)], bridge=False, width=6)],
        under=[],
        survey={"stations": [0, 40], "heights": [6, 6]},
    )
    inputs = SimpleNamespace(
        points=np.array([[0, 6, 0], [100, 6, 0], [0, 6, 100], [100, 6, 100]]),
        roads={"bridge_ways": [deck]},
        buildings=[],
    )
    result = produce(C.VisualTile(0, 0, 0), np.empty((0, 3)), inputs)[0]
    top = result.meshes[0]
    footprint = shapely.union_all(shapely.polygons(top.positions[top.indices][:, :, [0, 2]]))
    assert footprint.covers(shapely.Point(59.5, 17))
    assert not deck["outline"].covers(shapely.Point(59.5, 17))


def test_deck_pavement_and_paint_do_not_make_extra_collision_slabs():
    from turku3d.coarse.bridge_details import surfaces

    road = way("road", [(0, 0), (30, 0)], width=8)
    road["tags"] = {"highway": "secondary", "lanes": "2", "surface": "asphalt"}
    walk = way("walk", [(0, 5), (30, 5)], width=3)
    road["member_ways"] = [road.copy(), walk]
    meshes = surfaces(road, shapely.box(0, -5, 30, 7), lambda p: np.full(len(p), 5.0))
    paint = [m for m in meshes if m.metadata["surface"] == "paint"]
    assert paint and all(not m.metadata["collision"] for m in paint)
    pavement = [m for m in meshes if m.material == "sidewalk"]
    assert pavement and max(p.positions[:, 1].max() for p in pavement) == 5.14


def test_bridge_context_building_welds_micrometre_wall_edges_before_export():
    from turku3d.coarse.build import extrude
    from turku3d.coarse.inputs import Building

    polygon = shapely.Polygon(
        [(690, -30), (696.889, -30), (696.889002, -29.999999), (696.889, -21.117), (690, -21.117)]
    )
    b = Building("bridge-bank", polygon, 7.947, 17.879, "facade_plaster")
    roof, ri, floor, fi, wall, _, wi = extrude(b)
    for points, indices in ((roof, ri), (floor, fi), (wall, wi)):
        tri = points.astype(np.float32)[indices]
        area = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1) / 2
        assert np.all(area >= 1e-6)


def test_lidar_piers_require_vertical_returns_and_ignore_bank_walls():
    from turku3d.coarse.bridge_survey import pier_stations

    axis = shapely.LineString([(0, 0), (60, 0)])
    x, y, z = np.meshgrid([2, 12, 48], np.linspace(1.2, 1.55, 12), [-2, 2])
    points = np.c_[x.ravel(), y.ravel(), z.ravel()]
    assert pier_stations(axis, 6, points, np.full(len(points), 10), [0, 60], [5, 5]) == [12, 48]
    points[:, 1] = 2
    assert pier_stations(axis, 6, points, np.full(len(points), 10), [0, 60], [5, 5]) == []


def test_central_bridge_bays_stay_level_while_mapped_branches_can_descend():
    from turku3d.coarse.bridges import approach_branches

    deck = way("deck", [(0, 0), (60, 0)], width=6)
    deck["member_ways"] = [deck.copy()]
    assert approach_branches(deck).is_empty
    ramp = way("ramp", [(20, 0), (20, 30)], width=3)
    deck["member_ways"].append(ramp)
    branches = approach_branches(deck)
    assert branches.covers(shapely.Point(20, 20))
    assert not branches.covers(shapely.Point(40, 5))


def test_detailed_bridge_collision_is_clipped_instead_of_repeated_in_every_cell():
    from types import SimpleNamespace

    from turku3d.coarse.bridges import collision

    tile = C.VisualTile(0, 0, 0)
    rail = profile_wall("rail", [[10, 10], [130, 10]], [4, 5], [5.2, 6.2], thickness=0.1)
    empty = np.empty((0, 3))
    left, right = [], []
    for ct, boundary in [(C.CollisionTile(0, 0), left), (C.CollisionTile(1, 0), right)]:
        p, idx, _ = collision(
            [SimpleNamespace(meshes=[rail])],
            tile,
            ct,
            empty,
            empty.astype(np.uint32),
            np.empty(0, np.uint8),
        )
        assert p[:, 0].min() >= ct.bbox.xmin and p[:, 0].max() <= ct.bbox.xmax
        assert len(idx)
        boundary.extend(map(tuple, p[p[:, 0] == 62.5]))
    assert set(left) == set(right)
