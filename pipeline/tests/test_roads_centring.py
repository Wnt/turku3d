"""Car edges onto the middle of the surveyed carriageway (turku3d.roads.centring)."""

from collections import Counter

import shapely
from turku3d.roads import centring
from turku3d.roads.attributes import HIGHWAYS
from turku3d.roads.tile_graph import ATTR_COLUMNS, EDGE_FLAGS, NODE_FLAGS


def graph(*lines, flags=0, car=True):
    nodes, edges, attrs, events = [], [], [], []
    for line in lines:
        a = len(nodes) // 2
        nodes += [*line[0], *line[-1]]
        edges.append([a, a + 1, 2 if car else 1, 0.0, *[v for p in line[1:-1] for v in p]])
        row = [0] * len(ATTR_COLUMNS)
        row[ATTR_COLUMNS.index("s1")] = row[ATTR_COLUMNS.index("length")] = 40.0
        row[ATTR_COLUMNS.index("flags")] = flags
        attrs.append(row)
        events.append([20.0, 2, -1])
    return {"revision": 2, "bits": {"car": 2, "edge_flags": dict(EDGE_FLAGS)}, "nodes": nodes, "edges": edges,
            "edgeAttrColumns": list(ATTR_COLUMNS), "edgeAttrs": attrs, "edgeEvents": events}


def interior(edge):
    return list(zip(edge[4::2], edge[5::2], strict=True))


def test_an_edge_on_the_pavement_moves_to_the_carriageway_middle_with_fixed_ends():
    """Damstraat: the OSM line runs on the carriageway's north edge, the lane on the pavement."""
    road = shapely.box(-10, 1.0, 60, 5.0)  # carriageway z 1..5, the OSM line at z 0
    g = graph([(0, 0), (40, 0)])
    stats = Counter()
    centring.centre(g, road, stats)
    edge = g["edges"][0]
    assert g["nodes"] == [0, 0, 40, 0]  # junction and seam nodes stay
    middle = [z for x, z in interior(edge) if centring.TAPER <= x <= 40 - centring.TAPER]
    assert middle and all(abs(z - 3.0) < 0.05 for z in middle)
    assert all(0 <= z <= 3.0 + 1e-6 for _, z in interior(edge))  # tapered in from both ends
    assert edge[3] > 40 and stats["moved_edges"] == 1
    assert 20 < g["edgeEvents"][0][0] <= edge[3]  # the crossing keeps its share of the edge


def test_bridges_tunnels_far_and_wide_carriageways_and_paths_stay():
    line = [(0, 0), (40, 0)]
    for flags, road, car in ((EDGE_FLAGS["BRIDGE"], shapely.box(-10, 1, 60, 6), True),
                             (EDGE_FLAGS["TUNNEL"], shapely.box(-10, 1, 60, 6), True),
                             (0, shapely.box(-10, 4, 60, 9), True),  # another street 4 m away
                             (0, shapely.box(-10, -8, 60, 8), True),  # a 16 m square
                             (0, shapely.box(-10, 1, 60, 6), False)):  # a footway
        g = graph(line, flags=flags, car=car)
        centring.centre(g, road)
        assert g["edges"][0][4:] == [] and g["edges"][0][3] == 0.0


def test_a_centred_edge_is_left_alone():
    g = graph([(0, 3.5), (20, 3.5), (40, 3.5)])
    centring.centre(g, shapely.box(-10, 1, 60, 6))
    assert g["edges"][0][4:] == [20, 3.5]


def test_an_edge_off_every_street_part_stays_and_the_shift_tapers_out_towards_it():
    """Rokin garage ramp: the parking aisle leaves the carriageway's verge and runs down a ramp that the street survey
    does not cover, 3-4 m beside the carriageway; it was pulled onto the ramp wall."""
    road = shapely.box(-10, 4.0, 90, 7.0)  # carriageway z 4..7
    verge = shapely.box(-10, 3.0, 90, 4.0)
    streets = shapely.union_all([road, verge, shapely.box(-10, -1.0, 30, 3.0)])  # pavement under x < 30 only
    g = graph([(0, 1.5), (40, 1.5), (80, 1.5)])  # OSM line at z 1.5: on the pavement for x < 30, then the ramp
    centring.centre(g, road, Counter(), streets)
    pts = interior(g["edges"][0])
    assert all(abs(z - 1.5) < 1e-6 for x, z in pts if x > 30 + centring.OFF_STREET)  # the ramp stays
    on_pavement = [z for x, z in pts if centring.TAPER <= x <= 30 - centring.TAPER]
    assert on_pavement and all(z > 4.0 for z in on_pavement)  # still centred where the pavement is surveyed
    # Without the street survey the old behaviour holds: every vertex moves.
    g = graph([(0, 1.5), (40, 1.5), (80, 1.5)])
    centring.centre(g, road)
    assert all(z > 3.0 for x, z in interior(g["edges"][0]) if centring.TAPER <= x <= 80 - centring.TAPER)


def test_a_short_unsurveyed_gap_between_placed_vertices_follows_them():
    road = shapely.box(-10, 1.0, 90, 5.0)
    streets = shapely.difference(shapely.box(-10, -2.0, 90, 6.0), shapely.box(38, -2.0, 42, 0.6))  # a 4 m hole
    g = graph([(0, 0), (80, 0)])
    centring.centre(g, road, Counter(), streets)
    middle = [z for x, z in interior(g["edges"][0]) if centring.TAPER <= x <= 80 - centring.TAPER]
    assert middle and all(abs(z - 3.0) < 0.05 for z in middle)


def test_an_untagged_two_way_edge_takes_the_narrower_surveyed_carriageway_width():
    """Grimburgwal: a two-way canal street with the 6.5 m default over a 3.5 m carriageway put the eastbound lane 1.6 m
    off the middle, over the quay edge and its lamp posts. Tagged widths, wider carriageways, multi-lane ways and
    edges off the survey keep their width."""
    road = shapely.box(-10, 1.75, 60, 5.25)  # 3.5 m carriageway centred on the OSM line at z 3.5
    columns = list(ATTR_COLUMNS)

    def width_after(width, flags=0, lanes=(1, 1), carriageway=road):
        g = graph([(0, 3.5), (40, 3.5)], flags=flags)
        row = g["edgeAttrs"][0]
        row[columns.index("width")] = width
        row[columns.index("lanes_fwd")], row[columns.index("lanes_bwd")] = lanes
        stats = Counter()
        centring.centre(g, carriageway, stats)
        return row[columns.index("width")], stats["narrowed_edges"]

    assert width_after(6.5) == (3.5, 1)  # already centred: no shift, still narrowed
    assert width_after(6.5, flags=EDGE_FLAGS["WIDTH_TAGGED"]) == (6.5, 0)
    assert width_after(3.2) == (3.2, 0)  # a one-lane default narrower than the street stays
    assert width_after(13.0, lanes=(2, 2)) == (13.0, 0)
    assert width_after(6.5, carriageway=shapely.box(-10, 2.5, 60, 4.5)) == (centring.MIN_WIDTH, 1)
    assert width_after(6.5, carriageway=shapely.box(-10, 8, 60, 12)) == (6.5, 0)  # another street: not measured


def test_finnish_register_carriageway_moves_lanes_only_beside_mapped_kerbside_bays():
    """Turku Kristiinankatu: the 11.7 m Ajorata holds 5 m of perpendicular bays (OSM parking=street_side), the OSM line
    runs 1.5 m off the middle of the 6.2 m left to drive and the northbound lane ran into the parked cars' noses."""
    carriageway = shapely.box(-10, -4.4, 90, 7.3)
    bay = shapely.box(20, 1.8, 60, 7.0)
    lot = shapely.box(20, 7.3, 60, 30)  # an off-street lot beside the kerb is no bays
    bays = centring.kerbside_bays(carriageway, [bay, lot])
    assert bays.equals(bay)
    assert centring.kerbside_bays(carriageway, [lot]).is_empty and centring.kerbside_bays(carriageway, []).is_empty
    g = graph([(0, 0), (80, 0)])
    width = ATTR_COLUMNS.index("width")
    g["edgeAttrs"][0][width] = 6.5
    stats = Counter()
    centring.centre(g, carriageway, stats, bays=bays)
    middle = [z for x, z in interior(g["edges"][0]) if 30 <= x <= 50]
    assert middle and all(abs(z - (-4.4 + 1.8) / 2) < 0.05 for z in middle)  # the middle of the 6.2 m driving part
    assert all(abs(z) < 0.05 for x, z in interior(g["edges"][0]) if x < 4 or x > 76)  # away from the bays it stays
    assert g["edgeAttrs"][0][width] == 6.2 and stats["narrowed_edges"] == 1
    # A four-lane street beside the same bays keeps its line, and so does every street without bays.
    wide = graph([(0, 0), (80, 0)])
    wide["edgeAttrs"][0][ATTR_COLUMNS.index("lanes_fwd")] = wide["edgeAttrs"][0][ATTR_COLUMNS.index("lanes_bwd")] = 2
    centring.centre(wide, carriageway, bays=bays)
    assert wide["edges"][0][4:] == []
    bare = graph([(0, 0), (80, 0)])
    centring.centre(bare, carriageway, bays=shapely.Polygon())
    assert bare["edges"][0][4:] == []


def test_lanes_stay_beside_bays_on_a_street_wider_than_one_lane_pair():
    """A 16.5 m Turku Ajorata with 2.5 m parallel bays on alternate sides: the part beside a bay reaches past the cut
    (and is wider than WIDE), so the lanes keep the OSM line instead of weaving 3 m towards whichever side is free."""

    def shifted(carriageway, bays):
        g = graph([(0, 0), (80, 0)])
        g["edgeAttrs"][0][ATTR_COLUMNS.index("width")] = 7.0
        g["edgeAttrs"][0][ATTR_COLUMNS.index("lanes_fwd")] = g["edgeAttrs"][0][ATTR_COLUMNS.index("lanes_bwd")] = 1
        centring.centre(g, carriageway, bays=bays)
        return max((abs(z) for x, z in interior(g["edges"][0])), default=0.0)

    assert shifted(shapely.box(-10, -8.25, 90, 8.25),
                   shapely.union_all([shapely.box(10, -8.25, 30, -5.75), shapely.box(40, 5.75, 60, 8.25)])) < 0.05
    # 14 m of street beside 6 m of bays (Turku way 1111837009): the far kerb is past the cut, stays too.
    assert shifted(shapely.box(-10, -4, 90, 16), shapely.box(10, 10, 70, 16)) < 0.05
    # A bay pocket beside an 11 m street whose line runs 1.5 m off its middle: the 7 m of lanes clear the pocket by
    # more than BAY_CLEARANCE, so the line gets no kink there (Turku way 1118382891); a pocket the lanes reach moves them.
    assert shifted(shapely.union_all([shapely.box(-10, -7, 90, 4), shapely.box(30, 4, 50, 6.5)]),
                   shapely.box(30, 4, 50, 6.5)) < 0.05
    assert shifted(shapely.union_all([shapely.box(-10, -7, 90, 3), shapely.box(30, 3, 50, 5.5)]),
                   shapely.box(30, 3, 50, 5.5)) > 1.4


def test_lanes_beside_bays_drift_at_a_limited_slope():
    """Beside bays a lane drifts at most BAY_SLOPE per metre: a 10 m stub between two junctions moves 0.75 m in its
    middle rather than kinking the full 2 m, and the shift leaves the end nodes at the same slope."""
    carriageway = shapely.box(-10, -7, 30, 3)
    g = graph([(0, 0), (12, 0)])
    g["edgeAttrs"][0][ATTR_COLUMNS.index("width")] = 7.0
    centring.centre(g, carriageway, bays=shapely.box(-10, 1, 30, 3))
    points = [(0.0, 0.0), *interior(g["edges"][0]), (12.0, 0.0)]
    assert 0.5 < max(abs(z) for _, z in points) <= centring.BAY_SLOPE * 6 + 1e-6
    assert all(abs(z1 - z0) <= centring.BAY_SLOPE * (x1 - x0) + 1e-6 for (x0, z0), (x1, z1) in zip(points, points[1:], strict=False))


def test_a_driveway_junction_beside_bays_moves_with_the_lane():
    """Turku Kristiinankatu is cut into 9-13 m edges by driveway junctions; pinned there its lane could not leave the
    parked cars' noses. A node where only driveways (service) join the street moves with it; a junction with another
    street and a tile-seam node stay."""

    def street(side="service", flags=0, bays=shapely.box(-10, 1.8, 70, 7.0)):
        nodes = [0, 0, 30, 0, 60, 0, 30, -12]  # a street a - n - b, a driveway n - d away from the bays
        edges, attrs = [], []
        for a, b, highway in ((0, 1, "residential"), (1, 2, "residential"), (1, 3, side)):
            edges.append([a, b, 2, 30.0])
            row = [0] * len(ATTR_COLUMNS)
            row[ATTR_COLUMNS.index("way")] = 7 if highway == "residential" else 8
            row[ATTR_COLUMNS.index("highway")] = HIGHWAYS.index(highway)
            row[ATTR_COLUMNS.index("width")] = 6.5
            row[ATTR_COLUMNS.index("lanes_fwd")] = row[ATTR_COLUMNS.index("lanes_bwd")] = 1
            attrs.append(row)
        g = {"revision": 2, "bits": {"car": 2, "edge_flags": dict(EDGE_FLAGS), "node_flags": dict(NODE_FLAGS),
                                     "highways": list(HIGHWAYS)},
             "nodes": nodes, "edges": edges, "edgeAttrColumns": list(ATTR_COLUMNS), "edgeAttrs": attrs,
             "edgeEvents": [None, None, [6.0, 2, -1]], "nodeFlags": [0, flags, 0, 0]}
        stats = Counter()
        centring.centre(g, shapely.box(-10, -4.4, 70, 7.3), stats, bays=bays)
        return g, stats

    g, stats = street()
    assert abs(g["nodes"][3] - (-1.3)) < 0.05 and stats["moved_nodes"] == 1  # onto the middle of the 6.2 m part
    for edge in g["edges"][:2]:
        z = [z for _, z in interior(edge)]
        assert z and all(-1.3 - 0.05 <= v <= 0.05 for v in z)
        points = [g["nodes"][2 * edge[0]: 2 * edge[0] + 2], *interior(edge), g["nodes"][2 * edge[1]: 2 * edge[1] + 2]]
        assert all(abs(z1 - z0) <= centring.BAY_SLOPE * abs(x1 - x0) + 0.02
                   for (x0, z0), (x1, z1) in zip(points, points[1:], strict=False))
    driveway = g["edges"][2]
    assert abs(driveway[3] - 10.7) < 0.1 and abs(g["edgeEvents"][2][0] - 6.0 * 10.7 / 12) < 0.05
    seam, _ = street(flags=NODE_FLAGS["SEAM"])
    assert seam["nodes"][2:4] == [30, 0]
    crossing, _ = street(side="residential")
    assert crossing["nodes"][2:4] == [30, 0]
    # The bays end just past the driveway: the node still moves and the lane drifts back onto the line beyond it.
    end, _ = street(bays=shapely.box(-10, 1.8, 33, 7.0))
    assert abs(end["nodes"][3] - (-1.3)) < 0.05
    beyond = interior(end["edges"][1])
    assert beyond and -1.3 < beyond[0][1] < -0.3 and all(abs(z) < 0.05 for x, z in beyond if x >= 40)


def test_seam_move_uses_unclipped_source_and_keeps_its_original_identity():
    from turku3d import config as C
    from turku3d.roads.tile_graph import Way, build_tile_graph

    # Oblique OSM way; moving it shifts both x and z, off the nominal tile boundary.
    ways = [Way([(180, 90), (320, 110)], 3, 42, "residential", {"highway": "residential"})]
    line = shapely.LineString(ways[0].coords)
    carriageway = line.buffer(5, cap_style="square")
    bays = shapely.Polygon([(170, 91), (330, 114), (330, 118), (170, 95)])
    origins, moved = [], []
    for i in (0, 1):
        tile = C.VisualTile(0, i, 0)
        context = {}
        g = build_tile_graph(ways, [], [], tile.bbox, tile.id, tile.size, context)
        centring.centre(g, carriageway, Counter(), bays=bays, seam_context=context)
        assert len(g["seamOrigins"]) == 1
        n, origin = next(iter(g["seamOrigins"].items()))
        origins.append(origin)
        moved.append(g["nodes"][2 * int(n):2 * int(n) + 2])
    assert origins[0] == origins[1] == [250, 100]
    assert moved[0] == moved[1]
    assert moved[0][0] != 250 and moved[0][1] < 99


def test_a_short_clipped_stub_uses_the_same_seam_rule_as_its_long_neighbour():
    from turku3d import config as C
    from turku3d.roads.tile_graph import Way, build_tile_graph
    # Real end is 12 m from the seam; both sides apply its slope limit, not their clipped lengths.
    way = Way([(100, 100), (262, 100)], 3, 42, "residential", {"highway": "residential"})
    moved = []
    for i in (0, 1):
        tile = C.VisualTile(0, i, 0)
        context = {}
        g = build_tile_graph([way], [], [], tile.bbox, tile.id, tile.size, context)
        centring.centre(g, shapely.box(90, 95, 280, 105), bays=shapely.box(90, 101, 280, 105), seam_context=context)
        n = next(iter(g["seamOrigins"]))
        moved.append(g["nodes"][2 * int(n):2 * int(n) + 2])
    assert moved == [[250, 98.2], [250, 98.2]]
