"""Courtyard pool outlines, rim collision and terrace seams stay source-driven."""

import json

import numpy as np
import shapely
from turku3d import config as C
from turku3d.coarse import gardens, retaining
from turku3d.coarse.build import grid_mesh
from turku3d.coarse.garden_inputs import features, flat_level, prepare
from turku3d.coarse.inputs import CELLS
from turku3d.col.materials import mat_id
from turku3d.ground.mesh import Mesh


def test_pool_cut_has_exact_water_rim_and_identical_solid_faces_across_tile_seam():
    pool = shapely.Polygon([(244, 40), (252, 44), (258, 32), (250, 28)])
    scene = dict(pools=[(pool, 9.0, "osm:pool")], terraces=[(pool.buffer(2), 9.0)])
    wet_area = []
    seams = []
    for tile in (C.VisualTile(0, 0, 0), C.VisualTile(0, 1, 0)):
        p, ix = grid_mesh(tile, np.full((CELLS + 1, CELLS + 1), 9.0))
        dry = Mesh(p, ix, np.full(len(ix), mat_id("grass"), np.uint8))
        empty = Mesh(np.empty((0, 3)), np.empty((0, 3), np.uint32), np.empty(0, np.uint8))
        dry, wet, _, solid = gardens.produce(tile, scene, dry, empty, empty, dry)
        area = shapely.union_all(shapely.polygons(wet.positions[wet.indices][:, :, [0, 2]]))
        assert (
            area.symmetric_difference(pool.intersection(shapely.box(*tile.bbox.as_tuple()))).area
            < 1e-8
        )
        wet_area.append(area)
        assert np.all(wet.positions[:, 1] == 8.88)
        stone = dry.positions[dry.indices[dry.material == mat_id("quay")]]
        assert len(stone)
        assert stone[:, :, 1].max() == 9.06
        solid_stone = solid.positions[solid.indices[solid.material == mat_id("quay")]]
        np.testing.assert_array_equal(stone, solid_stone)
        dry_area = shapely.union_all(shapely.polygons(dry.positions[dry.indices][:, :, [0, 2]]))
        assert dry_area.intersection(area).area < 1e-8
        assert (
            dry_area.union(area).symmetric_difference(shapely.box(*tile.bbox.as_tuple())).area
            < 1e-7
        )
        seams.append({tuple(p) for p in wet.positions if p[0] == 250})
    assert wet_area[0].union(wet_area[1]).symmetric_difference(pool).area < 1e-8
    assert seams[0] == seams[1] and len(seams[0]) == 2


def test_small_water_requires_garden_context_and_rivers_remain_unmodified(tmp_path, monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq

    pool, pond = shapely.box(30, 30, 42, 34), shapely.box(180, 180, 195, 190)
    rows = [
        (pool, {"natural": "water"}),
        (pond, {"natural": "water"}),
        (shapely.box(5, 5, 20, 100), {"natural": "water", "water": "river"}),
        (shapely.box(30, 36, 45, 45), {"leisure": "garden"}),
    ]
    for k, tags in enumerate(
        ({"leisure": "swimming_pool"}, {"water": "reflecting_pool"}, {"man_made": "basin"})
    ):
        rows.append((shapely.box(60 + k * 20, 30, 70 + k * 20, 35), tags))
    path = tmp_path / "osm.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                dict(osm_id=str(k), tags=json.dumps(t), geometry_local=g.wkb)
                for k, (g, t) in enumerate(rows)
            ]
        ),
        path,
    )
    monkeypatch.setattr(C, "laz_tiles_for_bbox", lambda bbox: [])
    x, z = np.meshgrid(np.arange(0, 250, 0.4), np.arange(0, 250, 0.4))
    points = np.c_[x.ravel(), np.full(x.size, 8.0), z.ravel()]
    monkeypatch.setattr("turku3d.ground.inputs.read_returns", lambda *args: (points, None))
    scene = prepare(C.VisualTile(0, 0, 0), path, [])
    assert len(scene["pools"]) == 4
    assert any(g.equals(pool) for g, _, _ in scene["pools"])
    assert not any(g.equals(pond) for g, _, _ in scene["pools"])
    assert all(level == 8 for _, level, _ in scene["pools"])
    assert len(features(C.VisualTile(0, 0, 0), path)[1]) == 5


def test_terrace_rejects_slope_and_preserves_halo_continuity():
    x, z = np.meshgrid(np.arange(10, 20, 0.5), np.arange(10, 20, 0.5))
    region = shapely.box(10, 10, 20, 20)
    points = np.c_[x.ravel(), np.full(x.size, 9.0), z.ravel()]
    assert flat_level(points, region) == 9
    points[:, 1] = points[:, 0] / 2
    assert flat_level(points, region) is None
    p = np.array([[15.0, 10, 15], [20, 10, 15], [20.75, 10, 15], [21.5, 10, 15], [30, 10, 15]])
    graded = gardens.grade(p, dict(terraces=[(region, 9)]))
    np.testing.assert_array_equal(graded[:, 1], [9, 9, 9.5, 10, 10])


def test_generic_wall_uses_measured_crossfall_but_never_overlays_water_or_stairs():
    line = shapely.LineString([(20, 20), (40, 20)])
    records = [(line, {"barrier": "wall"}, False)]

    def sample(x, z):
        return 3 + np.clip(z - 20, -2, 2)

    tile = C.VisualTile(0, 0, 0)
    assert retaining.produce(tile, records, sample)[0]
    assert retaining.produce(tile, records, lambda x, z: 3) == ([], [])
    assert retaining.produce(tile, records, sample, water=[line.buffer(1)]) == ([], [])
    assert retaining.produce(tile, records, sample, stairs=[line.buffer(1)]) == ([], [])
