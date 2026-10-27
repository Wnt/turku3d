import hashlib
import json

import numpy as np
import pytest
from test_assemble import fixture
from turku3d import config as C
from turku3d.layers import write_manifest, write_visual
from turku3d.package.assemble import assemble
from turku3d.package.facades import facade_density_floor, facade_source_density, halve_densities
from turku3d.package.gltf import GlbMesh


def test_optional_facade_density_floor_is_validated(tmp_path):
    wall = tmp_path / "facades/b"
    wall.mkdir(parents=True)
    meta = wall / "0.json"
    meta.write_text(json.dumps({"min_pixels_per_metre": 32}))
    assert facade_density_floor(tmp_path / "facades", "b_0") == 32
    meta.write_text(json.dumps({"min_pixels_per_metre": 2}))
    with pytest.raises(ValueError, match="invalid minimum facade density"):
        facade_density_floor(tmp_path / "facades", "b_0")


def test_density_ladder_steps_every_far_facade_down_before_any_twice():
    ranked = ["far", "mid", "near", "showcase"]  # farthest from spawn first
    floors = {"far": 0, "mid": 0, "near": 0, "showcase": 16}
    plan: dict[str, int] = {}
    assert halve_densities(plan, ranked, floors, 2) == [("far", 64, 16), ("mid", 64, 16)]
    assert halve_densities(plan, ranked, floors, 3) == [
        ("near", 64, 16),
        ("showcase", 64, 16),
        ("far", 16, 8),
    ]
    halve_densities(plan, ranked, floors, 100)
    assert plan == {"far": 1, "mid": 1, "near": 1, "showcase": 16}  # stops at its minimum
    assert halve_densities(plan, ranked, floors, 1) == []
    assert halve_densities(plan, ranked, floors, 1, below_floors=True) == [("showcase", 16, 8)]


def test_source_density_reads_the_sidecar(tmp_path):
    wall = tmp_path / "facades/b"
    wall.mkdir(parents=True)
    (wall / "3.json").write_text(json.dumps({"pixels_per_metre": [31.8, 32.1]}))
    assert facade_source_density(tmp_path / "facades", "b_3") == 31.8


def test_tile_keys_cover_only_their_own_buildings_facades(tmp_path, monkeypatch):
    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "work"))
    layers, out = tmp_path / "layers", tmp_path / "dist"
    fixture(layers, ids=("t0_0_0", "t0_1_0"))
    buildings = layers / "buildings"
    for tid, building in (("t0_0_0", "b1"), ("t0_1_0", "b2")):
        x = C.VisualTile.parse(tid).origin[0]
        wall = GlbMesh(
            building,
            np.array([[x + 10.0, 0, 10], [x + 30.0, 0, 10], [x + 10.0, 10, 10]]),
            np.array([[0, 1, 2]], np.uint32),
            uvs=np.zeros((3, 2), np.float32),
            extras=dict(
                layer="buildings",
                object_id=building,
                material_key="road",
                building_id=building,
                facade_index=0,
            ),
        )
        write_visual(buildings / f"{tid}.vis.glb", [wall])
    write_manifest(
        buildings, inputs={}, params={}, code="test", tiles=["t0_0_0.vis.glb", "t0_1_0.vis.glb"]
    )
    inventory = buildings / "facades.geojson"
    inventory.write_text(json.dumps(dict(type="FeatureCollection", features=[])))
    facades = tmp_path / "assembly/facades"
    export = dict(inventory_sha256=hashlib.sha256(inventory.read_bytes()).hexdigest(), bound=[])
    for building in ("b1", "b2"):
        (facades / building).mkdir(parents=True)
        (facades / building / "style.json").write_text('{"style": 1}')
    (facades.parent / "MANIFEST.json").write_text(json.dumps(export))
    kwargs = dict(layers=layers, out=out, facades=facades)
    tiles = ["t0_0_0", "t0_1_0"]
    assert {"t0_0_0", "t0_1_0"} <= set(assemble(tiles, area="work-3x3", **kwargs)["changed"])
    # Another tile's facade data and buildings that no tile carries leave a tile packed.
    (facades / "b2/style.json").write_text('{"style": 2}')
    (facades / "b9").mkdir()
    (facades / "b9/style.json").write_text('{"style": 9}')
    assert set(assemble(tiles, area="work-3x3", **kwargs)["changed"]) == {
        "t0_1_0",
        "t1_0_0",
        "t2_0_0",
    }
    assert assemble(tiles, area="work-3x3", **kwargs)["changed"] == []
    (facades / "b1/style.json").write_text('{"style": 3}')
    assert "t0_0_0" in assemble(tiles, area="work-3x3", **kwargs)["changed"]
