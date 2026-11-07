"""Synthetic tunnel profile, ground-cut and passage rules; no tile packaging."""

import json

import numpy as np
import shapely
from turku3d import config as C
from turku3d.coarse.build import extrude
from turku3d.coarse.inputs import Building
from turku3d.coarse.tunnels import RAIL_MOUTH_REACH, dimensions, floor_profile, produce, rail_stretch, selected
from turku3d.roads import build_graph, encode


def test_selection_and_clearance():
    assert selected({"highway": "footway", "tunnel": "building_passage"})
    assert selected({"railway": "rail", "tunnel": "yes"})
    assert selected({"highway": "residential", "covered": "yes", "layer": "-1"})
    assert selected({"highway": "residential", "covered": "yes", "layer": "0"})
    assert selected({"highway": "trunk", "layer": "-1"})
    assert not selected({"highway": "residential", "covered": "yes", "layer": "1"})
    assert dimensions({"highway": "trunk", "lanes": "3"}) == (11.5, 4.5)
    assert not selected({"waterway": "stream", "tunnel": "culvert"})
    # Deep metro/rail and underground stations are not boxes at grade; a shallow rail tunnel still is.
    assert not selected({"railway": "subway", "tunnel": "yes", "layer": "-2", "level": "-3"})
    assert not selected({"railway": "rail", "tunnel": "yes", "level": "-2"})
    assert not selected({"railway": "station", "layer": "-2", "location": "underground"})
    assert not selected({"railway": "platform", "layer": "-6"})
    assert selected({"railway": "subway", "tunnel": "yes", "layer": "-1"})
    assert selected({"highway": "trunk", "tunnel": "yes", "layer": "-2"})
    assert dimensions({"highway": "footway"}) == (3, 2.5)
    assert dimensions({"highway": "residential", "width": "8 m"}) == (8, 4.5)


def test_rail_tunnel_is_drawn_only_near_its_mouth():
    """Rotterdam: the rail tunnel and metro under Binnenrotte and Blaak are chains of tunnel ways (layer -1/-2);
    only a way that meets the open track has a mouth. Their boxes stood as 5 m walls at street level."""
    open_track = shapely.LineString([(0, 0), (100, 0)])
    rails = shapely.STRtree([open_track])
    rail = {"railway": "rail", "tunnel": "yes", "layer": "-1"}
    # Tunnel to tunnel: buried, nothing drawn (also without any open track around).
    assert rail_stretch("inner", shapely.LineString([(400, 0), (600, 0)]), rail, rails) is None
    assert rail_stretch("inner", shapely.LineString([(400, 0), (600, 0)]), rail, None) is None
    # One mouth: the stretch from the mouth into the hill, its inner end marked.
    key, line, tags = rail_stretch("mouth", shapely.LineString([(100, 0), (900, 0)]), rail, rails)
    assert line.length == RAIL_MOUTH_REACH and line.coords[0] == (100, 0) and tags["_rail_inner"] == 1
    key, line, tags = rail_stretch("mouth", shapely.LineString([(900, 0), (100, 0)]), rail, rails)
    assert line.length == RAIL_MOUTH_REACH and line.coords[-1] == (100, 0) and tags["_rail_inner"] == 0
    # Roads keep their boxes whatever their ends meet.
    road = {"highway": "primary", "tunnel": "yes"}
    assert rail_stretch("road", shapely.LineString([(400, 0), (600, 0)]), road, rails)[2] is road


def test_a_deep_metro_or_a_platform_is_no_mouth_for_the_tunnel_meeting_it():
    """Rotterdam's Calandlijn: layer -1 tunnel ways meet the layer -2 ways and platforms of Beurs/Blaak stations, which
    are not built as tubes (deep rule) but are no open track either; counted as track they made the -1 ways boxes
    along Blaak again."""
    from turku3d.coarse.tunnels import open_track

    assert open_track({"railway": "subway"}) and open_track({"railway": "rail", "bridge": "yes", "layer": "1"})
    assert not open_track({"railway": "subway", "tunnel": "yes", "layer": "-2"})
    assert not open_track({"railway": "subway", "layer": "-2"})
    assert not open_track({"railway": "platform"}) and not open_track({"railway": "abandoned"})
    assert not open_track({"railway": "tram", "tunnel": "covered", "layer": "-1"})


def test_rail_tunnel_stretch_is_buried_at_its_inner_end():
    tile = C.VisualTile(0, 0, 0)
    pos = np.array([[0, 2, 0], [250, 2, 0], [0, 2, 250], [250, 2, 250]], float)
    idx = np.array([[0, 2, 1], [1, 2, 3]], np.uint32)
    line = shapely.LineString([(10, 100), (160, 100)])  # the mouth at x = 10, the hill beyond
    tags = {"railway": "rail", "tunnel": "yes", "_rail_inner": 1}
    result = produce(tile, [("rail", line, tags)], pos, idx, [], extrude, lambda x, z: 2.0)
    floor = next(m for m in result.meshes if m.object_id.endswith(":floor"))
    inner = floor.positions[floor.positions[:, 0] > 159]
    _, clearance = dimensions(tags)
    assert len(inner) and inner[:, 1].max() + clearance <= 2 - 0.4  # the box roof is under the ground at its end
    assert floor.positions[:, 0].max() <= 160 + 1e-6  # no ramp back up to the street at the buried end
    assert not result.profiles  # no street over the tube takes its floor
    # The street over the buried end keeps its ground (no hole cut over the tube there).
    tpos, tidx, tkeep = result.terrain(pos, idx)
    ground = shapely.union_all(shapely.polygons(tpos[tidx[tkeep]][:, :, [0, 2]]))
    assert all(ground.contains(shapely.Point(x, 100)) for x in (155, 158, 161))


def test_outside_passages_skip_ground_but_keep_owned_building_openings():
    tile = C.VisualTile(0, 0, 0)
    pos = np.array([[0, 2, 0], [250, 2, 0], [0, 2, 250]], float)
    idx = np.array([[0, 2, 1]], np.uint32)
    ways = [
        (
            "outside",
            shapely.LineString([(255, 0), (255, 20)]),
            {"highway": "footway", "width": "4", "tunnel": "building_passage"},
        )
    ]

    def unused(x, z):
        raise AssertionError("unrelated ground must not be prepared")

    empty = produce(tile, ways, pos, idx, [], extrude, unused, unused)
    assert empty.keep.all() and not empty.meshes and not empty.profiles
    building = Building("edge", shapely.box(230, 5, 260, 15), 2, 14, "facade_plaster")
    owned = produce(
        tile, ways, pos, idx, [(building, extrude(building))], extrude, unused, lambda x, z: 2
    )
    assert owned.keep.all() and owned.ceilings
    assert any(b.base > 2 for b, _ in owned.shells)


def test_measured_dip_and_buried_profile():
    d = np.arange(5)
    np.testing.assert_equal(floor_profile(np.array([5, 3, 2, 3, 5]), d), [5, 3, 2, 3, 5])
    np.testing.assert_equal(floor_profile(np.array([5, 9, 10, 9, 6]), d), [5, 5.25, 5.5, 5.75, 6])


def test_portal_foundation_reaches_terrain_under_full_wall_width():
    from turku3d.coarse.tunnels import grounded_wall

    def ground(x, z):
        return abs(z - 1) * 4

    mesh = grounded_wall("wing", [[0.2, 1], [1.8, 1]], [2, 2], [5, 5], ground, 1)
    assert mesh.positions[:, 1].min() <= -0.2
    assert mesh.positions[:, 1].max() == 5


def test_portal_floor_meets_actual_ground_across_its_width():
    tile = C.VisualTile(0, 0, 0)
    pos = np.array([[0, 2, 0], [20, 2, 0], [0, 4, 20], [20, 4, 20]], float)
    idx = np.array([[0, 2, 1], [1, 2, 3]], np.uint32)
    line = shapely.LineString([(0, 10), (20, 10)])
    tags = {"highway": "service", "width": "4", "tunnel": "yes"}

    def surface(x, z):
        return 2 + z * 0.02 + np.maximum(0, z - tile.size / 128 * 5) * 0.04

    result = produce(
        tile, [("crossfall", line, tags)], pos, idx, [], extrude, lambda x, z: 2.5, surface
    )
    road = next(m for m in result.meshes if m.object_id.endswith(":floor"))
    portal = road.positions[np.isin(road.positions[:, 0], [0, 20])]
    assert len(portal) >= 4
    np.testing.assert_allclose(portal[:, 1], surface(portal[:, 0], portal[:, 2]))
    edge = np.unique(portal[np.isclose(portal[:, 0], 0)], axis=0)
    edge = edge[np.argsort(edge[:, 2])]
    assert len(edge) > 2
    for a, b in zip(edge[:-1], edge[1:], strict=True):
        middle = (a + b) / 2
        np.testing.assert_allclose(middle[1], surface(middle[0], middle[2]))
    # The measured interior dip remains; only the portal's short join changes.
    middle = road.positions[np.isclose(road.positions[:, 0], 10)]
    np.testing.assert_allclose(middle[:, 1], 2.5)


def scene(height=2, passage=False):
    tile = C.VisualTile(0, 0, 0)
    pos = np.array([[0, height, 0], [20, height, 0], [0, height, 20], [20, height, 20]], float)
    idx = np.array([[0, 2, 1], [1, 2, 3]], np.uint32)
    line = shapely.LineString([(0, 10), (20, 10)])
    tags = {"highway": "service", "width": "4", "tunnel": "building_passage" if passage else "yes"}
    building = Building("b", shapely.box(5, 5, 15, 15), 2, 14, "facade_plaster")
    shells = [(building, extrude(building))] if passage else []
    return produce(tile, [("test", line, tags)], pos, idx, shells, extrude, lambda x, z: 2)


def test_ground_cut_preserves_outside_and_roof_seals_inside():
    result = scene()
    assert not result.keep.any()
    edge = next(m for m in result.meshes if m.object_id.endswith("ground-edge"))
    tris = edge.positions[edge.indices][:, :, [0, 2]]
    assert abs(sum(shapely.Polygon(t).area for t in tris) - 320) < 1e-8
    roof = next(m for m in result.meshes if m.object_id.endswith(":roof"))
    assert roof.positions[:, 1].min() == 6.5
    assert result.collision(
        C.CollisionTile(0, 0), np.zeros((0, 3)), np.zeros((0, 3), np.uint32), np.zeros(0, np.uint8)
    )[1].size
    # Overburden above the tube remains available to walk on.
    high = scene(height=10)
    # High overburden stays inside; only the short portal banks are removed.
    edge = next(m for m in high.meshes if m.object_id.endswith("ground-edge"))
    assert len(edge.indices) > 0
    assert edge.positions[:, 1].min() == 10
    terrain = shapely.union_all(shapely.polygons(edge.positions[edge.indices][:, :, [0, 2]]))
    assert terrain.covers(shapely.Point(10, 10))
    assert not terrain.covers(shapely.Point(0.5, 10))


def test_building_passage_keeps_upper_shell_and_road_floor_metadata():
    result = scene(passage=True)
    assert len(result.shells) == 3
    assert result.keep.all()
    assert not result.meshes
    assert not any(
        m.object_id.endswith(":roof") or ":portal:" in m.object_id for m in result.meshes
    )
    assert not any(m.object_id.endswith((":left", ":right")) for m in result.meshes)
    assert len(result.ceilings) == 1
    for b, _ in result.shells:
        if b.footprint.contains(shapely.Point(10, 10)):
            assert b.base >= 6.5
    raised = next(shell for b, shell in result.shells if b.base > 2)
    np.testing.assert_array_equal(result.ceilings[0].positions, raised[2])
    np.testing.assert_array_equal(result.ceilings[0].indices, raised[3])
    graph = build_graph([([(0, 10), (20, 10)], 3)], [0, 0, 20, 20], "test", connected=False)
    roads = result.roads({"json": encode(graph)})
    encoded = json.loads(roads["json"])
    assert encoded["edgeHeights"] == [[2] * 11]
    assert len(encoded["edges"][0]) // 2 == len(encoded["edgeHeights"][0])


def test_building_passage_opens_its_whole_mouth_at_an_oblique_facade():
    """Rotterdam's Pannekoekstraat: the way ends at the mapped facade node, the surveyed outline's facade is oblique
    and a little further out, and a flat-capped cut left a wall across half of the mouth."""
    tile = C.VisualTile(0, 0, 0)
    pos = np.array([[0, 2, 0], [40, 2, 0], [0, 2, 40], [40, 2, 40]], float)
    idx = np.array([[0, 2, 1], [1, 2, 3]], np.uint32)
    building = Building("b", shapely.Polygon([(5, 5), (15, 5), (17, 15), (5, 15)]), 2, 14, "facade_plaster")
    line = shapely.LineString([(0, 10), (16, 10)])  # ends where the facade crosses the axis
    tags = {"highway": "service", "width": "4", "tunnel": "building_passage"}
    result = produce(tile, [("p", line, tags)], pos, idx, [(building, extrude(building))], extrude, lambda x, z: 2.0)
    for point in ((16.2, 11.5), (15.6, 8.6), (10, 10)):  # both halves of the mouth and the middle
        part = next(b for b, _ in result.shells if b.footprint.buffer(1e-6).contains(shapely.Point(point)))
        assert part.base >= 6.5, point
    # The rest of the building keeps its ground floor.
    assert any(b.base == 2 and b.footprint.contains(shapely.Point(10, 6)) for b, _ in result.shells)


def test_passage_parts_keep_collision_when_their_centroid_moves_outside_visual_tile(monkeypatch):
    from turku3d.coarse import build

    monkeypatch.setattr(build, "CELLS", 4)
    tile = C.VisualTile(0, 0, 0)
    pos, idx = build.grid_mesh(tile, np.zeros((5, 5)))
    building = Building("boundary", shapely.box(-10, 10, 30, 20), 0, 10, "facade_plaster")
    building.facade_asset_description = {"source": "stored per-building appearance"}
    way = (
        "passage",
        shapely.LineString([(0, 0), (0, 40)]),
        {"highway": "service", "width": "4", "tunnel": "building_passage"},
    )
    result = produce(
        tile, [way], pos, idx, [(building, extrude(building))], extrude, lambda x, z: 0
    )
    assert any(b.footprint.centroid.x < 0 for b, _ in result.shells)
    assert all(
        b.facade_asset_description == building.facade_asset_description for b, _ in result.shells
    )
    cells = build.collision_tiles(
        tile, pos, idx, np.full(len(idx), 1, np.uint8), 0, result.shells, result.keep
    )
    assert cells[C.CollisionTile(0, 0)][0][:, 0].min() == -10


def test_reversing_approach_strip_preserves_both_valid_patches():
    from turku3d.coarse.kerbs import terrain_patches

    # Offset approaches can fold at a hairpin; the bowtie has two valid lobes.
    corners = np.array([[0.0, 0.0], [2.0, 2.0], [0.0, 2.0], [2.0, 0.0]])
    patches = list(terrain_patches(corners, 1, (0, 0, 2, 2)))
    assert patches
    assert sum(shapely.Polygon(p).area for p in patches) == 2


def test_portal_rejects_hillside_crossfall_and_carves_open_approach():
    from turku3d.coarse.build import grid_mesh

    tile = C.VisualTile(0, 0, 0)
    heights = np.full((129, 129), 2.0)
    # A hillside next to a low road: the old portal blend lifted the floor 5 m.
    heights[6:] = 7
    pos, idx = grid_mesh(tile, heights)
    line = shapely.LineString([(10, 10), (30, 10)])
    tags = {"highway": "service", "width": "7", "tunnel": "yes"}

    def surface(x, z):
        return 2 + max(0, z - 10) * 2

    result = produce(tile, [("bank", line, tags)], pos, idx, [], extrude, lambda x, z: 2, surface)
    floor = next(m for m in result.meshes if m.object_id.endswith(":floor"))
    portal = floor.positions[np.isclose(floor.positions[:, 0], 10)]
    assert len(portal) > 2
    assert portal[:, 1].max() <= 2.35 + 1e-8
    # The apron meets original terrain two metres before the mouth.
    outer = floor.positions[np.isclose(floor.positions[:, 0], 8)]
    assert len(outer) > 2
    np.testing.assert_allclose(outer[:, 1], [surface(x, z) for x, _, z in outer])
    apron = shapely.box(8, 6.5, 10, 13.5)
    cut = shapely.union_all(shapely.polygons(pos[idx[~result.keep]][:, :, [0, 2]]))
    assert cut.intersection(apron).area > 0
    # Render and collision consume the very same repaired floor triangles.
    cp, ci, _ = result.collision(
        C.CollisionTile(0, 0), np.empty((0, 3)), np.empty((0, 3), np.uint32), np.empty(0, np.uint8)
    )
    floor_triangles = floor.positions[floor.indices]
    np.testing.assert_allclose(cp[ci[: len(floor.indices)]], floor_triangles)


def test_triangle_crossing_clearance_without_a_vertex_inside_is_cut():
    tile = C.VisualTile(0, 0, 0)
    pos = np.array([[0, -2, 0], [20, 10, 0], [0, 10, 20]], float)
    idx = np.array([[0, 2, 1]], np.uint32)
    line = shapely.LineString([(2, 4), (10, 4)])
    result = produce(
        tile,
        [("crossing", line, {"highway": "footway", "tunnel": "yes"})],
        pos,
        idx,
        [],
        extrude,
        lambda x, z: 2,
    )
    assert not result.keep[0]


def test_portal_blend_does_not_follow_overburden_up_inside_the_mouth():
    tile = C.VisualTile(0, 0, 0)
    pos = np.array([[0, 2, 0], [20, 2, 0], [0, 2, 20], [20, 2, 20]], float)
    idx = np.array([[0, 2, 1], [1, 2, 3]], np.uint32)
    line = shapely.LineString([(2, 10), (18, 10)])

    def surface(x, z):
        return 2 + min(max(x - 2, 0), max(18 - x, 0)) * 4

    result = produce(
        tile,
        [("buried", line, {"highway": "footway", "tunnel": "yes"})],
        pos,
        idx,
        [],
        extrude,
        surface,
        surface,
    )
    floor = next(m for m in result.meshes if m.object_id.endswith(":floor"))
    inside = floor.positions[(floor.positions[:, 0] >= 2) & (floor.positions[:, 0] <= 18)]
    np.testing.assert_allclose(inside[:, 1], 2)


def test_portal_has_one_frame_and_full_width_clear_in_render_and_collision():
    result = scene()
    frames = [m for m in result.meshes if ":portal:" in m.object_id]
    assert len(frames) == 2
    opening = shapely.box(0, 8.001, 20, 11.999)
    for mesh in [*frames, *[m for m in result.meshes if m.object_id.endswith((":left", ":right"))]]:
        points = mesh.positions[(mesh.positions[:, 1] > 2.01) & (mesh.positions[:, 1] < 6.49)]
        assert not shapely.contains_xy(opening, points[:, 0], points[:, 2]).any()
        for tri in mesh.positions[mesh.indices]:
            if tri[:, 1].min() >= 6.5:
                continue
            assert shapely.Polygon(tri[:, [0, 2]]).intersection(opening).area < 1e-9


def test_parallel_hospital_tunnels_share_opening_and_cut_building_shell():
    # Four mapped ways: two main carriageways plus a parallel ramp/lane.
    tile = C.VisualTile(0, 0, 0)
    pos = np.array([[0, 2, 0], [60, 2, 0], [0, 2, 60], [60, 2, 60]], float)
    idx = np.array([[0, 2, 1], [1, 2, 3]], np.uint32)
    ways = [
        (
            str(i),
            shapely.LineString([(5, z), (30, z + i * 0.3), (55, z + i * 0.6)]),
            {"highway": "trunk", "lanes": str(lanes), "layer": "-1", "tunnel": "yes"},
        )
        for i, (z, lanes) in enumerate([(15, 1), (22, 2), (34, 2), (41, 1)])
    ]
    building = Building("hospital", shapely.box(10, 5, 50, 51), 2, 24, "facade_plaster")
    result = produce(tile, ways, pos, idx, [(building, extrude(building))], extrude, lambda x, z: 2)
    assert len(result.profiles) == 1
    assert len([m for m in result.meshes if ":portal:" in m.object_id]) == 2
    corridor = shapely.box(10.01, 12.76, 49.99, 43.24)
    for b, shell in result.shells:
        if b.base == 2:
            assert b.footprint.intersection(corridor).area < 1e-8
        # Building visual walls and collision share these vertices and indices.
        for tri in shell[4][shell[6]]:
            if tri[:, 1].min() < 6.5:
                assert shapely.Polygon(tri[:, [0, 2]]).intersection(corridor).area < 1e-8
    assert result.ceilings
    for mesh in result.meshes:
        if ":portal:" in mesh.object_id or mesh.object_id.endswith((":left", ":right")):
            for tri in mesh.positions[mesh.indices]:
                if tri[:, 1].min() < 6.5:
                    assert shapely.Polygon(tri[:, [0, 2]]).intersection(corridor).area < 1e-8


def test_covered_road_also_lifts_building_and_keeps_crossing_tunnel_separate():
    from turku3d.coarse.tunnels import merge_parallel

    tags = {"highway": "service", "covered": "yes", "width": "4"}
    way = ("covered", shapely.LineString([(0, 10), (20, 10)]), tags)
    crossing = ("crossing", shapely.LineString([(10, 0), (10, 20)]), tags)
    assert len(merge_parallel([way, crossing])) == 2
    tile = C.VisualTile(0, 0, 0)
    pos = np.array([[0, 2, 0], [20, 2, 0], [0, 2, 20], [20, 2, 20]], float)
    idx = np.array([[0, 2, 1], [1, 2, 3]], np.uint32)
    building = Building("covered", shapely.box(5, 5, 15, 15), 2, 14, "facade_plaster")
    result = produce(
        tile, [way], pos, idx, [(building, extrude(building))], extrude, lambda x, z: 2
    )
    raised = [b for b, _ in result.shells if b.footprint.contains(shapely.Point(10, 10))]
    assert len(raised) == 1 and raised[0].base >= 6.5


def test_quay_discovery_uses_walkways_without_place_or_way_ids():
    from turku3d.coarse.underpasses import quay_passages

    def row(key, geom, **tags):
        return dict(osm_id=key, geometry_local=geom.wkb, tags=json.dumps(tags))

    rows = [
        row(1, shapely.box(20, 10, 30, 60), man_made="bridge", material="stone"),
        row(2, shapely.LineString([(10, 12), (20, 12)]), highway="footway"),
        row(3, shapely.LineString([(30, 12), (40, 12)]), highway="footway"),
        row(4, shapely.LineString([(20, 58), (30, 58)]), highway="footway", tunnel="yes"),
    ]
    result = quay_passages(rows)
    assert len(result) == 2
    assert any(key == "4" for key, _, _ in result)
    assert all(tags["vault"] == "barrel" for _, _, tags in result)
    rows[2]["tags"] = json.dumps(dict(highway="footway", footway="sidewalk"))
    assert len(quay_passages(rows)) == 1


def test_barrel_vault_has_clear_centre_stone_shell_and_unbroken_floor():
    tile = C.VisualTile(0, 0, 0)
    pos = np.array([[0, 2, 0], [20, 2, 0], [0, 2, 20], [20, 2, 20]], float)
    idx = np.array([[0, 2, 1], [1, 2, 3]], np.uint32)
    line = shapely.LineString([(2, 10), (18, 10)])
    result = produce(
        tile,
        [
            (
                "vault",
                line,
                {"highway": "footway", "vault": "barrel", "width": "2.4", "maxheight": "2.7"},
            )
        ],
        pos,
        idx,
        [],
        extrude,
        lambda x, z: 2,
    )
    roof = next(m for m in result.meshes if m.object_id.endswith(":roof"))
    assert roof.metadata["vault"]
    np.testing.assert_allclose(roof.positions[:, 1].max(), 5)
    centre = roof.positions[np.isclose(roof.positions[:, 2], 10)]
    assert centre[:, 1].min() >= 4.7 - 1e-8
    floor = next(m for m in result.meshes if m.object_id.endswith(":floor"))
    polys = shapely.union_all(shapely.polygons(floor.positions[floor.indices][:, :, [0, 2]]))
    assert line.buffer(1.2, cap_style="flat").difference(polys).area < 1e-8
    assert not any(":portal:" in m.object_id for m in result.meshes)


def test_portal_jambs_meet_sloping_header_without_coplanar_overlap():
    from turku3d.wall_surfaces import validate_meshes

    tile = C.VisualTile(0, 0, 0)
    pos = np.array([[0, 2, 0], [50, 2, 0], [0, 4, 50], [50, 4, 50]], float)
    idx = np.array([[0, 2, 1], [1, 2, 3]], np.uint32)
    ways = [
        (
            "portal",
            shapely.LineString([(10, 10), (30, 15)]),
            {"highway": "service", "width": "4", "tunnel": "yes"},
        )
    ]
    result = produce(tile, ways, pos, idx, [], extrude, lambda x, z: 2 + z * 0.04)
    assert validate_meshes(result.visuals(tile), tile.id)["violations"] == 0


def test_tunnel_cuts_building_around_complete_lining_before_facade_paint():
    tile = C.VisualTile(0, 0, 0)
    pos = np.array([[0, 2, 0], [50, 2, 0], [0, 2, 50], [50, 2, 50]], float)
    idx = np.array([[0, 2, 1], [1, 2, 3]], np.uint32)
    building = Building("lining", shapely.box(10, 5, 30, 15), 2, 14, "facade_plaster")
    ways = [
        (
            "lining",
            shapely.LineString([(5, 10), (35, 10)]),
            {"highway": "service", "width": "4", "tunnel": "yes"},
        )
    ]
    result = produce(tile, ways, pos, idx, [(building, extrude(building))], extrude, lambda x, z: 2)
    ground_parts = [b.footprint for b, _ in result.shells if b.base == 2]
    # Clear road z=8..12, plus 0.4 m wall lining on each side. The new
    # building cut must be outside both lining faces, including at neighbours.
    lining = shapely.box(10, 7.6, 30, 12.4)
    assert sum(p.intersection(lining).area for p in ground_parts) < 1e-8
    assert any(b.base > 2 for b, _ in result.shells)


def test_a_wide_mouth_corner_does_not_climb_the_bank_beside_it():
    tile = C.VisualTile(0, 0, 0)
    pos = np.array([[0, 2, 0], [60, 2, 0], [0, 2, 60], [60, 2, 60]], float)
    idx = np.array([[0, 2, 1], [1, 2, 3]], np.uint32)
    line = shapely.LineString([(10, 30), (40, 30)])

    def surface(x, z):  # the road at 2 m, a bank rising beside it towards one corner
        return 2.0 + max(0.0, z - 33.0) * 1.5

    result = produce(tile, [("wide", line, {"highway": "service", "tunnel": "yes", "width": "30"})],
                     pos, idx, [], extrude, lambda x, z: 2, surface)
    floor = next(m for m in result.meshes if m.object_id.endswith(":floor"))
    mouth = floor.positions[np.isclose(floor.positions[:, 0], 10)]
    assert len(mouth) and mouth[:, 1].max() <= 2.0 + 0.35 + 1e-6


def test_an_approach_below_the_tunnel_floor_ramps_up_to_the_mouth():
    tile = C.VisualTile(0, 0, 0)
    pos = np.array([[0, 2, 0], [60, 2, 0], [0, 2, 60], [60, 2, 60]], float)
    idx = np.array([[0, 2, 1], [1, 2, 3]], np.uint32)
    line = shapely.LineString([(20, 30), (50, 30)])

    def surface(x, z):  # the street outside the west mouth lies 1.2 m below the floor
        return 0.8 if x < 20 else 2.0

    result = produce(tile, [("low", line, {"highway": "service", "tunnel": "yes"})],
                     pos, idx, [], extrude, lambda x, z: 2, surface)
    floor = next(m for m in result.meshes if m.object_id.endswith(":floor"))
    tris = floor.positions[floor.indices]
    outside = tris[:, :, 0].max(1) <= 20 + 1e-6
    normals = np.cross(tris[outside, 1] - tris[outside, 0], tris[outside, 2] - tris[outside, 0])
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    centre = np.abs(tris[outside, :, 2].mean(1) - 30) < 1.5
    assert outside.any() and np.abs(normals[centre, 1]).min() > 0.97  # the lane ramps at under ~14 deg


def test_underground_way_without_measured_depth_under_open_ground_is_not_built():
    """Tapiola: garage aisles and service tunnels (layer -1/-2) under a level square would stand on it as boxes."""
    tile = C.VisualTile(0, 0, 0)
    pos = np.array([[0, 2, 0], [250, 2, 0], [0, 2, 250]], float)
    idx = np.array([[0, 2, 1]], np.uint32)
    line = shapely.LineString([(20, 40), (120, 40)])
    for tags in ({"tunnel": "yes", "layer": "-1"}, {"tunnel": "yes", "layer": "-2", "service": "parking_aisle"},
                 {"highway": "footway", "tunnel": "yes", "level": "2"}):
        result = produce(tile, [("deep", line, {"highway": "service", **tags})], pos, idx, [], extrude, lambda x, z: 2)
        assert not result.profiles and not result.meshes
        assert len(result.unbuilt) == 1
    # Measured overburden (a hill over the middle) keeps the tube; so does no layer at all on level ground.
    hill = produce(tile, [("hill", line, {"highway": "service", "tunnel": "yes", "layer": "-1"})], pos, idx, [],
                   extrude, lambda x, z: 2 + max(0.0, 6 - abs(x - 70) / 5))
    assert len(hill.profiles) == 1 and not hill.unbuilt
    # Indoor corridors are never built, whatever the ground model shows under them, passages included.
    for tags in ({"highway": "footway", "tunnel": "yes", "indoor": "yes", "layer": "-1", "level": "0"},
                 {"highway": "footway", "tunnel": "yes", "level": "1"},
                 {"highway": "footway", "tunnel": "building_passage", "indoor": "yes"}):
        inside = produce(tile, [("mall", line, tags)], pos, idx, [], extrude, lambda x, z: 2 + max(0.0, 6 - abs(x - 70) / 5))
        assert not inside.profiles and not inside.meshes and len(inside.unbuilt) == 1
    plain = produce(tile, [("plain", line, {"highway": "service", "tunnel": "yes"})], pos, idx, [], extrude,
                    lambda x, z: 2)
    assert len(plain.profiles) == 1 and not plain.unbuilt
    # A covered road at grade (Rotterdam Blaak under the station canopy) is a real street: unchanged.
    for covered in ({"tunnel": "covered", "layer": "-1"}, {"covered": "yes", "layer": "-1"}):
        kept = produce(tile, [("covered", line, {"highway": "secondary", **covered})], pos, idx, [], extrude,
                       lambda x, z: 2)
        assert len(kept.profiles) == 1 and not kept.unbuilt


def test_underground_way_under_a_building_keeps_its_tube():
    tile = C.VisualTile(0, 0, 0)
    pos = np.array([[0, 2, 0], [250, 2, 0], [0, 2, 250]], float)
    idx = np.array([[0, 2, 1]], np.uint32)
    line = shapely.LineString([(20, 40), (120, 40)])
    building = Building("hospital", shapely.box(10, 20, 130, 60), 2, 20, "facade_plaster")
    result = produce(tile, [("under", line, {"highway": "service", "tunnel": "yes", "layer": "-1"})], pos, idx,
                     [(building, extrude(building))], extrude, lambda x, z: 2)
    assert len(result.profiles) == 1 and not result.unbuilt
    # A garage aisle or a way two levels down under the same building is no road at grade: not built.
    for tags in ({"tunnel": "yes", "layer": "-2"}, {"tunnel": "yes", "layer": "-1", "service": "parking_aisle"}):
        garage = produce(tile, [("garage", line, {"highway": "service", **tags})], pos, idx,
                         [(building, extrude(building))], extrude, lambda x, z: 2)
        assert not garage.profiles and len(garage.unbuilt) == 1


def test_unbuilt_tunnel_edges_leave_the_published_graph():
    tile = C.VisualTile(0, 0, 0)
    pos = np.array([[0, 2, 0], [250, 2, 0], [0, 2, 250]], float)
    idx = np.array([[0, 2, 1]], np.uint32)
    line = shapely.LineString([(20, 40), (120, 40)])
    tags = {"highway": "service", "tunnel": "yes", "layer": "-2"}
    result = produce(tile, [("deep", line, tags)], pos, idx, [], extrude, lambda x, z: 2)
    # v2 rows: the tunnel aisle (TUNNEL flag) and a street crossing it at grade; a signal post on the street.
    graph = {"format": "turku3d-roadgraph", "version": 1, "frame": "engine-xz", "bits": {"edge_flags": {"TUNNEL": 8}},
             "nodes": [20, 40, 120, 40, 70, 0, 70, 80], "edges": [[0, 1, 3, 100], [2, 3, 3, 80]],
             "edgeIds": ["aisle", "street"], "edgeAttrColumns": ["way", "flags"], "edgeAttrs": [[7, 8], [8, 1]],
             "edgeEvents": [None, [[10, 17, -1]]], "edgeHeights": [[2, 2], [2, 2]], "signalPosts": [[1, 0, 1, 72, 10, 2]]}
    after = json.loads(result.roads({"json": json.dumps(graph).encode()})["json"])
    assert after["edgeIds"] == ["street"] and after["edges"] == [[2, 3, 3, 80]]
    assert after["edgeAttrs"] == [[8, 1]] and after["edgeEvents"] == [[[10, 17, -1]]] and after["edgeHeights"] == [[2, 2]]
    assert after["signalPosts"] == [[0, 0, 1, 72, 10, 2]]


def test_garage_aisles_are_unbuilt_even_over_a_false_dip():
    """Tapiola: the ground interpolated under the mall dips a metre, which read as measured depth."""
    tile = C.VisualTile(0, 0, 0)
    pos = np.array([[0, 2, 0], [250, 2, 0], [0, 2, 250]], float)
    idx = np.array([[0, 2, 1]], np.uint32)
    line = shapely.LineString([(20, 40), (120, 40)])
    hump = lambda x, z: 2 + max(0.0, 6 - abs(x - 70) / 5)  # noqa: E731
    for tags in ({"tunnel": "yes", "layer": "-2"}, {"tunnel": "yes", "layer": "-1", "service": "parking_aisle"}):
        garage = produce(tile, [("garage", line, {"highway": "service", **tags})], pos, idx, [], extrude, hump)
        assert not garage.profiles and len(garage.unbuilt) == 1


def test_a_wide_passage_never_carves_the_crown_of_its_road():
    from turku3d.coarse.tunnels import passage_floors

    line = shapely.LineString([(0, 0), (40, 0)])
    xz = shapely.get_coordinates(shapely.line_interpolate_point(line, np.linspace(0, 40, 21)))
    offset = np.tile([0.0, 1.0], (len(xz), 1))

    def crowned(x, z):  # 0.4 m higher on the centreline than at the 13 m edges
        return 6.0 - 0.4 * abs(z) / 13

    floors = np.asarray(passage_floors(xz, offset, 26.0, crowned))
    assert len(floors) == 20 * 13 * 2
    for tri in floors[::7]:
        centre = tri.mean(axis=0)
        assert centre[1] >= crowned(centre[0], centre[2]) - 1e-9  # the plane is never below the ground
        assert centre[1] - crowned(centre[0], centre[2]) < 0.35  # and stays near it


def test_a_ramp_into_an_unbuilt_garage_is_a_spur_and_not_built():
    """Tapionaukio: the garage ramp off the street led into unbuilt layer -1 parking aisles; its tube closed the
    street corner with a 5 m wall."""
    tile = C.VisualTile(0, 0, 0)
    pos = np.array([[0, 2, 0], [250, 2, 0], [0, 2, 250]], float)
    idx = np.array([[0, 2, 1]], np.uint32)
    street = shapely.LineString([(0, 40), (20, 40)])
    hill = lambda x, z: 2 + 5 * max(0.0, min(1.0, (x - 25) / 5))  # noqa: E731  overburden: the ramp has depth
    ramp = ("ramp", shapely.LineString([(20, 40), (40, 40)]),
            {"highway": "service", "tunnel": "yes", "layer": "-1", "_approaches": [street, None]})
    alone = produce(tile, [ramp], pos, idx, [], extrude, hill)
    assert len(alone.profiles) == 1 and not alone.unbuilt  # a measured tunnel on its own is built
    aisle = ("aisle", shapely.LineString([(40, 40), (40, 120)]),
             {"highway": "service", "tunnel": "yes", "layer": "-1", "service": "parking_aisle"})
    result = produce(tile, [ramp, aisle], pos, idx, [], extrude, hill)
    assert not result.profiles and len(result.unbuilt) == 2
    # The same ramp into a tunnel that is built keeps its tube.
    through = ("through", shapely.LineString([(40, 40), (40, 120)]), {"highway": "service", "tunnel": "yes"})
    kept = produce(tile, [ramp, through], pos, idx, [], extrude, hill)
    assert len(kept.profiles) == 2 and not kept.unbuilt


def test_an_entry_and_exit_ramp_pair_into_an_unbuilt_tunnel_is_a_spur():
    """Tapiolanympyra: two one-way ramps from the roundabout meet underground before the unbuilt service tunnel."""
    tile = C.VisualTile(0, 0, 0)
    pos = np.array([[0, 2, 0], [250, 2, 0], [0, 2, 250]], float)
    idx = np.array([[0, 2, 1]], np.uint32)
    hill = lambda x, z: 2 + 5 * max(0.0, min(1.0, (x - 25) / 5))  # noqa: E731
    road_in, road_out = shapely.LineString([(0, 30), (20, 30)]), shapely.LineString([(0, 50), (20, 50)])
    entry = ("in", shapely.LineString([(20, 30), (40, 40)]),
             {"highway": "service", "tunnel": "yes", "layer": "-1", "_approaches": [road_in, None]})
    exit_ = ("out", shapely.LineString([(40, 40), (20, 50)]),
             {"highway": "service", "tunnel": "yes", "layer": "-1", "_approaches": [None, road_out]})
    deep = ("deep", shapely.LineString([(40, 40), (120, 40)]), {"highway": "service", "tunnel": "yes", "layer": "-2"})
    result = produce(tile, [entry, exit_, deep], pos, idx, [], extrude, hill)
    assert not result.profiles and len(result.unbuilt) == 3
    # A through tunnel with mouths far apart keeps its tube even with an unbuilt branch.
    far_out = shapely.LineString([(200, 40), (220, 40)])
    through = ("through", shapely.LineString([(20, 30), (40, 40), (200, 40)]),
               {"highway": "service", "tunnel": "yes", "layer": "-1", "_approaches": [road_in, far_out]})
    branch = ("garage", shapely.LineString([(40, 40), (40, 120)]),
              {"highway": "service", "tunnel": "yes", "layer": "-1", "service": "parking_aisle"})
    kept = produce(tile, [through, branch], pos, idx, [], extrude,
                   lambda x, z: 2 + 5 * max(0.0, min(1.0, (x - 25) / 5, (195 - x) / 5)))
    assert len(kept.profiles) == 1 and len(kept.unbuilt) == 1


def test_passage_floors_stay_above_a_kink_inside_a_strip():
    """The ground grid kinks between a strip's probes; overlapping passages cut it there (Merituulentie at x 166)."""
    from turku3d.coarse.tunnels import passage_floors

    line = shapely.LineString([(0, 0), (10, 0)])
    xz = shapely.get_coordinates(shapely.line_interpolate_point(line, np.linspace(0, 10, 6)))
    offset = np.tile([0.0, 1.0], (len(xz), 1))

    def ridge(x, z):  # a 0.6 m sharp ridge at x = 4.7, between the 2 m stations
        return 3.0 + max(0.0, 0.6 - abs(x - 4.7))

    floors = np.asarray(passage_floors(xz, offset, 4.0, ridge))
    for tri in floors:
        for w in np.linspace(0, 1, 6):
            for v in np.linspace(0, 1 - w, 6):
                p = tri[0] * w + tri[1] * v + tri[2] * (1 - w - v)
                assert p[1] + 0.04 >= ridge(p[0], p[2]) - 0.15
