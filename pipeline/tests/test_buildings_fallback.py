"""Fallbacks preserve geometry checks and never invent evidence of accuracy."""

import numpy as np
import pytest
import shapely
from turku3d.buildings.fallback import lidar_extrusion, placeholder_extrusion
from turku3d.buildings.inputs import Building
from turku3d.buildings.select import choose
from turku3d.buildings.shells import make_shell
from turku3d.buildings.tiles import collision_tiles, ownership_point


def test_missing_reconstruction_uses_measured_height():
    building = Building("missing", shapely.box(0, 0, 10, 10))
    points = np.array([[2, 8, 2], [3, 8.04, 3], [4, 7.96, 4]])
    surfaces, evidence = lidar_extrusion(building, points, {"rf_h_ground": 1.0})
    shell, record = choose(
        building, {"city": [], "roofer": [], "lidar_extrusion": surfaces}, points
    )
    assert shell is not None and record["pass"]
    assert record["choice"] == "lidar_extrusion"
    assert evidence["roof_m"] == 8 and evidence["height_points"] == 3
    assert evidence["height_source"] == "class6_inside_footprint"
    assert not evidence["accuracy_certified"]
    assert shell.hausdorff == 0
    for tile in collision_tiles([shell], [building.footprint], provenance=True).values():
        assert len(tile["building_index"]) == len(tile["indices"])
        assert (tile["building_index"] == 0).all()


def test_measured_gable_is_closed_and_placeholder_is_labelled():
    from turku3d.buildings.facades import make_visuals

    building = Building(
        "test-17",
        shapely.box(0, 0, 20, 10),
        {"osm": {"addr:street": "Test Street", "addr:housenumber": "17"}},
    )
    x, z = np.meshgrid(np.linspace(0.2, 19.8, 30), np.linspace(0.2, 9.8, 20))
    points = np.column_stack((x.ravel(), (6 + 2 * (1 - np.abs(z - 5) / 5)).ravel(), z.ravel()))
    surfaces, evidence = lidar_extrusion(building, points, {"rf_h_ground": 0.0})
    assert evidence["roof_shape"] == "gable"
    shell = make_shell(surfaces, building.footprint)
    assert len(shell.indices) > 0
    placeholder, details = placeholder_extrusion(building, {"rf_h_ground": 1.0})
    assert details["height_source"] == "default_10m"
    visual, facades = make_visuals(
        building,
        make_shell(placeholder, building.footprint),
        shapely.GeometryCollection(),
        [],
        "roof_metal_gray",
        placeholder=True,
    )
    assert not facades
    walls = [mesh for mesh in visual if mesh.extras.get("material_key") == "work_in_progress"]
    assert walls and all(mesh.image[:8] == b"\x89PNG\r\n\x1a\n" for mesh in walls)


def test_measured_hip_and_multilevel_roofs_are_closed():
    from turku3d.buildings.fallback import _roof_from_class6

    footprint = shapely.box(0, 0, 20, 10)
    x, z = np.meshgrid(np.linspace(0.2, 19.8, 30), np.linspace(0.2, 9.8, 20))
    heights = [
        ("hip", 6 + 2 * np.minimum(1 - np.abs(z - 5) / 5, np.minimum(x, 20 - x) / 4)),
        ("multi_level", np.where(x < 10, 7.0, 10.0)),
    ]
    for expected, y in heights:
        points = np.column_stack((x.ravel(), y.ravel(), z.ravel()))
        surfaces, evidence = _roof_from_class6(footprint, 0, points)
        assert evidence["roof_shape"] == expected
        assert len(make_shell(surfaces, footprint).indices) > 0


def test_ownership_stays_in_requested_tile_for_cross_boundary_footprint():
    from turku3d import config as C

    footprint = shapely.box(245, 10, 270, 30)
    tile = C.VisualTile(0, 0, 0)
    point = ownership_point(footprint, [tile])
    assert tile.bbox.xmin <= point.x < tile.bbox.xmax
    shell = make_shell(
        placeholder_extrusion(Building("edge", footprint), {"rf_h_ground": 0})[0], footprint
    )
    published = collision_tiles([shell], [footprint], visual_tiles=[tile])
    assert len(published) == 1


def test_nearby_height_does_not_count_as_residual_evidence():
    building = Building("newer_than_lidar", shapely.box(0, 0, 10, 10))
    points = np.array([[11, 8, 2], [11.2, 8.04, 3]])
    surfaces, evidence = lidar_extrusion(building, points, {"rf_h_ground": 1.0})
    shell, record = choose(building, {"lidar_extrusion": surfaces}, points)
    assert shell is not None and not record["pass"]
    assert record["candidates"]["lidar_extrusion"]["points"] == 0
    assert evidence["height_source"] == "nearest_class6_outside_footprint"
    assert evidence["nearest_support_distance_m"] == 1
    with pytest.raises(ValueError, match="no elevated"):
        lidar_extrusion(building, np.empty((0, 3)), {"rf_h_ground": 1.0})


def test_fallback_retains_courtyard_and_refuses_unknown_ground():
    poly = shapely.Polygon([(0, 0), (10, 0), (10, 10), (0, 10)], [[(3, 3), (3, 7), (7, 7), (7, 3)]])
    building = Building("courtyard", poly)
    points = np.array([[1, 8, 1], [5, 30, 5]])
    surfaces, evidence = lidar_extrusion(building, points, {"rf_h_ground": 0.0})
    shell = make_shell(surfaces, poly)
    assert shell.footprint.equals(poly)
    assert evidence["roof_m"] == 8
    with pytest.raises(ValueError, match="no lidar ground"):
        lidar_extrusion(building, points, {})


def test_surface_distance_measures_facades_as_well_as_roofs():
    from turku3d.buildings.distance import surface_distances
    from turku3d.buildings.fallback import extrusion

    poly = shapely.box(0, 0, 10, 10)
    shell = make_shell(extrusion(poly, 0, 8), poly)
    points = np.array([[0.03, 4, 5], [5, 8.04, 5], [5, 4, 5], [-3, 8, -4]])
    np.testing.assert_allclose(
        surface_distances(shell.positions, shell.indices, points), [0.03, 0.04, 4, 5]
    )
    _, record = choose(Building("walls", poly), {"city": extrusion(poly, 0, 8)}, points[:2])
    assert record["pass"]
    assert record["candidates"]["city"]["metric"] == "euclidean_point_to_shell"
    assert not record["candidates"]["city"]["roof_vertical"]["pass"]


def test_distance_tree_matches_exhaustive_triangle_query():
    from trimesh.triangles import closest_point
    from turku3d.buildings.distance import surface_distances

    rng = np.random.default_rng(103)
    positions = rng.normal(size=(60, 3))
    indices = np.arange(60).reshape(-1, 3)
    points = rng.normal(size=(35, 3))
    expected = np.full(len(points), np.inf)
    for tri in positions[indices]:
        q = closest_point(np.broadcast_to(tri, (len(points), 3, 3)), points)
        expected = np.minimum(expected, np.linalg.norm(q - points, axis=1))
    np.testing.assert_allclose(surface_distances(positions, indices, points), expected, atol=1e-12)


def test_local_overlap_plane_precedes_distant_building_proxy():
    building = Building("under_trees", shapely.box(0, 0, 4, 4))
    x, z = np.meshgrid(np.linspace(0.1, 3.9, 12), np.linspace(0.1, 3.9, 12))
    overlap = np.column_stack((x.ravel(), 6 + 0.02 * x.ravel(), z.ravel()))
    distant = np.array([[55, 32, 2], [55.1, 32.1, 3]])
    surfaces, evidence = lidar_extrusion(building, distant, {"rf_h_ground": 3.0}, overlap)
    assert evidence["height_source"] == "local_overlap_class12_plane"
    assert evidence["roof_m"] == pytest.approx(6.04)
    assert evidence["plane_support_area_ratio"] > 0.8
    assert not evidence["accuracy_certified"]
    _, record = choose(building, {"lidar_extrusion": surfaces}, distant)
    assert not record["pass"] and record["candidates"]["lidar_extrusion"]["points"] == 0


def test_walker_validation_preserves_shared_manifest(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from turku3d import config as C
    from turku3d.buildings import validate
    from turku3d.layers import read_manifest, write_manifest

    monkeypatch.setattr(C, "work_root", lambda: tmp_path)
    layer = tmp_path / "build/layers/buildings"
    layer.mkdir(parents=True)
    (layer / "metrics.json").write_text(json.dumps({"collision_building_ids": []}))
    for name in ("footprints.geojson", "facades.geojson"):
        (layer / name).write_text(json.dumps({"features": []}))
    write_manifest(layer, inputs={"fixture": "source-hash"}, params={}, code="test", tiles=[])

    def run(command):
        result = {
            "pass": True,
            "walkers": 2000,
            "entered": 0,
            "endedInside": 0,
            "elapsed_ms": 1,
            "terrain": "synthetic validation fixture",
        }
        from pathlib import Path

        Path(command[-1]).write_text(json.dumps(result))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(validate.subprocess, "run", run)
    assert validate.main() == 0
    manifest = read_manifest(layer)
    assert manifest["params"]["walker_validation"] == "pass"
    assert manifest["inputs"] == {"fixture": "source-hash"}
    assert manifest["code"] == "test" and manifest["tiles"] == {}
    assert json.loads((layer / "metrics.json").read_text())["walker_validation"]["pass"]
