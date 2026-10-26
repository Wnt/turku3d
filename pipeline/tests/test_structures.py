import json

import numpy as np
import pytest
from shapely.geometry import LineString
from turku3d.col.materials import mat_id, material_class
from turku3d.layers import read_collision, read_manifest
from turku3d.package.gltf import accessor_array, read_glb
from turku3d.structures.geometry import profile_wall, quay, ramp, stairs
from turku3d.structures.layer import write_layer
from turku3d.structures.survey import Lidar, group_flights


def test_stair_endpoints_and_helpers():
    meshes, meta = stairs("flight", [60, 2, 60], [66, 3.6, 66], 2)
    assert meta["risers"] == 10
    assert not meta["flagged_steep"]
    slope, treads, *cheeks = meshes
    assert not slope.visual and treads.visual
    assert material_class(mat_id(treads.material)) == "query"
    for vertex in slope.positions:
        assert np.linalg.norm(treads.positions - vertex, axis=1).min() < 0.02
    normals = np.cross(
        slope.positions[slope.indices[:, 1]] - slope.positions[slope.indices[:, 0]],
        slope.positions[slope.indices[:, 2]] - slope.positions[slope.indices[:, 0]],
    )
    assert (normals[:, 1] > 0).all()
    assert len(cheeks) == 2
    assert stairs("steep", [0, 0, 0], [1, 2, 0], 2)[1]["flagged_steep"]


def test_stair_profile_matches_surveyed_crossfall_at_both_landings():
    profile = [[0.1, 0.2, 0.3], [0.9, 1.0, 1.1]]
    meshes, meta = stairs("crossfall", [0, 0.2, 0], [4, 1, 0], 2, landing_profile=profile)
    assert meta["landing_profile_y"] == profile
    slope = meshes[0]
    for x, heights in ((0, profile[0]), (4, profile[1])):
        for z, height in zip((-1, 0, 1), heights, strict=True):
            assert np.any(np.all(np.isclose(slope.positions, [x, height, z]), axis=1))


@pytest.mark.parametrize(
    "bottom,top,width",
    [
        ([0, 0, 0], [0, 1, 0], 2),
        ([0, 1, 0], [4, 0, 0], 2),
        ([0, 0, 0], [4, 1, 0], 0),
        ([0, 0, 0], [4, float("nan"), 0], 2),
    ],
)
def test_invalid_flights(bottom, top, width):
    with pytest.raises(ValueError):
        stairs("bad", bottom, top, width)


def test_grouping_does_not_merge_neighbouring_or_disconnected_flights():
    lines = [(f"a{i}", LineString([(0, i * 0.4), (2, i * 0.4)]), "66626") for i in range(5)]
    lines += [(f"b{i}", LineString([(3, i * 0.4), (5, i * 0.4)]), "66626") for i in range(5)]
    lines += [
        ("unrelated", LineString([(8, 0), (10, 0)]), "66618"),
        ("isolated", LineString([(0, 10), (2, 10)]), "66626"),
    ]
    flights, rejected = group_flights(lines)
    assert len(flights) == 2
    assert [f.width for f in flights] == [2, 2]
    assert len(rejected) == 1
    assert rejected[0]["object_id"] == "isolated"
    reverse, _ = group_flights(list(reversed(lines)))
    assert [f.object_id for f in flights] == [f.object_id for f in reverse]


def test_lidar_heldout_landing_and_fence_heights():
    x, z = np.meshgrid(np.linspace(-0.4, 0.4, 15), np.linspace(-0.4, 0.4, 15))
    p = np.column_stack((x.ravel(), 3 + 0.1 * x.ravel(), z.ravel()))
    classes = np.full(len(p), 2)
    p = np.vstack((p, [[0, 4.1, 0], [0.05, 4.2, 0], [-0.05, 4.2, 0]]))
    lidar = Lidar(p, np.r_[classes, [1, 1, 1]])
    h, error = lidar.landing([0, 0])
    assert h == pytest.approx(3)
    assert error < 1e-8
    assert lidar.fence_height([0, 0], 3) == pytest.approx((1.2, "class1_p95"))
    assert lidar.fence_height([10, 10], 3) == (1.2, "default_missing_class1")
    with pytest.raises(ValueError, match="insufficient"):
        lidar.landing([10, 10])


def test_wall_top_checked_against_independent_lidar():
    p = [[x, 2, z] for x in [-0.1, 0, 0.1] for z in [-0.1, 0, 0.1]]
    assert Lidar(p, [1] * len(p)).wall_top([0, 0]) == (2, 0)
    with pytest.raises(ValueError):
        profile_wall("bad", [[0, 0], [0, 0]], [0, 0], [1, 1])


def test_ramp_preserves_positions_and_quay_is_closed():
    p = np.array([[0, 0, -2], [0, 0, 2], [4, 1, 2], [4, 1, -2]], float)
    r = ramp("r", p, [[0, 1, 2], [0, 2, 3]])
    np.testing.assert_array_equal(r.positions, p)
    for mesh in quay("q", [[0, 0], [3, 1], [6, 0]], [-2, -2, -2], [0, 0, 0]):
        p, inverse = np.unique(mesh.positions, axis=0, return_inverse=True)
        tri = inverse[mesh.indices]
        # The entire bent shell is closed, without duplicate interior caps.
        edges = np.sort(np.concatenate([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]]), axis=1)
        _, counts = np.unique(edges, axis=0, return_counts=True)
        assert np.all(counts == 2)
        normals = np.cross(
            mesh.positions[mesh.indices[:, 1]] - mesh.positions[mesh.indices[:, 0]],
            mesh.positions[mesh.indices[:, 2]] - mesh.positions[mesh.indices[:, 0]],
        )
        assert (np.linalg.norm(normals, axis=1) > 0).all()


def test_whole_object_crosses_grid_without_clipping_or_duplicate_faces(tmp_path):
    mesh = stairs("corner", [60.1234, 2.718, 60.234], [65.567, 4.8, 65.897], 3)[0][0]
    out = tmp_path / "structures"
    write_layer([mesh], out, inputs={}, params={}, metrics={})
    files = list(out.glob("*.col.npz"))
    assert len(files) == 1
    exported = read_collision(files[0])
    np.testing.assert_array_equal(
        exported.positions[exported.indices], mesh.positions[mesh.indices]
    )
    assert read_manifest(out)["params"]["seams"] == "objects"


def test_layer_contract_glb_cache_and_stale_output(tmp_path):
    meshes, metrics = stairs("cross-tile", [247, 2, 247], [253, 4, 253], 3)
    out = tmp_path / "structures"
    manifest, cached = write_layer(
        meshes, out, inputs={"fixture": "abc"}, params={}, metrics=metrics
    )
    assert not cached
    assert len(list(out.glob("*.vis.glb"))) >= 1
    assert read_manifest(out)["format"] == "turku3d-layer"
    assert not list(out.glob("*.col"))
    materials = set()
    for path in out.glob("*.col.npz"):
        with np.load(path, allow_pickle=False) as d:
            assert d["positions"].dtype == np.float64
            assert d["indices"].dtype == np.uint32
            assert d["material"].dtype == np.uint8
            assert len(d["material"]) == len(d["indices"])
            materials.update(d["material"])
    assert materials == {16, 17, 19}
    for path in out.glob("*.vis.glb"):
        js, binary = read_glb(path.read_bytes())
        for node in js["nodes"][1:]:
            assert node["extras"]["layer"] == "structures"
            assert node["extras"]["material_key"]
            prim = js["meshes"][node["mesh"]]["primitives"][0]
            uv = accessor_array(js, binary, prim["attributes"]["TEXCOORD_0"])
            assert np.isfinite(uv).all()
    before = (out / "MANIFEST.json").stat().st_mtime_ns
    assert write_layer(meshes, out, inputs={"fixture": "abc"}, params={}, metrics=metrics)[1]
    assert before == (out / "MANIFEST.json").stat().st_mtime_ns
    damaged = out / next(iter(manifest["tiles"]))
    damaged.write_bytes(b"bad")
    assert not write_layer(meshes, out, inputs={"fixture": "abc"}, params={}, metrics=metrics)[1]
    smaller, metrics = stairs("small", [1, 0, 1], [5, 1, 1], 1)
    write_layer(smaller, out, inputs={"fixture": "def"}, params={}, metrics=metrics)
    assert len(list(out.glob("*.vis.glb"))) == 1
    assert json.loads((out / "MANIFEST.json").read_text())["layer"] == "structures"


def ground_fixture(tmp_path, *, quays=None, material="sidewalk"):
    from turku3d.structures.inputs import Ground

    root = tmp_path / "ground"
    root.mkdir()
    p = np.array([[-2, -0.4, -3], [-2, -0.4, 3], [8, 1.6, 3], [8, 1.6, -3]], np.float64)
    np.savez(
        root / "c_0_0.col.npz",
        positions=p,
        indices=np.array([[0, 1, 2], [0, 2, 3]], np.uint32),
        material=np.full(2, mat_id(material), np.uint8),
    )
    (root / "MANIFEST.json").write_text(json.dumps({"layer": "ground"}))
    (root / "quays.json").write_text(json.dumps(quays or []))
    return Ground(root)


def test_ground_barycentric_contact_no_extrapolation(tmp_path):
    ground = ground_fixture(tmp_path)
    assert ground.height([2.7, 0]) == pytest.approx(0.54)
    with pytest.raises(ValueError, match="no ground contact"):
        ground.height([20, 0])
    assert ground.quays == []
    from turku3d.structures.inputs import Ground

    (ground.root / "quays.json").unlink()
    assert Ground(ground.root).quays == []


def test_survey_to_layer_join_and_missing_ramps_are_reported(tmp_path):
    from turku3d.structures.build import build
    from turku3d.structures.inputs import Ground

    ground = ground_fixture(tmp_path)
    x, z = np.meshgrid(np.arange(-1, 6, 0.06), np.arange(-2, 2, 0.06))
    p = np.column_stack((x.ravel(), 0.2 * x.ravel(), z.ravel()))
    lidar = Lidar(p, np.full(len(p), 2))
    records = [
        {
            "object_id": f"nosing-{i}",
            "geometry": LineString([(i * 0.4, -1), (i * 0.4, 1)]),
            "code": "66626",
        }
        for i in range(11)
    ]
    meshes, metrics = build(records, lidar, ground)
    assert len(meshes) == 4
    assert metrics["status"] == "READY_FOR_VALIDATION"
    assert metrics["object_counts"] == {"stairs": 1}
    assert metrics["measurements"][0]["landing_error_bound_m"] < 1e-8
    records.append(
        {"object_id": "uncovered-ramp", "geometry": LineString([(0, 2), (4, 2)]), "code": "62540"}
    )
    _, metrics = build(records, lidar, ground)
    assert metrics["status"] == "BLOCKED"
    assert metrics["issues"][0]["object_id"] == "uncovered-ramp"
    (ground.root / "ramps.json").write_text(
        json.dumps(
            [
                {
                    "object_id": "uncovered-ramp",
                    "line_xz": [[0, 2], [4, 2]],
                    "length_m": 4,
                    "covered_length_m": 4,
                }
            ]
        )
    )
    _, metrics = build(records, lidar, Ground(ground.root))
    assert metrics["status"] == "READY_FOR_VALIDATION"


def test_missing_lidar_uses_surveyed_stair_with_ground_contact(tmp_path):
    from turku3d.structures.build import build

    ground = ground_fixture(tmp_path)
    records = [
        {
            "object_id": f"nosing-{i}",
            "geometry": LineString([(i * 0.4, -1), (i * 0.4, 1)]),
            "code": "66626",
        }
        for i in range(11)
    ]
    meshes, metrics = build(records, Lidar([[0, 0, 0]], [1]), ground)
    assert len(meshes) == 4
    assert metrics["status"] == "READY_FOR_VALIDATION"
    assert metrics["survey_only"][0]["reason"].startswith("insufficient landing")
    assert metrics["measurements"][0]["evidence"] == "survey-only"
    assert not metrics["issues"]


def test_inconclusive_osm_wall_top_uses_reported_survey_only_height(tmp_path):
    from turku3d.structures.build import build

    ground = ground_fixture(tmp_path)
    records = [
        {
            "object_id": "osm:wall",
            "geometry": LineString([(2, -1), (2, 1)]),
            "code": "wall",
            "tags": {"barrier": "wall"},
        }
    ]
    meshes, metrics = build(records, Lidar([[0, 0, 0]], [1]), ground)
    assert len(meshes) == 1
    assert metrics["status"] == "READY_FOR_VALIDATION"
    assert metrics["measurements"][0]["height_evidence"]["source"] == "survey-only"
    assert metrics["survey_only"][0]["object_id"] == "osm:wall"


def test_ground_ramp_material_alone_does_not_claim_survey_coverage(tmp_path):
    from turku3d.structures.build import build

    ground = ground_fixture(tmp_path, material="ramp")
    records = [
        {"object_id": "ramp-edge", "geometry": LineString([(0, 2), (4, 2)]), "code": "62540"}
    ]
    meshes, metrics = build(records, Lidar([[0, 0, 0]], [2]), ground)
    assert metrics["status"] == "BLOCKED"
    assert not meshes
    assert any("measured ground ramp terrain" in x["reason"] for x in metrics["issues"])


def test_retaining_wall_uses_measured_ground_sides(tmp_path):
    from turku3d.structures.build import build

    ground = ground_fixture(tmp_path)
    records = [
        {"object_id": "retaining", "geometry": LineString([(2, -1), (2, 1)]), "code": "66634"}
    ]
    meshes, metrics = build(records, Lidar([[0, 0, 0]], [2]), ground)
    assert len(meshes) == 1
    assert metrics["status"] == "READY_FOR_VALIDATION"
    wall = metrics["measurements"][0]
    assert wall["height_evidence"] == {"source": "ground_sides", "offset_m": 0.6}
    assert min(np.array(wall["upper_y"]) - wall["lower_y"]) == pytest.approx(0.24)


def test_cli_writes_blocking_report_without_false_layer_manifest(tmp_path, monkeypatch):
    from turku3d.structures import __main__ as cli

    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path))
    monkeypatch.setattr(cli, "read_surveys", lambda bounds: [])
    assert cli.main(["--ground-layer", str(tmp_path / "missing-ground")]) == 1
    metrics = json.loads((tmp_path / "reports/structures/metrics.json").read_text())
    assert metrics["status"] == "BLOCKED"
    assert "W1-01 ground layer is required" in metrics["issues"][0]["reason"]
    assert not (tmp_path / "build/layers/structures/MANIFEST.json").exists()


def test_city_wins_only_where_osm_is_covered_and_clips_to_aoi(monkeypatch):
    import geopandas as gpd
    from turku3d import config as C
    from turku3d.structures.inputs import read_surveys

    city_line = LineString([(-1, 0), (2, 0)])
    osm_line = LineString([(0, 0), (6, 0)])

    def to_en(line):
        return LineString([(C.ORIGIN_E + x, C.ORIGIN_N - z) for x, z in line.coords])

    city = gpd.GeoDataFrame(
        [
            {
                "feature_key": "survey-fence",
                "kplkoodi": "66630",
                "geometry_local": city_line.wkt,
                "geometry": to_en(city_line),
            }
        ],
        crs="EPSG:3877",
    )
    osm = gpd.GeoDataFrame(
        [
            {
                "osm_type": "way",
                "osm_id": 42,
                "tags": '{"barrier":"fence"}',
                "geometry_local": osm_line.wkt,
                "geometry": to_en(osm_line),
            }
        ],
        crs="EPSG:3877",
    )
    monkeypatch.setattr(gpd, "read_parquet", lambda path: city if "Rakenne" in path.name else osm)
    records = read_surveys(C.BBoxLocal(0, -2, 5, 2))
    assert len(records) == 2
    assert records[0]["source"] == "city"
    assert records[0]["geometry"].bounds == (0, 0, 2, 0)
    assert records[1]["source"] == "osm"
    assert records[1]["geometry"].bounds == (3, 0, 5, 0)


def test_actual_ground_quays_json_export(tmp_path):
    from turku3d.structures.inputs import Ground

    ground = ground_fixture(tmp_path)
    profile = {
        "object_id": "ground-quay-42",
        "kind": "quay",
        "centers_xz": [[1, 0], [2, 0]],
        "side_heights": [[0.2, -2], [-2, 0.4]],
        "normals_xz": [[0, 1], [0, 1]],
    }
    (ground.root / "quays.json").write_text(json.dumps([profile]))
    loaded = Ground(ground.root)
    assert loaded.quays[0][0] == "ground-quay-42"
    np.testing.assert_array_equal(loaded.quays[0][2], [-2, -2])
    np.testing.assert_array_equal(loaded.quays[0][3], [0.2, 0.4])


def test_read_only_ground_snapshot_checks_manifest_hashes(tmp_path, monkeypatch):
    from turku3d.structures.layer import sha256
    from turku3d.structures.snapshot import snapshot_ground

    ground = ground_fixture(tmp_path)
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "consumer"))
    artifacts = {p.name: sha256(p) for p in ground.paths if p.name != "MANIFEST.json"}
    manifest = {"layer": "ground", "artifacts": artifacts, "acceptance_passed": False}
    (ground.root / "MANIFEST.json").write_text(json.dumps(manifest))
    before = {p.name: (p.stat().st_mtime_ns, sha256(p)) for p in ground.root.iterdir()}
    snapshot, evidence = snapshot_ground(ground.root)
    assert snapshot != ground.root
    assert evidence["ground_acceptance_passed"] is False
    assert evidence["manifest_hash_mismatches"] == []
    assert before == {p.name: (p.stat().st_mtime_ns, sha256(p)) for p in ground.root.iterdir()}
    (ground.root / "quays.json").write_text("changed producer output")
    with pytest.raises(ValueError, match="differs from its manifest"):
        snapshot_ground(ground.root)


def test_real_physics_cases_use_ground_and_helpers(tmp_path):
    from turku3d.structures.acceptance import prepare_cases

    ground = ground_fixture(tmp_path)
    meshes, measured = stairs("flight", [0, 0, 0], [4, 0.8, 0], 2)
    measured["object_id"] = "flight"
    cases, issues = prepare_cases(meshes, ground, [measured])
    assert not issues
    assert len(cases) == 2
    assert set(cases[0]["material"]) == {2, 16, 17, 19}
    assert cases[0]["routes"][0]["start"] == pytest.approx([-0.6, -0.12, 0])
    assert cases[1]["routes"][0]["target"] == cases[0]["routes"][0]["start"]


def test_retaining_wall_probe_approaches_from_lower_ground(tmp_path):
    from turku3d.structures.acceptance import prepare_cases

    ground = ground_fixture(tmp_path)
    wall = profile_wall("wall", [[2, -1], [2, 1]], [0, 0], [1, 1])
    measured = {
        "object_id": "wall",
        "kind": "wall",
        "profile_xz": [[2, -1], [2, 1]],
        "lower_y": [0, 0],
        "upper_y": [1, 1],
    }
    cases, issues = prepare_cases([wall], ground, [measured])
    assert not issues
    assert len(cases) == 1
    route = cases[0]["routes"][0]
    assert route["start"][1] < route["target"][1]
    assert np.linalg.norm(
        np.array(route["target"])[[0, 2]] - np.array(route["start"])[[0, 2]]
    ) == pytest.approx(1.7)
    assert route["fallback_solid_hit_y"] == pytest.approx(0.5)
    assert len(cases[0]["solid_triangles"]) == len(wall.indices)


def test_real_physics_routes_fail_on_missing_ground(tmp_path):
    from turku3d.structures.acceptance import prepare_cases

    ground = ground_fixture(tmp_path)
    meshes, measured = stairs("outside", [20, 0, 0], [24, 0.8, 0], 2)
    measured["object_id"] = "outside"
    cases, issues = prepare_cases(meshes, ground, [measured])
    assert not cases
    assert "no ground contact" in issues[0]["reason"]


def test_real_barrier_probe_crosses_segment_interior_not_its_open_end(tmp_path):
    from turku3d.structures.acceptance import prepare_cases

    ground = ground_fixture(tmp_path)
    mesh = profile_wall("f", [[2, -1], [2, 1]], [0.4, 0.4], [1.6, 1.6], material="fence")
    measurement = {
        "kind": "fence",
        "object_id": "f",
        "profile_xz": [[2, -1], [2, 1]],
        "lower_y": [0.4, 0.4],
        "upper_y": [1.6, 1.6],
    }
    cases, issues = prepare_cases([mesh], ground, [measurement])
    assert not issues
    route = cases[0]["routes"][0]
    assert route["start"][2] == route["target"][2] == 0
    assert sorted([route["start"][0], route["target"][0]]) == [0, 4]


def test_default_scope_is_exact_office_core():
    from turku3d import config as C
    from turku3d.structures.inputs import office_bounds

    assert office_bounds() == C.OFFICE_BLOCK.core
    assert {t.id for t in C.collision_tiles_for_bbox(office_bounds())} == {
        f"c_{i}_{j}" for i in range(1, 5) for j in range(2, 6)
    }


def test_v1_snapshot_ignores_unlisted_tiles_and_rejects_corruption(tmp_path, monkeypatch):
    from turku3d.layers import CollisionLayer, write_collision, write_manifest
    from turku3d.structures.snapshot import snapshot_ground

    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "work"))
    source = tmp_path / "ground"
    layer = CollisionLayer(
        np.zeros((3, 3), np.float64), np.array([[0, 1, 2]], np.uint32), np.array([2], np.uint8)
    )
    write_collision(source / "c_1_2.col.npz", layer)
    write_manifest(source, inputs={}, params={}, code="fixture", tiles=["c_1_2.col.npz"])
    (source / "c_9_9.col.npz").write_bytes(b"unlisted stale data")
    snapshot, _ = snapshot_ground(source)
    assert not (snapshot / "c_9_9.col.npz").exists()
    (source / "c_1_2.col.npz").write_bytes(b"corrupted declared data")
    with pytest.raises(ValueError, match="differs from its manifest"):
        snapshot_ground(source)
