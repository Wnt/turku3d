"""Small synthetic ground fixtures; no city inputs or network required."""

import numpy as np
import pytest
import shapely
from shapely.geometry import LineString, box
from turku3d import config as C
from turku3d.col.materials import mat_id
from turku3d.ground.build import visual_meshes
from turku3d.ground.curbs import fit_curbs, fit_profile
from turku3d.ground.materials import surface_material
from turku3d.ground.mesh import HeightQuery, Mesh, build_mesh, seam_errors, tile_mesh
from turku3d.ground.points import Surface, select_ground, split_points
from turku3d.package.gltf import accessor_array, build_glb, read_glb


class ConstantMaterials:
    def lookup(self, xz):
        return np.full(len(xz), mat_id("road"), np.uint8)


@pytest.fixture(scope="module")
def curb_cloud():
    x, z = np.meshgrid(np.arange(-2, 6, 0.1), np.arange(-2, 15, 0.1))
    rng = np.random.default_rng(20)
    y = 0.01 * x + 0.02 * z + (x > 2) * 0.15 + rng.normal(0, 0.003, x.shape)
    return np.c_[x.ravel(), y.ravel(), z.ravel()]


def test_class12_eligibility_and_roof_rejection():
    x, z = np.meshgrid(np.arange(0, 3, 0.15), np.arange(0, 3, 0.15))
    ground = np.c_[x.ravel(), 0.03 * x.ravel(), z.ravel()]
    near = ground.copy()
    near[:, 1] += 0.01
    roof = ground.copy()
    roof[:, 1] += 4
    accepted, stats = select_ground(np.r_[ground, near, roof], np.repeat([2, 12, 12], len(ground)))
    assert stats["class12_accepted"] == len(near)
    assert accepted[:, 1].max() < 0.15


def test_split_is_order_independent_and_duplicate_safe(curb_cloud):
    classes = np.full(len(curb_cloud), 2)
    (train, _), (held, _) = split_points(curb_cloud, classes)
    (reverse, _), _ = split_points(curb_cloud[::-1], classes)
    assert np.array_equal(train, reverse[::-1])
    assert not set(map(tuple, train)) & set(map(tuple, held))
    assert len(held) > len(curb_cloud) * 0.15


def test_profile_removes_cross_slope(curb_cloud):
    surface = Surface(curb_cloud)
    fitted = fit_profile(surface, np.array([2.15, 5.0]), np.array([1.0, 0.0]))
    assert fitted is not None
    shift, heights = fitted
    assert abs(shift + 0.15) < 0.16
    assert abs(heights[1] - heights[0] - 0.15) < 0.015
    control = fit_profile(surface, np.array([4.0, 5.0]), np.array([1.0, 0.0]), snap=False)
    assert abs(np.diff(control[1])[0]) < 0.01


def test_alternating_stations_and_quay_classification(curb_cloud):
    lines = [("curb", LineString([(2.15, 0), (2.15, 12)]))]
    curbs, rejected = fit_curbs(lines, Surface(curb_cloud))
    assert not rejected
    curb = curbs[0]
    assert curb.kind == "curb"
    assert np.all(curb.fitting_distances % 2 == 0)
    assert all(v["distance"] % 2 == 1 for v in curb.validation)
    assert set(curb.fitting_distances).isdisjoint(v["distance"] for v in curb.validation)
    quay = curb_cloud.copy()
    quay[quay[:, 0] > 2, 1] += 1.5
    assert fit_curbs(lines, Surface(quay))[0][0].kind == "quay"


def test_odd_station_heights_cannot_change_breakline(curb_cloud):
    line = [("curb", LineString([(2.15, 0), (2.15, 12)]))]
    changed = curb_cloud.copy()
    # Modify only spatial bands used by odd held-out stations.
    odd_band = np.abs(changed[:, 2] - (np.round((changed[:, 2] - 1) / 2) * 2 + 1)) <= 0.35
    changed[odd_band & (changed[:, 0] > 2), 1] += 0.3
    a = fit_curbs(line, Surface(curb_cloud))[0][0]
    b = fit_curbs(line, Surface(changed))[0][0]
    assert np.array_equal(a.heights, b.heights)
    assert np.array_equal(a.centers, b.centers)
    assert a.validation == b.validation  # QA stores locations, never training-derived truth.


@pytest.fixture(scope="module")
def built_mesh():
    x, z = np.meshgrid(np.arange(0, 125.01, 0.5), np.arange(0, 62.51, 0.5))
    y = 0.01 * x + 0.005 * z + (z > 30) * 0.15
    points = np.c_[x.ravel(), y.ravel(), z.ravel()]
    dense_x, dense_z = np.meshgrid(np.arange(52, 74, 0.1), np.arange(28, 32, 0.1))
    dense_y = 0.01 * dense_x + 0.005 * dense_z + (dense_z > 30) * 0.15
    points = np.r_[points, np.c_[dense_x.ravel(), dense_y.ravel(), dense_z.ravel()]]
    surface = Surface(points)
    curbs = fit_curbs([("seam-curb", LineString([(55, 30.15), (70, 30.15)]))], surface)[0]
    assert curbs
    tiles = [C.CollisionTile(0, 0), C.CollisionTile(1, 0)]
    water = box(80, 40, 120, 60)
    mesh, info = build_mesh(
        surface, C.BBoxLocal(0, 0, 125, 62.5), tiles, curbs, water, -0.25, ConstantMaterials()
    )
    return mesh, tiles, water


def test_cdt_seams_water_and_materials(built_mesh, tmp_path):
    mesh, tiles, water = built_mesh
    meshes = {t: tile_mesh(mesh, t) for t in tiles}
    assert not seam_errors(meshes)
    assert max(len(m.indices) for m in meshes.values()) <= 150000
    centers = mesh.positions[mesh.indices].mean(axis=1)
    wet = shapely.contains_xy(water, centers[:, 0], centers[:, 2])
    assert wet.any()
    assert np.all(mesh.material[wet] == mat_id("water"))
    assert np.all(mesh.positions[mesh.indices[wet], 1] == -0.25)
    assert not np.any(mesh.material[~wet] == mat_id("water"))
    solid_triangles = shapely.polygons(mesh.positions[mesh.indices[~wet]][:, :, [0, 2]])
    assert np.all(shapely.area(shapely.intersection(solid_triangles, water)) < 1e-9)
    for tile, part in meshes.items():
        assert part.positions.dtype == np.float64
        assert part.indices.dtype == np.uint32
        assert part.material.dtype == np.uint8
        part.save(tmp_path / f"{tile.id}.col.npz")
        restored = Mesh.load(tmp_path / f"{tile.id}.col.npz")
        assert np.array_equal(restored.positions, part.positions)
        assert len(part.material) == len(part.indices)
        a = part.positions[part.indices]
        assert np.all(np.cross(a[:, 1] - a[:, 0], a[:, 2] - a[:, 0])[:, 1] > 0)
    broken = meshes[tiles[1]].positions.copy()
    broken[broken[:, 0] == 62.5, 1] += 1e-10
    meshes[tiles[1]] = Mesh(broken, meshes[tiles[1]].indices, meshes[tiles[1]].material)
    assert seam_errors(meshes)


def test_constrained_curb_is_sharp_and_visual_is_separate(curb_cloud):
    curbs = fit_curbs([("curb", LineString([(2.15, 0), (2.15, 12)]))], Surface(curb_cloud))[0]
    mesh, _ = build_mesh(
        Surface(curb_cloud),
        C.BBoxLocal(-1, -1, 5, 14),
        [],
        curbs,
        shapely.Polygon(),
        0,
        ConstantMaterials(),
    )
    query = HeightQuery(mesh)
    c = curbs[0]
    center, normal = c.centers[2], c.normals[2]
    heights = query(np.array([center - 0.031 * normal, center + 0.031 * normal]))
    assert abs(abs(heights[1] - heights[0]) - 0.15) < 0.02
    before = mesh.positions.copy()
    visual = visual_meshes(mesh, C.VisualTile(0, 0, 0), curbs)
    assert any(m.extras.get("visual_only_bevel") for m in visual)
    assert np.array_equal(before, mesh.positions)
    js, blob = read_glb(build_glb(visual))
    for node in js["nodes"][1:]:
        assert {"layer", "object_id", "material_key"} <= node["extras"].keys()
        primitive = js["meshes"][node["mesh"]]["primitives"][0]
        pos = accessor_array(js, blob, primitive["attributes"]["POSITION"])
        uv = accessor_array(js, blob, primitive["attributes"]["TEXCOORD_0"])
        np.testing.assert_allclose(uv, pos[:, [0, 2]] * [1, -1])


def test_physical_paving_overrides_road_function():
    assert surface_material("Ajorata", "Nupukivi") == "cobblestone"
    assert surface_material("Jalkakäytävä", "Asfaltti") == "sidewalk"
    assert surface_material("Ajorata", "Asfaltti") == "road"


def test_flush_crossing_semantics(curb_cloud):
    lowered = curb_cloud.copy()
    lowered[lowered[:, 0] > 2, 1] -= 0.13
    line = [("lowered", LineString([(2.15, 0), (2.15, 12)]))]
    surface = Surface(lowered)
    assert fit_curbs(line, surface)[0][0].kind == "curb_dropped"
    assert fit_curbs(line, surface, box(1, 0, 3, 12))[0][0].kind == "crossing"


def test_layer_cache_noop_and_missing_tile_repair(monkeypatch, tmp_path):
    import json
    from argparse import Namespace

    from turku3d.ground import build as builder

    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path))
    ortho = tmp_path / "source.vrt"
    ortho.write_text("<VRTDataset/>")
    source = tmp_path / "source.laz"
    source.write_bytes(b"synthetic source identity")
    monkeypatch.setattr(builder, "source_paths", lambda *args: {"ortho": ortho, "lidar": source})
    x, z = np.meshgrid(np.arange(8.0), np.arange(8.0))
    cloud = np.c_[x.ravel(), np.zeros(x.size), z.ravel()]
    monkeypatch.setattr(
        builder, "read_lidar", lambda *args: (cloud, np.full(len(cloud), 2), np.zeros(30))
    )
    monkeypatch.setattr(
        builder,
        "vectors",
        lambda *args: ([], shapely.Polygon(), shapely.Polygon(), shapely.Polygon(), None),
    )
    monkeypatch.setattr(builder, "Materials", lambda *args: ConstantMaterials())
    calls = []

    def fake_cdt(surface, bounds, tiles, *args, **kwargs):
        calls.append(1)
        p, f = [], []
        for tile in tiles:
            b = tile.bbox
            start = len(p)
            p.extend(
                [[b.xmin, 0, b.zmin], [b.xmax, 0, b.zmin], [b.xmax, 0, b.zmax], [b.xmin, 0, b.zmax]]
            )
            f.extend([[start, start + 2, start + 1], [start, start + 3, start + 2]])
        return Mesh(
            np.array(p, np.float64),
            np.array(f, np.uint32),
            np.full(len(f), mat_id("road"), np.uint8),
        ), {}

    monkeypatch.setattr(builder, "build_mesh", fake_cdt)
    monkeypatch.setattr(
        builder,
        "evaluate_ground",
        lambda *args: {"passed": False, "checks": {"coverage": False}, "metrics": {}},
    )

    def report(data, directory):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "ground-qa.json").write_text(json.dumps(data))

    monkeypatch.setattr(builder, "write_report", report)
    args = Namespace(tiles=["t0_0_0"], source_area="fixture", water_level=None)
    assert builder.run(args) == 2  # Cache must preserve a failed acceptance result.
    output = tmp_path / "build/layers/ground"
    before = {p.name: p.stat().st_mtime_ns for p in output.iterdir()}
    assert builder.run(args) == 2
    assert before == {p.name: p.stat().st_mtime_ns for p in output.iterdir()}
    (output / "c_0_0.col.npz").unlink()
    assert builder.run(args) == 2
    assert len(calls) == 1  # Repair reuses the canonical geometry cache.
    assert (output / "c_0_0.col.npz").exists()
    assert (output / "c_1_0.col.npz").stat().st_mtime_ns == before["c_1_0.col.npz"]
    assert (output / "t0_0_0.vis.glb").stat().st_mtime_ns == before["t0_0_0.vis.glb"]


def test_qa_does_not_trim_bad_heldout_returns(built_mesh):
    import json

    from turku3d.ground.qa import evaluate

    mesh, tiles, water = built_mesh
    land = Mesh(
        mesh.positions,
        mesh.indices[mesh.material != mat_id("water")],
        mesh.material[mesh.material != mat_id("water")],
    ).compact()
    surface = Surface(land.positions)
    holdout = np.array([[10.0, 0.15, 10.0], [20.0, 10.3, 20.0]])
    report = evaluate(
        {t: tile_mesh(mesh, t) for t in tiles},
        land.positions,
        holdout,
        [],
        water,
        shapely.Polygon(),
        C.BBoxLocal(0, 0, 125, 62.5),
        ConstantMaterials(),
        surface,
    )
    assert report["metrics"]["lidar"]["heldout_count"] == 2
    assert report["metrics"]["lidar"]["rmse_m"] > 7
    assert not report["checks"]["lidar_rmse"]
    assert not report["checks"]["curb_steps"]  # Missing stations are not a vacuous pass.
    json.dumps(report, allow_nan=False)
    holdout[1, 1] = 0.3
    report = evaluate(
        {t: tile_mesh(mesh, t) for t in tiles},
        land.positions,
        holdout,
        [],
        water,
        shapely.Polygon(),
        C.BBoxLocal(0, 0, 125, 62.5),
        ConstantMaterials(),
        surface,
    )
    assert report["checks"]["lidar_rmse"]
    json.dumps(report, allow_nan=False)


def test_quay_handoff_uses_water_level_without_ground_over_river():
    x, z = np.meshgrid(np.arange(-2.0, -0.05, 0.1), np.arange(-1.0, 14.0, 0.1))
    points = np.c_[x.ravel(), 2.5 + 0.01 * z.ravel(), z.ravel()]
    water = box(0, -2, 5, 15)
    curbs, rejected = fit_curbs(
        [("river-edge", LineString([(0, 0), (0, 12)]))],
        Surface(points),
        water=water,
        water_level=0.2,
    )
    assert not rejected
    assert curbs[0].kind == "quay"
    assert np.all(np.min(curbs[0].heights, axis=1) == 0.2)


def test_profile_measurement_tracks_actual_edge_and_rejects_a_ramp():
    from turku3d.ground.qa import measure_mesh_step

    offsets = np.linspace(-1.2, 1.2, 241)
    for edge in [-0.25, 0, 0.25]:
        profile = 3 + 0.02 * offsets + 0.15 * np.clip((offsets - edge + 0.03) / 0.06, 0, 1)
        step, width, detected = measure_mesh_step(offsets, profile)
        assert abs(step - 0.15) < 0.001
        assert width < 0.07
        assert abs(detected - edge) < 0.04
    step, width, _ = measure_mesh_step(offsets, 3 + 0.12 * offsets)
    assert abs(step) < 0.001
    assert width > 0.2


def test_ill_conditioned_profile_is_not_extrapolated():
    # Points along two scan lines cannot define independent across/along slopes.
    t = np.linspace(-0.3, 0.3, 20)
    points = np.r_[np.c_[t * 0.001 - 0.3, 2 + t, t], np.c_[t * 0.001 + 0.3, 2.15 + t, t]]
    assert fit_profile(Surface(points), np.array([0.0, 0.0]), np.array([1.0, 0.0])) is None


def test_vector_corners_do_not_become_lidar_fitting_stations(curb_cloud):
    line = LineString([(2.15, 0), (2.15, 5.3), (2.25, 5.3), (2.25, 12)])
    curbs, _ = fit_curbs([("corner", line)], Surface(curb_cloud))
    assert curbs
    assert 5.3 in curbs[0].distances
    assert 5.3 not in curbs[0].fitting_distances
    assert np.all(curbs[0].fitting_distances % 2 == 0)


def test_curb_qa_uses_withheld_returns_not_cached_fitting_heights(curb_cloud):
    from turku3d.ground.qa import evaluate

    (train, _), (held, _) = split_points(curb_cloud, np.full(len(curb_cloud), 2))
    surface = Surface(train)
    curbs = fit_curbs([("independent", LineString([(2.15, 0), (2.15, 12)]))], surface)[0]
    bounds = C.BBoxLocal(-1, -1, 5, 14)
    mesh, _ = build_mesh(surface, bounds, [], curbs, shapely.Polygon(), 0, ConstantMaterials())
    before = mesh.positions.copy()
    tiles = {C.CollisionTile(0, 0): mesh}
    good = evaluate(
        tiles,
        train,
        held,
        curbs,
        shapely.Polygon(),
        shapely.Polygon(),
        bounds,
        ConstantMaterials(),
        surface,
    )
    assert good["checks"]["curb_steps"]
    changed = held.copy()
    changed[changed[:, 0] > 2, 1] += 0.1
    bad = evaluate(
        tiles,
        train,
        changed,
        curbs,
        shapely.Polygon(),
        shapely.Polygon(),
        bounds,
        ConstantMaterials(),
        surface,
    )
    assert not bad["checks"]["curb_steps"]
    assert np.array_equal(before, mesh.positions)


def test_breakline_interchange_validation_and_direct_heights(tmp_path, curb_cloud):
    import json

    from turku3d.ground.breaklines import load_breaklines, records, validation_geojson

    curbs = fit_curbs([("external", LineString([(2.15, 0), (2.15, 12)]))], Surface(curb_cloud))[0]
    path = tmp_path / "breaklines.json"
    data = records(curbs)
    path.write_text(json.dumps(data))
    loaded = load_breaklines(path)
    assert np.array_equal(loaded[0].heights, curbs[0].heights)
    assert np.array_equal(loaded[0].centers, curbs[0].centers)
    assert loaded[0].validation
    sidecar = validation_geojson(loaded)
    assert sidecar["turku3d_coords"] == "engine-xz"
    assert sidecar["features"]
    assert all(abs(f["properties"]["step"] - 0.15) < 0.02 for f in sidecar["features"])
    data[0]["normals_xz"][0] = [0.0, 0.0]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="unit length"):
        load_breaklines(path)


def test_missing_curb_relief_fails_length_acceptance(curb_cloud):
    from turku3d.ground.qa import evaluate

    surface = Surface(curb_cloud)
    source_lines = [("missing", LineString([(2.15, 0), (2.15, 13.1)]))]
    p = np.array([[-1.0, 0, -1.0], [5.0, 0, -1.0], [5.0, 0, 14.0], [-1.0, 0, 14.0]])
    mesh = Mesh(
        p, np.array([[0, 2, 1], [0, 3, 2]], np.uint32), np.full(2, mat_id("road"), np.uint8)
    )
    report = evaluate(
        {C.CollisionTile(0, 0): mesh},
        curb_cloud,
        curb_cloud,
        [],
        shapely.Polygon(),
        shapely.Polygon(),
        C.BBoxLocal(-1, -1, 5, 14),
        ConstantMaterials(),
        surface,
        source_lines,
    )
    assert report["metrics"]["curbs"]["mesh_step_length_fraction"] == 0
    assert not report["checks"]["curb_length"]
    assert not report["checks"]["curb_steps"]
    import json

    json.dumps(report, allow_nan=False)


def test_plain_ground_acceptance_rejects_interior_hole(built_mesh):
    from turku3d.ground.ground_qa import evaluate_ground

    mesh, tiles, water = built_mesh
    x, z = np.meshgrid(np.arange(0.5, 125), np.arange(0.5, 62.5))
    accepted = np.c_[x.ravel(), np.zeros(x.size), z.ravel()]
    meshes = {t: tile_mesh(mesh, t) for t in tiles}
    bounds = C.BBoxLocal(0, 0, 125, 62.5)
    report = evaluate_ground(meshes, accepted, water, shapely.Polygon(), bounds)
    assert report["passed"]
    part = meshes[tiles[0]]
    centers = part.positions[part.indices].mean(axis=1)
    remove = np.argmin(np.linalg.norm(centers[:, [0, 2]] - [20, 20], axis=1))
    meshes[tiles[0]] = Mesh(
        part.positions, np.delete(part.indices, remove, axis=0), np.delete(part.material, remove)
    )
    broken = evaluate_ground(meshes, accepted, water, shapely.Polygon(), bounds)
    assert not broken["checks"]["holes"]
    assert broken["metrics"]["mesh"]["interior_open_edges"] == 3
