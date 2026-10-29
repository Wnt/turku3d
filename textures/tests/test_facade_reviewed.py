"""Review boundary: incomplete evidence must never become an invented full facade."""

import copy
import json
from pathlib import Path

import numpy as np
from turkutex.facade_program import fit_rules


def programs():
    return json.loads((Path(__file__).parents[1] / "library/facade-programs.json").read_text())


def test_reviewed_geometry_storeys_and_private_evidence_boundary():
    rows = programs()
    assert len({b["building_id"] for b in rows}) == len(rows)
    for building in rows:
        assert building["storeys"]["value"] == building["attributes"]["levels"]
        assert building["storeys"]["source"] == "register"
        for wall in building["walls"]:
            g = wall["geometry"]
            assert abs(np.linalg.norm(np.subtract(g["p1"], g["p0"])) - wall["width_m"]) < 0.01
            assert abs(np.linalg.norm(g["normal"]) - 1) < 0.001
            for rect in wall["windows"]["rectangles_m"] + wall["base"]["openings_m"]:
                x, y, w, h = rect
                assert min(x, y) >= -0.002 and min(w, h) > 0
                assert x + w <= wall["width_m"] + 0.002
                assert y + h <= wall["height_m"] + 0.002
            if wall["observation"]["needs_review"]:
                assert wall["wall"]["colour"]["value"] is None
                assert not wall["windows"]["rectangles_m"]
            if wall["observation"]["scope"] == "ground-only":
                assert not wall["windows"]["rectangles_m"]
                assert wall["split"]["middle_repeats"] is None
    text = json.dumps(rows)
    assert all(s not in text for s in ("/Users/", "pano_id", "data:image", ".jpg", ".png"))


def test_partial_unknown_and_conflicted_walls_cannot_train_full_facade_rules():
    base = copy.deepcopy(programs()[0])
    wall = next(w for w in base["walls"] if w["wall_id"] == base["representative_wall"])
    wall["observation"].update(visible_share=1, scope="full-wall", register_height_conflict=False)
    wall["bays"]["count"] = 5
    assert fit_rules([base])["exemplars"]
    for field, value in (("scope", "ground-only"), ("register_height_conflict", True)):
        candidate = copy.deepcopy(base)
        w = next(w for w in candidate["walls"] if w["wall_id"] == candidate["representative_wall"])
        w["observation"][field] = value
        assert not fit_rules([candidate])["exemplars"]
    wall["bays"]["count"] = None
    assert not fit_rules([base])["exemplars"]
