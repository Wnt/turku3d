"""Small synthetic ground fixtures; no city inputs or network required."""

import numpy as np
import pytest
import shapely
from shapely.geometry import LineString, box
from turku3d import config as C
from turku3d.col.materials import mat_id
from turku3d.ground.breaklines import clamp_curb_heights
from turku3d.ground.build import visual_meshes
from turku3d.ground.curbs import fit_curbs, fit_profile
from turku3d.ground.materials import surface_material
from turku3d.ground.mesh import (
    HeightQuery,
    Mesh,
    build_mesh,
    seam_errors,
    tile_mesh,
)
from turku3d.ground.points import Surface, select_ground, split_points
from turku3d.ground.ramps import classify_ramps
from turku3d.package.gltf import accessor_array, build_glb, read_glb


class ConstantMaterials:
    def lookup(self, xz):
        return np.full(len(xz), mat_id("road"), np.uint8)


def test_surveyed_ramp_uses_existing_ground_triangles_without_relabelling_water():
    positions = np.array(
        [[0, 0, 0], [2, 0.2, 0], [0, 0, 2], [2, 0.2, 2], [4, 0.2, 0], [4, 0.2, 2]],
        dtype=np.float64,
    )
    indices = np.array([[0, 2, 1], [1, 2, 3], [1, 3, 4], [4, 3, 5]], dtype=np.uint32)
    mesh = Mesh(
        positions.copy(),
        indices.copy(),
        np.array(
            [mat_id("sidewalk"), mat_id("sidewalk"), mat_id("water"), mat_id("water")], np.uint8
        ),
    )
    before = (mesh.positions.copy(), mesh.indices.copy())
    result, records = classify_ramps(mesh, [("survey:1", LineString([(0.5, 0.5), (3.5, 0.5)]))])
    assert result == {"survey_lines": 1, "covered_lines": 0, "triangles": 2}
    assert records[0]["object_id"] == "survey:1"
    np.testing.assert_array_equal(mesh.positions, before[0])
    np.testing.assert_array_equal(mesh.indices, before[1])
    np.testing.assert_array_equal(
        mesh.material, [mat_id("ramp"), mat_id("ramp"), mat_id("water"), mat_id("water")]
    )


def test_steep_surveyed_slope_is_visible_blocking_wall():
    mesh = Mesh(
        np.array([[0, 0, 0], [1, 1, 0], [0, 0, 1]], np.float64),
        np.array([[0, 2, 1]], np.uint32),
        np.array([mat_id("paving_slab")], np.uint8),
    )
    summary, records = classify_ramps(mesh, [("survey:wall", LineString([(0.1, 0.1), (0.7, 0.1)]))])
    assert summary["covered_lines"] == 1
    assert records[0]["classification"] == "wall"
    assert records[0]["wall_triangles"] == 1
    assert mesh.material.tolist() == [mat_id("wall")]


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


def test_cdt_seams_hold_on_a_2x3_grid_with_an_added_column():
    """A tile added east of a 2x2 block is meshed with it: every shared edge matches bytewise."""
    x, z = np.meshgrid(np.arange(0, 187.51, 1.0), np.arange(0, 125.01, 1.0))
    y = 0.02 * x + 0.01 * z + 0.3 * np.sin(x / 9.0) * np.cos(z / 7.0)
    surface = Surface(np.c_[x.ravel(), y.ravel(), z.ravel()])
    tiles = [C.CollisionTile(i, j) for j in range(2) for i in range(3)]
    mesh, _ = build_mesh(
        surface,
        C.BBoxLocal(0, 0, 187.5, 125),
        tiles,
        [],
        shapely.Polygon(),
        0.0,
        ConstantMaterials(),
    )
    meshes = {t: tile_mesh(mesh, t) for t in tiles}
    assert not seam_errors(meshes)
    for tile, part in meshes.items():
        east = C.CollisionTile(tile.i + 1, tile.j)
        if east in meshes:  # the added column shares real vertices with the block
            assert np.sum(part.positions[:, 0] == tile.bbox.xmax) >= 2
    broken = meshes[tiles[2]].positions.copy()
    broken[broken[:, 0] == 125.0, 1] += 1e-9
    meshes[tiles[2]] = Mesh(broken, meshes[tiles[2]].indices, meshes[tiles[2]].material)
    assert ["c_1_0", "c_2_0"] in seam_errors(meshes)


def test_unsupported_shore_is_bounded_and_bridge_cuts_land():
    x, z = np.meshgrid(np.arange(0, 6, 0.5), np.arange(0, 6, 0.5))
    surface = Surface(np.c_[x.ravel(), 1 + x.ravel(), z.ravel()])
    water = box(-4, 1, -1, 4)
    bridge = box(-2, 1.5, 4, 3.5)
    mesh, _ = build_mesh(
        surface,
        C.BBoxLocal(-4, 0, 5, 5),
        [],
        [],
        water,
        0,
        ConstantMaterials(),
        bridges=bridge,
    )
    unsupported = mesh.positions[:, 0] < -2
    assert mesh.positions[unsupported, 1].min() >= 0
    centers = mesh.positions[mesh.indices].mean(axis=1)
    cut = bridge.intersection(water.buffer(2))
    inside_cut = shapely.contains_xy(cut, centers[:, 0], centers[:, 2])
    landward = shapely.contains_xy(box(2, 2, 4, 3), centers[:, 0], centers[:, 2])
    assert not np.any(inside_cut & (mesh.material != mat_id("water")))
    assert np.any(inside_cut & (mesh.material == mat_id("water")))
    assert np.any(landward & (mesh.material != mat_id("water")))


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
    centers = mesh.positions[mesh.indices].mean(axis=1)[:, [0, 2]]
    low = center - 0.7 * normal if c.heights[2, 0] < c.heights[2, 1] else center + 0.7 * normal
    high = center + 0.7 * normal if c.heights[2, 0] < c.heights[2, 1] else center - 0.7 * normal
    assert mesh.material[np.argmin(np.linalg.norm(centers - low, axis=1))] == mat_id("road")
    assert mesh.material[np.argmin(np.linalg.norm(centers - high, axis=1))] == mat_id("sidewalk")
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


def test_measured_wall_keeps_height_and_renders_collision_face(curb_cloud):
    cloud = curb_cloud.copy()
    cloud[cloud[:, 0] > 2, 1] += 0.45
    surface = Surface(cloud)
    walls = fit_curbs([("wall", LineString([(2.15, 0), (2.15, 12)]))], surface)[0]
    assert walls
    walls[0].kind = "wall"
    original = walls[0].heights.copy()
    clamp_curb_heights(walls)
    np.testing.assert_array_equal(walls[0].heights, original)
    mesh, _ = build_mesh(
        surface,
        C.BBoxLocal(-1, -1, 5, 14),
        [],
        walls,
        shapely.Polygon(),
        0,
        ConstantMaterials(),
    )
    assert mat_id("wall") in mesh.material
    assert any(
        part.extras["material_key"] == "wall"
        for part in visual_meshes(mesh, C.VisualTile(0, 0, 0), walls)
    )


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


def test_measured_curb_rise_is_clamped_without_moving_midpoint(curb_cloud):
    curb = fit_curbs([("curb", LineString([(2.15, 0), (2.15, 12)]))], Surface(curb_cloud))[0][0]
    midpoint = curb.heights.mean(axis=1).copy()
    curb.heights[0] = midpoint[0] + np.array([-0.01, 0.01])
    curb.heights[1] = midpoint[1] + np.array([0.3, -0.3])
    clamp_curb_heights([curb])
    np.testing.assert_allclose(curb.heights.mean(axis=1), midpoint)
    np.testing.assert_allclose(np.abs(np.diff(curb.heights[:2], axis=1))[:, 0], [0.06, 0.20])


def test_collision_mesh_step_coverage_counts_actual_height(tmp_path, curb_cloud):
    import json

    from turku3d.ground.breaklines import records
    from turku3d.ground.curb_coverage import measure

    surface = Surface(curb_cloud)
    curb = fit_curbs([("mapped", LineString([(2.15, 0), (2.15, 12)]))], surface)[0][0]
    bounds = C.BBoxLocal(0, 0, 5, 12)
    flat = Surface(curb_cloud.copy())
    flat.points[:, 1] -= (flat.points[:, 0] > 2) * 0.15
    before, _ = build_mesh(flat, bounds, [], [], shapely.Polygon(), 0, ConstantMaterials())
    after, _ = build_mesh(surface, bounds, [], [curb], shapely.Polygon(), 0, ConstantMaterials())
    old, new = tmp_path / "before", tmp_path / "after"
    before.save(old / "c_0_0.col.npz")
    after.save(new / "c_0_0.col.npz")
    line = records([curb])[0]
    line["provenance"] = "kantakartta+lidar"
    source = tmp_path / "breaklines.json"
    source.write_text(json.dumps([line]))
    result = measure(old, new, source)["combined"]
    assert result["before"]["step_length_m"] == 0
    assert result["after"]["fraction"] > 0.8
    assert result["curb_material_length_m"] / result["measured_length_m"] > 0.8


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
        lambda *args: ([], shapely.Polygon(), shapely.Polygon(), shapely.Polygon(), None, []),
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
        lambda *args: {
            "passed": False,
            "checks": {"coverage": False},
            "metrics": {"coverage": {"open_cells": 10, "occupied_fraction": 0.5}},
        },
    )

    def report(data, directory):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "ground-qa.json").write_text(json.dumps(data))

    monkeypatch.setattr(builder, "write_report", report)
    args = Namespace(tiles=["t0_0_0"], source_area="fixture", water_level=None)
    assert builder.run(args) == 2  # Cache must preserve a failed acceptance result.
    monkeypatch.setattr(
        builder, "split_points", lambda *args: pytest.fail("Cache repair resplit raw lidar")
    )
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
    swapped = dict(
        data[0],
        object_id="swapped-high-side",
        centers_xz=[[4, 0], [4, 1], [4, 2]],
        normals_xz=[[1, 0], [1, 0], [1, 0]],
        side_heights=[[0, 0.06], [0.08, 0], [0.18, 0]],
    )
    path.write_text(json.dumps([swapped]))
    steps = [f["properties"]["step"] for f in validation_geojson(load_breaklines(path))["features"]]
    assert steps == pytest.approx([0.13])
    tiny = dict(
        data[0],
        object_id="millimetre-transition",
        centers_xz=[[2, 3], [2.003, 3.002]],
        normals_xz=[[1, 0], [1, 0]],
        side_heights=[[0, 0.1], [0, 0.1]],
    )
    path.write_text(json.dumps(data + [tiny]))
    assert len(load_breaklines(path)) == 1
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
    from turku3d.ground.water import add_river_bed

    mesh, tiles, water = built_mesh
    mesh = add_river_bed(mesh, -0.25)
    assert np.any(mesh.positions[:, 1] == -3.25)
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


def test_tiles_meshed_in_different_envelopes_are_byte_identical():
    """Per-tile ground: a collision tile depends only on samples inside it, not on the envelope."""
    rng = np.random.default_rng(3)
    xz = rng.uniform([-25, -25], [275, 87.5], (12000, 2))
    y = 0.3 * np.sin(xz[:, 0] / 9) + 0.2 * np.cos(xz[:, 1] / 7) + rng.normal(0, 0.004, len(xz))
    cloud = np.c_[xz[:, 0], y, xz[:, 1]]

    def build(columns):
        cells = [C.CollisionTile(i, 0) for i in columns]
        bounds = C.BBoxLocal(columns[0] * 62.5 - 20, -20, (columns[-1] + 1) * 62.5 + 20, 82.5)
        inside = (
            (cloud[:, 0] >= bounds.xmin)
            & (cloud[:, 0] <= bounds.xmax)
            & (cloud[:, 2] >= bounds.zmin)
            & (cloud[:, 2] <= bounds.zmax)
        )
        mesh, _ = build_mesh(
            Surface(cloud[inside]),
            bounds,
            cells,
            [],
            shapely.Polygon(),
            0,
            ConstantMaterials(),
            tolerance=0.02,
        )
        return {c: tile_mesh(mesh, c) for c in cells}

    left, right = build([0, 1, 2]), build([1, 2, 3])
    for cell in (C.CollisionTile(1, 0), C.CollisionTile(2, 0)):
        for field in ("positions", "indices", "material"):
            assert getattr(left[cell], field).tobytes() == getattr(right[cell], field).tobytes()
    mixed = {C.CollisionTile(0, 0): left[C.CollisionTile(0, 0)]}
    mixed |= {c: right[c] for c in (C.CollisionTile(1, 0), C.CollisionTile(3, 0))}
    mixed[C.CollisionTile(2, 0)] = left[C.CollisionTile(2, 0)]
    assert seam_errors(mixed) == []


def test_adding_a_tile_builds_only_that_tile(monkeypatch, tmp_path):
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
        lambda *args: ([], shapely.Polygon(), shapely.Polygon(), shapely.Polygon(), None, []),
    )
    monkeypatch.setattr(builder, "Materials", lambda *args: ConstantMaterials())
    built = []

    def fake_cdt(surface, bounds, cells, *args, **kwargs):
        built.append(bounds)
        p, f = [], []
        for cell in cells:
            b = cell.bbox
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
        # One tile alone is below 95 % observed; coverage is checked over all listed tiles.
        lambda meshes, accepted, water, buildings, bounds, *rest: {
            "passed": bounds.xmin > 0,
            "checks": {"coverage": bounds.xmin > 0},
            "metrics": {
                "coverage": {"open_cells": 100, "occupied_fraction": 0.99 if bounds.xmin else 0.94}
            },
        },
    )
    monkeypatch.setattr(builder, "write_report", lambda *args: None)
    output = tmp_path / "build/layers/ground"
    assert builder.run(Namespace(tiles=["t0_0_0"], source_area="fixture", water_level=None)) == 2
    assert len(built) == 1
    # The east margin of t0_0_0 lies in unlisted t0_1_0 and ships from t0_0_0's build.
    assert (output / "c_4_0.col.npz").exists() and not (output / "t0_1_0.vis.glb").exists()
    before = {p.name: p.stat().st_mtime_ns for p in output.iterdir()}
    args = Namespace(tiles=["t0_0_0", "t0_1_0"], source_area="fixture", water_level=None)
    assert builder.run(args) == 0  # 96.5 % observed over both tiles
    assert len(built) == 2  # Only the new tile is meshed.
    after = {p.name: p.stat().st_mtime_ns for p in output.iterdir()}
    for name in ("t0_0_0.vis.glb", "c_0_0.col.npz", "c_3_3.col.npz", "c_-1_0.col.npz"):
        assert after[name] == before[name]
    assert (output / "t0_1_0.vis.glb").exists() and (output / "c_8_0.col.npz").exists()
    manifest = json.loads((output / "MANIFEST.json").read_text())
    assert set(manifest["tile_builds"]) == {"t0_0_0", "t0_1_0"}
    assert manifest["params"]["visual_tiles"] == ["t0_0_0", "t0_1_0"]
    assert builder.owners([C.VisualTile(0, 0, 0), C.VisualTile(0, 1, 0)])[
        "c_4_0.col.npz"
    ] == C.VisualTile(0, 1, 0)


def test_seams_between_separate_tile_builds_are_checked(tmp_path):
    from turku3d.ground.build import tile_seam_errors
    from turku3d.layers import CollisionLayer, write_collision

    owned = {}
    for i in (3, 4):  # c_3_0 in t0_0_0, c_4_0 in t0_1_0; the shared edge x = 250 at 0.25 m
        x = i * C.COLLISION_TILE_SIZE
        p = np.array([[x, 0, 0], [x + 62.5, 0, 0], [x + 62.5, 0, 62.5], [x, 0, 62.5]], float)
        p[[0, 3] if i == 4 else [1, 2], 1] = 0.25
        write_collision(
            tmp_path / f"c_{i}_0.col.npz",
            CollisionLayer(p, np.array([[0, 2, 1], [0, 3, 2]], np.uint32), np.ones(2, np.uint8)),
        )
        owned[f"c_{i}_0.col.npz"] = C.VisualTile(0, i // 4, 0)
    assert tile_seam_errors(tmp_path, owned) == []
    owned["c_4_0.col.npz"] = C.VisualTile(0, 0, 0)  # one build: its own QA covers the seam
    assert tile_seam_errors(tmp_path, owned) == []
    owned["c_4_0.col.npz"] = C.VisualTile(0, 1, 0)
    p = np.array([[250, 0.3, 0], [312.5, 0, 0], [312.5, 0, 62.5], [250, 0.25, 62.5]], float)
    write_collision(
        tmp_path / "c_4_0.col.npz",
        CollisionLayer(p, np.array([[0, 2, 1], [0, 3, 2]], np.uint32), np.ones(2, np.uint8)),
    )
    assert tile_seam_errors(tmp_path, owned) == [["c_3_0", "c_4_0"]]
