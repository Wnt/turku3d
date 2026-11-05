"""props.facing: footway furniture faces the footway; furniture@3 turns drawn meters in place."""
import math

import numpy as np
from test_racks import PATH, STREET, batched_tile, graph
from turku3d.props import Prop, facing
from turku3d.props.models import model


def meter(x, z, yaw=0.0, kind="parking_meter"):
    return Prop(f"GIS_Pysakointiautomaatit:{x}:{z}", kind, x, 0.0, z, yaw)


def front(p):
    return math.sin(p.yaw), math.cos(p.yaw)


# A street along x at z = 0 and its sidewalk centreline along x at z = 5.
SIDEWALK = (PATH, (-30, 5.0), (30, 5.0))
ROAD = (STREET, (-30, 0.0), (30, 0.0))


def test_a_meter_on_the_kerb_edge_faces_back_over_the_footway():
    m = meter(0, 3.0, yaw=math.pi)  # the old nearest_road pose: front to the road
    facing.place([m], graph(ROAD, SIDEWALK))
    fx, fz = front(m)
    assert m.orientation == "foot_side" and fz > 0.999


def test_a_meter_at_the_back_of_the_footway_faces_out_over_it_toward_the_street():
    m = meter(0, 7.0)
    facing.place([m], graph(ROAD, SIDEWALK))
    assert m.orientation == "foot_side" and front(m)[1] < -0.999


def test_a_crossing_does_not_name_the_footway_side():
    crossing = (PATH, (1.0, -8.0), (1.0, 8.0))
    m = meter(0, 3.0, yaw=math.pi)
    facing.place([m], graph(ROAD, crossing))
    assert m.orientation == "back_to_kerb" and front(m)[1] > 0.999


def test_at_a_corner_the_street_whose_footway_the_meter_stands_on_wins():
    cross = (STREET, (-2.5, -30.0), (-2.5, 30.0))  # nearer centreline, no footway along it here
    m = meter(0, 3.0, yaw=-math.pi / 2)
    facing.place([m], graph(ROAD, SIDEWALK, cross))
    assert m.orientation == "foot_side" and front(m)[1] > 0.999


def test_a_footway_across_the_street_is_not_this_meters_footway():
    far = (PATH, (-30, -5.0), (30, -5.0))
    m = meter(0, 3.0, yaw=math.pi)
    facing.place([m], graph(ROAD, far))
    assert m.orientation == "back_to_kerb" and front(m)[1] > 0.999


def test_away_from_streets_the_path_is_faced_and_other_kinds_are_untouched():
    board = meter(0, 4.0, kind="info_board")
    bench = Prop("b", "bench", 1, 0.0, 4.0, 1.0)
    facing.place([board, bench], graph((PATH, (-30, 0.0), (30, 0.0))))
    assert board.orientation == "path_view" and front(board)[1] < -0.999
    assert bench.yaw == 1.0
    lone = meter(0, 0, yaw=0.5)
    facing.place([lone], None)
    assert lone.yaw == 0.5


def test_migration_turns_a_drawn_meter_about_its_origin_and_records_the_rule(tmp_path, monkeypatch):
    from turku3d.migrations import core, furniture

    source = meter(2.0, 3.0, yaw=math.pi)  # payment side to the road (z = 0)
    roads = graph(ROAD, SIDEWALK)
    product = batched_tile(tmp_path, [source])
    monkeypatch.setattr(furniture, "_tile_inputs", lambda tile: ([source], [], [], [], roads))
    furniture.transform(product)
    document, binary = core.glb_chunks(product.read(product.manifest["visual"]["t0_0_0"]["url"]))
    pos = furniture.read_accessor(document, binary, product, "", document["meshes"][0]["primitives"][0]["attributes"]["POSITION"])
    idx = furniture.read_accessor(document, binary, product, "", 2)[:, 0]
    row = document["nodes"][0]["extras"]["objects"][0]
    assert row["orientation"] == "foot_side" and abs(row["yaw_deg"]) < 0.1
    world = pos[np.unique(idx[row["start"]: row["start"] + row["count"]])].astype(np.float64)
    local = np.asarray(model("parking_meter", "").positions, np.float64)
    (x, z), residual = furniture.fit_origin(world, 0.0, (2.0, 3.0), local)
    assert residual < 0.01 and math.hypot(x - 2.0, z - 3.0) < 0.01
    assert product.manifest["furniture"]["benches"]["facing_turned"] == 1
