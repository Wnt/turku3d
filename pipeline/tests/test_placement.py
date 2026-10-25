"""Placement acceptance uses tiny exact synthetic surfaces and mapped exclusions."""

from dataclasses import replace

import numpy as np
from shapely.geometry import LineString, box
from turku3d.placement import Instance, Surfaces, Validator


def surface(material=2, height=3, deck=False):
    return Surfaces(
        np.array(
            [
                [[-10, height, -10], [10, height, -10], [10, height, 10]],
                [[-10, height, -10], [10, height, 10], [-10, height, 10]],
            ]
        ),
        [material, material],
        [deck, deck],
    )


def bench(**kwargs):
    return replace(Instance("bench", "bench", 0, 3, 0, width=1.8, length=0.56), **kwargs)


def test_bench_over_water():
    v = Validator(surface(14), {"water": box(-10, -10, 10, 10)})
    assert "water" in v.check(bench())
    assert v.repair(bench())[0] is None


def test_bridge_requires_structures_deck_not_ground_label():
    zones = {"bridge": box(-10, -10, 10, 10), "water": box(-10, -10, 10, 10)}
    v = Validator(surface(24), zones)
    assert "missing_bridge_deck" in v.check(bench())
    assert v.repair(bench())[0] is None
    assert Validator(surface(24, deck=True), zones).check(bench()) == []


def test_sign_in_road_moves_to_edge_and_faces_road():
    road = box(-1, -10, 1, 10)
    v = Validator(surface(), {"carriageway": road, "road": LineString([(0, -10), (0, 10)])})
    sign = Instance("sign", "sign", 0, 9, 0)
    assert "carriageway" in v.check(sign)
    fixed, _ = v.repair(sign)
    assert fixed is not None and 1.1 <= abs(fixed.x) <= 2
    assert v.check(fixed) == [] and fixed.y == 3


def test_car_in_driveway_cannot_be_published():
    v = Validator(surface(1), {"parking": box(-10, -10, 10, 10), "driveway": box(-5, -5, 5, 5)})
    car = Instance("car", "car", 0, 3, 0, width=1.8, length=4.5)
    assert "driveway" in v.check(car)
    assert v.repair(car)[0] is None


def test_snap_floating_and_buried_height_and_reject_missing_support():
    v = Validator(surface())
    assert "floating" in v.check(bench(y=3.151))
    assert "buried" in v.check(bench(y=2.97))
    assert v.check(bench(y=3.15)) == []
    assert v.repair(bench(y=12))[0].y == 3
    assert v.repair(bench(x=40))[0] is None


def test_whole_base_clearance_and_repair_limit():
    v = Validator(surface(), {"building": box(0.7, -0.2, 5, 0.2)})
    assert "building" in v.check(bench())
    fixed, _ = v.repair(bench())
    assert fixed is not None and np.hypot(fixed.x, fixed.z) <= 2
    assert v.check(fixed) == []
    assert v.repair(bench(object_id="second"))[0] is not None
    assert v.check(fixed) == []


def test_tree_rejects_pavement_without_explicit_tree_pit():
    tree = Instance("tree", "tree", 0, 3, 0)
    assert "surface_type" in Validator(surface()).check(tree)
    assert Validator(surface(), {"tree_pit": box(-1, -1, 1, 1)}).check(tree) == []


def test_sloped_support_uses_triangle_not_nearest_vertex():
    triangles = [
        [[-10, 2, -10], [10, 4, -10], [10, 4, 10]],
        [[-10, 2, -10], [10, 4, 10], [-10, 2, 10]],
    ]
    v = Validator(Surfaces(triangles, [2, 2]))
    assert v.surfaces.at(0, 0)[0] == 3
    assert "uneven_support" in v.check(bench())


def test_missing_bridge_with_no_triangle_is_explicit():
    v = Validator(surface(), {"bridge": box(30, -10, 50, 10)})
    fixed, issues = v.repair(bench(x=40))
    assert fixed is None
    assert "missing_bridge_deck" in issues


def test_full_batch_revalidation_fails_before_publication(tmp_path, monkeypatch):
    import pytest
    from turku3d.placement import inputs, report
    from turku3d.props import Prop

    class BrokenRepair(Validator):
        def repair(self, obj):
            return obj, []

    monkeypatch.setattr(inputs, "context", lambda *args: BrokenRepair(surface()))
    saved = tmp_path / "placement.json"

    def record(layer, bounds, records, hard, area):
        assert hard[0]["violations"] == ["floating"]
        saved.write_text("failed")
        return saved

    monkeypatch.setattr(report, "write_report", record)
    with pytest.raises(ValueError, match="hard placement violations"):
        inputs.place_objects([Prop("bad", "bench", 0, 20, 0)], "props", None, {})
    assert saved.read_text() == "failed"


def test_distant_walkway_does_not_invent_orientation():
    v = Validator(surface(), {"walkway": LineString([(100, -10), (100, 10)])})
    assert v.facing(bench()) is None
