"""Observation chain: projection onto walls, view choice and the stored description it assembles."""

import numpy as np
from shapely.geometry import Polygon
from turku3d.observe import crops, lod2, reader, views
from turku3d.observe.panoramas import Pano


class _Meta:
    def __init__(self, source="google", ident="p", date="2026-07"):
        self.source, self.id, self.date = source, ident, date


def _wall(length=20.0, height=12.0):
    profile = [(x, height) for x in np.linspace(0.4, length - 0.4, 9)]
    return lod2.Wall("b:w00", "b", 0, np.array([0.0, 0.0]), np.array([length, 0.0]), 0.0, height, height, profile, 0)


def _scene(blocks=()):
    scene = lod2.Scene.__new__(lod2.Scene)
    scene.buildings = {}
    for k, ring in enumerate(blocks):
        tris = []
        pts = np.array(ring, float)
        for (x0, y0), (x1, y1) in zip(pts, np.roll(pts, -1, 0), strict=True):
            tris += [[[x0, y0, 0], [x1, y1, 0], [x1, y1, 30]], [[x0, y0, 0], [x1, y1, 30], [x0, y0, 30]]]
        scene.buildings[f"n{k}"] = lod2.Building(f"n{k}", f"n{k}", Polygon(ring), [], {}, np.array(tris, float))
    scene._index()
    return scene


def test_sample_follows_heading_and_elevation():
    width, height = 720, 360
    u, v = np.meshgrid(np.arange(width), np.arange(height))
    equirect = np.stack([u * 255 // width, v * 255 // height, np.zeros_like(u)], -1).astype(np.uint8)
    east_up = np.array([[1.0, 0.0, 1.0]])  # azimuth 90, elevation 45
    rgb, empty = crops.sample(equirect, 0.0, east_up)
    assert abs(int(rgb[0, 0]) - 255 * 3 // 4) <= 2 and abs(int(rgb[0, 1]) - 255 // 4) <= 2
    turned, _ = crops.sample(equirect, 90.0, east_up)  # the image centre now looks east
    assert abs(int(turned[0, 0]) - 255 // 2) <= 2
    assert not empty.any()


def test_wall_hidden_by_a_block_is_seen_only_where_the_line_of_sight_is_free():
    wall = _wall()
    # The wall faces south (its normal is -N); a block stands in front of its left half.
    scene = _scene([[(0, -8), (10, -8), (10, -4), (0, -4)]])
    building = lod2.Building("b", "b", Polygon([(0, 0), (20, 0), (20, 10), (0, 10)]), [wall], {}, np.zeros((0, 3, 3)))
    pano = Pano(_Meta(), np.array([15.0, -14.0, 2.5]), 0.0, "test")
    view = views.measure(scene, building, wall, pano)
    # The sight line past the block's corner (10, -4) meets the wall at x = 8.
    assert view is not None and 7.5 <= view.x0 <= 9.0 and view.x1 == 20.0
    behind = Pano(_Meta(), np.array([15.0, 6.0, 2.5]), 0.0, "test")
    assert views.measure(scene, building, wall, behind) is None


def test_choose_prefers_cover_then_a_second_opinion():
    wall = _wall(40.0)

    def view(x0, x1, quality, source, e):
        return views.WallView(wall, Pano(_Meta(source, f"{source}{e}"), np.array([e, -12.0, 2.5]), 0.0, "t"), x0, x1,
                              (x1 - x0) / 40, 12.0, 10.0, 40.0, quality)

    left, right = view(0, 22, 0.8, "google", 10.0), view(18, 40, 0.7, "google", 30.0)
    twin = view(0, 22, 0.79, "google", 10.5)
    other = view(5, 30, 0.6, "lookaround", 18.0)
    chosen = views.choose([left, twin, right, other], 3)
    assert chosen == [left, right, other]  # the twin 0.5 m from the first view adds nothing


def test_assembled_description_is_valid_and_framed():
    from turku3d.world.kinds.building import description as fmt

    wall = _wall()
    record = {
        "building": "100000000A", "register": {"address": "Testikatu 1"},
        "walls": [dict(wall.to_dict(), exposed=[[0, 20, 0]], seen=True)],
        "contexts": [],
        "crops": [{"wall": "b:w00", "file": "/x/w00-1.jpg", "x0_m": 0.0, "x1_m": 20.0,
                   "panorama": {"provider": "google", "id": "abc", "date": "2026-07"}}],
    }
    reading = {
        "description": {
            "style": {"type": "stone_apartment"}, "identity_cues": ["red plaster"],
            "materials": {"wall": {"family": "plaster", "colour": "#a8564f"}},
            "opening_types": {"w": {"kind": "window", "w_m": 1.1, "h_m": 1.5}},
            "wall_defaults": {"material": "wall", "measured_on": "", "bays": {"count": 4, "pitch_m": 3.6}},
            "walls": [{"id": "w00", "measured_on": "w00-1.jpg", "material": "wall",
                       "bays": {"count": 5, "pitch_m": 3.6, "first_m": 2.0, "conf": {"first_m": 0.4}},
                       "ground_floor_m": 1.2, "plinth": {"height_m": 1.2},
                       "zones": [{"name": "base", "material": "wall", "from_m": 0.2, "to_m": 4.0}],
                       "storeys": [{"kind": "upper", "count": 3, "height_m": 3.1, "window": "w", "src": "invented"}],
                       "conf": {"*": 0.7}},
                      {"id": "w77", "like": "w00"}],
            "roof": {"material": {"family": "sheet_metal", "colour": "#444444"},
                     "chimneys": [{"anchor": {"wall": "w00", "u_m": 9.0, "setback_m": 3.0}}]},
        },
        # The picture lies a metre to the left of the wall on the ruler: the facade is seen from u -1 to 19.
        "views": [{"file": "w00-1.jpg", "usable": True, "hidden_share": 0.25, "facade_u_m": [-1.0, 19.0],
                   "ground_uv_m": [5.0, 0.9],  # the ground shows 0.4 m above where the survey has it (0.5)
                   "hidden_m": [[3.0, 0.0, 5.0, 2.0]]}],
    }
    meta = {"model": "m", "effort": "low", "seconds": 1.0, "cost_usd": 0.1, "images": 1, "at": "2026-11-12T22:00:00Z"}
    doc = reader.assemble(record, reading, meta, ground=lambda e, n: wall.base + 0.5)
    assert fmt.errors(doc) == []
    assert doc["id"] == "building/100000000A" and doc["tier"] == "observed"
    assert [w["id"] for w in doc["walls"]] == ["w00"]  # a wall the building does not have is dropped
    only = doc["walls"][0]
    assert only["frame"]["p0"] == [0.0, 0.0] and only["frame"]["p1"] == [20.0, 0.0]
    assert only["observed"]["visible_share"] == 0.75
    assert only["ground_floor_m"] == 0.8 and only["plinth"]["height_m"] == 0.8  # heights follow the ground line
    assert only["zones"][0]["from_m"] == 0.0 and only["zones"][0]["to_m"] == 3.6
    assert only["storeys"][0]["height_m"] == 3.1  # a storey's own height is not a position
    assert only["bays"]["conf"] == {"first_m": 0.4}  # a confidence keyed by a position's name is left alone
    assert only["bays"]["first_m"] == 3.0 and only["observed"]["hidden_m"] == [[4.0, 0.0, 6.0, 2.0]]
    assert doc["roof"]["chimneys"][0]["anchor"]["u_m"] == 10.0  # roof parts follow their wall
    assert "measured_on" not in only and "w00 +1.00 m x1.000 v-0.40 m" in doc["provenance"][0]["note"]
    assert doc["wall_defaults"]["bays"] == {"pitch_m": 3.6}  # a default grid fits walls of any length
    # What the generator could not build goes back to the reader on the ruler it measured on.
    text = reader.feedback(record, reading, {"w00": [{"what": "w", "u_m": 19.8, "v_m": 4.0, "why": "too wide"}]},
                           ground=lambda e, n: wall.base + 0.5)
    assert "measured on w00-1.jpg" in text and "u -1.0 to 19.0 m" in text and "w at u 18.8 m, v 4.4 m" in text
    assert "google:abc" in only["src"] and "src" not in only["storeys"][0]


def test_registration_trusts_only_the_ends_a_crop_sees():
    whole = {"x0_m": 0.0, "x1_m": 20.0}
    assert reader.registration(20.0, whole, [-1.0, 19.4]) == (-1.0, 20.0 / 20.4)  # both ends: shift and scale
    assert reader.registration(20.0, whole, [-1.0, 26.0]) == (-1.0, 1.0)  # the right end is a neighbour's: shift
    assert reader.registration(20.0, whole, [-2.0, 21.5]) == (0.0, 1.0)  # a facade 18 % too long: untouched
    right = {"x0_m": 9.0, "x1_m": 20.0}
    assert reader.registration(20.0, right, [1.0, 20.5]) == (0.5, 1.0)  # the left end is out of sight
    assert reader.registration(20.0, right, None) == (0.0, 1.0)


def test_street_presence_is_the_facade_area_the_street_sees():
    from types import SimpleNamespace

    from turku3d.observe.prepare import street_presence

    wall = _wall()
    building = SimpleNamespace(walls=[wall])
    crops = [{"wall": wall.id, "x0_m": 2.0, "x1_m": 12.0}, {"wall": wall.id, "x0_m": 0.0, "x1_m": 6.0}]
    presence = street_presence(building, crops)
    assert presence == {"area_m2": round(10.0 * (wall.eaves - wall.base), 1), "frontage_m": 10.0}
    assert street_presence(building, []) == {"area_m2": 0.0, "frontage_m": 0.0}


def test_a_second_look_asks_for_the_complained_walls_only_and_keeps_the_rest():
    from pathlib import Path

    record = {"register": {"address": "Testikatu 1"}, "contexts": [
        {"file": "/p/context-1.jpg", "panorama": {"provider": "g", "date": "2026-06"}, "distance_m": 20.0,
         "pose": {"yaw": 10.0}},
        {"file": "/p/context-2.jpg", "panorama": {"provider": "g", "date": "2026-06"}, "distance_m": 25.0,
         "pose": {"yaw": 90.0}}], "walls": [], "crops": []}
    for name in ("w00", "w01"):
        record["walls"].append({"id": f"b:{name}", "length_m": 12.0, "bearing_deg": 90.0, "base_h": 5.0,
                                "eaves_h": 11.0, "top_h": 13.0, "adjoining": []})
        record["crops"].append({"file": f"/p/{name}-1.jpg", "wall": f"b:{name}", "distance_m": 9.0, "oblique_deg": 4.0,
                                "px_per_m": 50.0, "x0_m": 0.0, "x1_m": 12.0,
                                "panorama": {"provider": "g", "date": "2026-06"}})
    first = {"description": {"materials": {"plaster": {}}, "opening_types": {"win": {"w_m": 1.2}},
                             "walls": [{"id": "w00", "measured_on": "w00-1.jpg", "bays": {"count": 4}},
                                       {"id": "w01", "measured_on": "w01-1.jpg", "bays": {"count": 3}}]},
             "views": [{"file": "w00-1.jpg", "usable": True, "hidden_share": 0.1},
                       {"file": "w01-1.jpg", "usable": True, "hidden_share": 0.2}]}
    complaints = "- wall w01 (measured on w01-1.jpg; the wall is 12.0 m long):\n  - window at u 11.8 m: outside"
    task = reader.task_text(record, Path("/p"), complaints, first)
    assert "w01-1.jpg: wall w01" in task and "w00-1.jpg" not in task and "context-2.jpg" not in task
    assert '"id": "w01"' in task and '"id": "w00"' not in task
    answer = {"walls": [{"id": "w01", "measured_on": "w01-1.jpg", "bays": {"count": 2}}],
              "opening_types": {"door": {"w_m": 1.0}}, "views": [{"file": "w01-1.jpg", "usable": True, "hidden_share": 0.3}]}
    merged = reader.corrected(first, answer)
    assert [w["bays"]["count"] for w in merged["description"]["walls"]] == [4, 2]
    assert sorted(merged["description"]["opening_types"]) == ["door", "win"]
    assert [v["hidden_share"] for v in merged["views"]] == [0.1, 0.3]
    assert first["description"]["walls"][1]["bays"]["count"] == 3  # the first reading itself is untouched
    assert set(reader.correction_schema()["properties"]) == {"walls", "opening_types", "materials", "roof", "views",
                                                             "notes"}
    # A roof given in the answer replaces the roof; materials are laid over the first ones.
    roofed = reader.corrected(first, {"walls": [], "views": [], "materials": {"tile": {"family": "roof_tile"}},
                                      "roof": {"material": "tile", "dormers": [{"type": "gable"}]}})
    assert roofed["description"]["roof"]["dormers"] == [{"type": "gable"}]
    assert "tile" in roofed["description"]["materials"]
    assert [w["bays"]["count"] for w in roofed["description"]["walls"]] == [4, 3]


def test_walls_no_crop_showed_get_views_from_other_paths():
    from turku3d.observe.prepare import blocked_walls

    def crop(wall, name, distance):
        return {"wall": wall, "file": f"/p/{name}", "distance_m": distance,
                "panorama": {"provider": "lookaround", "id": name, "date": "2026-06-10"}}

    record = {"crops": [crop("b:w00", "w00-1.jpg", 18.6), crop("b:w00", "w00-2.jpg", 18.9), crop("b:w01", "w01-1.jpg", 9.0),
                        crop("b:w01", "w01-2.jpg", 12.0)]}
    reading = {"views": [{"file": "w00-1.jpg", "usable": False, "hidden_share": 0.97},
                         {"file": "w00-2.jpg", "usable": True, "hidden_share": 0.9},
                         {"file": "w01-1.jpg", "usable": False, "hidden_share": 1.0}]}  # w01-2 showed its wall
    blocked = blocked_walls(record, reading)
    assert list(blocked) == ["b:w00"] and [v["distance_m"] for v in blocked["b:w00"]] == [18.6, 18.9]
    # The quay path (same provider and year, the same distance from the wall) is avoided; the street is not.
    assert views.same_path("lookaround", "2026-06-11", 17.0, blocked["b:w00"])
    assert not views.same_path("lookaround", "2026-06-11", 10.3, blocked["b:w00"])
    assert not views.same_path("google", "2026-07", 18.6, blocked["b:w00"])
    # A wall the first reading left out is added by the answer about it.
    first = {"description": {"walls": [{"id": "w01"}]}, "views": []}
    assert [w["id"] for w in reader.corrected(first, {"walls": [{"id": "w00"}], "views": []})["description"]["walls"]] \
        == ["w01", "w00"]


def test_a_stalled_preparation_does_not_hold_the_run():
    import time

    import pytest
    from turku3d.observe.__main__ import within

    assert within(1.0, lambda: 3) == 3
    with pytest.raises(TimeoutError):
        within(0.05, lambda: time.sleep(1.0))
    with pytest.raises(KeyError):
        within(1.0, lambda: {}["missing"])


def test_against_measures_agreement_per_attribute():
    from turku3d.observe import against

    assert against.delta_e("#808080", "#808080") == 0
    assert 2.0 < against.delta_e("#d3c9b6", "#cbb992") < 12.0
    assert against.colour_class("#f2f1ec") == "white" and against.colour_class("#9c5a45") == "red"
    assert against.era(1925) == "1920-39" and against.era(None) == "undated"
    rows = [{"style_auto": "slab_block", "style_read": "detached_house", "wall_family_auto": "plaster",
             "wall_family_read": "brick", "balconies_auto": 0, "balconies_read": 2, "dormers_auto": 0,
             "dormers_read": 0, "chimneys_auto": 0, "chimneys_read": 0, "wall_dE": 4.0},
            {"style_auto": "wooden_house", "style_read": "wooden_house", "wall_family_auto": "weatherboard",
             "wall_family_read": "weatherboard", "balconies_auto": 0, "balconies_read": 0, "dormers_auto": 0,
             "dormers_read": 1, "chimneys_auto": 0, "chimneys_read": 0}]
    rates = {name: against.rate(rows, agrees) for name, agrees, _ in against.BUILDING_ATTRIBUTES}
    assert rates["wall material family"] == (1, 2) and rates["balconies (has / has not)"] == (1, 2)
    assert rates["wall colour within dE 10"] == (1, 1)  # a row without both colours is not counted
    lines = against.table(rows, against.BUILDING_ATTRIBUTES, None)
    assert any("plaster drawn, brick read ×1" in line for line in lines)
