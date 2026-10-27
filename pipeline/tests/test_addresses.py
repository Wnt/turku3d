"""Delivery address pool from a synthetic buildings layer and road graph (no city data)."""

import json

import pytest
from shapely.geometry import box, mapping
from turku3d import config as C
from turku3d.content import addresses
from turku3d.content.addresses import build_pool, slug
from turku3d.package.manifest import DistTree, verify_tree
from turku3d.roads import CAR, CLASS_SHIFT, FOOT, ONEWAY

STREET = 2 << CLASS_SHIFT
ORIGIN = (0.0, 5.0)


def en(x, z):
    return [C.ORIGIN_E + x, C.ORIGIN_N - z]


def wall(building, index, a, b, normal, street_facing, base=2.0):
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [en(*a), en(*b)]},
        "properties": {
            "building_id": building,
            "facade_index": index,
            "base_height": base,
            "width_m": ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5,
            "normal": [normal[0], 0.0, normal[1]],
            "street_facing": street_facing,
        },
    }


def building(bid, address="", entrances=()):
    return {
        "building_id": bid,
        "semantics": {
            "address": address,
            "entrances": [
                {"geometry": {"type": "Point", "coordinates": en(*p)}, "tags": {"entrance": kind}}
                for p, kind in entrances
            ],
        },
    }


@pytest.fixture
def layer(tmp_path):
    return write_layer(tmp_path / "buildings")


def write_layer(path):
    """A 20 m block north of an east-west street at z = 0, a second block behind it and an unaddressed shed.

    The street runs from x = -100 to 300; cars may drive it only westward (oneway from b to a reversed).
    """
    blocks = {"A": box(50, 10, 70, 30), "B": box(150, 60, 170, 80), "S": box(-60, 10, -50, 20)}
    metrics = {
        "buildings": [
            building("A", "Testikatu  4", [((60, 10), "main"), ((52, 10), "garage")]),
            building("B", "Takapiha 2"),
            building("S"),
        ]
    }
    facades = [
        wall("A", 0, (50, 10), (70, 10), (0, -1), True),
        wall("A", 1, (70, 10), (70, 30), (1, 0), False),
        wall("A", 2, (60, 10), (70, 10), (0, -1), True, base=14.0),  # roof step, not a door wall
        wall(
            "B", 0, (150, 60), (170, 60), (0, -1), False
        ),  # faces the street 60 m away: not street-side
        wall("S", 0, (-60, 10), (-50, 10), (0, -1), True),
    ]
    path.mkdir(parents=True)
    (path / "metrics.json").write_text(json.dumps(metrics))
    (path / "facades.geojson").write_text(json.dumps({"features": facades}))
    (path / "footprints.geojson").write_text(
        json.dumps(
            {
                "features": [
                    {"geometry": mapping(p), "properties": {"building_id": k}}
                    for k, p in blocks.items()
                ]
            }
        )
    )
    return path


def graph(car_oneway_west=False):
    # Node 0 at x = 300, node 1 at x = -100: with ONEWAY a car may only drive 0 -> 1 (westward).
    flags = FOOT | CAR | STREET | (ONEWAY if car_oneway_west else 0)
    return {"nodes": [300.0, 0.0, -100.0, 0.0], "edges": [[0, 1, flags, 400.0]]}


def test_slug_is_stable_ascii():
    assert slug("Läntinen Rantakatu 13a") == "lantinen-rantakatu-13a"
    assert slug("Itäinen  Rantakatu 15") == "itainen-rantakatu-15"


def test_pool_has_a_street_side_drop_off_per_address(layer):
    pool = build_pool(layer, graph(), origin=ORIGIN)
    assert pool["format"] == "turku3d-addresses" and pool["origin"] == [0.0, 5.0]
    [entry] = pool["addresses"]  # the back block faces no street; the shed has no address
    assert (
        entry["id"] == "testikatu-4"
        and entry["address"] == "Testikatu 4"
        and entry["building"] == "A"
    )
    # The main entrance on the street wall, 2.5 m out onto the sidewalk; the garage door loses.
    assert entry["door"] == "entrance" and entry["point"] == [60.0, 7.5]
    assert entry["street"] == [60.0, 0.0]
    assert entry["foot"] == 60.0 and entry["car"] == 60.0
    assert entry["tile"] == C.VisualTile.at(60, 7.5).id
    assert pool["tiles"] == {entry["tile"]: 1}


def test_oneway_streets_bind_cars_only(layer):
    [entry] = build_pool(layer, graph(car_oneway_west=True), origin=ORIGIN)["addresses"]
    assert (
        entry["foot"] == 60.0 and entry["car"] is None
    )  # the drop-off is east, cars drive west only
    [west] = build_pool(layer, graph(car_oneway_west=True), origin=(140.0, 5.0))["addresses"]
    assert west["foot"] == 80.0 and west["car"] == 80.0


def test_register_points_add_a_second_address_at_the_wall_nearest_to_them(layer):
    pool = build_pool(
        layer, graph(), [("Kulmakatu 1", (69.0, 25.0)), ("Kaukana 5", (0.0, 900.0))], ORIGIN
    )
    by_id = {e["id"]: e for e in pool["addresses"]}
    assert set(by_id) == {"testikatu-4", "kulmakatu-1"}
    # The east wall has no street in front, so the corner address uses the street wall nearest to the point.
    assert by_id["kulmakatu-1"]["door"] == "wall" and by_id["kulmakatu-1"]["point"] == [69.0, 7.5]
    assert [e["id"] for e in pool["addresses"]] == [
        "testikatu-4",
        "kulmakatu-1",
    ]  # by route distance


def test_too_close_and_out_of_area_addresses_are_skipped(layer):
    assert (
        build_pool(layer, graph(), origin=(58.0, 5.0))["addresses"] == []
    )  # the pizzeria's own door
    assert build_pool(layer, graph(), origin=ORIGIN, tiles={"t0_9_9"})["addresses"] == []


def test_publish_records_a_verified_manifest_entry(tmp_path, monkeypatch):
    root = tmp_path / "layers"
    write_layer(root / "buildings")
    (root / "roads").mkdir(parents=True)
    (root / "roads/roadgraph.json").write_text(json.dumps(graph()))
    (root / "roads/MANIFEST.json").write_text(
        json.dumps({"params": {"graph": {"file": "roadgraph.json"}}})
    )
    monkeypatch.setattr(addresses, "PIZZERIA", ORIGIN)
    monkeypatch.setattr(addresses, "read_address_points", lambda: [])
    tree = DistTree(tmp_path / "dist")
    assert addresses.publish(root, tree, {C.VisualTile.at(60, 7.5).id}) is True
    assert tree.addresses["count"] == 1 and tree.addresses["url"].startswith("addresses/")
    tree.commit(code="test")
    assert verify_tree(tmp_path / "dist") == []
    again = DistTree(tmp_path / "dist")
    assert addresses.publish(root, again, {C.VisualTile.at(60, 7.5).id}) is False
    (root / "buildings/metrics.json").unlink()
    assert addresses.publish(root, again, set()) is True and again.addresses is None
