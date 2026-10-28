"""Footing corrections preserve the shared shell and leave elevated walls alone."""

from dataclasses import replace

import numpy as np
import pytest
import trimesh
from shapely.geometry import LineString, box
from turku3d.buildings.facades import make_visuals
from turku3d.buildings.fallback import extrusion
from turku3d.buildings.grounding import ground_contact
from turku3d.buildings.inputs import Building
from turku3d.buildings.shells import Surface, make_shell


def shell(base=0.464):
    footprint = box(0, 0, 4, 4)
    return make_shell(extrusion(footprint, base, 8), footprint)


def test_floating_footing_meets_terrain_without_moving_roof_or_breaking_closure():
    original = shell()
    fitted, drop = ground_contact(original, lambda p: (0.0, 1))
    assert drop == pytest.approx(0.464)
    assert fitted.positions[:, 1].min() == pytest.approx(0)
    assert np.array_equal(fitted.indices, original.indices)
    roof = original.positions[:, 1] == 8
    assert np.array_equal(fitted.positions[roof], original.positions[roof])
    assert trimesh.Trimesh(fitted.positions, fitted.indices).is_watertight
    # Visuals and their map binding dimensions come from the same corrected vertices.
    _, facades = make_visuals(Building("b", fitted.footprint), fitted, LineString(), [], "roof")
    assert all(f["properties"]["base_height"] == pytest.approx(0) for f in facades)
    assert all(f["properties"]["top_height"] == pytest.approx(8) for f in facades)
    assert original.positions[:, 1].min() == pytest.approx(0.464)


def test_elevated_wall_without_ground_surface_contact_is_not_fitted():
    original = shell(base=0)
    vertices = np.array([[0, 0.5, 2], [4, 0.5, 2], [4, 2, 2]], float)
    elevated = replace(
        original,
        positions=np.concatenate([original.positions, vertices]),
        indices=np.vstack([original.indices, np.arange(3) + len(original.positions)]),
        surface_ids=np.append(original.surface_ids, len(original.surfaces)),
        surfaces=[*original.surfaces, Surface([vertices], "WallSurface")],
    )
    fitted, drop = ground_contact(elevated, lambda p: (0.0, 1))
    assert fitted is elevated and drop == 0


@pytest.mark.parametrize("depth", [0.812, 1.015])
def test_local_depression_requests_capped_extension_with_nearby_ground_support(depth):
    original = shell(base=0)

    def terrain(point):
        # The four-metre wall's midpoint is lower than its supported endpoints.
        return (-depth if np.linalg.norm(point - [2, -0.4]) < 0.01 else 0.0, 1)

    fitted, drop = ground_contact(original, terrain)
    assert drop == pytest.approx(0.75)
    assert fitted.positions[:, 1].min() == pytest.approx(-0.75)
    assert depth - drop < 0.3
    assert trimesh.Trimesh(fitted.positions, fitted.indices).is_watertight
    roof = original.positions[:, 1] == 8
    assert np.array_equal(fitted.positions[roof], original.positions[roof])


def test_sampling_includes_midpoint_when_two_metre_spacing_would_skip_it():
    footprint = box(0, 0, 6, 4)
    original = make_shell(extrusion(footprint, 0, 8), footprint)

    def terrain(point):
        return (-0.5 if np.linalg.norm(point - [3, -0.4]) < 0.01 else 0.0, 1)

    _, drop = ground_contact(original, terrain)
    assert drop == pytest.approx(0.5)


@pytest.mark.parametrize("terrain", [None, (-2.0, 1), (1.0, 1)])
def test_missing_far_below_or_higher_ground_does_not_move_the_shell(terrain):
    original = shell(base=0)
    fitted, drop = ground_contact(original, lambda p: terrain)
    assert fitted is original and drop == 0


def test_publication_uses_ground_for_collision_facade_inventory_and_cache_input(
    tmp_path, monkeypatch
):
    import hashlib
    import json

    from turku3d.buildings import build

    footprint = box(1, 1, 5, 5)
    original = make_shell(extrusion(footprint, 0.464, 8), footprint)
    ground = tmp_path / "ground"
    ground.mkdir()
    positions = np.array([[0, 0, 0], [6, 0, 0], [6, 0, 6], [0, 0, 6]], float)
    np.savez(
        ground / "c_0_0.col.npz",
        positions=positions,
        indices=np.array([[0, 1, 2], [0, 2, 3]]),
        material=np.array([1, 1]),
    )
    source = ground / "MANIFEST.json"
    source.write_text('{"synthetic": true}')
    monkeypatch.setattr(build, "ortho_roof_key", lambda *args: ("roof_metal_gray", {}))
    record = {"building_id": "b", "choice": "city", "pass": True, "candidates": {"city": {}}}
    output = tmp_path / "buildings"
    build.publish(
        [Building("b", footprint)], {"b": original}, [record], output, LineString(), [], {}, {}
    )
    inventory = json.loads((output / "facades.geojson").read_text())
    assert all(f["properties"]["base_height"] == pytest.approx(0) for f in inventory["features"])
    with np.load(output / "c_0_0.col.npz") as collision:
        assert collision["positions"][:, 1].min() == pytest.approx(0)
        assert collision["positions"][:, 1].max() == pytest.approx(8)
    manifest = json.loads((output / "MANIFEST.json").read_text())
    assert manifest["inputs"][str(source)] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert record["ground_extension_m"] == pytest.approx(0.464)
