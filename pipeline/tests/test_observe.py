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
                       "storeys": [{"kind": "upper", "count": 3, "height_m": 3.1, "window": "w", "src": "invented"}],
                       "conf": {"*": 0.7}},
                      {"id": "w77", "like": "w00"}],
            "roof": {"material": {"family": "sheet_metal", "colour": "#444444"},
                     "chimneys": [{"anchor": {"wall": "w00", "u_m": 9.0, "setback_m": 3.0}}]},
        },
        # The picture lies a metre to the left of the wall on the ruler: the facade is seen from u -1 to 19.
        "views": [{"file": "w00-1.jpg", "usable": True, "hidden_share": 0.25, "facade_u_m": [-1.0, 19.0],
                   "hidden_m": [[3.0, 0.0, 5.0, 2.0]]}],
    }
    meta = {"model": "m", "effort": "low", "seconds": 1.0, "cost_usd": 0.1, "images": 1, "at": "2026-11-12T22:00:00Z"}
    doc = reader.assemble(record, reading, meta)
    assert fmt.errors(doc) == []
    assert doc["id"] == "building/100000000A" and doc["tier"] == "observed"
    assert [w["id"] for w in doc["walls"]] == ["w00"]  # a wall the building does not have is dropped
    only = doc["walls"][0]
    assert only["frame"]["p0"] == [0.0, 0.0] and only["frame"]["p1"] == [20.0, 0.0]
    assert only["observed"]["visible_share"] == 0.75
    assert only["bays"]["conf"] == {"first_m": 0.4}  # a confidence keyed by a position's name is left alone
    assert only["bays"]["first_m"] == 3.0 and only["observed"]["hidden_m"] == [[4.0, 0.0, 6.0, 2.0]]
    assert doc["roof"]["chimneys"][0]["anchor"]["u_m"] == 10.0  # roof parts follow their wall
    assert "measured_on" not in only and "w00 +1.00 m" in doc["provenance"][0]["note"]
    assert doc["wall_defaults"]["bays"] == {"pitch_m": 3.6}  # a default grid fits walls of any length
    assert "google:abc" in only["src"] and "src" not in only["storeys"][0]


def test_registration_trusts_only_the_ends_a_crop_sees():
    whole = {"x0_m": 0.0, "x1_m": 20.0}
    assert reader.registration(20.0, whole, [-1.0, 19.4]) == (-1.0, 20.0 / 20.4)  # both ends: shift and scale
    assert reader.registration(20.0, whole, [-1.0, 26.0]) == (-1.0, 1.0)  # the right end is a neighbour's: shift
    assert reader.registration(20.0, whole, [-2.0, 21.5]) == (0.0, 1.0)  # a facade 18 % too long: untouched
    right = {"x0_m": 9.0, "x1_m": 20.0}
    assert reader.registration(20.0, right, [1.0, 20.5]) == (0.5, 1.0)  # the left end is out of sight
    assert reader.registration(20.0, right, None) == (0.0, 1.0)
