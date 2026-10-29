"""Synthetic tunnel profile, ground-cut and passage rules; no tile packaging."""

import json

import numpy as np
import shapely
from turku3d import config as C
from turku3d.coarse.build import extrude
from turku3d.coarse.inputs import Building
from turku3d.coarse.tunnels import dimensions, floor_profile, produce, selected
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
    assert dimensions({"highway": "footway"}) == (3, 2.5)
    assert dimensions({"highway": "residential", "width": "8 m"}) == (8, 4.5)


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
