"""Street graphs of a location (turku3d.world.roads)."""

import shapely

from turku3d.roads.tile_graph import Way
from turku3d.world.roads import within_ground


def test_a_street_is_cut_where_the_location_has_no_ground():
    ground = shapely.box(0, 0, 62.5, 62.5)
    street = Way([(-20.0, 30.0), (100.0, 30.0)], 7, 1, "residential", {})
    outside = Way([(80.0, 80.0), (120.0, 80.0)], 7, 2, "residential", {})
    points = [(10.0, 10.0, {"highway": "traffic_signals"}), (90.0, 10.0, {"highway": "stop"})]
    ways, kept, crossings = within_ground([street, outside], points, [], ground)
    assert [w.way for w in ways] == [1]
    xs = [x for x, _ in ways[0].coords]
    assert min(xs) == 0.0 and max(xs) == 62.5
    assert kept == [points[0]] and crossings == []
