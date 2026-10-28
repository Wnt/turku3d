"""Pure scheduling/cache contracts; no tile generation, server or timing thresholds."""

import json
import time
from pathlib import Path

from turku3d.coarse import inputs
from turku3d.coarse import service as S
from turku3d.coarse.service import FILE, WORKERS, Builder, rank


def test_speed_and_direction_order_actual_crossings_before_ring():
    east = (490, 125, 27, 0)
    assert rank("t0_2_0", 10, east) < rank("t0_1_1", 0, east)
    assert rank("t0_2_0", 10, east) < rank("t0_3_0", 260, east)
    assert rank("t0_2_0", 10, east)[1] == 10 / 27
    assert rank("t0_2_0", 10, (490, 125, 10, 0))[1] == 1
    west = (490, 125, -27, 0)
    assert rank("t0_0_0", 240, west) < rank("t0_2_0", 10, west)
    assert rank("t0_2_1", 0, (490, 490, 27, 27)) < rank("t0_2_0", 0, (490, 490, 27, 27))
    assert WORKERS == 1


def test_first_pass_remains_servable_through_upgrade(tmp_path):
    builder = Builder(tmp_path, code="test", workers=0)
    tile = "t0_2_0"
    target = builder.manifest(tile)
    target.parent.mkdir()
    target.write_text(json.dumps({"code": "test", "pass_kind": "first"}))
    assert builder.request(tile, 0) == {"state": "built"}
    assert builder._next_upgrade() == (tile, 0)
    builder.building[tile] = time.time()
    assert builder.request(tile, 0) == {"state": "built"}
    assert tile in builder.servable and tile in builder.upgrading
    builder.building.clear()
    builder.stop()


def test_upgrade_yields_to_new_first_pass(tmp_path):
    builder = Builder(tmp_path, code="test", workers=0)
    builder.upgrade_queue.add("t0_1_0")
    builder.requested["t0_1_0"] = time.time()
    builder.priority["t0_2_0"] = 10
    builder.requested["t0_2_0"] = time.time()
    from turku3d.coarse.upgrade_queue import next_job

    assert next_job(builder) == (("t0_2_0", 10), False)
    assert builder._next_upgrade() == ("t0_1_0", 0)
    assert not builder.upgrade_queue
    builder.stop()


def test_surface_code_does_not_invalidate_roof_or_ground_cache(monkeypatch):
    original = Path.read_bytes
    first = {d: inputs._key([], d) for d in ("wfs", "ground", "surfaces")}

    def changed(path):
        return original(path) + (
            b"\n# changed surface producer\n" if path.name == "surfaces.py" else b""
        )

    monkeypatch.setattr(Path, "read_bytes", changed)
    assert inputs._key([], "wfs") == first["wfs"]
    assert inputs._key([], "ground") == first["ground"]
    assert inputs._key([], "surfaces") != first["surfaces"]


def test_paving_rule_change_invalidates_streets_without_repreparing_ground(monkeypatch):
    original = Path.read_bytes
    first = {d: inputs._key([], d) for d in ("wfs", "ground", "roads")}

    def changed(path):
        return original(path) + (
            b"\n# changed paving rule\n" if path.name == "materials.py" else b""
        )

    monkeypatch.setattr(Path, "read_bytes", changed)
    assert inputs._key([], "wfs") != first["wfs"]
    assert inputs._key([], "wfs-first") == inputs._key([], "wfs")
    assert inputs._key([], "ground") == first["ground"]
    assert inputs._key([], "roads") == first["roads"]


def test_immutable_payload_urls_keep_shared_textures_relative():
    digest = "a" * 64
    assert FILE.fullmatch(f"vis.{digest}.glb")
    assert FILE.fullmatch(f"roads.{digest}.json")
    assert FILE.fullmatch(f"col/c_8_0.{digest}.col")
    assert not FILE.fullmatch(f"../vis.{digest}.glb")


def test_queue_ranks_ring_then_cone_and_drops_what_the_player_left(tmp_path):
    builder = Builder(tmp_path / "coarse", run=lambda *_: None, code="test", workers=0)
    requests = (
        ("t0_1_0", 0),
        ("t0_0_1", 0),
        ("t0_2_0", 125),
        ("t0_-1_0", 125),  # behind once driving east
        ("t0_4_0", 600),  # ahead, inside the cone
        ("t0_9_0", 2000),  # ahead, beyond 30 s of travel
        ("t0_1_3", 600),  # beside the path
    )
    for tile, d in requests:
        builder.request(tile, d)
    # No position report yet: every tile stays, nearest first by the reported distance.
    assert builder.order()[:2] in (["t0_0_1", "t0_1_0"], ["t0_1_0", "t0_0_1"])
    assert len(builder.order()) == 7
    builder.building["t0_7_7"] = 0.0  # a running build is never interrupted
    builder.steer(260, 125, 27, 0)  # driving east through t0_1_0
    assert builder.order() == [
        "t0_1_0",
        "t0_2_0",
        "t0_4_0",
        "t0_0_1",
    ]  # swept path before lateral ring
    assert "t0_7_7" in builder.building
    assert builder.request("t0_9_0", 2000) == {"state": "deferred"}
    assert "t0_9_0" not in builder.order()
    builder.steer(260, 125, 0, -27)  # turned north: the cone east is dropped, the ring stays
    assert builder.order() == ["t0_1_0", "t0_0_1", "t0_2_0"]
    builder.request("t0_1_-3", 700)
    assert builder.order()[:2] == ["t0_1_0", "t0_1_-3"]
    builder.steer(260, 125, 0.5, 0)  # standing: ring only (and the ring's parent quadrants)
    builder.request("t0_3_1", 400)  # quadrant of the ring's parent t1_1_0
    assert builder.order() == ["t0_1_0", "t0_0_1", "t0_2_0", "t0_3_1"]
    del builder.building["t0_7_7"]
    builder.workers = 2
    builder.recent = [4.0, 6.0]
    eta = builder.eta()
    assert [eta[t] for t in builder.order()] == [5.0, 5.0, 10.0, 10.0]
    assert builder.request("t0_2_0", 125) == {"state": "queued", "position": 2, "eta": 10.0}


def test_queue_holds_the_ring_and_the_nearest_cone_tiles_up_to_its_cap(tmp_path):
    builder = Builder(tmp_path / "coarse", run=lambda *_: None, code="test", workers=0)
    builder.steer(260, 125, 27, 0)
    for i in range(7):
        for j in (-1, 0, 1):
            builder.request(f"t0_{i}_{j}", 0)
    order = builder.order()
    ring = {f"t0_{i}_{j}" for i in (0, 1, 2) for j in (-1, 0, 1)}
    assert len(order) == S.QUEUE_MAX and ring <= set(order)
    assert order[:5] == [
        f"t0_{i}_0" for i in range(1, 6)
    ]  # imminent crossings before lateral tiles


def test_roof_crop_preserves_supported_planes_and_evidence(tmp_path, monkeypatch):
    import copy

    import numpy as np
    import shapely
    from turku3d.coarse import roofs
    from turku3d.coarse.inputs import Building

    x, z = np.meshgrid(np.arange(-10, 20, 0.25), np.arange(-10, 20, 0.25))
    cloud = np.c_[x.ravel(), np.full(x.size, 5.2), z.ravel()]
    original = [Building("roof", shapely.box(0, 0, 10, 10), 0, 5, "facade_plaster")]
    original[0].roof_parts = [(original[0].footprint, np.array([0.0, 0.0, 5.0]))]
    cropped = copy.deepcopy(original)
    source = tmp_path / "survey.laz"
    source.touch()
    monkeypatch.setattr(roofs, "_class6", lambda *_: cloud)
    roofs.refine(original, [], points=cloud)
    roofs.refine(cropped, [source])
    assert original[0].height_evidence == cropped[0].height_evidence
    assert original[0].height_evidence[0]["source"] == "class6_plane_offset"
    assert np.array_equal(original[0].roof_parts[0][1], cropped[0].roof_parts[0][1])


def test_lateral_work_does_not_starve_full_quality_upgrades(tmp_path):
    builder = Builder(tmp_path, code="test", workers=0)
    builder.steer(260, 125, 27, 0)
    builder.upgrade_queue.add("t0_1_0")
    builder.requested["t0_1_0"] = time.time()
    builder.priority["t0_1_1"] = 125  # beside the moving car, away from its swept path
    assert builder._next_upgrade() == ("t0_1_0", 0)
    builder.building["t0_1_0"] = time.time()
    builder.recent = [5]
    builder.full_recent = [60]
    builder.workers = 1
    assert builder.eta()["t0_1_1"] == 5  # full upgrade owns its own lane
    builder.building.clear()
    builder.stop()


def test_first_pass_keeps_structural_collision_inputs_without_preparing_decoration(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    import numpy as np
    import shapely
    from turku3d import config as C

    source = tmp_path / "source"
    source.touch()
    datasets = (
        "ground",
        "ortho",
        "wfs",
        "roads",
        "trees",
        "barriers",
        "forests",
        "tunnels",
        "poles",
        "surfaces",
        "markings",
        "kerbs",
        "appearance",
        "register",
    )
    paths = {d: [source] for d in datasets}
    paths["wfs"] = [source, None, None, source]  # optional roof survey must wait
    spot = tmp_path / "spot.parquet"
    spot.touch()
    lidar = tmp_path / "bridge.laz"
    lidar.touch()
    paths["roads"] = [source, lidar, spot]  # bridge elevations are structural in both passes
    monkeypatch.setattr(inputs, "_sources", lambda *_: paths)
    building = inputs.Building("solid", shapely.box(0, 0, 10, 10), 0, 10, "facade_plaster")
    called = []

    def prepare(tile, dataset, sources):
        called.append(dataset)
        if dataset == "ground":
            return {"points": np.zeros((4, 3)), "heights": np.zeros((129, 129))}
        if dataset == "wfs":
            assert len(sources) == 3
            return {"buildings": [building], "streets": [], "water": []}
        if dataset == "roads":
            assert sources == [source, lidar, spot]
            return {"bridge_ways": [{"id": "crossing"}]}
        if dataset == "tunnels":
            return [{"id": "passage"}]
        raise AssertionError(f"first pass prepared decorative input {dataset}")

    monkeypatch.setattr(inputs, "_prepare_one", prepare)
    sources = SimpleNamespace(missing=lambda _: [], input_key=lambda _: "raw-key")
    result = inputs.gather(C.VisualTile(0, 0, 0), tmp_path, sources, first_pass=True)
    assert called == ["ground", "wfs", "roads", "tunnels"]
    assert result.kerbs is None
    assert result.buildings[0].key == "solid"
    assert result.roads["bridge_ways"][0]["id"] == "crossing"
    assert result.tunnels[0]["id"] == "passage"
    # Full preparation must reuse the identical structural bridge cache.
    roads_path, roads_key = inputs._entry(tmp_path, C.VisualTile(0, 0, 0), "roads", paths["roads"])
    assert inputs._load(roads_path, roads_key)["bridge_ways"][0]["id"] == "crossing"


def test_fixed_producer_retries_a_failed_tile_without_old_backoff(tmp_path):
    builder = Builder(tmp_path, run=lambda *_: None, code="old", workers=0)
    tile = "t0_1_2"
    builder.failed[tile] = ("old producer failed", time.time())
    builder.failure_codes[tile] = "old"
    assert builder.request(tile, 0)["state"] == "failed"
    builder.code = lambda: "fixed"
    assert builder.request(tile, 0)["state"] == "queued"
    builder.stop()


def test_missing_first_pass_inputs_do_not_start_full_preparation(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import pytest
    from turku3d import config as C

    def prepare(*_):
        raise AssertionError("pending first pass started full survey preparation")

    monkeypatch.setattr(inputs, "prepare", prepare)
    sources = SimpleNamespace(missing=lambda _: [SimpleNamespace(key="wfs/source")])
    with pytest.raises(inputs.MissingInputs) as error:
        inputs.gather(C.VisualTile(0, 0, 0), tmp_path, sources, first_pass=True)
    assert error.value.units == ["wfs/source"]


def test_idle_foreground_workers_upgrade_old_served_tiles_in_request_order(tmp_path):
    from turku3d.coarse.upgrade_queue import next_job

    builder = Builder(tmp_path, code="test", workers=0)
    builder.upgrade_queue.update(("t0_0_0", "t0_5_5", "t0_3_-2"))
    builder.requested = {tile: time.time() - 300 for tile in builder.upgrade_queue}
    builder.upgrade_requested["t0_5_5"] = time.time()
    job = next_job(builder)
    assert job == (("t0_5_5", 0), True)
    builder.building["t0_5_5"] = time.time()
    assert next_job(builder) == (("t0_0_0", 0), True)
    assert next_job(builder) == (("t0_3_-2", 0), True)
    builder.building.clear()
    builder.stop()


def test_revalidating_textured_tile_skips_structural_downgrade(tmp_path):
    from types import SimpleNamespace

    from turku3d.coarse.upgrade_queue import next_job

    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"pass_kind": "full"}))
    builder = SimpleNamespace(
        two_pass=True,
        upgrading=set(),
        cached=lambda _: True,
        manifest=lambda _: path,
        _next=lambda: ("t0_0_0", 0),
    )
    assert next_job(builder) == (("t0_0_0", 0), True)
    assert builder.upgrading == {"t0_0_0"}
