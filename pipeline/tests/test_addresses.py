"""Delivery address pool from a synthetic buildings layer and road graph (no city data)."""

import json

import pytest
from shapely.geometry import box, mapping
from turku3d import config as C
from turku3d.content import addresses
from turku3d.content.addresses import build_pool, slug, street_of
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
    return write_buildings(path, blocks, metrics["buildings"], facades)


def write_buildings(path, blocks, records, facades):
    path.mkdir(parents=True)
    (path / "metrics.json").write_text(json.dumps({"buildings": records}))
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


def graph(car_oneway_west=False, footways=()):
    """The street at z = 0; with ONEWAY a car may only drive node 0 (x = 300) -> node 1 (x = -100).

    `footways` are extra walk-only edges ((x0, z0), (x1, z1)), not connected to the street.
    """
    flags = FOOT | CAR | STREET | (ONEWAY if car_oneway_west else 0)
    nodes, edges = [300.0, 0.0, -100.0, 0.0], [[0, 1, flags, 400.0]]
    for a, b in footways:
        n = len(nodes) // 2
        nodes += [*a, *b]
        edges.append([n, n + 1, FOOT, ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5])
    return {"nodes": nodes, "edges": edges}


def test_slug_is_stable_ascii():
    assert slug("Läntinen Rantakatu 13a") == "lantinen-rantakatu-13a"
    assert slug("Itäinen  Rantakatu 15") == "itainen-rantakatu-15"


def test_street_of_drops_the_house_number():
    assert street_of("Läntinen  Rantakatu 13a") == "läntinen rantakatu"
    assert street_of("Volter Kilven katu 2") == "volter kilven katu"
    assert street_of("Linnankatu 9 B") == "linnankatu"


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
        json.dumps(
            {
                "inputs": {"osm": {"path": str(tmp_path / "osm.parquet")}},
                "params": {"graph": {"file": "roadgraph.json"}},
            }
        )
    )
    monkeypatch.setattr(addresses, "PIZZERIA", ORIGIN)
    monkeypatch.setattr(addresses, "read_address_points", lambda: [])
    monkeypatch.setattr(addresses, "read_streets", lambda osm: {})
    tree = DistTree(tmp_path / "dist")
    assert addresses.publish(root, tree, {C.VisualTile.at(60, 7.5).id}) is True
    assert tree.addresses["count"] == 1 and tree.addresses["url"].startswith("addresses/")
    tree.commit(code="test")
    assert verify_tree(tmp_path / "dist") == []
    again = DistTree(tmp_path / "dist")
    assert addresses.publish(root, again, {C.VisualTile.at(60, 7.5).id}) is False
    (root / "buildings/metrics.json").unlink()
    assert addresses.publish(root, again, set()) is True and again.addresses is None


def test_a_wall_shared_with_a_neighbour_is_skipped_for_the_street_wall(tmp_path):
    """Linnankatu 13a: the register point is nearest to the party wall, whose sidewalk spot is in the neighbour.

    The footway east of the neighbour lies in front of the party wall, but a drop-off there would be
    inside the neighbour's footprint, so the door moves to the street wall instead of the address
    being dropped.
    """
    blocks = {"N": box(100, 10, 110, 30), "M": box(110, 10, 118, 30)}
    facades = [
        wall("N", 0, (100, 10), (110, 10), (0, -1), True),
        wall("N", 1, (110, 10), (110, 30), (1, 0), False),  # shared with M
        wall("M", 0, (110, 10), (118, 10), (0, -1), True),
    ]
    layer = write_buildings(tmp_path / "b", blocks, [building("N"), building("M")], facades)
    roads = graph(footways=[((121.0, 10.0), (121.0, 40.0))])
    [entry] = build_pool(layer, roads, [("Testikatu 13a", (108.0, 25.0))], ORIGIN)["addresses"]
    assert entry["building"] == "N" and entry["door"] == "wall"
    assert entry["point"] == [108.0, 7.5]


def test_a_sidewalk_drawn_along_the_wall_counts_as_in_front(tmp_path):
    """The walkable network runs on the facade line, slightly inside the footprint, as OSM sidewalks do."""
    blocks = {"W": box(40, 30, 60, 50)}
    facades = [wall("W", 0, (40, 30), (60, 30), (0, -1), False)]  # not flagged, street 30 m away
    layer = write_buildings(tmp_path / "b", blocks, [building("W", "Rantapolku 1")], facades)
    roads = graph(footways=[((-50.0, 30.5), (70.0, 30.5))])
    [entry] = build_pool(layer, roads, origin=(-40.0, 31.0))["addresses"]
    assert entry["door"] == "wall" and entry["point"] == [50.0, 27.5]
    assert entry["street"] == [50.0, 30.5]
    # Without that sidewalk the street is 30 m in front: not this wall's street side.
    assert build_pool(layer, graph(), origin=ORIGIN)["addresses"] == []


def test_the_door_faces_the_address_street(tmp_path):
    """A corner block: the door goes on the wall facing the address's own street, not the widest wall."""
    blocks = {"K": box(50, 10, 90, 22)}
    facades = [
        wall("K", 0, (50, 10), (90, 10), (0, -1), True),  # 40 m, on the street at z = 0
        wall("K", 1, (90, 10), (90, 22), (1, 0), True),  # 12 m, on Sivukatu at x = 96
    ]
    layer = write_buildings(tmp_path / "b", blocks, [building("K", "Sivukatu 2")], facades)
    # The street at z = 0 with a footway north along x = 93 from the junction at node 2.
    flags = FOOT | CAR | STREET
    roads = {
        "nodes": [300.0, 0.0, -100.0, 0.0, 93.0, 0.0, 93.0, 60.0],
        "edges": [[0, 2, flags, 207.0], [2, 1, flags, 193.0], [2, 3, FOOT, 60.0]],
    }
    streets = {
        "sivukatu": [[(96.0, -20.0), (96.0, 80.0)]],
        "testikatu": [[(-100.0, 0.0), (300.0, 0.0)]],
    }
    [entry] = build_pool(layer, roads, origin=ORIGIN, streets=streets)["addresses"]
    assert entry["point"] == [92.5, 16.0]
    [widest] = build_pool(layer, roads, origin=ORIGIN)["addresses"]
    assert widest["point"] == [70.0, 7.5]
