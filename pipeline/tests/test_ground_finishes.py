"""Surveyed finish distinctions remain visual-only and generate periodic PBR maps."""
from types import SimpleNamespace

import numpy as np
import shapely

from turku3d import config as C
from turku3d.col.materials import mat_id
from turku3d.coarse import ground_finishes as finishes, inputs, surfaces


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
        maps = finishes.maps(key, 128)
        for slot, pixels in maps.items():
            np.testing.assert_array_equal(pixels, finishes.maps(key, 128)[slot])
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
