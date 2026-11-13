"""The proven game's landmark descriptions as a layer of the effective description (landmark.py)."""

import copy

import numpy as np
from test_building_compile import gabled_shell
from turku3d.world.kinds.building import compile as compile_module
from turku3d.world.kinds.building import describe, description, landmark

ROW = {  # the shape of an old elevation: engine frame x = E - 23459500, z = -(N - 6704500)
    "normal_xz": [-0.566, -0.824], "axis_xz": [0.824, -0.566], "origin_xz": [64.467, 50.65], "width_m": 17.35,
    "height_m": 9.9, "plinth_m": 0.55, "bands": [[2.48, 0.1]], "base_offset_m": 1.0,
    "pilasters": [[0.35, 0.4, 0.55, 4.45]],
    "openings": [{"x_m": 2.0, "sill_m": 2.7, "width_m": 1.0, "height_m": 3.0, "rows": 4},
                 {"x_m": 8.7, "sill_m": 0.1, "width_m": 1.9, "height_m": 2.3, "kind": "door", "shape": "rect"}],
    "medallions": [[7.2, 7.2, 0.24]],
    "relief": [{"polygon_m": [[5.8, 5.8], [11.5, 5.8], [8.7, 9.9]], "anchor_m": [8.7, 5.9], "depth_m": 0.16,
                "material": "elevation", "offset_m": 0.03},
               {"path_m": [[11.5, 8.0], [8.7, 9.9], [5.8, 8.0]], "anchor_m": [8.7, 5.9], "width_m": 0.15,
                "depth_m": 0.25, "material": "trim", "offset_m": 0.18}],
}
DESCRIPTION = {
    "version": 1, "building_id": "test", "name": "Test hall", "style": "1896-brick-market-hall", "_tier": "reviewed",
    "brick": {"colour": [133, 76, 63], "mortar_colour": [154, 137, 112], "bond": "running", "size_m": [0.27, 0.075]},
    "stone": {"colour": [113, 111, 103], "mortar_colour": [138, 133, 120], "size_m": [0.6, 0.3]},
    "trim_colour": [187, 181, 155], "roof": {"material": "metal", "colour": [117, 55, 44], "seam_m": 0.6},
    "elevations": [ROW],
}


def end_shell():
    """A hall whose front (left to right seen from outside) lies on the elevation's line."""
    origin = np.array([23459500.0 + 64.467, 6704500.0 - 50.65])
    axis = np.array([0.824, 0.566])  # the elevation axis in E, N
    return gabled_shell(origin + axis * 17.35, origin, depth=30.0, eaves=8.0, ridge=3.0, base=4.0)


def test_an_elevation_lands_where_the_old_game_drew_it():
    doc = describe.automatic("test", end_shell(), {"use": "1050", "year": 1893, "material": "tiili", "storeys": 1})
    out = landmark.layer("test", doc, None, copy.deepcopy(DESCRIPTION))
    front = [w for w in out["walls"] if w.get("openings")]
    assert len(front) == 1, out["walls"]
    wall = front[0]
    # The elevation's x runs along its axis; the frame runs left to right seen from outside, here the other way.
    window = next(o for o in wall["openings"] if out["opening_types"][o["type"]]["kind"] == "window")
    assert abs(window["u_m"] - (17.35 - 2.0)) < 0.02 and window["v_m"] == 2.7
    assert out["opening_types"][window["type"]]["head"] == "round"
    assert abs(wall["frame"]["base_h"] - 5.0) < 1e-6
    assert wall["verticals"][0]["courses"]["pitch_m"] == 0.65
    assert {"colour" in r for r in wall["reliefs"]} == {True, False}
    assert wall["attached"][0]["type"] == "medallion"
    assert out["roof"]["chimneys"] == [] and out["style"]["type"] == "public"


def test_a_landmark_compiles_with_its_relief_and_validates():
    shell = end_shell()
    register = {"use": "1050", "year": 1893, "material": "tiili", "storeys": 1}
    doc = describe.automatic("test", shell, register)
    over = landmark.layer("test", doc, None, copy.deepcopy(DESCRIPTION))
    doc.pop("roof")
    describe.lay(doc, over)
    doc = describe.measured(doc)
    assert description.errors({k: v for k, v in doc.items() if k not in ("shell", "register")}) == []
    output = compile_module.compile(doc)
    assert not [line for line in output.report if "no shell wall" in line], output.report
    trim = sum(len(p.triangles) for p in output.parts if p.name == "trim")
    assert trim > 200, trim


def test_a_reading_of_the_walls_outranks_an_automatic_estimate():
    doc = describe.automatic("test", end_shell(), {"use": "1050", "year": 1893, "material": "tiili"})
    automatic = dict(copy.deepcopy(DESCRIPTION), _tier="observed")
    assert landmark.layer("test", doc, {"walls": [{"id": "w"}]}, automatic) is None
    assert landmark.layer("test", doc, {"measured": {}}, automatic) is not None
    assert landmark.layer("test", doc, {"walls": [{"id": "w"}]}, copy.deepcopy(DESCRIPTION)) is not None
