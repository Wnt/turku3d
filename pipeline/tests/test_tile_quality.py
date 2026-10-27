"""Tile quality ledger (turku3d.region.quality) on a synthetic dist tree, layers and facade assembly."""

import json
import struct

import pytest
from turku3d.region import quality as Q


def glb(nodes):
    """A minimal GLB v2 whose JSON chunk carries `nodes` (an empty BIN chunk follows)."""
    text = json.dumps({"asset": {"version": "2.0"}, "buffers": [], "nodes": nodes}).encode()
    text += b" " * (-len(text) % 4)
    body = struct.pack("<II", len(text), 0x4E4F534A) + text + struct.pack("<II", 0, 0x004E4942)
    return struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body


def wall(building, index, width, street=True):
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 0]]},
        "properties": {
            "building_id": building,
            "facade_index": index,
            "width_m": width,
            "base_height": 0.0,
            "top_height": 10.0,
            "street_facing": street,
        },
    }


GENERATED = {"source_mode": "generate", "generated_qa": {"ok": True}}
DERIVED = {"source_mode": "derived", "derived_from": "a_0"}
PHOTO = {"method": "rectified-photo-bay-inpaint-v1"}


@pytest.fixture
def root(tmp_path):
    """Tiles t0_0_0 (buildings a, b) and t0_1_0 (building c, untextured ground, no maps)."""
    tiles, layers, assembly = tmp_path / "tiles", tmp_path / "layers", tmp_path / "assembly"
    (layers / "buildings").mkdir(parents=True)
    (layers / "vegetation").mkdir()
    for tile, ids in (("t0_0_0", ["a", "b"]), ("t0_1_0", ["c"])):
        nodes = [{"mesh": 0, "extras": {"building_id": b}} for b in ids]
        (layers / f"buildings/{tile}.vis.glb").write_bytes(glb(nodes))
    walls = [
        wall("a", 0, 10),
        wall("a", 1, 10),
        wall("b", 0, 20),
        wall("b", 1, 5, street=False),
        wall("c", 0, 8),
    ]
    (layers / "buildings/facades.geojson").write_text(json.dumps({"features": walls}))
    (layers / "vegetation/MANIFEST.json").write_text(
        json.dumps({"params": {"budgets": {"t0_0_0": {"trees": 7}}}})
    )
    bound = {"a_0": GENERATED, "a_1": DERIVED}
    for facade_id, meta in bound.items():
        building, index = facade_id.split("_")
        (assembly / "facades" / building).mkdir(parents=True, exist_ok=True)
        sidecar = {
            **meta,
            "building_id": building,
            "facade_index": int(index),
            "width_m": 10,
            "height_m": 10.0,
        }
        (assembly / "facades" / building / f"{index}.json").write_text(json.dumps(sidecar))
    (assembly / "MANIFEST.json").write_text(
        json.dumps({"bound": sorted(bound), "inventory_sha256": "x"})
    )
    tiles.mkdir()
    manifest = {
        "built": "2026-10-27T10:00:00Z",
        "visual": {
            "t0_0_0": {
                "url": "visual/0/t0_0_0.glb",
                "sha256": "v1",
                "bytes": 10,
                "level": 0,
                "wire_bytes": 7_000_000,
                "ground_texture_bytes": 4,
            },
            "t0_1_0": {"url": "u", "sha256": "v2", "bytes": 10, "level": 0},
            "t0_-1_0": {"url": "u", "sha256": "v3", "bytes": 10, "level": 0, "coarse_dtm": True},
            "t1_0_0": {"url": "u", "sha256": "v4", "bytes": 10, "level": 1},
        },
        "collision": {
            "c_0_0": {"sha256": "c1"},
            "c_1_0": {"sha256": "c2"},
            "c_4_0": {"sha256": "c3"},
        },
        "vehicles": [{"x": 10.0, "z": 10.0}, {"x": 20.0, "z": 30.0}, {"x": 300.0, "z": 10.0}],
    }
    (tiles / "manifest.json").write_text(json.dumps(manifest))
    return tiles, layers, assembly


def test_measure_counts_buildings_facade_sources_ground_trees_and_cars(root):
    measured = Q.measure(*root)
    assert sorted(measured) == ["t0_0_0", "t0_1_0"]  # coarse leaves and parents are not playable
    a = measured["t0_0_0"]
    assert a["buildings"] == 2
    assert a["facades"] == {
        "generate": 1,
        "derived": 1,
        "derived-neighbour": 0,
        "fast": 0,
        "other": 0,
        "none": 2,
    }
    assert a["street_facades"]["none"] == 1  # b_1 is not street-facing
    assert a["facade_coverage"] == pytest.approx(0.5)  # 200 of 400 m2 street-facing wall
    assert (a["ground"], a["trees"], a["parked_cars"], a["wire_bytes"]) == (
        "ortho-splats",
        7,
        2,
        7_000_000,
    )
    c = measured["t0_1_0"]
    assert (c["buildings"], c["facade_coverage"], c["ground"], c["parked_cars"]) == (
        1,
        0.0,
        "untextured",
        1,
    )
    assert a["collision_sha256"] != c["collision_sha256"]


def test_fast_maps_cover_roughly_but_not_as_generated_style(root):
    tiles, layers, assembly = root
    sidecar = assembly / "facades/a/0.json"
    fast = {"source_mode": "fast", "fast_description": {"version": 1}, "references": []}
    meta = {k: v for k, v in json.loads(sidecar.read_text()).items() if k != "generated_qa"}
    sidecar.write_text(json.dumps({**meta, **fast}))
    record = Q.measure(*root)["t0_0_0"]
    assert (record["facades"]["fast"], record["facades"]["generate"]) == (1, 0)
    assert (record["facade_coverage"], record["rough_coverage"]) == (0.25, 0.5)
    sidecar.write_text(json.dumps({**meta, **fast, "references": ["photo.jpg"]}))
    assert Q.measure(*root)["t0_0_0"]["facades"]["other"] == 1


def test_photo_maps_count_as_other_and_unbound_dimensions_as_none(root):
    tiles, layers, assembly = root
    sidecar = assembly / "facades/a/0.json"
    sidecar.write_text(json.dumps({**json.loads(sidecar.read_text()), **PHOTO}))
    other = assembly / "facades/a/1.json"
    other.write_text(json.dumps({**json.loads(other.read_text()), "width_m": 3}))
    facades = Q.measure(*root)["t0_0_0"]["facades"]
    assert (facades["other"], facades["derived"], facades["none"]) == (1, 0, 3)


def test_record_keeps_build_times_of_unchanged_tiles_and_validator_verdicts(root):
    tiles, layers, assembly = root
    Q.record(tiles, layers, assembly, changed=["t0_0_0", "t0_1_0"], seconds=12.5)
    ledger = json.loads((tiles / Q.DIST_LEDGER).read_text())
    assert ledger["format"] == Q.FORMAT
    assert ledger["tiles"]["t0_0_0"]["build_seconds"] == 12.5
    assert ledger["tiles"]["t0_0_0"]["validator"] == {"status": "unvalidated"}
    report = {
        "pass": False,
        "started": "2026-10-27T11:00:00Z",
        "tiles": [
            {"id": "c_0_0", "errors": []},
            {"id": "c_4_0", "errors": ["bad"]},
        ],
        "checks": {
            "buildings": {"status": "pass", "perTile": [{"tile": "c_0_0", "status": "pass"}]}
        },
    }
    (tiles.parent / "report.json").write_text(json.dumps(report))
    Q.validated(tiles, tiles.parent / "report.json")
    ledger = Q.read(tiles / Q.DIST_LEDGER)
    assert ledger["t0_0_0"]["validator"]["status"] == "pass"
    assert ledger["t0_1_0"]["validator"]["status"] == "fail"
    assert ledger["t0_0_0"]["full_quality_since"] == "2026-10-27T11:00:00Z"
    assert ledger["t0_1_0"]["full_quality_since"] is None
    # A rebuild of t0_1_0 alone: t0_0_0 keeps its time and verdict; changed collision drops t0_1_0's.
    manifest = json.loads((tiles / "manifest.json").read_text())
    manifest["visual"]["t0_1_0"]["sha256"] = "v2b"
    manifest["collision"]["c_4_0"]["sha256"] = "c3b"
    manifest["built"] = "2026-10-27T12:00:00Z"
    (tiles / "manifest.json").write_text(json.dumps(manifest))
    Q.record(tiles, layers, assembly, changed=["t0_1_0"], seconds=3.0)
    ledger = Q.read(tiles / Q.DIST_LEDGER)
    assert (ledger["t0_0_0"]["built"], ledger["t0_0_0"]["build_seconds"]) == (
        "2026-10-27T10:00:00Z",
        12.5,
    )
    assert ledger["t0_0_0"]["validator"]["status"] == "pass"
    assert (ledger["t0_1_0"]["built"], ledger["t0_1_0"]["build_seconds"]) == (
        "2026-10-27T12:00:00Z",
        3.0,
    )
    assert ledger["t0_1_0"]["validator"] == {"status": "unvalidated"}


def test_a_failing_report_without_tile_attribution_fails_every_checked_tile():
    report = {"pass": False, "tiles": [{"id": "c_0_0", "errors": []}], "checks": {}}
    assert Q.verdicts(report)["t0_0_0"]["status"] == "fail"


def test_publish_keeps_history_and_unlisted_tiles(root, tmp_path):
    tiles, layers, assembly = root
    release = tmp_path / "release"
    release.mkdir()
    (release / "tiles").symlink_to(tiles)
    Q.record(tiles, layers, assembly, changed=["t0_0_0", "t0_1_0"])
    published = tmp_path / "data"
    first = Q.publish(published, release, "abc")["tiles"]
    assert first["t0_0_0"]["validator"]["release"] == "abc"
    since = first["t0_0_0"]["full_quality_since"]
    assert since and first["t0_1_0"]["full_quality_since"] is None  # untextured ground
    # The next release no longer lists t0_1_0: the data root keeps its record, unlisted.
    ledger = json.loads((tiles / Q.DIST_LEDGER).read_text())
    del ledger["tiles"]["t0_1_0"]
    (tiles / Q.DIST_LEDGER).write_text(json.dumps(ledger))
    # The release record names each tile's builder and the tiles built with older tile code.
    record = {"tile_commits": {"t0_0_0": "old"}, "stale_by_code": ["t0_0_0"]}
    (release / "live-build.json").write_text(json.dumps(record))
    second = Q.publish(published, release, "def")["tiles"]
    assert second["t0_1_0"]["listed"] is False
    assert second["t0_0_0"]["full_quality_since"] == since
    assert (second["t0_0_0"]["tile_commit"], second["t0_0_0"]["stale_by_code"]) == ("old", True)
    text = Q.table(second)
    assert "t0_1_0 (unlisted)" in text and "ortho-splats" in text
    assert "t0_0_0 (stale code)" in text


def test_the_assembler_cli_writes_the_ledger_into_its_dist(tmp_path, monkeypatch, capsys):
    from test_assemble import fixture
    from turku3d.package import assemble

    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "work"))
    fixture(tmp_path / "layers")
    argv = ["assemble", "--layers", str(tmp_path / "layers"), "--area", "playable-tiles"]
    monkeypatch.setattr("sys.argv", [*argv, "--tile", "t0_0_0"])
    assemble.main()
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    ledger = Q.read(tmp_path / "work/dist/tiles" / Q.DIST_LEDGER)
    assert "t0_0_0" in result["changed"] and list(ledger) == ["t0_0_0"]
    assert ledger["t0_0_0"]["build_seconds"] > 0
    assert (ledger["t0_0_0"]["buildings"], ledger["t0_0_0"]["ground"]) == (0, "untextured")
