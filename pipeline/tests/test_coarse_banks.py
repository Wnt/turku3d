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


def test_small_pond_has_its_own_level_shallow_bed_and_gentle_grassy_bank():
    from turku3d.coarse.banks import small_bodies

    tile = C.VisualTile(0, 0, 0)
    p, ix = grid_mesh(tile, np.full((CELLS + 1, CELLS + 1), 23.0))
    pond = shapely.box(80.3, 80.3, 110.3, 100.3)
    river = shapely.box(180, -50, 280, 300)
    assert len(small_bodies([pond, river])) == 1
    assert small_bodies([pond, river])[0].equals(pond)
    dry, wet, bank, solid = produce(
        p, ix, np.full(len(ix), mat_id("grass"), np.uint8), np.ones(len(ix), bool),
        [pond, river], 0.2, ponds=[(pond, 22.65)],
    )
    inside = shapely.contains_xy(pond.buffer(1e-7), wet.positions[:, 0], wet.positions[:, 2])
    assert np.all(wet.positions[inside, 1] == 22.65)
    assert np.all(wet.positions[~inside, 1] == 0.2)
    faces = bank.positions[bank.indices]
    normals = np.cross(faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0])
    assert np.all(np.abs(normals[bank.material == mat_id("grass"), 1]) > 0)
    near = shapely.contains_xy(pond.buffer(2), dry.positions[:, 0], dry.positions[:, 2])
    assert np.max(dry.positions[near, 1]) - 22.65 <= 0.35 + 1e-8
    tri = dry.positions[dry.indices]
    centres = tri.mean(axis=1)
    near_tri = shapely.contains_xy(pond.buffer(4), centres[:, 0], centres[:, 2])
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])[near_tri]
    slope = np.linalg.norm(normal[:, [0, 2]], axis=1) / np.abs(normal[:, 1])
    assert np.max(slope) <= np.tan(np.deg2rad(30))
    lip_tri = shapely.contains_xy(pond.buffer(2), centres[:, 0], centres[:, 2])
    assert np.all(dry.material[lip_tri] == mat_id("grass"))
    wet_solid = solid.positions[shapely.contains_xy(pond.buffer(1e-7), solid.positions[:, 0], solid.positions[:, 2])]
    assert wet_solid[:, 1].min() >= 22.0 - 1e-8


def test_small_pond_keeps_mapped_retaining_edge():
    p, ix = grid_mesh(C.VisualTile(0, 0, 0), np.full((CELLS + 1, CELLS + 1), 23.0))
    pond = shapely.box(80.3, 80.3, 110.3, 100.3)
    *_, bank, _ = produce(
        p, ix, np.full(len(ix), mat_id("grass"), np.uint8), np.ones(len(ix), bool),
        [pond], 0.2, [(pond.boundary.buffer(0.5), mat_id("quay"))], ponds=[(pond, 22.65)],
    )
    assert len(bank.indices)
    stone = bank.positions[bank.indices[bank.material == mat_id("quay")]]
    assert len(stone)
    np.testing.assert_allclose(stone[:, :, 1].max(axis=1), 23)


def test_spawn_neighbours_do_not_turn_a_compact_river_reach_into_a_pond(tmp_path):
    from types import SimpleNamespace

    import pyarrow as pa
    import pyarrow.parquet as pq
    from turku3d.coarse.banks import pond_bodies, small_bodies
    from turku3d.coarse.inputs import _frame

    # The short reach straddles the eastern spawn tiles. Its long continuation
    # lies west of them and is absent from their tile-filtered water inputs.
    reach = shapely.box(230, 215, 310, 255)
    continuation = shapely.box(-200, 255, 245, 315)
    pond = shapely.box(350, 350, 365, 365)
    path = tmp_path / "water.parquet"
    pq.write_table(pa.Table.from_pylist([
        dict(geometry_local=g.wkb) for g in (reach, continuation, pond)
    ]), path)
    for tile_id in ("t0_0_0", "t0_0_1", "t0_1_0", "t0_1_1"):
        tile = C.VisualTile.parse(tile_id)
        water = [g for g, _ in _frame(path, tile.bbox)]
        inputs = SimpleNamespace(water=water, sources={"wfs": [None, None, str(path)]})
        bodies = pond_bodies(inputs)
        assert all(g.equals(pond) for g in bodies), tile_id
        if tile_id == "t0_1_0":
            assert small_bodies(water)  # Reproduces the old tile-dependent decision.
            assert bodies == []
        if tile_id == "t0_1_1":
            assert len(bodies) == 1 and bodies[0].equals(pond)


def test_small_flowing_water_and_sea_fragments_are_never_ponds(tmp_path):
    import json
    from types import SimpleNamespace

    import pyarrow as pa
    import pyarrow.parquet as pq
    from turku3d.coarse.banks import pond_bodies

    body = shapely.box(260, 220, 300, 250)
    water = tmp_path / "water.parquet"
    osm = tmp_path / "osm.parquet"
    pq.write_table(pa.Table.from_pylist([dict(geometry_local=body.wkb)]), water)
    inputs = SimpleNamespace(water=[body], sources={
        "wfs": [None, None, str(water)], "roads": [str(osm)],
    })
    for tags in ({"water": "river"}, {"water": "sea"}, {"waterway": "stream"}):
        pq.write_table(pa.Table.from_pylist([
            dict(tags=json.dumps(tags), geometry_local=body.exterior.wkb)
        ]), osm)
        assert pond_bodies(inputs) == []


def test_open_water_joins_survey_breaks_and_remnant_islands_across_tile_edges(tmp_path):
    import json

    import pyarrow as pa
    import pyarrow.parquet as pq
    from turku3d.coarse.build import water_level
    from turku3d.coarse.water import LEVEL, flowing_areas, prepare

    river = shapely.box(200, -10, 300, 400)
    sea = shapely.box(200, 400, 300, 600)
    island = shapely.box(260, 340, 270, 350)
    remnant = shapely.box(248, 244, 252, 248)
    pond = shapely.box(50, 50, 65, 65)
    # A survey break crosses a visual tile edge; a tiny survey-only hole leaves
    # a block of dry terrain. The independent mapped island must stay dry.
    survey = tmp_path / "water.parquet"
    pq.write_table(pa.Table.from_pylist([
        dict(geometry_local=g.wkb) for g in (
            river.intersection(shapely.box(0, -20, 500, 249)).difference(remnant),
            river.intersection(shapely.box(0, 251, 500, 410)).difference(island),
            sea, pond,
        )
    ]), survey)
    osm = tmp_path / "osm.parquet"
    pq.write_table(pa.Table.from_pylist([
        dict(tags=json.dumps({"water": kind}), geometry_local=g.wkb)
        for g, kind in ((river.difference(island), "river"), (sea, "sea"))
    ]), osm)
    interior = shapely.union_all(flowing_areas(osm)).buffer(-3.01)
    for tile in (C.VisualTile(0, 0, 0), C.VisualTile(0, 0, 1)):
        water = prepare(tile, survey, osm)
        body = shapely.union_all(water)
        assert body.covers(remnant)
        assert body.intersection(island).area == 0
        if tile.j == 0:
            assert body.intersection(pond).equals(pond)
        # Different shoreline samples cannot move either tile's open water.
        level = water_level(np.array([[250, 1 + tile.j * 8, 250]]), water)
        assert level == LEVEL
        p, ix = grid_mesh(tile, np.full((CELLS + 1, CELLS + 1), 4.0))
        dry, wet, bank, _ = produce(
            p, ix, np.full(len(ix), mat_id("grass"), np.uint8), np.ones(len(ix), bool),
            water, level,
        )
        assert np.all(wet.positions[:, 1] == LEVEL)
        for mesh in (dry, bank):
            centres = mesh.positions[mesh.indices].mean(axis=1)
            assert not shapely.contains_xy(interior, centres[:, 0], centres[:, 2]).any()


def test_survey_coverage_edge_over_open_water_is_never_a_tile_boundary_bank():
    tile = C.VisualTile(0, 0, 0)
    p, ix = grid_mesh(tile, np.full((CELLS + 1, CELLS + 1), .2))
    dry, wet, bank, _ = produce(
        p, ix, np.full(len(ix), mat_id("grass"), np.uint8), np.ones(len(ix), bool),
        [shapely.box(*tile.bbox.as_tuple())], 0,
    )
    assert not len(dry.indices) and not len(bank.indices)
    assert len(wet.indices) == len(ix) and np.all(wet.positions[:, 1] == 0)


def test_retaining_cut_masks_stay_polygonal():
    """A degenerate cut leaves a line sliver; GEOS cannot subtract a mixed-dimension mask from an
    empty polygon (t0_3_1 failed its full pass on it), so cuts keep only their areas."""
    import pytest
    import shapely
    from turku3d.coarse.retaining import _areas

    mixed = shapely.GeometryCollection([shapely.box(0, 0, 1, 1), shapely.LineString([(2, 0), (3, 0)])])
    with pytest.raises(shapely.errors.GEOSException):
        shapely.Polygon().difference(mixed)
    mask = _areas(mixed)
    assert mask.equals(shapely.box(0, 0, 1, 1))
    assert shapely.Polygon().difference(mask).is_empty
    assert _areas(shapely.GeometryCollection([shapely.LineString([(0, 0), (1, 0)])])).is_empty
    square = shapely.box(0, 0, 2, 2)
    assert _areas(square) is square
