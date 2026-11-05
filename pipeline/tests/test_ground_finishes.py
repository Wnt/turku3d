"""Surveyed finish distinctions remain visual-only and generate periodic PBR maps."""
from types import SimpleNamespace

import numpy as np
import shapely

from turku3d import config as C
from turku3d.col.materials import mat_id
from turku3d.coarse import street_materials as finishes, ground_finishes, inputs, surfaces


def test_finish_covers_interior_triangles_as_well_as_cut_boundary_triangles():
    p = np.array([[0, 0, 0], [0, 0, 2], [2, 0, 0], [2, 0, 2]], float)
    ix = np.array([[0, 1, 2], [1, 3, 2]], np.uint32)
    material = np.full(2, mat_id('cobblestone'), np.uint8)
    for region in [shapely.box(-1, -1, 3, 3), shapely.box(-1, -1, 1.5, 3)]:
        pos, indices, ids, _ = surfaces.split_boundaries(
            p, ix, material, np.ones(2, bool), [(region, finishes.FIELDSTONE)])
        centers = pos[indices].mean(axis=1)
        inside = shapely.contains_xy(region, centers[:, 0], centers[:, 2])
        assert np.all(ids[inside] == finishes.FIELDSTONE)
        assert np.all(ids[~inside] == mat_id('cobblestone'))
    _, _, physical, _ = surfaces.split_boundaries(
        p, ix, material, np.ones(2, bool), [(region, finishes.FIELDSTONE)], preserve_materials=True)
    assert np.all(physical == mat_id('cobblestone'))
    assert np.all(material == mat_id('cobblestone'))


def test_survey_finishes_preserve_collision_and_polygon_boundaries(monkeypatch):
    stone, red, ordinary = [shapely.box(i * 10, 0, (i + 1) * 10, 10) for i in range(3)]
    rows = [(stone, SimpleNamespace(Katuosalaji='Ajorata', Pintamateriaali='Kenttäkivi')),
            (red, SimpleNamespace(Katuosalaji='Pyöräkaista', Pintamateriaali='Asfalttibetoni, punainen (AB 16, rautaoksidi FeO2)')),
            (ordinary, SimpleNamespace(Katuosalaji='Ajorata', Pintamateriaali='Asfalttibetoni'))]
    monkeypatch.setattr(inputs, '_frame', lambda path, bounds: rows if path == 'streets' else [])
    result = inputs.prepare_wfs(C.VisualTile(0, 0, 0), None, 'streets', None)
    assert [m for _, m in result['streets']] == [mat_id('cobblestone'), mat_id('cycleway'), mat_id('road')]
    assert result['street_finishes'] == [(stone, finishes.FIELDSTONE), (red, finishes.RED_ASPHALT)]
    for polygon, material in result['street_finishes']:
        p = np.array([[polygon.bounds[0], 0, 0], [polygon.bounds[2], 0, 0], [polygon.bounds[0], 0, 10]], float)
        mesh = surfaces.visuals(C.VisualTile(0, 0, 0), p, np.array([[0, 2, 1]]), np.array([material]), np.array([True]))[0]
        assert mesh.extras['material_key'] == 'draft_' + finishes.FINISH_KEYS[material]


def test_procedural_finishes_are_repeatable_finite_pbr_with_metric_relief(tmp_path):
    for key in finishes.FINISH_KEYS.values():
        maps = ground_finishes.maps(key, 128)
        for slot, pixels in maps.items():
            np.testing.assert_array_equal(pixels, ground_finishes.maps(key, 128)[slot])
            assert pixels.shape == (128, 128, 3) and pixels.dtype == np.uint8
            # Wrapped edges behave like interior neighbours, rather than a visible seam.
            edge = np.abs(pixels[:, 0].astype(float) - pixels[:, -1]).mean()
            interior = np.abs(np.diff(pixels.astype(float), axis=1)).mean()
            assert edge < max(interior * 3, 8)
        assert maps['normal'][..., 2].min() > 128
        assert np.all(maps['orm'][..., 2] == 0)
    # New finish channels do not require extra layers in the existing 8-layer library.
    channels = [surfaces.KEYS.index(key) for key in finishes.FINISH_KEYS.values()]
    library = surfaces.library(tmp_path / 'out', tmp_path / 'empty', channels)
    for key in finishes.FINISH_KEYS.values():
        assert (library / ('draft_' + key) / 'normal.png').exists()


def test_register_concrete_paving_is_unit_pavers_and_osm_paving_stones_lie_under_the_register(tmp_path):
    import json

    import pyarrow as pa
    import pyarrow.parquet as pq
    from turku3d.coarse import garden_finishes

    assert finishes.street_finish('Betonilaatta') == finishes.CONCRETE_PAVERS
    assert finishes.street_finish('Betonikivi') == finishes.BRICK
    assert finishes.street_finish('Asfalttibetoni') is None
    rows = [dict(tags=json.dumps(tags), geometry_local=geom.wkb) for tags, geom in (
        ({'highway': 'footway', 'surface': 'paving_stones'}, shapely.LineString([(10, 5), (40, 5)])),
        ({'area': 'yes', 'highway': 'pedestrian', 'surface': 'bricks'}, shapely.box(50, 50, 60, 60)),
        ({'highway': 'footway', 'surface': 'paving_stones', 'bridge': 'yes'}, shapely.LineString([(0, 90), (30, 90)])),
    )]
    path = tmp_path / 'osm.parquet'
    pq.write_table(pa.Table.from_pylist(rows), path)
    found = garden_finishes.osm_regions(C.VisualTile(0, 0, 0), path)
    assert [m for _, m in found] == [finishes.CONCRETE_PAVERS, finishes.BRICK]
    assert abs(found[0][0].area - 30 * 2.5) < 1e-6
    maps = ground_finishes.maps('concrete_pavers', 256)
    albedo = maps['albedo'].reshape(-1, 3).astype(float)
    brick = ground_finishes.maps('brick_paving', 256)['albedo'].reshape(-1, 3).astype(float).mean(axis=0)
    mean = albedo.mean(axis=0)
    assert 1.05 < mean[0] / mean[2] < brick[0] / brick[2]  # warm grey, less red than the brick
    assert albedo.std() > 4  # visible joints


def test_warm_grey_stone_dust_is_gravel_and_neutral_asphalt_is_not():
    rgb = np.full((64, 64, 3), (186, 188, 189), np.uint8)  # sunlit asphalt: blue at least green
    rgb[:, 32:] = (190, 192, 180)  # sunlit kivituhka: green/blue 1.07
    classes = surfaces.cover_classes(rgb, 0.5)
    assert np.all(classes[:, :24] == mat_id('road'))
    assert np.all(classes[:, 40:] == mat_id('gravel'))
    shade = np.full((64, 64, 3), (70, 74, 66), np.uint8)  # the same warm tone in shade decides nothing
    assert np.all(surfaces.cover_classes(shade, 0.5) == mat_id('road'))
