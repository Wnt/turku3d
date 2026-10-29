"""One visible wall owner from source geometry through GPU precision and neighbours."""

import copy

import numpy as np
import pytest
import shapely
from turku3d import config as C
from turku3d.coarse.inputs import Building
from turku3d.coarse.stored_facades import assemble_walls, exposed_wall_quads
from turku3d.facade_program_geometry import wall_parts
from turku3d.facade_programs import compile_wall, measured
from turku3d.package.gltf import GlbMesh
from turku3d.wall_surfaces import overlaps, validate_meshes, validate_neighbours, validate_ownership


def quad(x0=0, x1=10, bottom=0, top=10, z=0):
    return np.array([[x0, bottom, z], [x1, bottom, z], [x1, top, z], [x0, top, z]], float)


def mesh(points, identity="test"):
    return GlbMesh(
        identity, points, np.array([[0, 3, 2], [0, 2, 1]]), extras=dict(building_id=identity)
    )


def test_build_guard_reports_wall_ids_and_near_parallel_triangle_overlap():
    first = mesh(quad(), "first-building")
    second = mesh(quad(z=0.004), "second-building")
    assert validate_meshes([first, second], "t0_0_0")["violations"] == 2
    from turku3d.wall_assembly import assemble_structures

    assert validate_meshes(assemble_structures([first, second]), "t0_0_0")["violations"] == 0
    second.positions[:, 2] = 0.02
    assert validate_meshes([first, second], "t0_0_0")["violations"] == 0


def test_diverging_planes_are_partitioned_only_where_they_can_compete():
    first = quad()
    second = quad(top=12, z=0.004)
    second[1:3, 2] = 0.010
    result = exposed_wall_quads(np.concatenate([first, second])).reshape(-1, 4, 3)
    assert not overlaps(list(result), [str(i) for i in range(len(result))])
    assert any(p[:, 0].min() > 0 and p[:, 1].max() == 10 for p in result)


def test_passage_splits_and_adjacent_buildings_share_wall_assembly():
    b = Building("part", shapely.box(0, 0, 10, 10), 0, 10, "facade_plaster")
    shells = [
        (b, (None, None, None, None, quad())),
        (copy.copy(b), (None, None, None, None, quad(bottom=3, top=12))),
    ]
    pieces = assemble_walls(shells)
    faces = np.concatenate(pieces).reshape(-1, 4, 3)
    assert not overlaps(list(faces), ["passage"] * len(faces))
    assert sum(shapely.Polygon(p[:, :2]).area for p in faces) == pytest.approx(120)


def test_neighbour_admission_rejects_duplicate_owners_and_overlapping_boundary(tmp_path):
    left, right = C.VisualTile(0, 0, 0), C.VisualTile(0, 1, 0)
    b = Building("boundary", shapely.box(240, 0, 260, 10), 0, 10, "facade_plaster")
    assert b.visual_owner == right.id  # half-open centroid ownership
    with pytest.raises(ValueError, match="wall ownership"):
        validate_ownership([(b, None)], left)
    validate_neighbours([mesh(quad(-10, 10, z=10))], [(b, None)], right, tmp_path)
    alias = copy.copy(b)
    alias.visual_owner = left.id
    with pytest.raises(ValueError, match="wall ownership"):
        validate_neighbours([], [(alias, None)], left, tmp_path)
    alias.key = "different-building"
    # Cross-tile overlaps are reported, not fatal: a failed admission left tiles without their textured pass.
    validate_neighbours([mesh(quad(240, 260, z=10))], [(alias, None)], left, tmp_path)


@pytest.mark.parametrize("identity", ["103277347A", "103304667L", "1032865783", "1032865794"])
def test_real_reviewed_blanko_linnankatu_13_and_11_walls(identity):
    """Use existing reviewed descriptors; never copy private city geometry into git."""
    program = measured(identity)
    if program is None:
        pytest.skip("reviewed facade publication is not available in this environment")
    tested = 0
    for source in program["walls"]:
        if source.get("observation", {}).get("needs_review", True):
            continue
        wall = copy.deepcopy(source)
        geometry = wall["geometry"]
        coordinates = geometry.get("coordinates") or [geometry["p0"], geometry["p1"]]
        en = np.asarray(coordinates, float)[:, :2]
        endpoints = np.c_[en[:, 0] - C.ORIGIN_E, en[:, 1] * 0, C.ORIGIN_N - en[:, 1]]
        bottom = geometry.get("base_h", 0)
        vertices = endpoints[[0, 1, 1, 0]].copy()
        vertices[:, 1] = [bottom, bottom, bottom + wall["height_m"], bottom + wall["height_m"]]
        wall["measured_geometry"] = geometry
        wall["measured_size"] = [wall["width_m"], wall["height_m"]]
        parts = list(wall_parts(vertices, wall, compile_wall(wall, program["storeys"]["value"])))
        faces = [p for p, _, _ in parts]
        assert not overlaps(
            faces, [f"{identity}/{source['wall_id']}/{i}" for i in range(len(parts))]
        )
        # Paint must cover the entire wall exactly once, including all openings/rails.
        expected = np.linalg.norm(endpoints[1] - endpoints[0]) * wall["height_m"]
        assert sum(shapely.Polygon(uv).area for _, uv, _ in parts) == pytest.approx(
            expected, abs=1e-6
        )
        tested += 1
    assert tested


def test_structural_wall_assembly_preserves_exposed_area_uvs_and_facade_owner():
    from turku3d.wall_assembly import assemble_structures

    facade = mesh(quad(x1=6), "reviewed-facade")
    portal = mesh(quad(x0=4, z=0.001), "portal")
    portal.uvs = portal.positions[:, :2].copy()
    exposed = assemble_structures([portal], [facade])
    assert validate_meshes([facade, *exposed], "fixture")["violations"] == 0
    triangles = exposed[0].positions[exposed[0].indices]
    assert sum(
        np.linalg.norm(np.cross(p[1] - p[0], p[2] - p[0])) / 2 for p in triangles
    ) == pytest.approx(40)
    np.testing.assert_allclose(exposed[0].uvs, exposed[0].positions[:, :2])
    assert exposed[0].extras["building_id"] == "portal"
    np.testing.assert_array_equal(facade.positions, quad(x1=6))


def test_shared_frontage_has_same_owner_from_either_neighbour():
    a = Building("a", shapely.box(240, 0, 250, 10), 0, 10, "facade_plaster")
    b = Building("b", shapely.box(250, 0, 260, 10), 0, 10, "facade_plaster")
    first = (a, (None, None, None, None, quad(240, 260)))
    second = (b, (None, None, None, None, quad(240, 260, z=0.001)))
    left = assemble_walls([first], [second])[0]
    right = assemble_walls([second], [first])[0]
    assert len(left) == 4 and not len(right)


def test_back_to_back_party_walls_do_not_compete():
    first = mesh(quad())
    second = mesh(quad(z=0.001))
    second.indices = second.indices[:, ::-1]
    assert validate_meshes([first, second], "fixture")["violations"] == 0


@pytest.mark.parametrize("pass_kind,content_hash", [("first", "v1"), ("full", "v2")])
def test_neighbours_ignore_other_passes_and_versions(tmp_path, pass_kind, content_hash):
    left, right = C.VisualTile(0, 0, 0), C.VisualTile(0, 1, 0)
    validate_neighbours(
        [mesh(quad(240, 260))], [], left, tmp_path, pass_kind="full", content_hash="v1"
    )
    meshes = [mesh(quad(-10, 10))]
    result = validate_neighbours(
        meshes, [], right, tmp_path, pass_kind=pass_kind, content_hash=content_hash
    )
    assert result["resolved"] == 0
    assert len(meshes) == 1


def test_matching_neighbour_removes_duplicate_without_failing(tmp_path):
    import json

    left, right = C.VisualTile(0, 0, 0), C.VisualTile(0, 1, 0)
    validate_neighbours([mesh(quad(240, 260))], [], left, tmp_path, content_hash="v1")
    meshes = [mesh(quad(-10, 10))]
    result = validate_neighbours(meshes, [], right, tmp_path, content_hash="v1")
    assert result["resolved"] == 2
    assert not meshes
    record = json.loads((tmp_path / ".wall-surfaces" / "full" / f"{right.id}.json").read_text())
    assert record["pass_kind"] == "full" and record["content_hash"] == "v1"


def test_sloping_wall_plane_uses_actual_surface_not_vertical_proxy():
    first = quad()
    first[2:, 2] = 0.05
    second = first.copy()
    second[:, 2] += 0.02
    assert not overlaps([first, second], ["a", "b"])
