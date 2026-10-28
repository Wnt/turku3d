import json

import numpy as np
import pytest
from shapely.geometry import LineString
from turku3d import config as C
from turku3d.col.materials import mat_id, material_class
from turku3d.layers import read_collision
from turku3d.package.gltf import accessor_array, read_glb
from turku3d.structures.geometry import profile_wall, quay, ramp, stairs
from turku3d.structures.layer import omit_failed, write_tile
from turku3d.structures.survey import Lidar, group_flights


def test_batched_faces_keep_metric_uvs_winding_and_degenerate_rejection():
    from turku3d.structures.geometry import Faces

    panels = np.array(
        [
            [[1, 2, 3], [4, 2, 5], [4, 7, 5], [1, 7, 3]],
            [[1, 2, 3], [1, 7, 3], [4, 7, 5], [4, 2, 5]],
            [[1, 2, 3], [4, 2, 5], [5, 2, 3], [2, 2, 1]],
            [[1, 2, 3]] * 4,
        ],
        dtype=float,
    )
    reference, batched = Faces(), Faces()
    for panel in panels:
        reference.face(panel)
    batched.quads(panels)
    assert np.array_equal(reference.positions, batched.positions)
    assert np.array_equal(reference.indices, batched.indices)
    assert np.allclose(reference.uvs, batched.uvs, atol=1e-14)
    tri = Faces()
    tri.triangles(
        np.asarray(batched.positions), np.asarray(batched.indices), np.asarray(batched.uvs)
    )
    assert np.array_equal(
        np.asarray(reference.positions)[reference.indices], np.asarray(tri.positions)[tri.indices]
    )


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


def test_whole_object_reaches_each_collision_cell_of_its_tile_without_clipping(tmp_path):
    mesh = stairs("corner", [60.1234, 2.718, 60.234], [65.567, 4.8, 65.897], 3)[0][0]
    out = tmp_path / "structures"
    out.mkdir()
    write_tile([mesh], out, C.VisualTile(0, 0, 0))
    files = list(out.glob("*.col.npz"))
    assert {path.stem.removesuffix(".col") for path in files} == {
        "c_0_0",
        "c_0_1",
        "c_1_0",
        "c_1_1",
    }
    for path in files:
        exported = read_collision(path)
        np.testing.assert_array_equal(
            exported.positions[exported.indices], mesh.positions[mesh.indices]
        )


def test_collision_edge_coordinates_are_exact_in_both_tiles(tmp_path):
    mesh = stairs("edge", [3, 0, 60], [3, 1, 62.5 - 5e-14], 2)[0][0]
    out = tmp_path / "structures"
    out.mkdir()
    write_tile([mesh], out, C.VisualTile(0, 0, 0))
    for tile in ("c_0_0", "c_0_1"):
        points = read_collision(out / f"{tile}.col.npz").positions
        edge = points[np.abs(points[:, 2] - 62.5) < 1e-8, 2]
        assert len(edge) > 0
        assert np.all(edge == 62.5)


def test_tile_contract_glb_and_an_object_across_the_tile_edge(tmp_path):
    """A flight across the t0_0_0/t0_1_1 corner stays whole, in its owner tile's cells only."""
    meshes, _ = stairs("cross-tile", [247, 2, 247], [253, 4, 253], 3)
    out = tmp_path / "structures"
    out.mkdir()
    write_tile(meshes, out, C.VisualTile(0, 0, 0))
    assert sorted(p.name for p in out.glob("*.col.npz")) == ["c_3_3.col.npz"]
    assert [p.name for p in out.glob("*.vis.glb")] == ["t0_0_0.vis.glb"]
    materials = set()
    for path in out.glob("*.col.npz"):
        with np.load(path, allow_pickle=False) as d:
            assert d["positions"].dtype == np.float64
            assert d["indices"].dtype == np.uint32
            assert d["material"].dtype == np.uint8
            assert len(d["material"]) == len(d["indices"])
            materials.update(d["material"])
    assert materials == {16, 17, 19}
    js, binary = read_glb((out / "t0_0_0.vis.glb").read_bytes())
    for node in js["nodes"][1:]:
        assert node["extras"]["layer"] == "structures"
        assert node["extras"]["material_key"]
        prim = js["meshes"][node["mesh"]]["primitives"][0]
        uv = accessor_array(js, binary, prim["attributes"]["TEXCOORD_0"])
        assert np.isfinite(uv).all()


def test_failed_objects_are_omitted_with_their_parts():
    good, _ = stairs("city:good", [1, 0, 1], [5, 1, 1], 1)
    bad, _ = stairs("flight:bad", [1, 0, 5], [5, 1, 5], 1)
    wall = profile_wall("osm:way:9:0", np.array([[0, 9], [4, 9]]), [0, 0], [1, 1])
    issues = [
        {"object_id": "flight:bad", "reason": "stair landing/ground mismatch"},
        {"object_id": "osm:way:9:0:route:0", "reason": "real-data Rapier traversal failed"},
    ]
    kept, omitted = omit_failed([*good, *bad, wall], issues)
    assert kept == good and omitted == len(bad) + 1


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


def test_an_inconsistent_flight_is_rejected_by_its_nosing_not_a_supporting_edge():
    lines = [
        ("a-edge", LineString([(1.75, 0.5), (2.06, 0.5)]), "66618"),
        ("b-nosing", LineString([(0, 0), (2, 0)]), "66626"),
    ]
    flights, rejected = group_flights(lines)
    assert flights == []
    assert rejected == [{"object_id": "b-nosing", "reason": "inconsistent flight widths"}]


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


def test_structures_are_built_per_tile_from_survey_and_osm(tmp_path, monkeypatch):
    """OSM fence and tagged wall in t0_0_0 are probed on the ground; flat steps are excluded."""
    import producer_world as world
    from turku3d import tile_layer
    from turku3d.layers import layer_root, read_manifest
    from turku3d.structures.producer import PRODUCER

    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "work"))
    monkeypatch.setenv("TURKU3D_JOBS", "1")
    world.ground(["t0_0_0", "t0_1_0"])
    world.write_wfs("kanta_Rakenne", [], [], columns=("kplkoodi", "feature_key"))
    world.write_osm(
        [
            ({"barrier": "fence"}, LineString([(20, 20), (40, 20)])),
            ({"barrier": "wall", "height": "2"}, LineString([(20, 60), (20, 80)])),
            ({"highway": "steps"}, LineString([(100, 100), (100, 104)])),
            ({"barrier": "fence"}, LineString([(300, 20), (320, 20)])),  # in t0_1_0
        ]
    )
    xs, zs = np.meshgrid(np.arange(-10.25, 260, 1.0), np.arange(-10.25, 260, 1.0))
    ground = np.c_[xs.ravel(), np.full(xs.size, world.GROUND_Y), zs.ravel()]
    world.lidar(ground, np.full(len(ground), 2), C.BBoxLocal(-10, -10, 260, 260))
    out = layer_root() / "structures"
    tile_layer.run(out, PRODUCER, ["t0_0_0"])
    manifest = read_manifest(out)
    assert manifest["params"]["acceptance_passed"]
    assert set(manifest["tiles"]) == {"t0_0_0.vis.glb", "c_0_0.col.npz", "c_0_1.col.npz"}
    metrics = json.loads((out / "metrics.json").read_text())
    assert metrics["object_counts"] == {"fence": 1, "wall": 1}
    assert [s["object_id"] for s in metrics["tiles"]["t0_0_0"]["excluded_sources"]] == [
        "osm:way:3:0"
    ]
    wall = read_collision(out / "c_0_1.col.npz")
    assert wall.positions[:, 1].max() == pytest.approx(world.GROUND_Y + 2)
