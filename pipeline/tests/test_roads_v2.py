"""roads.json v2 (docs/specs/roads-json-v2.md): seam-independent ids, lanes, events and rule paint."""

import json
from pathlib import Path

import pytest
import shapely
from turku3d import config as C
from turku3d.roads import CAR, ONEWAY, encode, way_flags
from turku3d.roads import paint as road_paint
from turku3d.roads.attributes import EDGE_FLAGS, attributes, kmh, lanes
from turku3d.roads.tile_graph import (
    EVENT_BITS,
    EVENT_KINDS,
    NODE_FLAGS,
    Way,
    build_tile_graph,
    clip_polyline,
    validate,
    with_heights,
)

FIXTURE = Path(__file__).parents[2] / "game/tests/fixtures/roads-v2-seam.json"
TILE_A, TILE_B = C.VisualTile(0, 0, 0), C.VisualTile(0, 1, 0)


def way(coords, highway, way_id, **tags):
    tags = {"highway": highway, **tags}
    flags = way_flags(highway, tags)
    if tags.get("oneway") == "-1":
        coords = coords[::-1]
    return Way(list(coords), flags, way_id, highway, tags)


def town():
    """A two-lane main street crossing the t0_0_0 | t0_1_0 seam (x = 250) between junctions at
    x = 200 and x = 300, two side streets, a signal stop position, crossings on main-street vertices
    (as OSM maps them), and a long rural road without junctions near z = 230."""
    main = [
        (150, 100),
        (180, 100),
        (200, 100),
        (230, 100),
        (270, 100),
        (290, 100),
        (300, 100),
        (350, 100),
    ]
    ways = [
        way(main, "tertiary", 11, lanes="2", maxspeed="40"),
        way([(200, 20), (200, 100), (200, 180)], "residential", 12),
        way([(300, 20), (300, 100), (300, 180)], "residential", 13, oneway="yes"),
        way([(20, 230), (480, 231)], "unclassified", 14, maxspeed="60"),
        way([(180, 150), (180, 100), (180, 60)], "footway", 15, footway="crossing"),
        way([(320, 140), (340, 140)], "living_street", 16),
    ]
    points = [
        (290.0, 100.0, {"highway": "traffic_signals", "traffic_signals:direction": "forward"}),
        (230.0, 100.0, {"highway": "crossing", "crossing": "marked", "crossing:markings": "zebra"}),
        (270.0, 100.0, {"highway": "crossing", "crossing": "unmarked", "crossing:markings": "no"}),
        (180.0, 100.0, {"highway": "crossing", "crossing": "traffic_signals"}),
    ]
    crossings = [(shapely.LineString([(180, 150), (180, 60)]), {"footway": "crossing"})]
    return ways, points, crossings


def tile_graph(tile):
    ways, points, crossings = town()
    return build_tile_graph(ways, points, crossings, tile.bbox, f"coarse-{tile.id}", tile.size)


def rows(graph):
    columns = {name: i for i, name in enumerate(graph["edgeAttrColumns"])}
    for i, row in enumerate(graph["edges"]):
        attrs = {name: graph["edgeAttrs"][i][j] for name, j in columns.items()}
        yield graph["edgeIds"][i], row, attrs, graph["edgeEvents"][i] or []


def node_flags(graph):
    points = zip(graph["nodes"][::2], graph["nodes"][1::2], strict=True)
    return dict(zip(points, graph["nodeFlags"], strict=True))


def by_way(graph, way_id):
    return [r for r in rows(graph) if r[2]["way"] == way_id]


def test_clip_polyline_gives_both_sides_the_same_seam_point():
    line = [(100.0, 90.0), (400.0, 117.3)]
    left = clip_polyline(line, (0, 0, 250, 250))
    right = clip_polyline(line, (250, 0, 500, 250))
    assert left[0].points[-1] == right[0].points[0]
    assert left[0].s1 == pytest.approx(right[0].s0)
    assert (left[0].cut0, left[0].cut1, right[0].cut0, right[0].cut1) == (False, True, True, False)


def test_a_logical_edge_keeps_one_id_across_the_seam():
    a, b = tile_graph(TILE_A), tile_graph(TILE_B)
    main_a = [r for r in by_way(a, 11) if r[2]["s0"] > 0 or r[2]["s1"] < r[2]["length"]]
    main_b = [r for r in by_way(b, 11) if r[2]["s0"] > 0 or r[2]["s1"] < r[2]["length"]]
    assert len(main_a) == len(main_b) == 1
    (id_a, _, attrs_a, _), (id_b, _, attrs_b, _) = main_a[0], main_b[0]
    assert id_a == id_b
    assert (attrs_a["s0"], attrs_a["s1"], attrs_a["length"]) == (0.0, 50.0, 100.0)
    assert (attrs_b["s0"], attrs_b["s1"], attrs_b["length"]) == (50.0, 100.0, 100.0)
    shared = ("lanes_fwd", "lanes_bwd", "width", "kmh", "flags", "setback0", "setback1")
    assert [attrs_a[k] for k in shared] == [attrs_b[k] for k in shared]
    assert (
        attrs_a["flags"] & EDGE_FLAGS["START_JUNCTION"]
        and attrs_a["flags"] & EDGE_FLAGS["END_JUNCTION"]
    )
    assert attrs_a["setback0"] == pytest.approx(6.5 / 2 + 1, abs=0.06)
    # The seam point is a SEAM node in both tiles, the junctions are JUNCTION nodes.
    for graph in (a, b):
        assert node_flags(graph)[(250.0, 100.0)] & NODE_FLAGS["SEAM"]
    assert node_flags(a)[(200.0, 100.0)] & NODE_FLAGS["JUNCTION"]


def test_long_edges_are_cut_at_seams_deterministically():
    a, b = tile_graph(TILE_A), tile_graph(TILE_B)
    ((id_a, _, attrs_a, _),) = by_way(a, 14)
    ((id_b, _, attrs_b, _),) = by_way(b, 14)
    assert id_a != id_b
    for attrs in (attrs_a, attrs_b):
        assert attrs["s0"] == 0 and attrs["s1"] == attrs["length"]
    assert tile_graph(TILE_A)["edgeIds"] == a["edgeIds"]


def test_events_carry_direction_markings_and_controllers():
    a, b = tile_graph(TILE_A), tile_graph(TILE_B)

    def events(graph, s0, length):
        ((_, _, _, flat),) = [
            r for r in by_way(graph, 11) if r[2]["s0"] == s0 and r[2]["length"] == length
        ]
        return {flat[i]: flat[i + 1 : i + 3] for i in range(0, len(flat), 3)}

    b_main = events(b, 50.0, 100.0)
    assert b_main[40.0] == [EVENT_KINDS["SIGNAL"] | EVENT_BITS["FORWARD"], 0]
    assert len(b["controllers"]) == 1
    assert (
        b_main[20.0][0] & 15 == EVENT_KINDS["CROSSING"]
        and not b_main[20.0][0] & EVENT_BITS["MARKED"]
    )
    both = EVENT_BITS["FORWARD"] | EVENT_BITS["BACKWARD"]
    assert events(a, 0.0, 100.0)[30.0] == [
        EVENT_KINDS["CROSSING"] | both | EVENT_BITS["MARKED"],
        -1,
    ]
    # A signalised crossing on a node: on both car edges there, carrying the explicit way's paint.
    code, ref = events(a, 0.0, 20.0)[0.0]
    assert code & EVENT_BITS["EXPLICIT"] and code & EVENT_BITS["SIGNALISED"] and ref == 0
    assert len(a["controllers"]) == 1 and a["controllers"][0][0] != b["controllers"][0][0]


def test_lane_width_and_speed_rules():
    flags = way_flags("tertiary", {})
    assert lanes("tertiary", flags, {}) == (1, 1, False, False)
    assert lanes("tertiary", flags, {"lanes": "3"}) == (2, 1, True, False)
    assert lanes("tertiary", flags, {"lanes": "4", "lanes:forward": "3"}) == (3, 1, True, False)
    assert lanes("tertiary", flags, {"lanes": "1"}) == (1, 1, True, True)
    oneway = way_flags("primary", {"oneway": "-1"})
    assert oneway & ONEWAY and lanes("primary", oneway, {"oneway": "-1", "lanes:backward": "2"})[
        :2
    ] == (2, 0)
    assert lanes("footway", way_flags("footway", {}), {})[:2] == (0, 0)
    assert kmh("residential", {}) == (40, False)
    assert kmh("residential", {"maxspeed": "30"}) == (30, True)
    assert kmh("residential", {"zone:maxspeed": "FI:30"}) == (30, True)
    assert kmh("primary", {"maxspeed": "FI:urban"}) == (50, True)
    a = attributes(
        "secondary",
        way_flags("secondary", {}),
        {"width": "9,5", "surface": "asphalt", "lit": "yes"},
    )
    assert (
        a["width"] == 9.5
        and a["flags"] & EDGE_FLAGS["WIDTH_TAGGED"]
        and a["flags"] & EDGE_FLAGS["LIT"]
    )
    assert a["car_density"] == 11 and a["foot_density"] == 7
    gravel = attributes("track", way_flags("track", {}), {"surface": "gravel"})
    assert not gravel["flags"] & EDGE_FLAGS["PAVED"]


def test_rule_paint_follows_lanes_crossings_and_signals():
    b = tile_graph(TILE_B)
    items = road_paint.markings(b)
    kinds = road_paint.summary(items)
    assert kinds["centre"]["polygons"] > 0  # two-way tertiary main street
    assert kinds["stop"]["polygons"] == 1  # signal stop position, forward lanes only
    assert "crosswalk" not in kinds  # the only crossing in t0_1_0 is unmarked
    a = road_paint.summary(road_paint.markings(tile_graph(TILE_A)))
    assert a["crosswalk"]["polygons"] >= 7  # zebra bars at the marked point crossing
    assert "edge" in a and "edge" in kinds  # 60 km/h rural road
    living = [p for p, _, _, _ in items if p.intersects(shapely.box(318, 135, 342, 145))]
    assert not living
    stop = next(p for p, _, _, kind in items if kind == "stop")
    x0, z0, x1, z1 = stop.bounds
    assert (
        x1 == pytest.approx(290.0) and z0 >= 100.0 - 0.01
    )  # right-hand (south) half, ending at the signal


def test_heights_cover_every_edge_and_densify():
    graph = tile_graph(TILE_A)
    out = json.loads(with_heights({"json": encode(graph)}, lambda x, z: 0.01 * x + 2)["json"])
    validate(out)
    for row, ys in zip(out["edges"], out["edgeHeights"], strict=True):
        assert ys is not None and len(ys) == len(row) // 2
        coords = [
            (out["nodes"][2 * row[0]], out["nodes"][2 * row[0] + 1]),
            *zip(row[4::2], row[5::2], strict=True),
        ]
        coords.append((out["nodes"][2 * row[1]], out["nodes"][2 * row[1] + 1]))
        assert all(abs(y - (0.01 * x + 2)) < 1e-3 for (x, _), y in zip(coords, ys, strict=True))
        assert all(
            ((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2) ** 0.5 <= 8.01
            for p, q in zip(coords, coords[1:], strict=False)
        )


def fixture_graphs():
    out = {}
    for name, tile in (("a", TILE_A), ("b", TILE_B)):
        graph = tile_graph(tile)
        out[name] = json.loads(
            with_heights({"json": encode(graph)}, lambda x, z: round(0.01 * x + 2, 3))["json"]
        )
    return out


def test_client_fixture_matches_the_generator():
    """game/tests/fixtures/roads-v2-seam.json feeds the client merge tests (roadGraph-v2.test.ts)."""
    assert json.loads(FIXTURE.read_text()) == fixture_graphs()


@pytest.mark.skipif(
    not list(Path.home().glob("turku3d/data/raw/osm/work-3x3/*.parquet")), reason="no OSM clip"
)
def test_real_tiles_agree_on_shared_edges(monkeypatch):
    from turku3d.roads.tile_graph import prepare

    monkeypatch.delenv("TURKU3D_DATA", raising=False)
    path = sorted(Path.home().glob("turku3d/data/raw/osm/work-3x3/*.parquet"))[-1]
    graphs = [prepare(path, t.bbox, t.id, t.size) for t in (TILE_A, TILE_B)]
    pieces = {}
    for graph in graphs:
        for eid, _, attrs, _ in rows(graph):
            pieces.setdefault(eid, []).append(attrs)
    shared = [p for p in pieces.values() if len(p) > 1]
    assert shared
    for parts in shared:
        assert (
            len(
                {
                    (p["length"], p["lanes_fwd"], p["lanes_bwd"], p["width"], p["flags"])
                    for p in parts
                }
            )
            == 1
        )
    assert all(r[1][2] & CAR or r[2]["lanes_fwd"] == 0 for g in graphs for r in rows(g))


def test_paint_colours_survive_the_layer_round_trip(tmp_path):
    """Yellow centre lines live in COLOR_0 of the one paint mesh; wall assembly re-reads layers."""
    import numpy as np
    from turku3d.layers import write_visual
    from turku3d.package.gltf import GlbMesh
    from turku3d.wall_surfaces import layer_meshes

    colours = np.array([road_paint.YELLOW] * 3 + [road_paint.WHITE] * 3, np.float32)
    mesh = GlbMesh(
        "ground_paint",
        np.array([[0, 0, 0], [1, 0, 0], [0, 0, 1], [2, 0, 0], [3, 0, 0], [2, 0, 1]], float),
        np.array([[0, 2, 1], [3, 5, 4]]),
        colors=colours,
        extras=dict(layer="markings", object_id="paint_t0_0_0", material_key="road_paint"),
    )
    write_visual(tmp_path / "ground.vis.glb", [mesh])
    (read,) = layer_meshes([tmp_path / "ground.vis.glb"])
    assert np.allclose(read.colors, colours)


def test_deck_paint_lies_on_the_built_deck_and_ground_paint_stays_below():
    """A bridge edge's paint sits on the built deck (clipped to the tile); a street under it does not."""
    import numpy as np
    from turku3d.coarse.bridges import Deck
    from turku3d.coarse.surfaces import DECK_PAINT_LIFT, paint_visual
    from turku3d.col.materials import mat_id

    tile = TILE_A
    # Two road triangles, and a vertical road triangle (no plane) under the street's paint.
    positions = np.array(
        [[0, 1, 0], [250, 1, 0], [250, 1, 250], [0, 1, 250], [121, 1, 150.5], [125, 1, 150.5]],
        float,
    )
    positions = np.r_[positions, [[123, 3, 150.5]]]
    indices = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6]])
    materials = np.array([mat_id("road")] * 3)
    line = shapely.LineString([(100, 120), (300, 120)])
    deck = Deck({"width": 10.0}, line, np.array([0.0, 200.0]), np.array([8.0, 10.0]), [], None)
    on_deck = (shapely.box(150, 118, 300, 122), road_paint.YELLOW, True, "centre")
    under = (shapely.box(120, 150, 130, 151), road_paint.WHITE, False, "stop")
    keep = np.ones(3, bool)
    mesh = paint_visual(tile, positions, indices, materials, keep, [on_deck, under], [deck])
    p = mesh.positions + np.asarray(tile.origin)
    yellow = np.all(np.isclose(mesh.colors, road_paint.YELLOW), axis=1)
    assert p[yellow][:, 0].max() <= 250.0 + 1e-6  # clipped to the tile; the neighbour paints on
    expected = np.interp(p[yellow][:, 0] - 100, [0, 200], [8, 10]) + DECK_PAINT_LIFT
    assert np.allclose(p[yellow][:, 1], expected, atol=1e-3)
    assert np.allclose(p[~yellow][:, 1], 1.008)


if __name__ == "__main__":
    FIXTURE.write_text(json.dumps(fixture_graphs(), separators=(",", ":")) + "\n")
