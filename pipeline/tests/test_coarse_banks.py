"""Shoreline geometry must follow vectors, with matching visible and solid banks."""

import numpy as np
import shapely
from turku3d import config as C
from turku3d.coarse.banks import produce
from turku3d.coarse.build import collision_tiles, grid_mesh
from turku3d.coarse.inputs import CELLS
from turku3d.col.materials import mat_id


def river_mesh(regions=(), barriers=()):
    tile = C.VisualTile(0, 0, 0)
    p, ix = grid_mesh(tile, np.full((CELLS + 1, CELLS + 1), 4.0))
    # Oblique shore, crossing grid diagonals and four collision-tile seams.
    water = shapely.Polygon([(90.3, -20), (130.3, 270), (300, 270), (300, -20)])
    result = produce(
        p,
        ix,
        np.full(len(ix), mat_id("grass"), np.uint8),
        np.ones(len(ix), bool),
        [water],
        0.2,
        regions,
        barriers,
    )
    return tile, water, p, ix, result


def test_exact_shore_has_no_missing_or_overlapping_surface_area():
    _, water, _, _, (dry, river, bank, solid) = river_mesh()
    dry_area = shapely.union_all(shapely.polygons(dry.positions[dry.indices][:, :, [0, 2]]))
    wet_area = shapely.union_all(shapely.polygons(river.positions[river.indices][:, :, [0, 2]]))
    window = shapely.box(0, 0, 250, 250)
    assert wet_area.symmetric_difference(water.intersection(window)).area < 1e-7
    assert dry_area.union(wet_area).symmetric_difference(window).area < 1e-7
    assert dry_area.intersection(wet_area).area < 1e-8
    assert np.all(river.positions[:, 1] == 0.2)
    assert np.max(bank.positions[::4, 1]) < 0.2  # natural slope ends below water
    for mesh in (dry, river):
        t = mesh.positions[mesh.indices]
        assert np.all(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])[:, 1] > 0)
    assert len(solid.indices) == len(dry.indices) + len(bank.indices) + 2 * len(river.indices)


def test_mapped_quay_faces_water_and_matches_collision():
    quay = shapely.LineString([(90.3, -20), (130.3, 270)])
    tile, water, p, ix, (dry, river, bank, solid) = river_mesh(
        [(quay.buffer(0.45), mat_id("quay"))]
    )
    assert np.all(bank.material == mat_id("quay"))
    np.testing.assert_allclose(bank.positions[::4, 1], 4, atol=1e-12)
    triangles = bank.positions[bank.indices]
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    centres = triangles.mean(axis=1)
    into_water = (
        centres[:, [0, 2]]
        + normals[:, [0, 2]] / np.linalg.norm(normals[:, [0, 2]], axis=1)[:, None] * 0.01
    )
    assert shapely.contains_xy(water, into_water[:, 0], into_water[:, 1]).all()
    cols = collision_tiles(tile, p, ix, np.zeros(len(ix), np.uint8), 0.2, [], shore=solid)
    assert sum(len(f) for _, f, _ in cols.values()) == len(solid.indices)
    faces = np.concatenate([pos[f][m == mat_id("quay")] for pos, f, m in cols.values()])
    assert {tuple(t.ravel()) for t in faces} == {tuple(t.ravel()) for t in triangles}
    # Every visible bank top joins the dry ground exactly (including grid cuts).
    dry_points = {tuple(np.round(v, 8)) for v in dry.positions}
    assert all(
        tuple(np.round(v, 8)) in dry_points
        for v in bank.positions.reshape(-1, 4, 3)[:, :2].reshape(-1, 3)
    )


def test_retaining_wall_selects_stone_and_dry_tile_is_unchanged():
    line = shapely.LineString([(90.3, -20), (130.3, 270)])
    *_, (_, _, bank, _) = river_mesh(barriers=[(line, {"barrier": "retaining_wall"})])
    assert np.all(bank.material == mat_id("quay"))
    p = np.array([[0.0, 2.0, 0.0], [0.0, 2.0, 1.0], [1.0, 2.0, 0.0]])
    ix = np.array([[0, 1, 2]], np.uint32)
    dry, river, bank, solid = produce(
        p, ix, np.array([mat_id("grass")], np.uint8), np.array([True]), [], 0
    )
    np.testing.assert_array_equal(dry.positions, p)
    assert not len(river.indices) and not len(bank.indices)
    np.testing.assert_array_equal(solid.indices, ix)


def test_osm_quay_wall_and_embankment_tags_reach_bank_materials(tmp_path):
    import json

    import pyarrow as pa
    import pyarrow.parquet as pq
    from turku3d.coarse.surfaces import osm_surfaces

    tags = [
        {"man_made": "quay"},
        {"man_made": "wall"},
        {"man_made": "embankment"},
        {"barrier": "retaining_wall"},
        {"embankment": "yes"},
    ]
    rows = [
        dict(
            tags=json.dumps(t),
            geometry_local=shapely.LineString([(10, 10 + k * 5), (40, 10 + k * 5)]).wkb,
        )
        for k, t in enumerate(tags)
    ]
    path = tmp_path / "banks.parquet"
    pq.write_table(pa.Table.from_pylist(rows), path)
    regions = osm_surfaces(C.VisualTile(0, 0, 0), path)
    assert len(regions) == len(tags)
    assert all(m == mat_id("quay") for _, m in regions)
