"""Street-object assets are closed solids, and every register sign code draws a face."""

import math
import numpy as np

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
    "mesh panel": lambda: fence.mesh_panel(1.2, "#9a9d9c").parts(),
    "mesh post": lambda: fence.post("mesh", 1.2, "#9a9d9c").parts(),
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

    for model in [*furniture.SIZE, "bike-rack", "bus-shelter"]:
        bad, flipped, total = open_edges(list(furniture.model_parts(model, {"bike-rack": "4", "bus-shelter": "4.8x1.4"}.get(model, ""))))
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


def test_sign_face_picture_has_the_plate_shape_and_its_paints():
    import numpy as np

    drawn = signfaces.face("E1")
    picture = np.asarray(signfaces.face_image(drawn, density=256))
    width, height = drawn.size
    assert picture.shape[:2] == (round(height * 256), round(width * 256))
    alpha = picture[..., 3] > 127
    assert alpha[picture.shape[0] // 2, picture.shape[1] // 2] and not alpha[0, 0]  # rounded corners are outside the plate
    blue = (picture[..., 2] > 120) & (picture[..., 0] < 60) & alpha
    assert 0.3 < blue.sum() / alpha.sum() < 0.9  # the blue panel around the white triangle
    give_way = np.asarray(signfaces.face_image(signfaces.face("B5"), density=256))
    assert give_way[4, give_way.shape[1] // 2, 3] > 127 and give_way[-4, 4, 3] == 0  # a triangle standing on its point


def test_plate_front_is_the_face_picture_and_the_store_keeps_it(tmp_path):
    import numpy as np

    from turku3d.world import driver
    from turku3d.world.api import Instances, Output
    from turku3d.world.store import Store

    parts = signpost.plate_asset("C32", "C32 Nopeusrajoitus (40km/h)", "", False)
    image = signpost.plate_image("C32", "C32 Nopeusrajoitus (40km/h)", "", False)
    front = next(part for part in parts if part.material.family == "sign")
    assert front.material.params["atlas"] == image.url and image.data[:4] == b"\x89PNG"
    assert front.colors is None and 0.0 <= front.uvs.min() and front.uvs.max() <= 1.0
    assert len(front.triangles) < 200  # the outline only: the face is in the picture
    top = front.uvs[np.argmax(front.positions[:, 1])]
    assert top[1] < 0.01  # v runs down from the plate's top, as the picture's rows do

    store = Store(tmp_path)
    output = Output((0.0, 0.0, 0.0), [], [Instances(parts, np.array([[0, 2, 0, 0, 1, 1, 1.0]]))], images=[image])
    driver.KINDS["test-plate"] = type("Plate", (), {"compile": staticmethod(lambda description: output)})()
    try:
        body = driver.compile_entity(store, "test-plate", {"id": "test-plate/a"})
    finally:
        del driver.KINDS["test-plate"]
    assert [entry["url"] for entry in body["output"]["images"]] == [image.url]
    assert (tmp_path / image.url).read_bytes() == image.data
    row = driver.manifest_row({"id": "test-plate/a", "kind": "test-plate", "description": {"tier": "automatic"}, **body}, "target")
    assert row["images"] == [image.url]


def test_fence_strip_is_cut_from_the_nearest_panorama_left_to_right_as_seen(tmp_path):
    import numpy as np
    from PIL import Image

    from turku3d.world.objects import fencecrop

    class Pano:
        position, heading = np.array([5.0, 6.0, 2.5]), 0.0  # north of a fence that runs east: it is seen turned

        def to_dict(self):
            return {"id": "p"}

    class Panos:
        def spots(self, bounds, margin=0.0):
            return [{"E": 5.0, "N": 6.0, "id": "p"}, {"E": 5.0, "N": 60.0, "id": "far"}]

        def resolve(self, spot, base_h=None):
            return Pano() if spot["id"] == "p" else None

        def upright(self, pano):
            return {"straightened": False, "up": None, "method": "none"}

        def pixels(self, pano):
            picture = np.zeros((180, 360, 3), dtype=np.uint8)
            picture[:, 180:] = (255, 0, 0)  # red east of north, black west of it
            return picture

    line = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]])
    record = fencecrop.strip(line, Panos(), tmp_path / "strip.jpg", 1.2)
    assert record["distance_m"] == 6.0 and record["length_m"] == 10.0
    picture = np.asarray(Image.open(tmp_path / "strip.jpg"))
    # Looking south from the north side, east is on the left: the red half of the panorama is the strip's left.
    assert picture[60, 150, 0] > 200 and picture[60, -100, 0] < 50
    assert fencecrop.strip(line + [0.0, 100.0, 0.0], Panos(), tmp_path / "none.jpg") is None


def test_a_fence_reading_replaces_the_guesses_and_an_unseen_fence_builds_nothing():
    from turku3d.world.objects import fenceread

    automatic = {"schema": "turku-description/fence/1", "id": "fence/r1", "tier": "auto", "provenance": [], "family": "board",
                 "height": 2.6, "colour": "#d8d0b6", "guessed": ["family", "colour"], "at": [23459500.0, 6704500.0, 10.0],
                 "line": [[23459500.0, 6704500.0, 10.0], [23459502.4, 6704500.0, 10.0]]}
    strip = {"panorama": {"provider": "lookaround", "id": "p1"}}
    reading = {"family": "mesh", "height_m": 1.2, "colour": "#3a4a3c", "hedge": True, "confidence": "medium", "note": "green mesh"}
    stored = fenceread.observed(automatic, reading, strip, {"seconds": 6.0})
    assert (stored["tier"], stored["family"], stored["height"], stored["colour"], stored["guessed"]) == ("observed", "mesh", 1.2, "#3a4a3c", [])
    assert stored["seen"]["hedge"]  # the whole reading is kept beside the fields it replaces
    assert fence.Fence().compile({**automatic, **stored}).instances
    grey = fence.Fence().compile({**automatic, **stored, "family": "iron", "colour": "#5a5f63"})
    assert grey.instances[0].asset[0].material.params["colour"] == "#5a5f63"  # an iron fence takes its read paint
    assert fenceread.observed(automatic, {**reading, "confidence": "low"}, strip, {}) is None
    gone = fenceread.observed(automatic, {**reading, "family": "none", "height_m": 0}, strip, {})
    assert "height" not in gone
    output = fence.Fence().compile({**automatic, **gone})
    assert not output.parts and not output.instances


def test_fence_setting_is_old_town_only_among_pre_war_buildings():
    import shapely

    old = [shapely.box(0, 2, 10, 12), shapely.box(14, 2, 24, 12), shapely.box(40, 2, 50, 12)]
    tree = shapely.STRtree(old)
    line = shapely.LineString([(10, 2), (14, 2)])
    assert fence.setting(line, tree, old, np.array([1905.0, 1911.0, 1975.0])) == ("old-town", 0.0)
    assert fence.setting(line, tree, old, np.array([1905.0, 1988.0, 1975.0]))[0] == "garden"
    assert fence.setting(line, tree, old, np.array([np.nan, np.nan, np.nan]))[0] == "garden"
    assert fence.setting(shapely.LineString([(0, -5), (30, -5)]), tree, old, np.array([1905.0, 1911.0, 1975.0])) == ("old-town", 7.0)
    assert fence.setting(line, None, [], np.array([])) == ("garden", math.inf)


def test_plates_facing_two_ways_stack_in_one_column_and_twins_share_a_height():
    def heights(plates):
        description = {"id": "signpost/t", "at": [23458500.0, 6704150.0, 10.0], "plates": plates, "facing": {"bearing": 0.0}}
        out = signpost.SignPost().compile(description)
        return {name: round(float(row[1]), 2) for item in out.instances for row, name in zip(np.asarray(item.transforms).reshape(-1, 7), item.ids)}

    give_way = heights([{"code": "B5"}, {"code": "E1", "turn": 180}])
    crossing, triangle = give_way["signpost/t#plate1"], give_way["signpost/t#plate0"]
    assert crossing > triangle + 0.5 and triangle >= signpost.CLEAR - 0.01  # the crossing sign above the triangle
    twins = heights([{"code": "E1"}, {"code": "E1", "turn": 180}])
    assert twins["signpost/t#plate0"] == twins["signpost/t#plate1"]
