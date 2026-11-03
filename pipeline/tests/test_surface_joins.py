"""Reconstructed quay surfaces meet the same ground and bank in visuals/collision."""

import numpy as np
import shapely
from turku3d.ground.mesh import Mesh, HeightQuery
from turku3d.structures.geometry import Faces
from turku3d.coarse.gardens import horizontal, mesh
from turku3d.coarse import surface_join as joins
from turku3d.col.materials import mat_id


def plane(area, y, material="grass"):
    f = Faces()
    horizontal(f, area, y)
    return mesh(f, material)


def test_replacement_and_dry_side_loft_share_exact_boundary_heights_and_holes():
    domain = shapely.box(0, 0, 20, 20).difference(shapely.box(12, 12, 14, 14))
    base = plane(domain, 3)
    patch = joins.on_terrain(plane(shapely.box(4, 4, 8, 16), 1), base)
    result = joins.stitch(base, patch, base)
    q = HeightQuery(result)
    np.testing.assert_allclose(q([[4, 8], [3, 8], [2, 8], [6, 8]]), [1, 2, 3, 1], atol=1e-6)
    # Between constraints the loft is triangulated. Crossing its shared edge
    # by one millimetre must remain continuous, without a vertical opening.
    assert 0 < q([[3.999, 8]])[0] - q([[4, 8]])[0] < 0.002
    assert np.isnan(q([[13, 13]])[0])
    polygons, valid = joins.footprint(result)
    assert shapely.union_all(polygons[valid]).symmetric_difference(domain).area < 1e-8


def test_patch_is_cut_on_actual_material_edges_and_cannot_cover_water():
    from turku3d.coarse.banks import join

    base = join(plane(shapely.box(0, 0, 4, 8), 3, "cobblestone"), plane(shapely.box(4, 0, 8, 8), 3))
    patch = joins.on_terrain(plane(shapely.box(-2, 2, 10, 6), 1), base)
    polygons, valid = joins.footprint(patch)
    assert (
        shapely.union_all(polygons[valid]).symmetric_difference(shapely.box(0, 2, 8, 6)).area < 1e-8
    )
    centres = patch.positions[patch.indices].mean(axis=1)
    assert np.all(patch.material[centres[:, 0] < 4] == mat_id("cobblestone"))
    assert np.all(patch.material[centres[:, 0] > 4] == mat_id("grass"))


def test_bank_crest_follows_final_quay_but_its_submerged_base_stays():
    base = plane(shapely.box(0, 0, 10, 10), 3)
    target = plane(shapely.box(0, 0, 10, 10), 1.2)
    bank = Mesh(
        np.array([[0, 3, 0], [0, 3, 10], [0, -2, 0], [0, -2, 10]], float),
        np.array([[0, 1, 2], [1, 3, 2]], np.uint32),
        np.full(2, mat_id("quay"), np.uint8),
    )
    fixed = joins.align_upper(bank, base, target)
    np.testing.assert_allclose(fixed.positions[:, 1], [1.2, 1.2, -2, -2])
    triangles = fixed.positions[fixed.indices]
    area = (
        np.linalg.norm(
            np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1
        ).sum()
        / 2
    )
    assert abs(area - 32) < 1e-8


def test_ground_stair_landing_stops_at_dry_shore_and_keeps_a_landing_hole():
    from turku3d.coarse import stairs
    from turku3d import config as C
    from turku3d.coarse.kerbs import terrain_patches

    land = shapely.box(0, 0, 20, 10)
    line = shapely.LineString([(5, 5), (5, 10)])
    prepared = (
        [("flight", line, {"_profile": {"stations": [0, 5], "heights": [3, 1]}, "handrail": "no"})],
        [],
    )
    _, cuts = stairs.produce(C.VisualTile(0, 0, 0), prepared, lambda x, z: 1, landing_domain=land)
    assert shapely.union_all(cuts).difference(land).area < 1e-9
    area = shapely.box(0, 0, 4, 4).difference(shapely.box(1, 1, 2, 2))
    pieces = [shapely.Polygon(p) for p in terrain_patches(area, 3)]
    assert shapely.union_all(pieces).symmetric_difference(area).area < 1e-9


def test_source_query_keeps_each_side_of_a_shared_crest_at_its_own_level():
    from turku3d.coarse.banks import join

    source = join(plane(shapely.box(0, 0, 4, 4), 1), plane(shapely.box(0, 0, 4, 4), 3))
    q = HeightQuery(source)
    np.testing.assert_allclose(q([[2, 2], [2, 2]], reference=[1, 3]), [1, 3])


def test_near_edge_query_uses_metres_even_on_thin_overlay_triangles():
    p = np.array([[0, 1, 0], [8, 1, 0], [0, 1, 0.0001]])
    m = Mesh(p, np.array([[0, 1, 2]], np.uint32), np.array([8], np.uint8))
    assert np.isnan(HeightQuery(m)([[1, -1e-8]])[0])
    assert abs(HeightQuery(m, tolerance=1e-7)([[1, -1e-8]])[0] - 1) < 1e-8


def test_metric_near_hit_stays_on_the_edge_of_a_steep_sliver():
    p = np.array([[0, 1, 0], [8, 3, 0], [0, 2, 1e-9]])
    m = Mesh(p, np.array([[0, 1, 2]], np.uint32), np.array([8], np.uint8))
    assert abs(HeightQuery(m, tolerance=1e-7)([[1, -1e-8]])[0] - 1.25) < 1e-8


def test_bank_crest_inserts_an_interior_floor_break_and_ends_at_missing_floor():
    from turku3d.coarse.banks import join

    base = plane(shapely.box(0, 0, 10, 10), 3)
    target = join(plane(shapely.box(0, 0, 10, 5), 1), plane(shapely.box(0, 5, 10, 9), 1))
    target.positions[:, 1] = 1 + np.minimum(target.positions[:, 2], 10 - target.positions[:, 2]) / 5
    bank = Mesh(
        np.array([[0, 3, 0], [0, 3, 10], [0, -2, 0], [0, -2, 10]], float),
        np.array([[0, 1, 2], [1, 3, 2]], np.uint32),
        np.full(2, mat_id("quay"), np.uint8),
    )
    fixed = joins.align_upper(bank, base, target)
    assert abs(fixed.positions[abs(fixed.positions[:, 2] - 5) < 1e-8, 1].max() - 2) < 1e-8
    assert fixed.positions[:, 2].max() <= 9 + 1e-8
    assert fixed.positions[:, 1].min() == -2


def test_join_changes_invalidate_both_visual_and_collision_artifacts(monkeypatch):
    from pathlib import Path
    from turku3d.coarse import packaging, collision_key

    path = Path(joins.__file__)
    read = Path.read_bytes
    before = packaging.code_hash(), collision_key.code_hash()
    monkeypatch.setattr(
        Path, "read_bytes", lambda p: read(p) + b"\n# changed join\n" if p == path else read(p)
    )
    assert packaging.code_hash() != before[0]
    assert collision_key.code_hash() != before[1]


def test_bank_crest_ignores_floor_slivers_that_are_collinear_in_plan(monkeypatch):
    # t0_2_0 (farm build 30bf0cb2): this floor triangle passes the slope test
    # only through rounding noise; Linux LAPACK reports it as singular.
    sliver = np.array([
        [647.9223310179535, 11.474038265755487, 106.69282724168175],
        [648.4375, 11.485384384867123, 105.72849962723546],
        [648.3935322020116, 11.484416034887913, 105.81080148007261],
    ])
    solve = np.linalg.solve

    def strict(a, b):
        a = np.asarray(a, float)
        det = a[..., 0, 0] * a[..., 1, 1] - a[..., 0, 1] * a[..., 1, 0]
        if np.any(abs(det) <= 1e-9 * np.linalg.norm(a[..., 0, :], axis=-1)
                  * np.linalg.norm(a[..., 1, :], axis=-1)):
            raise np.linalg.LinAlgError("Singular matrix")
        return solve(a, b)

    monkeypatch.setattr(np.linalg, "solve", strict)
    from turku3d.coarse.banks import join

    target = join(
        plane(shapely.box(646, 104, 650, 108), 11.48),
        Mesh(sliver, np.array([[0, 1, 2]], np.uint32), np.zeros(1, np.uint8)),
    )
    base = plane(shapely.box(646, 104, 650, 108), 13)
    (x0, _, z0), (x1, _, z1) = sliver[0], sliver[1]
    bank = Mesh(
        np.array([[x0, 13, z0], [x1, 13, z1], [x0, 8, z0], [x1, 8, z1]], float),
        np.array([[0, 1, 2], [1, 3, 2]], np.uint32),
        np.full(2, mat_id("quay"), np.uint8),
    )
    fixed = joins.align_upper(bank, base, target)
    heights = fixed.positions[:, 1]
    assert np.all(np.isfinite(heights))
    np.testing.assert_allclose(np.sort(np.unique(np.round(heights, 6))), [8, 11.48])
