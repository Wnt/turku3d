"""Street-object assets are closed solids, and every register sign code draws a face."""

import pytest

from turku3d.world.kinds import fence, lamp, signal, signpost
from turku3d.world.objects import signfaces
from turku3d.world.objects.mesh import open_edges

REGISTER_CODES = ["A10", "A15", "A17", "A23", "A33", "A1.1", "B1", "B2", "B5", "B6", "C1", "C2", "C8", "C13", "C17", "C18", "C19", "C23",
                  "C32_5", "C34", "C34_2", "C35", "C37", "C38", "C39", "C40", "C41", "C43", "D1.1", "D1.3", "D1.4", "D1.6", "D1.9", "D3.1",
                  "D3.3", "D4", "D6", "D7.1", "D7.2", "E1", "E2", "E4.1", "E4.3", "E6", "E8", "E9.2", "E13.1", "E14.1", "E24", "E25",
                  "E26", "F11", "F13", "F19", "F21.1", "F24.1", "F27.1", "F29", "G1", "G28", "H1", "H3", "H9.1", "H10", "H11", "H12.7",
                  "H12.10_2_2", "H17.1", "H19.2", "H20", "H22.2", "H23.2", "H24", "H25", "511", "363b"]


def lamp_parts(**fields):
    description = {"id": "lamp/test", "at": [23459000.0, 6704000.0, 10.0], **fields}
    return lamp.Lamp().compile(description).instances[0].asset


ASSETS = {
    "plate E1": lambda: signpost.plate_asset("E1", "", "", False),
    "plate C34": lambda: signpost.plate_asset("C34_2", "C34_2 Nopeusrajoitusalue (30km/h)", "", False),
    "plate E25": lambda: signpost.plate_asset("E25", "", "", False),
    "plate H20": lambda: signpost.plate_asset("H20", "", "9-20\n(9-17)\nVyöhyke 2\nZon 2", True),
    "sign post": lambda: signpost.post_asset(3.2, 0.03),
    "lamp street-arm": lambda: lamp_parts(family="street-arm", height=10.1, arm={"bearing": 40.0, "reach": 2.6}),
    "lamp short curved arm": lambda: lamp_parts(family="street-arm", height=9.0, arm={"bearing": 0.0, "reach": 1.25}),
    "lamp street-top": lambda: lamp_parts(family="street-top", height=8.0),
    "lamp park": lambda: lamp_parts(family="park", height=4.6),
    "board gate": lambda: fence.gate("board", 1.8, 3.0, "#d8d0b6"),
    "iron gate": lambda: fence.gate("iron", 1.0, 1.0, "#1b1c1e"),
    "signal post": signal.post_asset,
    "signal vehicle head": lambda: signal.head_asset("vehicle"),
    "signal pedestrian head": lambda: signal.head_asset("pedestrian"),
    "lamp span-pole": lambda: lamp_parts(family="span-pole", height=9.0),
    "board panel": lambda: fence.board_panel(1.8, "#8a8074").parts(),
    "picket panel": lambda: fence.picket_panel(1.1, "#8a8074").parts(),
    "iron panel": lambda: fence.iron_panel(1.0).parts(),
    "iron post": lambda: fence.post("iron", 1.0, "#1b1c1e").parts(),
    "wall panel": lambda: fence.wall_panel(0.9).parts(),
    "railing panel": lambda: fence.railing_panel(1.0).parts(),
    "fence post": lambda: fence.post("board", 1.8, "#8a8074").parts(),
    "gate post": lambda: fence.gate_post(1.7).parts(),
}


@pytest.mark.parametrize("name", list(ASSETS))
def test_asset_is_closed_and_faces_out(name):
    bad, flipped, total = open_edges(ASSETS[name]())
    assert total > 0
    assert (bad, flipped) == (0, 0)


def test_every_register_code_draws_a_face():
    for code in REGISTER_CODES:
        drawn = signfaces.face(code, code, "8-17")
        assert drawn is not None, code
        points, triangles, colours = signfaces.face_mesh(drawn)
        assert len(triangles) > 0, code
        assert abs(points[:, 0]).max() <= drawn.size[0] / 2 + 1e-6, code


def test_two_sided_crossing_sign_and_plate_order():
    rows = [{"id": "2", "code": "E1", "label": "", "note": "", "e": 0.0, "n": 0.0}, {"id": "3", "code": "E1", "label": "", "note": "", "e": 0.1, "n": 0.0},
            {"id": "1", "code": "H25", "label": "", "note": "sininen", "e": 0.2, "n": 0.1}, {"id": "9", "code": "C38", "label": "", "note": "", "e": 30.0, "n": 0.0}]
    groups = sorted(signpost._group(rows), key=len)
    assert [len(g) for g in groups] == [1, 3]
    plates = signpost._plates(groups[1])
    assert [(p["code"], p.get("turn", 0)) for p in plates] == [("E1", 0), ("E1", 180), ("H25", 0)]
    assert plates[2]["blue"]


def test_wire_entity_is_a_closed_tube_with_its_hung_lamp():
    from turku3d.world.kinds.wire import Wire

    description = {"id": "wire/w1", "a": [23459100.0, 6704700.0, 20.0], "b": [23459112.0, 6704705.0, 20.4], "sag": 0.3, "type": "span",
                   "lamps": [{"at": [23459106.0, 6704702.5, 19.9]}]}
    output = Wire().compile(description)
    bad, flipped, total = open_edges(output.parts)
    assert total > 0 and bad == 0 and flipped == 0
    assert len(output.instances) == 1 and output.instances[0].ids == ["wire/w1#lamp0"]
    bad, flipped, total = open_edges(output.instances[0].asset)
    assert bad == 0 and flipped == 0


def test_repeated_register_rows_give_one_entity():
    from turku3d.world.api import Stub
    from turku3d.world.objects.common import unique

    stubs = [Stub("fence/r1", (0, 0, 1, 1), [], {}), Stub("fence/r1", (0, 0, 1, 1), [], {"again": True}), Stub("fence/r2", (0, 0, 1, 1), [], {})]
    assert [(stub.id, stub.facts) for stub in unique(stubs)] == [("fence/r1", {}), ("fence/r2", {})]


def test_every_furniture_model_is_closed_and_register_labels_pick_models():
    from turku3d.world.kinds import furniture

    for model in [*furniture.SIZE, "bike-rack"]:
        bad, flipped, total = open_edges(list(furniture.model_parts(model, "4" if model == "bike-rack" else "")))
        assert total > 0 and bad == 0 and flipped == 0, model
    row = {"kind": None, "type": "Roskis", "species": "Roskakori", "model": "Vihreä 60 l", "places": ""}
    assert furniture.classify(row) == ("bin", "green")
    assert furniture.classify({"kind": "bike-rack", "type": "", "species": "", "model": "", "places": "10"}) == ("bike-rack", "5")
    assert furniture.classify({"kind": None, "type": "Penkki", "species": "Leikkikaluste", "model": "", "places": ""})[0] is None


def test_signal_posts_stand_right_of_the_approach_past_the_carriageway_edge():
    import shapely

    road = shapely.box(-4.0, -50.0, 4.0, 50.0)  # an 8 m carriageway along a northbound way
    ways = [{"line": shapely.LineString([(0, -50), (0, 50)]), "oneway": False}]
    source = {"source": "osm", "key": "node/1"}
    stop = {"id": 1, "e": 0.0, "n": 0.0, "kind": "stop", "direction": "forward", "source": source}
    (post,) = signal.posts_of([stop], ways, road)
    assert (post["e"], post["n"]) == (4.35, 0.0) and post["heads"] == [{"type": "vehicle", "bearing": 180.0}]
    crossing = {"id": 2, "e": 0.0, "n": 20.0, "kind": "crossing", "direction": "", "source": source}
    posts = signal.posts_of([stop, crossing], ways, road)
    assert [(p["e"], p["n"]) for p in posts] == [(4.35, 0.0), (4.35, 20.0), (-4.35, 20.0)]
    assert posts[1]["heads"] == [{"type": "pedestrian", "bearing": 270.0}]  # the stop position owns the vehicle heads
    alone = signal.posts_of([crossing], ways, road)
    assert [h["type"] for h in alone[0]["heads"]] == ["vehicle", "pedestrian"] and alone[1]["heads"][0]["bearing"] == 0.0
    output = signal.Signal().compile({"id": "signal/n2r", "at": [23459000.0, 6704000.0, 10.0], "heads": alone[0]["heads"],
                                      "interaction": {"behaviour": "knock-over"}})
    assert [len(item.transforms) for item in output.instances] == [1, 1, 1]


def test_free_hanging_returns_across_a_street_are_a_span_with_its_lamp():
    import numpy as np
    import shapely

    from turku3d.world.objects import spans

    street = [shapely.LineString([(0, -60), (0, 60)])]
    rng = np.random.default_rng(1)
    across = np.column_stack([np.linspace(-5, 5, 41), np.full(41, 10.0) + rng.normal(0, 0.03, 41), np.full(41, 9.5), np.full(41, 5.0)])
    knot = np.column_stack([rng.normal(0.5, 0.1, 14), rng.normal(10.0, 0.1, 14), np.full(14, 9.2), np.full(14, 5.0)])
    along = np.column_stack([np.full(41, 2.0), np.linspace(-40, -30, 41), np.full(41, 9.5), np.full(41, 5.0)])  # an eave or a branch
    crown = np.column_stack([rng.uniform(-4, 4, 400), rng.uniform(30, 38, 400), rng.uniform(3, 11, 400), np.full(400, 5.0)])
    roofed = lambda e, n: abs(e) >= 8.0  # noqa: E731
    found, lamps = spans.find(np.vstack([across, knot, along, crown]), street, roofed, lambda e, n: 12.0)
    assert len(found) == 1 and found[0]["ends"] == ["facade", "facade"] and found[0]["kind"] == "span"
    assert sorted(round(found[0][end][0]) for end in ("a", "b")) == [-8, 8] and abs(found[0]["a"][1] - 10.0) < 0.2
    assert len(lamps) == 1 and abs(lamps[0]["at"][0] - 0.5) < 0.4 and 21.0 < lamps[0]["at"][2] < 22.0


def test_crossing_sign_on_a_give_way_post_faces_the_junction():
    rows = [{"id": "1", "code": "B5", "label": "", "note": "", "e": 0.0, "n": 0.0}, {"id": "2", "code": "E1", "label": "", "note": "", "e": 0.1, "n": 0.0}]
    plates = {plate["code"]: plate for plate in signpost._plates(rows)}
    assert plates["E1"].get("turn") == 180 and "turn" not in plates["B5"]
    alone = signpost._plates([dict(rows[1])])
    assert "turn" not in alone[0]
