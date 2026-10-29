"""The upper road owns its terrain surface; the lower passage retains full clearance."""

import json
from dataclasses import replace

import numpy as np
import shapely
from turku3d import config as C
from turku3d.coarse import road_top, tunnels
from turku3d.coarse.build import extrude, grid_mesh
from turku3d.coarse.inputs import Inputs
from turku3d.coarse.surfaces import grid_height


def ways(x=100):
    line = shapely.LineString([(x, 80), (x, 120)])
    return road_top.carried_roads(
        [("under", line, {"highway": "cycleway", "tunnel": "yes", "layer": "-1"})],
        [
            (
                shapely.LineString([(x - 30, 100), (x + 30, 100)]),
                {"highway": "secondary", "lanes": "2"},
            )
        ],
    )


def scene(tile, x):
    def ground(a, b):
        return 21 + np.clip((abs(a - x) - 1.5) / 4, 0, 1) * 3.5 + b * 0.002

    axis = np.arange(129) * tile.size / 128
    heights = np.array(
        [[ground(a + tile.origin[0], b + tile.origin[2]) for a in axis] for b in axis]
    )
    pos, idx = grid_mesh(tile, heights)

    def original(a, b):
        return grid_height(tile, heights, a, b)

    fixed, lifted = road_top.repair(tile, pos, ways(x), ground)
    sample = road_top.sampler(tile, fixed, original, lifted)
    result = tunnels.produce(tile, ways(x), fixed, idx, [], extrude, ground, original, sample)
    return pos, fixed, idx, sample, result


def test_road_deck_is_continuous_original_terrain_with_clear_lower_passage():
    tile = C.VisualTile(0, 0, 0)
    pos, fixed, idx, sample, result = scene(tile, 100)
    assert np.count_nonzero(fixed[:, 1] > pos[:, 1]) > 0
    deck = shapely.box(98.5, 85, 101.5, 115)
    retained = shapely.union_all(shapely.polygons(fixed[idx[result.keep]][:, :, [0, 2]]))
    edges = [m for m in result.meshes if m.object_id == "tunnel:ground-edge"]
    retained = shapely.union_all(
        [
            retained,
            *[
                shapely.union_all(shapely.polygons(m.positions[m.indices][:, :, [0, 2]]))
                for m in edges
            ],
        ]
    )
    assert deck.difference(retained).area < 1e-8
    np.testing.assert_allclose([sample(x, 100) for x in np.linspace(86, 114, 57)], 24.7)
    roof = next(m for m in result.meshes if m.object_id.endswith(":roof"))
    np.testing.assert_allclose(
        roof.positions[:, 1], [sample(x, z) - 0.3 for x, _, z in roof.positions]
    )
    # Every ceiling face looks down. There is no duplicate upward grey slab.
    triangles = roof.positions[roof.indices]
    assert (
        np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])[:, 1] < 0
    ).all()
    floor = next(m for m in result.meshes if m.object_id.endswith(":floor"))
    interior = floor.positions[(floor.positions[:, 2] >= 80) & (floor.positions[:, 2] <= 120)]
    assert min(sample(x, z) - 0.3 - y for x, y, z in interior) >= 2.5 - 1e-8
    assert interior[:, 1].max() < 21.3
    for mesh in result.meshes:
        if mesh.object_id.startswith("tunnel:under:") and not mesh.object_id.endswith(":floor"):
            assert max(y - sample(x, z) for x, y, z in mesh.positions) < 0
    graph = {
        "nodes": [90, 100, 110, 100, 100, 80, 100, 120],
        "edges": [[0, 1, 3, 20], [2, 3, 1, 40]],
    }
    roads = json.loads(result.roads({"json": json.dumps(graph)})["json"])
    assert roads["edgeHeights"][0] is None
    assert max(roads["edgeHeights"][1]) < 21.3


def test_deck_repair_is_bitwise_identical_on_tile_boundary():
    a = scene(C.VisualTile(0, 0, 0), 250)
    b = scene(C.VisualTile(0, 1, 0), 250)
    left = a[1][a[1][:, 0] == 250]
    right = b[1][b[1][:, 0] == 250]
    np.testing.assert_array_equal(left, right)
    np.testing.assert_array_equal(a[0][a[0][:, 0] < 235], a[1][a[1][:, 0] < 235])


def test_only_crossing_surface_roads_select_box_underpass():
    line = shapely.LineString([(0, 0), (0, 20)])
    road = (shapely.LineString([(-10, 10), (10, 10)]), {"highway": "residential"})
    for tags in ({"tunnel": "building_passage"}, {"vault": "barrel"}):
        assert "road_top" not in road_top.carried_roads([("a", line, tags)], [road])[0][2]
    for other in ([(0, 20), (10, 20)], [(5, 0), (5, 20)]):
        assert (
            "road_top"
            not in road_top.carried_roads(
                [("a", line, {})], [(shapely.LineString(other), road[1])]
            )[0][2]
        )


def test_carried_road_excludes_tree_crowns_and_parked_car_footprints():
    from turku3d.coarse.forests import produce
    from turku3d.coarse.parked import place
    from turku3d.parked.placement import Feature, footprint
    from turku3d.vegetation.trees import Tree

    tile = C.VisualTile(0, 0, 0)
    mask = road_top.exclusion(ways())
    empty = Inputs(
        np.empty((0, 3)), np.empty((0, 0)), np.empty((0, 0, 3)), [], [], [], None, {}, {}
    )
    mapped = [Tree(str(z), 100, z, "osm", crown_radius=4) for z in (100, 106, 112)]
    inputs = replace(
        empty, forests=[("row", shapely.LineString([(90, 106), (120, 106)]), "tree_row", "")]
    )
    trees, bushes = produce(tile, inputs, lambda x, z: 25, mapped, mask)
    assert trees and all(
        mask.distance(shapely.Point(t.x, t.z)) > t.crown_radius for t in trees + bushes
    )
    parking = Feature("lot", shapely.box(90, 90, 120, 120), {"amenity": "parking"})
    cars = place(tile, ([parking], []), np.full((129, 129), 25), carried=mask)
    assert cars
    assert all(
        not mask.intersects(footprint(c["x"], c["z"], c["yaw"], c["width"], c["length"]))
        for c in cars
    )
