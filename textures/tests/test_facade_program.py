"""Contracts that protect register authority, independent donors and stable variety."""

import copy
import json

import numpy as np
import pytest
from turkutex import facade_program as fp


def facade(floors=3):
    rgb = np.full((120, 240, 3), [198, 160, 115], dtype=np.uint8)
    labels = np.ones((120, 240), dtype=np.uint8)
    for y in [10, 50, 90]:
        for x in [15, 65, 115, 165, 215]:
            labels[y : y + 18, x : x + 12] = 2
    rgb[labels == 2] = [40, 60, 80]
    return fp.fit_wall(
        rgb, labels, np.zeros(labels.shape, bool), wall_id="a_1", width=24, height=12, floors=floors
    )


def test_storeys_come_only_from_register_and_unknown_is_preserved():
    w = facade(7)
    b = fp.merge_building("a", {"levels": 7}, [w])
    assert b["storeys"] == fp.value(7, "register", 1)
    assert w["split"]["middle_repeats"] == 5
    assert fp.merge_building("a", {}, [facade(None)])["storeys"]["value"] is None
    assert w["trim"]["cornice"]["value"] is None
    with pytest.raises(ValueError, match="register floors"):
        facade(0)


def test_occluder_mask_prevents_colour_and_opening_measurements():
    rgb = np.full((80, 120, 3), [0, 240, 0], dtype=np.uint8)
    w = fp.fit_wall(
        rgb,
        np.full((80, 120), 2),
        np.ones((80, 120), bool),
        wall_id="x",
        width=12,
        height=8,
        floors=2,
    )
    assert w["wall"]["colour"]["value"] is None
    assert w["bays"]["count"] == 0
    assert w["windows"]["rectangles_m"] == []


def test_rule_samples_are_order_independent_and_exclude_entire_target_building():
    donors = []
    for i in range(12):
        w = facade()
        w["wall"]["colour"]["value"] = f"#{100 + i:02x}a073"
        donors.append(fp.merge_building(str(i), {"levels": 3, "year": 1900 + i * 10}, [w]))
    rules = fp.fit_rules(donors)
    geometry = [dict(wall_id="0_other", width_m=30, height_m=12)]
    a = fp.sample_building("0", {"levels": 4, "year": 1940}, geometry, rules)
    assert a["donor_building_id"] != "0"
    b = fp.sample_building("0", {"levels": 4, "year": 1940}, geometry, fp.fit_rules(donors[::-1]))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert a["storeys"]["value"] == 4
    assert a["source"] == "rule-sampled"
    assert a["walls"][0]["windows"]["rectangles_m"] == []
    assert a["walls"][0]["wall"]["colour"]["source"] == "rule sample"
    assert len({fp.stable_seed(str(i)) for i in range(100)}) == 100
    assert fp.fit_rules([a])["exemplars"] == []


def test_merge_keeps_wall_differences_without_mutating_inputs():
    a = facade()
    b = copy.deepcopy(a)
    b["wall_id"] = "a_2"
    b["wall"]["colour"]["value"] = "#123456"
    program = fp.merge_building("a", {"levels": 3}, [b, a])
    assert len(program["walls"]) == 2
    assert {w["wall"]["colour"]["value"] for w in program["walls"]} == {
        a["wall"]["colour"]["value"],
        "#123456",
    }
    assert fp.paint(a, 3).size == (512, 256)
