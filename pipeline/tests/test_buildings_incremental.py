"""Per-building reconstruction keys: a new tile reconstructs only its own buildings."""

import io

import numpy as np
import pytest
import shapely
from turku3d import config as C
from turku3d.buildings import reconstruct as R
from turku3d.buildings.fallback import extrusion
from turku3d.buildings.inputs import Building
from turku3d.buildings.tiles import collision_tiles

TRANSFORM = {"scale": [0.001] * 3, "translate": [C.ORIGIN_E, C.ORIGIN_N, 0]}


def feature(building, height=8.0):
    """A roofer CityJSONFeature: one LoD 2.2 extrusion part under the building."""
    surfaces = extrusion(building.footprint, 0.0, height)
    kinds = sorted({s.kind for s in surfaces})
    vertices, boundaries = [], []
    for surface in surfaces:
        rings = []
        for ring in surface.rings:
            rings.append(list(range(len(vertices), len(vertices) + len(ring))))
            vertices += [[round(x * 1000), round(-z * 1000), round(y * 1000)] for x, y, z in ring]
        boundaries.append(rings)
    part = building.id + "-0"
    geometry = {
        "type": "MultiSurface",
        "lod": "2.2",
        "boundaries": boundaries,
        "semantics": {
            "surfaces": [{"type": kind} for kind in kinds],
            "values": [kinds.index(s.kind) for s in surfaces],
        },
    }
    return {
        "type": "CityJSONFeature",
        "id": building.id,
        "CityObjects": {
            building.id: {"type": "Building", "attributes": {"rf_h_ground": 0.0}},
            part: {"type": "BuildingPart", "parents": [building.id], "geometry": [geometry]},
        },
        "vertices": vertices,
    }


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Fake lidar files, roofer and class-6 reader; the layer store is real."""
    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "data"))
    laz = tmp_path / "data/raw/laz/pistepilvi"
    laz.mkdir(parents=True)
    for i in range(-1, 3):
        for j in range(-1, 3):
            tile = C.LazTile(int(C.ORIGIN_E + i * 500), int(C.ORIGIN_N - j * 500))
            (laz / f"{tile.name}.laz").write_bytes(b"fixture")
    monkeypatch.setattr(R, "reconstruction_code", lambda: "reconstruction-v1")
    monkeypatch.setattr(R, "selection_code", lambda: "selection-v1")
    batches = []

    def run_roofer(batch, cache, jobs):
        batches.append(sorted(b.id for b in batch))
        return {b.id: (TRANSFORM, feature(b)) for b in batch}

    monkeypatch.setattr(R, "run_roofer", run_roofer)
    roofs = []

    def class6(paths, cache):
        return np.array(roofs, dtype=float).reshape(-1, 3)

    monkeypatch.setattr(R, "class6", class6)
    layer = tmp_path / "layer"
    layer.mkdir()

    def build(buildings, established):
        roofs[:] = [
            [x, 8.0, z]
            for b in buildings
            for x in np.linspace(*b.footprint.bounds[0::2], 5)[1:-1]
            for z in np.linspace(*b.footprint.bounds[1::2], 5)[1:-1]
        ]
        batches.clear()
        shells, records, files, counts = R.reconstruct(
            buildings, established, {}, {}, layer, tmp_path / "cache", 1
        )
        for name, data in files.items():
            (layer / name).write_bytes(data)
        return shells, records, counts, list(batches)

    return build


def square(name, x, z, size=10.0):
    return Building(name, shapely.box(x, z, x + size, z + size), {"levels": "3"})


def test_new_buildings_reconstruct_alone_and_neighbours_reuse_the_layer(world):
    a, b = square("a", 10, 10), square("b", 40, 10)
    shells, records, counts, batches = world([a, b], {"a", "b"})
    assert counts == {"reconstructed": 2, "selected": 2, "buildings": 2}
    assert batches == [["a", "b"]]
    assert all(r["choice"] == "roofer" and r["pass"] for r in records)
    first = {bid: shell.positions.copy() for bid, shell in shells.items()}

    # A new tile's building far away runs alone; the others come back from the layer.
    c = square("c", 300, 10)
    shells, records, counts, batches = world([a, b, c], {"a", "b", "c"})
    assert counts == {"reconstructed": 1, "selected": 1, "buildings": 3}
    assert batches == [["c"]]
    for bid in ("a", "b"):
        np.testing.assert_array_equal(shells[bid].positions, first[bid])
    assert [r["building_id"] for r in records] == ["a", "b", "c"]
    assert all(r["pass"] for r in records)

    # A GIS gap filler next to b runs with b as roofer context but keeps b's stored result.
    d = square("gis-d", 55, 10)
    _, _, counts, batches = world([a, b, c, d], {"a", "b", "c"})
    assert counts["reconstructed"] == counts["selected"] == 1
    assert batches == [["b", "gis-d"]]

    # An established building among changes runs among established neighbours only.
    moved = square("b", 41, 10)
    _, _, counts, batches = world([a, moved, c, d], {"a", "b", "c"})
    assert counts["reconstructed"] == 1
    assert batches == [["a", "b"]]


def test_codes_lidar_and_properties_invalidate_only_what_they_feed(world, monkeypatch, tmp_path):
    a, b = square("a", 10, 10), square("b", 600, 10)
    world([a, b], {"a", "b"})
    monkeypatch.setattr(R, "selection_code", lambda: "selection-v2")
    _, _, counts, batches = world([a, b], {"a", "b"})
    assert counts == {"reconstructed": 0, "selected": 2, "buildings": 2}
    assert batches == []
    monkeypatch.setattr(R, "reconstruction_code", lambda: "reconstruction-v2")
    _, _, counts, batches = world([a, b], {"a", "b"})
    assert counts["reconstructed"] == 2 and batches == [["a", "b"]]
    # New lidar under b only reconstructs b.
    laz = R.roofer_lidar(b)[0]
    laz.write_bytes(b"refetched lidar")
    _, _, counts, batches = world([a, b], {"a", "b"})
    assert counts["reconstructed"] == 1 and batches == [["b"]]
    # A changed level count can change a placeholder: reselect, but keep the reconstruction.
    a.properties["levels"] = "4"
    _, _, counts, batches = world([a, b], {"a", "b"})
    assert counts == {"reconstructed": 0, "selected": 1, "buildings": 2}


def test_stored_shells_round_trip_and_publish_unchanged(world):
    a, b = square("a", 10, 10), square("b", 40, 10)
    shells, records, _, _ = world([a, b], {"a", "b"})
    again, stored, counts, _ = world([a, b], {"a", "b"})
    assert counts["selected"] == 0
    assert stored == records
    for bid, shell in shells.items():
        for field in ("positions", "indices", "material", "surface_ids"):
            np.testing.assert_array_equal(getattr(again[bid], field), getattr(shell, field))
        assert [s.kind for s in again[bid].surfaces] == [s.kind for s in shell.surfaces]
        assert again[bid].hausdorff == shell.hausdorff


def test_collision_tile_bytes_ignore_buildings_in_other_tiles(world):
    a, far = square("b-a", 10, 10), square("a-far", 300, 10)
    shells, _, _, _ = world([a, far], {"b-a", "a-far"})

    def tile_bytes(owners):
        tiles = collision_tiles(
            [shells[b.id] for b in owners],
            [b.footprint for b in owners],
            ids=[b.id for b in owners],
        )
        stream = io.BytesIO()
        np.savez_compressed(stream, **tiles["c_0_0"])
        return stream.getvalue()

    # "a-far" sorts before "b-a": a global building index would shift c_0_0's provenance.
    assert tile_bytes([a]) == tile_bytes([far, a])
