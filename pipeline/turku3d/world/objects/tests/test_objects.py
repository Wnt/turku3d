"""Street-object assets are closed solids, and every register sign code draws a face."""

import pytest

from turku3d.world.kinds import fence, lamp, signpost
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
    "lamp street-top": lambda: lamp_parts(family="street-top", height=8.0),
    "lamp park": lambda: lamp_parts(family="park", height=4.6),
    "board panel": lambda: fence.board_panel(1.8, "#8a8074").parts(),
    "picket panel": lambda: fence.picket_panel(1.1, "#8a8074").parts(),
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
