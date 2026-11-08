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


def test_surveyed_deck_outline_spans_past_a_short_osm_bridge_way():
    # Sleutelbrug, Amsterdam: OSM way and man_made=bridge outline 426-438 m; BGT deck and water 420-441 m.
    from turku3d.coarse.bridges import add_surveyed

    road = way("road", [(426, 559), (438, 559)], width=6)
    osm = shapely.box(426, 552, 438, 562)
    deck = shapely.box(420, 552, 441, 562)
    window = shapely.box(0, 0, 1000, 1000)
    outlines = add_surveyed({"74079560": ("74079560", osm, {"man_made": "bridge", "name": "Sleutelbrug"})},
                            [("bgt-deck:430:557", deck, {"layer": "1"})], window)
    assert list(outlines) == ["74079560"] and outlines["74079560"][1].equals(deck)
    merged = merge_crossings([road], list(outlines.values()))
    assert len(merged) == 1 and merged[0]["members"] == ["road"]
    assert merged[0]["line"].length >= 20.9  # the whole surveyed span, abutment to abutment
    assert merged[0]["tags"]["name"] == "Sleutelbrug"
    # A surveyed deck with no OSM outline is added as its own.
    alone = add_surveyed({}, [("bgt-deck:9:9", shapely.box(0, 0, 20, 8), {"layer": "1"})], window)
    assert list(alone) == ["bgt-deck:9:9"]


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


def test_indoor_corridor_below_a_roof_driveway_asks_no_headroom():
    deck = way("driveway", [(0, 0), (20, 0)])
    corridor = way("corridor", [(10, -5), (10, 5)], layer=0, bridge=False)
    assert passes_under(deck, corridor)  # an ordinary footway would need headroom
    corridor["tags"] = {"highway": "corridor", "indoor": "yes"}
    assert not passes_under(deck, corridor)
    corridor["tags"] = {"highway": "footway", "indoor": "yes"}
    assert not passes_under(deck, corridor)


def test_a_way_in_a_tunnel_below_ground_asks_no_headroom():
    """Tapiola: Merituulentie's short bridge over a cycleway underpass also crosses Tapiola Park's access tunnel
    (highway=service, tunnel=yes, layer -2); its road headroom over the street lifted the deck 3-5 m."""
    deck = way("merituulentie", [(0, 0), (15, 0)])
    lower = way("tunnel", [(7, -30), (7, 30)], layer=-2, bridge=False)
    for tags in ({"highway": "service", "tunnel": "yes", "layer": "-2"}, {"highway": "service", "tunnel": "yes", "level": "-1"}):
        lower["tags"] = tags
        assert not passes_under(deck, lower)
    # A way at grade under the deck, or under a canopy, still asks its headroom.
    for tags in ({"highway": "cycleway"}, {"highway": "service", "tunnel": "covered", "layer": "-1"}):
        lower["tags"] = tags
        assert passes_under(deck, lower)


def test_abutments_read_no_lower_than_their_approach_and_paths_their_own_level():
    """Merituulentie over the Tuuliniitynpolku underpass: the east end of the OSM bridge lies on the rim of the
    cutting (6.7 m) while its approach is at 8.4-8.6 m; the path's headroom was read on the road fill beside it."""
    import numpy as np
    from turku3d.coarse.bridges import abutment_heights, under_point

    axis = shapely.LineString([(0, 0), (12, 0)])

    def ground(xz):  # the east end on the rim of a 5.6 m cutting, 8.5 m approach east of it, 9.0 m west
        return np.interp(np.asarray(xz)[:, 0], [-1, 0, 3, 7, 12], [8.5, 6.7, 5.6, 5.6, 9.0])

    assert ground(np.array([[0.0, 0.0]]))[0] == 6.7
    east, west = abutment_heights(axis, ground)
    assert east == 8.5 and west == 9.0  # the approach, not the cutting rim
    # A raised end on a wall stays as measured.
    assert abutment_heights(axis, lambda xz: np.where(np.asarray(xz)[:, 0] == 0, 10.0, 8.5))[0] == 10.0
    path = shapely.LineString([(5, -8), (5, 8)])
    assert under_point(path, (6.5, 0)).equals(shapely.Point(5, 0))


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


def test_a_deck_is_built_by_every_tile_its_member_ways_reach_not_only_its_axis():
    # Willemsbrug, Rotterdam: the merged deck's curved north approach ran 60 m past its straight
    # axis into a tile that did not build the deck, so the approach fell to the ground at the seam.
    from turku3d.coarse.bridges import building_region, selects

    size = C.VISUAL_TILE_SIZE

    def box(i, j):
        return (i * size, j * size, (i + 1) * size, (j + 1) * size)

    road = way("road", [(300, 100), (480, 100)], width=10)
    ramp = way("ramp", [(320, 100), (330, 280)], width=4)
    outline = shapely.box(299, 94, 481, 106).union(ramp["line"].buffer(3, cap_style="flat"))
    deck = merge_crossings([road, ramp], [("outline", outline, {"layer": "1"})])[0]
    assert deck["line"].distance(shapely.box(*box(1, 1))) > deck["width"] / 2 + 5  # the axis alone misses it
    assert selects(deck, box(1, 0)) and selects(deck, box(1, 1))
    assert not selects(deck, box(0, 1)) and not selects(deck, box(2, 1)) and not selects(deck, box(1, 2))
    assert building_region(deck).equals(shapely.union_all([shapely.box(*box(1, 0)), shapely.box(*box(1, 1))]))
    # A single way without an outline keeps its band.
    lone = way("lone", [(300, 243), (480, 243)], width=10)
    assert selects(lone, box(1, 1)) and not selects(lone, box(1, 2))
    assert building_region(lone).equals(shapely.union_all([shapely.box(*box(1, 0)), shapely.box(*box(1, 1))]))
    # De Luchtsingel: an outline spanning a winding bridge, whose other ways are decks of their own on
    # other layers, does not make every tile it crosses build the one member it carries.
    walk = way("walk", [(300, 180), (300, 220)], width=3)
    winding = shapely.box(298, 175, 302, 225).union(shapely.box(302, 220, 420, 400))
    short = merge_crossings([walk], [("winding", winding, {"layer": "1"})])[0]
    assert selects(short, box(1, 0)) and not selects(short, box(1, 1))


def test_deck_axis_follows_the_carriageway_carrying_most_of_the_deck_not_a_short_wide_end():
    # Erasmusbrug, Rotterdam: the widest member, a 124 m way where the carriageways meet at the north
    # abutment, set a 194 m axis on the 650 m span; the rest of the deck lay past the axis end.
    def road(key, coords, width):
        return {**way(key, coords, width=width), "tags": {"highway": "secondary"}}

    north = road("north", [(300, 0), (0, 0)], 7.5)
    south = road("south", [(0, 8), (300, 8)], 7.5)
    stub = road("stub", [(0, 4), (-30, -40)], 14)
    walk = way("walk", [(0, 12), (300, 12)], width=10)  # a wider path never steers a road deck's axis
    outline = shapely.box(-1, -5, 301, 13).union(stub["line"].buffer(7, cap_style="flat"))
    deck = merge_crossings([north, south, stub, walk], [("outline", outline, {"layer": "1"})])[0]
    coords = np.asarray(deck["line"].coords)
    assert abs(coords[-1, 1] - coords[0, 1]) < 0.01 and deck["line"].length > 300
    assert deck["width"] == 18 and deck["tags"]["highway"] == "secondary"
    assert set(deck["members"]) == {"north", "south", "stub", "walk"}
    # Kolkswaterkering, Amsterdam: on a short crossing a longer diagonal way does not turn the axis.
    bus, side = road("bus", [(0, 0), (0, 6)], 7.5), road("side", [(10, 0), (10, 6)], 7.5)
    diagonal = road("diagonal", [(20, 0), (28, 6.5)], 7.5)
    path = way("path", [(31, 0), (31, 6)])
    sluice = shapely.box(-4, 0, 34, 6.4)
    deck = merge_crossings([bus, side, diagonal, path], [("sluice", sluice, {"layer": "1"})])[0]
    coords = np.asarray(deck["line"].coords)
    assert abs(coords[-1, 0] - coords[0, 0]) < 0.01 and deck["width"] == 38


def test_a_main_carriageway_that_curves_out_at_one_end_is_not_a_descending_link():
    # Erasmusbrug: the southbound lanes leave the deck band only on their approach curve; as a link
    # they rode the whole span flat at one end's height, 9 m below the northbound lanes.
    from turku3d.coarse.bridges import branch_profiles

    def road(key, coords, width=7.5):
        return {**way(key, coords, width=width), "tags": {"highway": "secondary"}}

    north = road("north", [(300, -5), (0, -5)])
    south = road("south", [(-40, -40), (0, 5), (300, 5)])
    link = road("link", [(150, 5), (200, 60)], width=4)
    outline = shapely.union_all([shapely.box(-1, -10, 301, 10), south["line"].buffer(4), link["line"].buffer(3)])
    deck = merge_crossings([north, south, link], [("deck", outline, {"layer": "1"})])[0]
    assert set(deck["members"]) == {"north", "south", "link"}
    profiles, _ = branch_profiles(deck, lambda xz: np.full(len(xz), 10.0), lambda xz: np.zeros(len(xz)))
    assert len(profiles) == 1 and profiles[0][0].equals(link["line"])

    # A Turku harbour ramp: its outline curves with the way, so the straight axis is a short stretch the
    # way runs along and then leaves on both ends. Mostly outside, it stays a link: its deck runs on.
    ramp = road("ramp", [(-25, 25), (0, 0), (26, 0), (51, 25)])
    short = merge_crossings([ramp], [("short", ramp["line"].buffer(3.75), {"layer": "1"})])[0]
    inside = ramp["line"].intersection(short["line"].buffer(short["width"] / 2)).length
    assert short["line"].length < 40 and short["line"].length / 2 < inside < ramp["line"].length / 2
    profiles, _ = branch_profiles(short, lambda xz: np.full(len(xz), 10.0), lambda xz: np.zeros(len(xz)))
    assert len(profiles) == 1 and profiles[0][0].length > 90


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
    # Erasmusbrug: the first station reads the street beside the abutment; the measured profile
    # stays, held level at that end.
    beside = points.copy()
    beside[beside[:, 0] < 2.0, 1] = 1.0
    report = measure(deck, beside, np.full(len(beside), 12))
    assert report["coverage"] == 1 and abs(report["heights"][0] - 5.1) < 0.05 and max(report["heights"]) > 5.55
    # A step inside the span still rejects it.
    step = points.copy()
    step[step[:, 0] > 30, 1] += 3.0
    assert "heights" not in measure(deck, step, np.full(len(step), 12))
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
    assert {m.metadata["surface"] for m in meshes} == {"stone", "steel", "paint", "granite", "bronze"}
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


def test_skew_bank_fill_fades_past_both_approach_caps_without_filling_the_span(monkeypatch):
    from types import SimpleNamespace

    from turku3d.coarse.bridges import produce
    from turku3d.coarse.inputs import CELLS

    monkeypatch.setattr("turku3d.coarse.tunnels.ground_sampler", lambda sample: sample)
    deck = way("bridge", [(20, 30), (60, 30)], width=8)
    deck.update(approaches=[], under=[], survey={"stations": [0, 40], "heights": [6, 6]})
    inputs = SimpleNamespace(
        points=np.array([[0, 6, 20], [100, 6, 20], [0, 6, 40], [100, 6, 40]]),
        roads={"bridge_ways": [deck]}, buildings=[],
    )
    # The axis joins the road at 6 m, but the side of each approach is on a
    # lower bank. Check immediately across the old flat caps at x=17 and 63.
    feather = 2 * C.VISUAL_TILE_SIZE / CELLS
    xs = [17 - 1e-5, 17 + 1e-5, 63 - 1e-5, 63 + 1e-5,
          17 - feather / 2, 63 + feather / 2, 17 - feather, 63 + feather, 40]
    positions = np.array([[x, 2.0, 33] for x in xs])
    produce(C.VisualTile(0, 0, 0), positions, inputs)
    np.testing.assert_allclose(positions[:4, 1], 6, atol=2e-5)
    np.testing.assert_allclose(positions[4:6, 1], 4, atol=1e-9)
    np.testing.assert_allclose(positions[6:, 1], 2, atol=1e-9)
    # A mapped lower passage remains open even inside the new end feather.
    deck["approach_under"] = [way("quay", [(15, 25), (15, 35)], bridge=False, width=1)]
    passage = np.array([[15.0, 2, 33]])
    produce(C.VisualTile(0, 0, 0), passage, inputs)
    assert passage[0, 1] == 2
    deck["landing"] = True
    positions[:, 1] = 2
    produce(C.VisualTile(0, 0, 0), positions, inputs)
    np.testing.assert_array_equal(positions[:, 1], 2)


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


def test_deck_pavement_makes_no_extra_collision_slab_and_lane_paint_is_road_paint():
    from turku3d.coarse.bridge_details import surfaces

    road = way("road", [(0, 0), (30, 0)], width=8)
    road["tags"] = {"highway": "secondary", "lanes": "2", "surface": "asphalt"}
    walk = way("walk", [(0, 5), (30, 5)], width=3)
    road["member_ways"] = [road.copy(), walk]
    meshes = surfaces(road, shapely.box(0, -5, 30, 7), lambda p: np.full(len(p), 5.0))
    # Deck lane paint comes from the road graph's rule paint (turku3d.roads.paint), not the deck.
    assert not [m for m in meshes if m.metadata["surface"] == "paint"]
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


def test_combined_deck_rails_exclude_caps_between_carriageway_openings():
    from turku3d.coarse.bridge_details import railing_lines

    left = way("left", [(0, -8), (80, -8)], width=7.5)
    right = way("right", [(80, 8), (0, 8)], width=7.5)
    path = way("path", [(0, 14), (80, 14)])
    deck = {**left, "outline": shapely.box(0, -12, 80, 16), "member_ways": [left, right, path]}
    rails = railing_lines([deck])[0]
    assert rails.length > 150
    for part in shapely.get_parts(rails):
        assert np.ptp(np.asarray(part.coords)[:, 1]) == 0
    assert not rails.intersects(shapely.LineString([(0, -4), (0, 4)]))


def test_adjacent_outlines_hide_inner_rails_but_keep_real_gaps_and_levels():
    from turku3d.coarse.bridge_details import railing_lines

    road = way("road", [(0, 0), (80, 0)], width=8)
    road["outline"] = shapely.box(0, -4, 80, 4)
    walk = way("walk", [(0, 6), (80, 6)], width=3)
    walk["outline"] = shapely.box(0, 4.5, 80, 7.5)
    rails = railing_lines([road, walk])
    assert all(abs(p[1] + 4) < 1e-6 for part in shapely.get_parts(rails[0]) for p in part.coords)
    assert all(abs(p[1] - 7.5) < 1e-6 for part in shapely.get_parts(rails[1]) for p in part.coords)
    walk["line"] = shapely.LineString([(0, 10), (80, 10)])
    walk["outline"] = shapely.box(0, 8.5, 80, 11.5)
    assert len(shapely.get_parts(railing_lines([road, walk])[0])) == 2
    walk["line"] = shapely.LineString([(0, 6), (80, 6)])
    walk["outline"] = shapely.box(0, 4.5, 80, 7.5)
    walk["layer"] = 2
    assert len(shapely.get_parts(railing_lines([road, walk])[0])) == 2


def test_furniture_omits_detached_stub_shorter_than_post_bay():
    from turku3d.coarse.bridge_details import furniture

    deck = way("stub", [(0, 0), (20, 0)], width=6)
    deck["landing"] = True
    assert (
        furniture(
            deck,
            shapely.LineString([(10, 3), (10.7, 3)]),
            lambda p: np.full(len(p), 5),
            lambda p: np.zeros(len(p)),
            [],
            shapely.box(0, -3, 20, 3),
        )
        == []
    )


def test_deck_resting_on_ground_over_nothing_is_a_terrace_with_railings_only_at_drops():
    from turku3d.coarse.bridges import drop_edges, on_grade

    deck = shapely.LineString([(0, 0), (20, 0)]).buffer(3)
    level = np.full(11, 10.2)
    roof, none = shapely.box(4, -2, 16, 10), shapely.Polygon()
    assert on_grade(level, level - 0.1, [], deck, none, roof)
    assert not on_grade(level, level - 0.1, [], deck, none, none)  # no roof: a bridge on banks
    assert not on_grade(level, level - 3, [], deck, none, roof)  # a real span
    assert not on_grade(level, level, [(5, 6, 4.5)], deck, none, roof)  # crosses a way
    assert not on_grade(level, level, [], deck, shapely.box(5, -10, 8, 10), roof)  # over water
    # Terrace at 10.2 on the hill side (z > 0), the street 4 m lower in front (z < -3).
    ground = lambda xz: np.where(np.asarray(xz)[:, 1] < -3.5, 6.0, 10.2)
    height = lambda xz: np.full(len(np.asarray(xz)), 10.2)
    edges = shapely.MultiLineString([[(0, -3), (20, -3)], [(0, 3), (20, 3)]])
    kept = drop_edges(edges, height, ground)
    assert kept.length > 19
    assert shapely.get_coordinates(kept)[:, 1].max() < -2.9  # only the street edge


def test_bridge_slab_of_an_empty_level_draws_nothing():
    """Rotterdam t0_1_1: a deck level cut away entirely left only empty pieces, and the cross-deck cuts took the
    minimum of no coordinates (ValueError), failing the whole tile."""
    line = shapely.LineString([(0, 0), (20, 0)])
    assert slab("empty", [(shapely.Polygon(), None)], line, np.array([0, 10, 20]), np.array([2, 2, 2]), "road") == []


def test_a_deck_is_not_cut_around_its_own_support_or_a_building_under_it():
    # Rotterdam Willemsbrug: the north approach viaduct passes over a 1981 building with no measured shell and the
    # pylon outline (building=service, bridge:support=pylon) spans the carriageway.
    from turku3d.coarse.bridges import deck_obstacle, through_deck
    from turku3d.coarse.inputs import Building

    assert deck_obstacle({"building": "yes"})
    assert not deck_obstacle({"building": "service", "bridge:support": "pylon"})
    assert not deck_obstacle({"building": "no"})
    assert not deck_obstacle({"highway": "primary"})

    line = shapely.LineString([(0, 0), (60, 0)])
    deck = line.buffer(4, cap_style="flat")
    stations, heights = np.array([0.0, 60.0]), np.array([10.0, 11.0])
    under = Building("under", shapely.box(10, -12, 22, 12), 0.5, 10.9, "facade_plaster")
    through = Building("through", shapely.box(35, -12, 42, 12), 0.5, 30.0, "facade_plaster")
    beside = Building("beside", shapely.box(48, 8, 55, 14), 0.5, 10.2, "facade_plaster")
    # The OSM outline of the building under the deck is a little larger than its measured footprint.
    obstacles = shapely.union_all([shapely.box(9.6, -12.4, 22.4, 12.4), *(b.footprint for b in (through, beside))])
    kept = through_deck(obstacles, [under, through, beside], deck, line, stations, heights)
    cut = deck.difference(kept.buffer(0.15))
    assert cut.covers(shapely.box(9, -4, 23, 4))  # no hole over the low building
    assert not cut.intersects(shapely.box(35.5, -3, 41.5, 3))  # a building rising through the deck stays a hole
    assert kept.covers(beside.footprint)  # not under the deck: unchanged
    assert through_deck(obstacles, [beside], deck, line, stations, heights) is obstacles

    # A mapped outline with no measured building on it (its building is not built) is no hole, inside the tile or
    # reaching past its edge (Erasmusbrug north approach); one a neighbour tile's measured building stands on is
    # kept, and so is one reaching past CONTEXT_REACH, where a measured building may not be loaded.
    unbuilt = shapely.box(24, -10, 31, 10)
    past_edge = shapely.box(55, -10, 65, 10)
    tile_box = shapely.box(0, -50, 60, 50)
    obstacles = shapely.union_all([unbuilt, past_edge, through.footprint])
    kept = through_deck(obstacles, [through], deck, line, stations, heights, tile_box)
    cut = deck.difference(kept.buffer(0.15))
    assert cut.covers(shapely.box(24, -4, 31, 4)) and cut.covers(shapely.box(55, -4, 60, 4))
    assert not cut.intersects(shapely.box(35.5, -3, 41.5, 3))
    neighbour = Building("neighbour", shapely.box(56, -9, 64, 9), 0.5, 30.0, "facade_plaster")
    kept = through_deck(obstacles, [through], deck, line, stations, heights, tile_box, [neighbour])
    assert not deck.difference(kept.buffer(0.15)).intersects(shapely.box(55.5, -3, 59.5, 3))
    far = shapely.box(50, -10, 400, 10)
    kept = through_deck(shapely.union_all([far, through.footprint]), [through], deck, line, stations, heights, tile_box)
    assert not deck.difference(kept.buffer(0.15)).intersects(shapely.box(50.5, -3, 59.5, 3))
