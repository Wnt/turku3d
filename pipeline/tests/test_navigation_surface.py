"""Routes must use the same final floors as the player, including stacked crossings."""
import json
from types import SimpleNamespace

import numpy as np

from turku3d.coarse.route_surface import Surface, bake
from turku3d.col.materials import mat_id
from turku3d.roads import encode


def cell(floors):
    triangles, materials = [], []
    for x0, x1, z0, z1, y0, y1, material in floors:
        a, b, c, d = (x0, y0, z0), (x0, y0, z1), (x1, y1, z0), (x1, y1, z1)
        triangles.extend([[a, b, c], [c, b, d]])
        materials.extend([mat_id(material)] * 2)
    positions = np.asarray(triangles, float).reshape(-1, 3)
    return SimpleNamespace(world_positions=lambda: positions,
                           indices=np.arange(len(positions)).reshape(-1, 3),
                           materials=np.array(materials))


def graph(nodes, edges, heights):
    return dict(json=encode(dict(format="turku3d-roadgraph", version=1, frame="engine-xz",
                                revision=2, nodes=nodes, edges=edges, edgeHeights=heights,
                                edgeIds=list(range(len(edges))), edgeEvents=[None] * len(edges),
                                edgeAttrColumns=["s0", "s1", "length"],
                                edgeAttrs=[[0, e[3], e[3]] for e in edges], controllers=[],
                                nodeFlags=[0] * (len(nodes) // 2))))


def test_bridge_endpoint_owns_approach_but_not_lower_crossing():
    physics = cell([(0, 10, -2, 2, 1, 1, "sidewalk"),
                    (0, 5, -2, 2, 6, 6, "bridge_deck"),
                    (5, 10, -2, 2, 6, 4, "sidewalk")])
    roads = graph([0, 0, 5, 0, 10, 0, 0, 1, 10, 1],
                  [[0, 1, 1, 5], [1, 2, 1, 5], [3, 4, 1, 10]], [[6, 6], None, [1, 1]])
    out = json.loads(bake(roads, [physics], lambda x, z: 1 if x <= 5 else 4)["json"])
    assert out["edgeHeights"][0] == [6, 6]
    assert out["edgeHeights"][1][0] == 6
    assert out["edgeHeights"][1][-1] == 4
    assert set(out["edgeHeights"][2]) == {1}


def test_final_triangle_grade_breaks_replace_coarse_height_chord():
    physics = cell([(0, 3, -1, 1, 0, 0, "sidewalk"),
                    (3, 4, -1, 1, 0, 1, "sidewalk"),
                    (4, 10, -1, 1, 1, 1, "sidewalk")])
    out = json.loads(bake(graph([0, 0, 10, 0], [[0, 1, 1, 10]], [None]),
                          [physics], lambda x, z: 0)["json"])
    xs = [0, *out["edges"][0][4::2], 10]
    query = np.linspace(0, 10, 101)
    np.testing.assert_allclose(np.interp(query, xs, out["edgeHeights"][0]),
                               np.clip(query - 3, 0, 1), atol=.002)


def test_stairs_keep_individual_tread_rises():
    physics = cell([(i, i + 1, -1, 1, i * .2, i * .2, "stair_tread_helper") for i in range(6)])
    out = json.loads(bake(graph([0, 0, 6, 0], [[0, 1, 1, 6]], [None]),
                          [physics], lambda x, z: x / 6)["json"])
    xs = [0, *out["edges"][0][4::2], 6]
    np.testing.assert_allclose(np.interp(np.arange(6) + .5, xs, out["edgeHeights"][0]),
                               np.arange(6) * .2, atol=.002)


def test_water_sensor_and_ceiling_do_not_replace_floor():
    physics = cell([(0, 10, -1, 1, 2, 2, "sidewalk"),
                    (0, 10, -1, 1, 3, 3, "water"),
                    (0, 10, -1, 1, 2.8, 2.8, "bridge_deck")])
    # Downward-facing soffit is closer to the survey reference than the floor.
    physics.indices[-2:] = physics.indices[-2:, ::-1]
    surface = Surface([physics])
    np.testing.assert_allclose(surface.heights([[2, 0]], [2.9]), [2])


def test_missing_graph_and_legacy_graph_remain_usable():
    assert bake(None, [], lambda x, z: 0) is None
    roads = dict(json=encode(dict(nodes=[0, 0, 1, 0], edges=[[0, 1, 1, 1]])))
    assert bake(roads, [], lambda x, z: 0) is roads


def test_millimetre_collision_seam_cannot_drop_route_to_river_bed():
    physics = cell([(0, 5, -1, 1, 6, 6, "bridge_deck"),
                    (5.006, 10, -1, 1, 6, 6, "bridge_deck"),
                    (0, 10, -1, 1, 1, 1, "gravel")])
    np.testing.assert_allclose(Surface([physics]).heights([[5.003, 0]], [6]), [6])
