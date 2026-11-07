"""Car edges onto the middle of the surveyed carriageway (turku3d.roads.centring)."""

from collections import Counter

import shapely
from turku3d.roads import centring
from turku3d.roads.tile_graph import ATTR_COLUMNS, EDGE_FLAGS


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
