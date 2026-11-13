"""House colours per part (observe/colour_parts.py) and the light of a panorama (observe/colour_light.py), on
synthetic pictures: a yellow boarded wall with a dark plinth, a white-framed window and a hedge in front of it."""

import numpy as np

from turku3d.observe import colour, colour_light, colour_parts
from turku3d.observe.colour_segment import LABELS, fold

PPM = 40.0
YELLOW, DARK, WHITE, GLASS, GREEN = (214, 190, 120), (70, 62, 58), (240, 240, 236), (40, 50, 60), (40, 90, 35)


def synthetic():
    """A crop of an 8 m wall, eaves 4 m: picture, labels, crop record, wall record."""
    crop = {"x_left_m": -1.0, "x0_m": 0.0, "x1_m": 8.0, "z_high_m": 6.0, "px_per_m": PPM, "quality": 0.9,
            "panorama": {"provider": "google", "id": "p", "date": "2026-07"}, "distance_m": 10, "oblique_deg": 5}
    wall = {"base_h": 10.0, "eaves_h": 14.0, "top_h": 14.0, "length_m": 8.0}
    h, w = int(7 * PPM), int(10 * PPM)
    picture = np.full((h, w, 3), 200, np.uint8)
    labels = np.full((h, w), LABELS.index("sky"), np.uint8)
    px = lambda x: int((x + 1.0) * PPM)  # noqa: E731
    pz = lambda z: int((6.0 - z) * PPM)  # noqa: E731
    picture[pz(4.0):pz(0.0), px(0):px(8)] = YELLOW
    labels[pz(4.0):pz(0.0), px(0):px(8)] = LABELS.index("building")
    picture[pz(0.6):pz(0.0), px(0):px(8)] = DARK
    picture[pz(2.9):pz(1.3), px(4.8):px(6.2)] = WHITE
    picture[pz(2.8):pz(1.4), px(4.9):px(6.1)] = GLASS
    labels[pz(2.8):pz(1.4), px(4.9):px(6.1)] = LABELS.index("window")
    picture[pz(1.8):pz(0.0), px(0):px(3)] = GREEN
    labels[pz(1.8):pz(0.0), px(0):px(3)] = LABELS.index("vegetation")
    rng = np.random.default_rng(1)
    picture = np.clip(picture + rng.normal(0, 3, picture.shape), 0, 255).astype(np.uint8)
    return picture, labels, crop, wall


def near(lab, rgb, limit=6.0):
    return np.linalg.norm(np.array(lab) - colour.to_lab(np.array([rgb], np.uint8))[0]) <= limit


def test_parts_of_a_wall_behind_a_hedge():
    picture, labels, crop, wall = synthetic()
    found = colour_parts.of_crop(picture, labels, crop, wall)
    assert 0.78 <= found["visible"] <= 0.88 and found["in_front"]["vegetation"] > 0.1  # the hedge hides 3 x 1.8 of 8 x 4 m
    parts = found["parts"]
    assert near(parts["wall"]["lab"], YELLOW) and near(parts["plinth"]["lab"], DARK) and near(parts["frame"]["lab"], WHITE)
    assert 0.45 <= parts["plinth"]["height_m"] <= 0.75
    assert "wall2" not in parts and "roof" not in parts


def test_a_hidden_wall_measures_nothing():
    picture, labels, crop, wall = synthetic()
    labels[:] = LABELS.index("vegetation")
    found = colour_parts.of_crop(picture, labels, crop, wall)
    assert found["visible"] == 0.0 and not found["parts"]


def test_overcast_and_sunny_equirects():
    h, w = 512, 1024
    grey = np.full((h, w, 3), (205, 207, 210), np.uint8)
    grey[h // 2:] = (110, 110, 112)
    sunny = grey.copy()
    sunny[: h // 2] = (90, 140, 225)
    sunny[h // 2:, ::2] = (40, 40, 44)  # hard shadows across the street
    assert colour_light.measure(grey)["own"] >= 0.9
    assert colour_light.measure(sunny)["own"] <= 0.1


def test_model_classes_fold_into_ours():
    table = fold({0: "wall", 1: "building, edifice", 4: "tree", 8: "windowpane, window", 14: "door, double door",
                  20: "car, auto, automobile", 32: "fence, fencing", 2: "sky", 6: "road, route", 9: "grass",
                  17: "plant, flora, plant life", 3: "nature--vegetation", 5: "construction--barrier--fence"})
    name = lambda k: LABELS[table[k]]  # noqa: E731
    assert [name(k) for k in (0, 1, 4, 8, 14, 20, 32, 2, 6, 9, 17, 3, 5)] == [
        "building", "building", "vegetation", "window", "door", "vehicle", "fence", "sky", "ground", "ground",
        "vegetation", "vegetation", "fence"]


def test_a_strip_under_cloud_outranks_a_whole_wall_in_sun():
    crop = {"quality": 0.8}
    strip_overcast = colour_parts.score(crop, {"visible": 0.06}, 0.98)
    whole_sunny = colour_parts.score(crop, {"visible": 0.9}, 0.0)
    assert strip_overcast > 2 * whole_sunny
    rows = [{"score": strip_overcast, "visible": 0.06, "panorama": {"provider": "google", "id": "a", "date": "2026-07"},
             "parts": {"wall": {"lab": [75.0, -1.0, -2.0], "px": 3000, "spread": 3.0, "share": 0.9}}},
            {"score": whole_sunny, "visible": 0.9, "panorama": {"provider": "lookaround", "id": "b", "date": "2023-06"},
             "parts": {"wall": {"lab": [60.0, -2.0, -17.0], "px": 90000, "spread": 3.0, "share": 0.9}}}]
    wall = colour_parts.combine(rows)["wall"]
    assert wall["lab"] == [75.0, -1.0, -2.0] and wall["n"] == 1 and wall["from"]["id"] == "a"  # the sunny view does not vote


def test_a_garage_door_is_not_the_wall():
    picture, labels, crop, wall = synthetic()
    labels[labels == LABELS.index("vegetation")] = LABELS.index("building")
    labels[labels == LABELS.index("window")] = LABELS.index("building")
    px = lambda x: int((x + 1.0) * PPM)  # noqa: E731
    pz = lambda z: int((6.0 - z) * PPM)  # noqa: E731
    picture[pz(4.0):pz(0.0), px(0):px(8)] = DARK  # black boards
    picture[pz(2.4):pz(0.0), px(0.3):px(7.7)] = WHITE  # two garage doors side by side: more pixels than the boards
    found = colour_parts.of_crop(picture, labels, crop, wall)
    assert near(found["parts"]["wall"]["lab"], DARK)


def test_the_paint_colour_of_a_pixel_by_its_visits_white():
    grey_under_cloud = colour.to_lab(np.array([[150, 150, 150]], np.uint8))[0]
    white_seen = colour.to_lab(np.array([[200, 200, 200]], np.uint8))[0]
    painted = colour_parts.paint(list(grey_under_cloud), list(white_seen), white_l=93.0)
    assert painted[0] > grey_under_cloud[0] + 8 and abs(painted[1]) < 1 and abs(painted[2]) < 1
    assert np.allclose(colour_parts.paint(list(grey_under_cloud), None), grey_under_cloud)


def test_a_measured_wall_with_its_panorama_validates():
    from turku3d.observe import colour_batch
    from turku3d.world.kinds.building import description

    result = {"parts": {"wall": {"colour": "#cacfd3", "spread": 3.1, "share": 0.6, "conf": 0.72, "visible": 0.4,
                                 "agreement": 2.5, "n": 2, "from": {"provider": "google", "id": "abc", "date": "2026-07",
                                                                    "overcast": 0.98}}}}
    block = colour_batch.wall_block(result)
    doc = colour_batch.description("1033533534", {"wall": block}, ["google:abc"])
    assert description.errors(doc) == [] and doc["measured"]["wall"]["from"]["overcast"] == 0.98
