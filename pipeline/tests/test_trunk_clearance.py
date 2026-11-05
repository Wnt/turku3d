"""No drawn trunk on walked or driven ground: the shared rule, its generator step and path-trees@1."""

import hashlib
import json
import random

import numpy as np
import pandas as pd
import shapely
from turku3d import config as C
from turku3d.migrations import core, path_trees, registry
from turku3d.vegetation import clearance
from turku3d.vegetation.trees import Tree

TILE = C.VisualTile(0, 0, 0)
DRIVE = shapely.LineString([(0, 50), (250, 50)]).buffer(2, cap_style="flat")  # a 4 m private drive
LANE = [(DRIVE, True)]


def trunk(object_id, x, z, base=0.6):
    return clearance.Trunk(object_id, object_id, x, z, base)


def test_real_trees_move_off_the_drive_generated_ones_go():
    trunks = [trunk("lidar:a", 100, 50.8), trunk("area:1:x", 120, 50.8), trunk("bush:area:1:y", 140, 49),
              trunk("lidar:far", 160, 60)]
    decisions = clearance.place(trunks, LANE)
    x, z = decisions["lidar:a"]
    assert abs(x - 100) < 1e-6 and DRIVE.distance(shapely.Point(x, z)) >= 0.6 + clearance.CLEARANCE
    assert np.hypot(x - 100, z - 50.8) <= clearance.MOVE
    assert decisions["area:1:x"] is None and decisions["bush:area:1:y"] is None
    assert "lidar:far" not in decisions


def test_out_of_reach_register_tree_stays_other_real_trees_go():
    square = shapely.box(0, 0, 100, 100)
    decisions = clearance.place([trunk("puut:k", 50, 50), trunk("osm:1", 52, 50), trunk("lidar:x", 54, 50)], [(square, True)])
    assert "puut:k" not in decisions
    assert decisions["osm:1"] is None and decisions["lidar:x"] is None


def test_moves_avoid_buildings_neighbours_and_the_tile_edge():
    house = shapely.box(90, 53, 110, 70)
    assert clearance.place([trunk("lidar:a", 100, 51.5)], LANE)["lidar:a"][1] > 53
    # The house beside the drive takes the only spot within reach.
    assert clearance.place([trunk("lidar:a", 100, 51.5)], LANE, [house])["lidar:a"] is None
    # A free spot taken by another tree: the crown is that tree.
    decisions = clearance.place([trunk("lidar:a", 100, 51.5), trunk("lidar:b", 100, 53.6)], LANE)
    assert decisions["lidar:a"] is None or decisions["lidar:a"][1] < 50
    decisions = clearance.place([trunk("lidar:e", 249.5, 50.5)], [(DRIVE.union(shapely.box(240, 0, 260, 49)), True)],
                                bounds=(0, 0, 250, 250))
    assert decisions["lidar:e"] is None


def test_visit_order_does_not_change_the_result():
    rng = random.Random(4)
    trunks = [trunk(f"lidar:{k}", rng.uniform(0, 250), rng.uniform(46, 54)) for k in range(60)]
    first = clearance.place(trunks, LANE)
    rng.shuffle(trunks)
    assert clearance.place(trunks, LANE) == first


def local_parquet(path, columns, geometries):
    """A minimal source fixture: ordinary Parquet with local WKB (``spatial._area``)."""
    pd.DataFrame({**columns, "geometry_local": [shapely.to_wkb(g) for g in geometries]}).to_parquet(path)
    return path


def osm_parquet(tmp_path, rows):
    return local_parquet(tmp_path / "osm.parquet", {"osm_type": ["way"] * len(rows), "osm_id": list(range(len(rows))),
                                                   "tags": [json.dumps(t) for t, _ in rows],
                                                   "highway": [t.get("highway") for t, _ in rows]},
                         [g for _, g in rows])


def test_walked_ground_keeps_private_drives_and_drops_bridges(tmp_path):
    drive = ({"highway": "service", "service": "driveway", "access": "private"}, shapely.LineString([(0, 50), (250, 50)]))
    bridge = ({"highway": "footway", "bridge": "yes"}, shapely.LineString([(0, 150), (250, 150)]))
    path = osm_parquet(tmp_path, [drive, bridge])
    walked = shapely.union_all([g for g, lane in clearance.walked_ground(TILE, path, None) if lane])
    assert walked.covers(shapely.Point(100, 51.9)) and not walked.covers(shapely.Point(100, 52.1))
    assert not walked.covers(shapely.Point(100, 150))


def test_medians_are_not_walked_ground(tmp_path):
    parts = [("Jalkakäytävä", shapely.box(0, 0, 250, 3)), ("Välikaista", shapely.box(0, 10, 250, 13))]
    streets = local_parquet(tmp_path / "GIS_Katualueet.parquet",
                            {"feature_key": ["a", "b"], "Katuosalaji": [k for k, _ in parts]}, [g for _, g in parts])
    rows = clearance.walked_ground(TILE, None, streets)
    walked = shapely.union_all([g for g, _ in rows])
    assert walked.covers(shapely.Point(10, 1)) and not walked.covers(shapely.Point(10, 11))
    assert [lane for _, lane in rows] == [False]  # a sidewalk is paving: narrow paving acts as a lane
    lanes, wide = clearance.split(rows)
    assert lanes.covers(shapely.Point(10, 1)) and wide.is_empty


def test_real_trees_stand_in_wide_paving_generated_ones_do_not():
    square = (shapely.box(0, 0, 40, 40), False)
    path = (shapely.box(0, 18, 40, 20.5), True)  # the mapped way across the square
    decisions = clearance.place([trunk("osm:1", 10, 10), trunk("lidar:2", 30, 30), trunk("area:3:x", 20, 30),
                                 trunk("lidar:4", 20, 19)], [square, path])
    assert "osm:1" not in decisions and "lidar:2" not in decisions
    assert decisions["area:3:x"] is None
    x, z = decisions["lidar:4"]
    assert z < 18 - 1.1 or z > 20.5 + 1.1


def test_register_stems_yield_only_to_the_drawn_lane():
    near = clearance.Trunk("puut:a", "puut:a", 100, 52.3, 1.5, 0.2)  # flare over the edge, stem beside it
    on = clearance.Trunk("puut:b", "puut:b", 120, 51.5, 1.5, 0.2)
    decisions = clearance.place([near, on], LANE)
    assert "puut:a" not in decisions and decisions["puut:b"][1] >= 52.2


def test_generator_step_moves_and_drops_and_resamples_height():
    from turku3d.coarse.forests import trunk_clearance

    class Inputs:
        trunk_ground = LANE
        water, buildings = [], []

    trees = [Tree("lidar:a", 100, 51.2, "lidar", crown_radius=3, trunk_radius=0.2),
             Tree("area:1:b", 140, 50.5, "osm-area", crown_radius=3)]
    bushes = [Tree("bush:c", 150, 50, "bush", crown_radius=1.1, trunk_radius=0)]
    trees, bushes = trunk_clearance(TILE, Inputs, lambda x, z: z / 10, trees, bushes)
    assert [t.object_id for t in trees] == ["lidar:a"] and bushes == []
    assert trees[0].z > 52 and trees[0].y == trees[0].z / 10


def test_registered_last_in_the_chain():
    assert registry.CHAIN[-1] is path_trees.MIGRATION and path_trees.MIGRATION.key == "path-trees@1"


def test_migration_matches_the_generator_rule(tmp_path, monkeypatch):
    from test_trees_lidar import vegetation_glb

    from turku3d.coarse.forests import trunk_base_radius
    from turku3d.col.format import ColTile, Primitive, read_col, write_col
    from turku3d.col.materials import mat_id

    monkeypatch.setattr(path_trees, "tile_inputs", lambda tile: (LANE, []))
    monkeypatch.setattr(path_trees, "tile_canopy", lambda tile: np.empty((0, 3)))
    positions = [[100, 0, 51.2], [140, 0, 50.5], [30, 0, 80]]
    scales = [[3, 10, 3], [3, 10, 3], [3, 10, 3]]
    ids = ["lidar:a", "area:1:b", "puut:c"]
    glb = vegetation_glb(positions, scales, ids)
    digest = hashlib.sha256(glb).hexdigest()
    (tmp_path / f"vis.{digest}.glb").write_bytes(glb)
    # One collision cell with flat ground at y=0 and the generator's trunk cylinders.
    cell = C.CollisionTile.at(100, 50)
    o = np.asarray(cell.origin)
    s = C.COLLISION_TILE_SIZE
    ground = np.array([[0, 0, 0], [s, 0, 0], [0, 0, s], [s, 0, s]], np.float32)
    prims = [Primitive.cylinder(mat_id("tree_trunk"), (x - o[0], 2.75, z - o[2]), 2.75, 0.18)
             for x, _, z in positions if C.CollisionTile.at(x, z) == cell]
    col = ColTile(cell.i, cell.j, ground, [[0, 2, 1], [1, 2, 3]], [mat_id("grass")] * 2, prims)
    data = write_col(col)
    col_url = f"col/{cell.id}.{hashlib.sha256(data).hexdigest()}.col"
    (tmp_path / "col").mkdir()
    (tmp_path / col_url).write_bytes(data)
    manifest = {"tile": TILE.id, "visual": {TILE.id: {"url": f"vis.{digest}.glb", "sha256": digest}},
                "collision": {cell.id: {"url": col_url, "prims": len(prims)}}}
    p = core.Product(tmp_path, manifest)
    path_trees.transform(p)
    document, binary = core.glb_chunks(p.read(p.manifest["visual"][TILE.id]["url"]))
    (nodes, arrays), = path_trees.instances(document, binary, p, "").values()
    kept = document["nodes"][nodes[0]]["extras"]["instance_object_ids"]
    # The generator's step on the same trees.
    trees = [Tree(i, x, z, "lidar", crown_radius=3, height=10, trunk_radius=0.18, variant="tree_default_fall")
             for i, (x, _, z) in zip(ids, positions, strict=True)]
    expect = clearance.apply_to_trees(trees, LANE, [], TILE.bbox.as_tuple(), lambda x, z: 0.0, trunk_base_radius)
    assert kept == [t.object_id for t in expect]
    assert np.allclose(arrays["TRANSLATION"][:, [0, 2]], [[t.x, t.z] for t in expect], atol=1e-4)
    trunks = read_col(p.read(p.manifest["collision"][cell.id]["url"])).primitives
    centres = sorted((round(pr.center[0] + o[0], 2), round(pr.center[2] + o[2], 2)) for pr in trunks)
    assert centres == sorted((round(t.x, 2), round(t.z, 2)) for t in expect if C.CollisionTile.at(t.x, t.z) == cell)
    stats = p.manifest["vegetation"]["trunk_clearance"]
    assert stats["dropped_area"] == 1 and stats["moved_lidar"] == 1 and not stats.get("collision_trunks_unmatched")


def test_a_tile_without_paving_or_lanes_keeps_its_trees():
    trees = [trunk("area:1:a", 10, 10), trunk("lidar:b", 30, 30)]
    assert clearance.place(trees, [(DRIVE, True)]) == {}
    assert clearance.place(trees, [(shapely.box(0, 200, 5, 205), False)]) == {}
    assert clearance.place([trunk("area:1:c", 100, 50)], [(DRIVE, False)]) == {"area:1:c": None}
