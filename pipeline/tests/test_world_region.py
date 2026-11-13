"""Regions built in units (docs/design/world-regions.md): the square query, ownership, seams and chunk objects."""

import json

from turku3d.world import region
from turku3d.world.api import Area
from turku3d.world.frame import to_survey
from turku3d.world.store import Store


def test_a_square_area_touches_its_corners_and_nothing_beyond_its_sides():
    square, circle = Area(0.0, 0.0, 100.0, "square"), Area(0.0, 0.0, 100.0)
    corner = (90.0, 90.0, 95.0, 95.0)
    assert square.touches(corner) and not circle.touches(corner)
    assert not square.touches((101.0, -5.0, 120.0, 5.0))
    assert square.grown(150.0).shape == "square"


def row(id_, x, z, hash_="h"):
    return {"id": id_, "kind": id_.split("/")[0], "tier": "auto", "role": "scene", "hash": hash_,
            "origin": [x, 10.0, z], "bounds": [-1, 0, -1, 1, 5, 1], "triangles": 10, "bytes": 100, "instances": []}


def unit_manifest(name, uid, rows, cells=()):
    return {"schema": "turku-location/1", "name": f"{name}/{uid}", "revision": 1, "built": "2026-11-13T00:00:00Z",
            "origin": [23459500, 6704500, 0], "focus": [250.0, 10.0, 250.0], "entities": rows,
            "collision": {c: {"url": f"objects/aa/{c}.col"} for c in cells}, "roads": {}, "timings": {"totalMs": 1},
            "failures": []}


def test_assemble_keeps_each_row_once_by_its_origin_and_reports_the_seams(tmp_path):
    store = Store(tmp_path / "store")
    e, n, _ = to_survey(250.0, 0.0, 250.0)
    region.extend(store, "r", (e, n), None, [], ["u_0_0", "u_1_0"])
    # A building on the border, found by both units with the same hash; a lamp each unit built in its margin, one
    # of which the owner made differently; a ground cell of the outer margin that no unit owns.
    store.put_location(unit_manifest("r", "u_0_0", [
        row("building/a", 495.0, 100.0), row("lamp/l1", 510.0, 300.0, "x"), row("ground/c_-1_0", -30.0, 10.0),
        row("trees/c_0_0", 10.0, 10.0)], cells=("c_0_0", "c_8_0", "c_-1_0")))
    store.put_location(unit_manifest("r", "u_1_0", [
        row("building/a", 495.0, 100.0), row("lamp/l1", 510.0, 300.0, "y"), row("lamp/l2", 600.0, 260.0)],
        cells=("c_8_0", "c_0_0")))
    index = region.assemble(store, "r", log=lambda *_: None, place=False)
    assert index["schema"] == "turku-region/1" and index["stats"]["entities"] == 4
    assert set(index["chunks"]) == {"0_0", "1_0", "2_1"}
    chunk = json.loads((store.root / index["chunks"]["1_0"]["url"]).read_bytes())
    assert [r["id"] for r in chunk["entities"]] == ["building/a"]
    lamp = json.loads((store.root / index["chunks"]["2_1"]["url"]).read_bytes())["entities"]
    assert {r["id"]: r["hash"] for r in lamp} == {"lamp/l1": "y", "lamp/l2": "h"}
    border = index["seams"]["borders"]["u_0_0|u_1_0"]
    assert (border["equal"], border["differ"], border["ids"]) == (1, 1, ["lamp/l1"])
    # Each collision cell from the unit that owns it; the outer margin's cell from nobody.
    assert set(index["collision"]) == {"c_0_0", "c_8_0"}
    # Assembling again from the same units writes the same chunk objects: a ring added later leaves them as they are.
    again = region.assemble(store, "r", log=lambda *_: None, place=False)
    assert again["revision"] == 2 and again["chunks"] == index["chunks"]
