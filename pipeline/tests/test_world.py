"""The world pipeline's spine: a description compiles to stored, content-addressed output and a manifest row."""

import json
import os
import struct
import types

import numpy as np
import pytest

from turku3d.world import driver, store as world_store
from turku3d.world.api import Area, Heightfield, Instances, Material, Output, Part, Stub, envelope
from turku3d.world.kinds import KINDS, _hashes
from turku3d.world.store import Store


class Posts:
    """A kind for the tests: one slab and a row of knock-over posts, sized by the description."""

    name = "posts"

    def discover(self, area, sources):
        return []

    def describe(self, stub, stored):
        return {**envelope(self.name, 1, stub, stored, "test"), **stub.facts, **{k: v for k, v in (stored or {}).items() if k == "height"}}

    def compile(self, description):
        side = description["side"]
        slab = Part("slab", np.array([[0, 0, 0], [side, 0, 0], [side, 0, side], [0, 0, side]], float),
                    np.array([[0, 2, 1], [0, 3, 2]]), Material("asphalt", {"wear": 0.3}))
        post = Part("post", np.array([[0, 0, 0], [0.1, 0, 0], [0, description["height"], 0]], float),
                    np.array([[0, 1, 2]]), Material("metal-painted", double_sided=True, surface="masked", emissive=(1.0, 0.5, 0.0)))
        places = np.array([[1, 0, 1, 0, 1, 1, 1], [3, 0, 1, 1.5, 1, 1, 1]], float)
        return Output((10.0, 5.0, -20.0), [slab], [Instances([post], places, ids=["a", "b"], behaviour="knock-over",
                                                               collision={"shape": "cylinder", "size": [0.05, 2.0]}, mass=12)],
                      heightfield=Heightfield((10.0, -20.0), side, np.full((2, 2), 5.0)))


@pytest.fixture
def posts(tmp_path, monkeypatch):
    monkeypatch.setitem(KINDS, "posts", Posts())
    monkeypatch.setitem(_hashes, "posts", "code-1")
    monkeypatch.setenv("TURKU3D_WORLD_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("TURKU3D_WORLD_DESCRIPTIONS", raising=False)
    stub = Stub("posts/p1", (0, 0, 4, 4), [{"source": "test", "key": "p1"}], {"side": 4.0, "height": 2.0})
    return Store(tmp_path / "store"), stub


def glb_json(path):
    data = path.read_bytes()
    magic, version, length = struct.unpack("<4sII", data[:12])
    assert (magic, version, length) == (b"glTF", 2, len(data))
    size, kind = struct.unpack("<I4s", data[12:20])
    assert kind == b"JSON"
    return json.loads(data[20:20 + size])


def test_entity_compiles_to_content_addressed_output_and_a_manifest_row(posts):
    store, stub = posts
    record = driver.entity_record(store, stub)
    assert record["id"] == "posts/p1" and record["origin"] == [10.0, 5.0, -20.0]
    # Bounds cover the slab and the placed posts, relative to the origin.
    assert record["bounds"][:3] == pytest.approx([-1.0, -2.0, -1.0], abs=0.01) and record["bounds"][3:] == pytest.approx([5.0, 2.0, 4.0], abs=0.01)
    mesh = record["output"]["lods"][0]
    assert mesh["url"] == f"objects/{mesh['sha256'][:2]}/{mesh['sha256']}.glb" and mesh["triangles"] == 2 and mesh["error_m"] == 0.01
    document = glb_json(store.root / mesh["url"])
    assert document["nodes"][0]["extras"] == {"entity": "posts/p1", "part": "slab"}
    assert document["materials"][0]["extras"]["turku"] == {"family": "asphalt", "params": {"wear": 0.3}, "surface": "opaque"}
    raster = record["output"]["heightfield"]
    assert (raster["size"], raster["origin"], raster["spacing"]) == ([2, 2], [10.0, -20.0], 4.0)
    assert np.frombuffer((store.root / raster["url"]).read_bytes(), "<f4").tolist() == [5.0] * 4
    # A hittable object is its own instance row: stable id, behaviour, collision shape and mass reach the manifest.
    row = driver.manifest_row(record, "scene")
    (instances,) = row["instances"]
    assert instances["ids"] == ["a", "b"] and instances["behaviour"] == "knock-over" and instances["mass"] == 12.0
    assert instances["collision"] == {"shape": "cylinder", "size": [0.05, 2.0]} and instances["bounds"][4] == 2.0
    assert instances["triangles"] == 1 and row["heightfield"]["url"] == raster["url"] and row["error_m"] == 0.01
    post = glb_json(store.root / instances["asset"])["materials"][0]
    assert (post["doubleSided"], post["alphaMode"], post["emissiveFactor"]) == (True, "MASK", [1.0, 0.5, 0.0])
    # The same description and code reuse the record; a stored description changes the output and its hash.
    assert driver.entity_record(store, stub) == record
    world_store.write_description({"schema": "turku-description/posts/1", "id": "posts/p1", "tier": "observed", "provenance": [], "height": 3.0}, by="test")
    taller = driver.entity_record(store, stub)
    assert taller["hash"] != record["hash"] and taller["description"]["tier"] == "observed"
    assert taller["output"]["lods"][0]["sha256"] == mesh["sha256"]
    with pytest.raises(ValueError):
        world_store.write_description({"schema": "turku-description/posts/1", "id": "posts/p1", "tier": "auto", "provenance": []}, by="test")


def test_refresh_rewrites_the_manifest_only_when_an_entity_changed(posts):
    store, stub = posts
    store.put_facts(stub.id, {"id": stub.id, "box": list(stub.box), "sources": stub.sources, "facts": stub.facts})
    row = driver.manifest_row(driver.entity_record(store, stub), "scene")
    store.put_location({"schema": "turku-location/1", "name": "t", "revision": 1, "entities": [row], "stats": driver.stats_of([row])})
    with store.building("t"):
        assert json.loads(store.building_path("t").read_bytes())["pid"] == os.getpid()
        assert [marker["name"] for marker in store.builds_running()] == ["t"]
        assert driver.refresh("t", store, log=lambda *_: None)["changed"] == []
    assert not store.building_path("t").exists()
    assert store.builds_running() == []
    assert store.location("t")["revision"] == 1
    world_store.write_description({"schema": "turku-description/posts/1", "id": "posts/p1", "tier": "reviewed", "provenance": [], "height": 2.5}, by="test")
    assert driver.refresh("t", store, ["posts/p1"], log=lambda *_: None)["changed"] == ["posts/p1"]
    after = store.location("t")
    assert after["revision"] == 2 and after["entities"][0]["hash"] != row["hash"] and after["entities"][0]["tier"] == "reviewed"


def test_a_kind_whose_discover_raises_costs_the_location_one_failure_row(posts, monkeypatch):
    store, stub = posts

    class Unreadable:
        name = "unreadable"

        def discover(self, area, sources):
            raise FileNotFoundError("No lidar tile 1_2")

    class NoSources:
        """Only what the driver itself reads: the terrain height at the focus; every other source is absent."""

        def __init__(self, log=print):
            self.timings = {}
            self.wfs = types.SimpleNamespace(fetched=[])
            self.terrain = types.SimpleNamespace(heights=lambda e, n: np.array([5.0]))

    monkeypatch.setattr(KINDS["posts"], "discover", lambda area, sources: [stub], raising=False)
    monkeypatch.setattr(driver, "KINDS", {"unreadable": Unreadable(), "posts": KINDS["posts"]})
    monkeypatch.setattr(driver, "Sources", NoSources)
    manifest = driver.build("t", Area(2.0, 2.0, 10.0), 50.0, store, log=lambda *_: None)
    assert [row["id"] for row in manifest["entities"]] == ["posts/p1"]
    assert manifest["failures"] == [{"id": "unreadable/*", "error": "discover: FileNotFoundError: No lidar tile 1_2"}]
    assert store.location("t")["failures"] == manifest["failures"]


def test_osm_table_has_one_row_per_point_line_and_area(tmp_path):
    import geopandas as gpd
    import osmium
    from osmium.osm.mutable import Node, Way

    from turku3d import config
    from turku3d.world.sources import osm

    corners = [(22.2600, 60.4500), (22.2604, 60.4500), (22.2604, 60.4502), (22.2600, 60.4502)]
    extract = tmp_path / "town.osm.pbf"
    with osmium.SimpleWriter(str(extract)) as writer:
        for index, (lon, lat) in enumerate(corners, start=1):
            writer.add_node(Node(id=index, location=(lon, lat), tags={"natural": "tree"} if index == 1 else {}))
        writer.add_way(Way(id=10, nodes=[1, 2, 3, 4, 1], tags={"building": "yes"}))
        writer.add_way(Way(id=11, nodes=[1, 2, 3, 4, 1], tags={"barrier": "fence"}))
        writer.add_way(Way(id=12, nodes=[1, 3], tags={"highway": "residential", "surface": "asphalt"}))
    osm.table(extract, tmp_path / "town.parquet", config.CRS_HORIZONTAL, log=lambda *_: None)
    rows = gpd.read_parquet(tmp_path / "town.parquet")
    found = {(row.osm_type, row.osm_id): row for row in rows.itertuples()}
    # A closed way is an area unless it is a linear thing; untagged nodes are only vertices.
    assert {key: row.geometry.geom_type for key, row in found.items()} == {
        ("node", 1): "Point", ("way", 10): "MultiPolygon", ("way", 11): "LineString", ("way", 12): "LineString"}
    assert found["way", 12].surface == "asphalt" and json.loads(found["way", 12].tags)["highway"] == "residential"
    # 0.0004 degrees of longitude at this latitude is 22 m, in the survey frame.
    building = found["way", 10]
    assert building.e1 - building.e0 == pytest.approx(22.0, abs=0.5) and 23_450_000 < building.e0 < 23_470_000
