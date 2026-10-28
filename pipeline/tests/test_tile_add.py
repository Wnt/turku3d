"""make tile-add: planning, data-root listing, refresh staleness and assembly of an expansion tile."""

import json

import numpy as np
import pytest
from test_assemble import fixture
from turku3d import config as C
from turku3d import refresh
from turku3d.layers import write_manifest, write_visual
from turku3d.package.assemble import assemble
from turku3d.package.gltf import GlbMesh
from turku3d.package.manifest import verify_tree
from turku3d.package.seams import verify_seams
from turku3d.region import tiles as T

CORE = list(C.CORE_TILES)


@pytest.fixture(autouse=True)
def roots(tmp_path, monkeypatch):
    data, work = tmp_path / "data", tmp_path / "work"
    monkeypatch.setenv("TURKU3D_DATA", str(data))
    monkeypatch.setenv("TURKU3D_WORK", str(work))
    return data, work


def test_plan_grows_the_area_by_one_touching_tile_anywhere_the_lidar_covers(monkeypatch):
    assert T.plan("t0_2_0", CORE) == [*CORE, "t0_2_0"]
    assert T.plan("t0_-1_1", CORE)[0] == "t0_-1_1"
    with pytest.raises(ValueError, match="already playable"):
        T.plan("t0_1_0", CORE)
    with pytest.raises(ValueError, match="touch"):
        T.plan("t0_3_0", CORE)
    assert T.plan("t0_2_2", CORE)[-1] == "t0_2_2"  # corner of t0_1_1
    with pytest.raises(ValueError, match="L0"):
        T.plan("t1_1_0", CORE)
    # t0_3_0's input area leaves work-3x3 at x = 1000; it reads its own area instead.
    assert T.plan("t0_3_0", [*CORE, "t0_2_0"])[-1] == "t0_3_0"
    assert C.source_area("t0_2_0") == "work-3x3"
    assert C.source_area("t0_3_0") == "tile-t0_3_0"
    assert C.source_area("t0_1_-1") == "work-3x3"
    from turku3d.fetch import laz

    monkeypatch.setattr(laz, "known_tiles", lambda: {("pistepilvi", "23459500_6704500")})
    with pytest.raises(ValueError, match="lidar tiles the city does not publish"):
        T.plan("t0_2_0", CORE)


def test_tile_input_area_is_the_ground_envelope():
    from turku3d.ground.build import envelope

    tile = C.VisualTile.parse("t0_3_0")
    area = C.area("tile-t0_3_0")
    assert area == C.tile_input_area(tile)
    assert area.core == envelope(tile)[1]
    assert area.with_margin.to_en().as_tuple() == (23460137.5, 6704137.5, 23460612.5, 6704612.5)
    assert sorted(t.name for t in area.laz_tiles) == [
        "23460000_6704500",
        "23460000_6705000",
        "23460500_6704500",
        "23460500_6705000",
    ]
    assert C.area_arg("tile-t0_3_0") == "tile-t0_3_0"
    with pytest.raises(ValueError):
        C.area_arg("tile-t1_0_0")


def test_ensure_inputs_fetches_only_outside_work_3x3(monkeypatch):
    from turku3d.fetch import cached

    checked, fetched = [], []
    state = {"missing": ["raw/wfs/tile-t0_3_0/x.parquet"]}

    def check(area):
        checked.append(area.name)
        return state["missing"] if area.name != "work-3x3" else []

    def run(command, check):
        fetched.append(command[-1])
        state["missing"] = []

    monkeypatch.setattr(cached, "check", check)
    monkeypatch.setattr(T.subprocess, "run", run)
    assert T.ensure_inputs("t0_2_0")["area"] == "work-3x3" and not fetched
    result = T.ensure_inputs("t0_3_0")
    assert fetched == ["tile-t0_3_0"] and result["area"] == "tile-t0_3_0"
    assert result["missing"] == 1 and "fetch_seconds" in result


def test_list_tile_appends_to_the_data_root_list_only(roots, monkeypatch):
    data, work = roots
    calls = []
    monkeypatch.setattr(T, "facade_fast", lambda step, args, log: calls.append((step, args)))
    record = T.list_tile("t0_2_0")
    assert record["tiles"] == [*CORE, "t0_2_0"]
    assert json.loads((data / C.PLAYABLE_TILES_FILE).read_text())["tiles"][-1] == "t0_2_0"
    assert not (work / C.PLAYABLE_TILES_FILE).exists()
    # Inventory registration is required even without staged maps. The texture
    # command is a separate environment, not part of this synthetic pipeline test.
    expected = (
        "inventory",
        [
            "--run",
            str(work / "textures/fast/t0_2_0"),
            "--inventory",
            str(work / "build/layers/buildings/facades.geojson"),
            "--assembly",
            str(data / "facade-approvals/assembly"),
            "--tiles",
            "t0_2_0",
        ],
    )
    assert calls == [expected]
    # Retrying an already listed tile still registers its inventory, without duplicates.
    assert T.list_tile("t0_2_0") == record
    assert calls == [expected, expected]


def test_add_tile_refuses_the_published_data_root(monkeypatch, roots):
    data, _ = roots
    monkeypatch.setenv("TURKU3D_WORK", str(data))
    with pytest.raises(RuntimeError, match="sandbox"):
        T.add_tile("t0_2_0")


def test_facade_fast_reads_result_before_cleanup_warning(monkeypatch):
    monkeypatch.setattr(
        T,
        "stream",
        lambda command, log: ['{"walls": 172, "errors": {}}', "Unclosed client session"],
    )
    assert T.facade_fast("refs", [], None) == {"walls": 172, "errors": {}}


def test_refresh_layers_built_for_another_list_are_stale(roots):
    _, work = roots
    layer = work / "build/layers/ground"
    layer.mkdir(parents=True)
    assert refresh.built_tiles(layer) == CORE  # layers from before the list
    assert refresh.tiles_current(layer)
    C.write_playable_tiles(work / C.PLAYABLE_TILES_FILE, [*CORE, "t0_2_0"])
    assert not refresh.tiles_current(layer)
    refresh.record_tiles(layer)
    assert refresh.built_tiles(layer) == [*CORE, "t0_2_0"]
    assert refresh.tiles_current(layer)


def massing(root, tile_id):
    tile = C.VisualTile.parse(tile_id)
    x, _, z = tile.origin
    mesh = GlbMesh(
        "outer-" + tile_id,
        np.array([[x + 10, 2, z + 10], [x + 20, 2, z + 10], [x + 10, 12, z + 10]], dtype=float),
        np.array([[0, 1, 2]], np.uint32),
        uvs=np.zeros((3, 2), np.float32),
        extras=dict(
            layer="buildings_outer",
            object_id="outer-" + tile_id,
            building_id="outer-" + tile_id,
            material_key="facade_plaster",
        ),
    )
    write_visual(root / f"{tile_id}.vis.glb", [mesh])
    return f"{tile_id}.vis.glb"


def test_expansion_tile_on_a_2x3_grid_keeps_seams_and_fills_its_parent(tmp_path, roots):
    """Five listed tiles on a 3x2 grid; the 500 m parent of the new tile gets coarse siblings."""
    _, work = roots
    listed = [*CORE, "t0_2_0"]
    C.write_playable_tiles(work / C.PLAYABLE_TILES_FILE, listed)
    layers, out = tmp_path / "layers", tmp_path / "dist"
    fixture(layers, ids=listed)
    outer = layers / "buildings_outer"
    files = [massing(outer, t) for t in ("t0_3_0", "t0_2_1")]
    write_manifest(outer, inputs={}, params={}, code="test", tiles=files)
    assemble(listed, layers=layers, out=out, area="playable-tiles")
    assert verify_tree(out) == verify_seams(out) == []
    manifest = json.loads((out / "manifest.json").read_text())
    # Seams between the core and the new tile are shared collision edges.
    assert {"c_7_0", "c_8_0", "c_8_3", "c_7_3"} <= manifest["collision"].keys()
    # Coarse leaves are visual only; t0_3_1 has neither massing nor terrain.
    assert manifest["visual"]["t0_3_0"]["coarse_dtm"] is False
    assert "t0_3_1" not in manifest["visual"]
    assert (
        not {f"c_{i}_{j}" for i in range(13, 16) for j in range(4)} & manifest["collision"].keys()
    )
    tileset = json.loads((out / "tileset.json").read_text())

    def node(n, tile_id):
        if n.get("extras", {}).get("id") == tile_id:
            return n
        return next((m for c in n.get("children", []) if (m := node(c, tile_id))), None)

    children = {c["extras"]["id"] for c in node(tileset["root"], "t1_1_0")["children"]}
    assert children == {"t0_2_0", "t0_3_0", "t0_2_1"}


def test_adding_a_tile_keeps_the_neighbour_visual_cached(tmp_path):
    layers, out = tmp_path / "layers", tmp_path / "dist"
    fixture(layers, ids=("t0_0_0", "t0_1_0"))
    assemble(["t0_0_0"], layers=layers, out=out, area="work-3x3")
    before = json.loads((out / "manifest.json").read_text())
    result = assemble(["t0_0_0", "t0_1_0"], layers=layers, out=out, area="work-3x3")
    after = json.loads((out / "manifest.json").read_text())
    assert "t0_1_0" in result["changed"] and "t0_0_0" not in result["changed"]
    assert after["visual"]["t0_0_0"] == before["visual"]["t0_0_0"]
    # Collision tiles whose layer files did not change keep their published bytes.
    assert after["collision"]["c_0_0"]["sha256"] == before["collision"]["c_0_0"]["sha256"]
    assert assemble(["t0_0_0", "t0_1_0"], layers=layers, out=out, area="work-3x3")["changed"] == []
