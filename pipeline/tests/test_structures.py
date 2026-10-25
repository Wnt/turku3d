import json

import numpy as np
import pytest
from shapely.geometry import LineString
from turku3d.col.materials import mat_id, material_class
from turku3d.package.gltf import accessor_array, read_glb
from turku3d.structures.geometry import profile_wall, quay, ramp, stairs
from turku3d.structures.layer import tile_mesh, write_layer
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


def test_clipping_preserves_surface_area_and_actual_seam_bits():
    mesh = stairs("corner", [60.1234, 2.718, 60.234], [65.567, 4.8, 65.897], 3)[0][0]
    original = mesh.positions[mesh.indices]

    def area(t):
        return np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1).sum() / 2

    pieces = tile_mesh(mesh)
    assert len(pieces) == 4
    assert sum(area(p[:, :3].reshape(-1, 3, 3)) for p in pieces.values()) == pytest.approx(
        area(original)
    )
    # These are referenced geometry vertices, before any boundary completion.
    for (i, j), p in pieces.items():
        for other, axis in [((i + 1, j), 0), ((i, j + 1), 2)]:
            if other in pieces:
                coordinate = other[0 if axis == 0 else 1] * 62.5
                a = np.unique(p[p[:, axis] == coordinate, :3], axis=0)
                q = pieces[other]
                b = np.unique(q[q[:, axis] == coordinate, :3], axis=0)
                assert a.tobytes() == b.tobytes()


def test_layer_contract_glb_cache_and_stale_output(tmp_path):
    meshes, metrics = stairs("cross-tile", [247, 2, 247], [253, 4, 253], 3)
    out = tmp_path / "structures"
    manifest, cached = write_layer(
        meshes, out, inputs={"fixture": "abc"}, params={}, metrics=metrics
    )
    assert not cached
    assert len(list(out.glob("*.vis.glb"))) == 4
    assert not list(out.glob("*.col"))
    tiles = {}
    for path in out.glob("*.col.npz"):
        with np.load(path, allow_pickle=False) as d:
            assert d["positions"].dtype == np.float64
            assert d["indices"].dtype == np.uint32
            assert d["material"].dtype == np.uint8
            assert len(d["material"]) == len(d["indices"])
            assert set(d["material"]) == {16, 17, 19}
            tiles[path.stem.split(".")[0]] = d["positions"]
    for cid, p in tiles.items():
        _, i, j = cid.split("_")
        i, j = int(i), int(j)
        for other, axis, edge in [
            (f"c_{i + 1}_{j}", 0, (i + 1) * 62.5),
            (f"c_{i}_{j + 1}", 2, (j + 1) * 62.5),
        ]:
            if other in tiles:
                q = tiles[other]
                assert p[p[:, axis] == edge].tobytes() == q[q[:, axis] == edge].tobytes()
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
    damaged = out / manifest["tiles"][0]
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
    with pytest.raises(FileNotFoundError, match="no classified quay"):
        Ground(ground.root)


def test_survey_to_layer_join_and_missing_ramps_are_reported(tmp_path):
    from turku3d.structures.build import build

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


def test_preserved_ground_ramp_covers_survey(tmp_path):
    from turku3d.structures.build import build

    ground = ground_fixture(tmp_path, material="ramp")
    records = [
        {"object_id": "ramp-edge", "geometry": LineString([(0, 2), (4, 2)]), "code": "62540"}
    ]
    meshes, metrics = build(records, Lidar([[0, 0, 0]], [2]), ground)
    assert metrics["status"] == "READY_FOR_VALIDATION"
    assert meshes[0].material == "ramp"
    np.testing.assert_array_equal(meshes[0].positions[meshes[0].indices], ground.triangles)


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
