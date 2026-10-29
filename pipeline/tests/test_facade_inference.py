"""Generated facade regressions: per-field support, wall scope and stone paint."""
import copy
from types import SimpleNamespace

import numpy as np
import pytest
import shapely
from turku3d import config as C
from turku3d.facade_inference import overlay, reading_fields
from turku3d.facade_programs import compile_wall, resolve
from turku3d.facade_sections import prepare
from turkutex.facade_structure import compile_structure
from test_facade_structure import fixture


def test_supported_leaves_and_explicit_negatives_do_not_erase_richer_fields():
    prior = dict(wall=dict(material=dict(value='stone', support=.4)),
                 trim=dict(cornice=dict(value=.4, support=.4)),
                 balconies=dict(value=[[1, 2, 3, 1]], support=.4))
    source = dict(wall=dict(material=dict(value='render', support=.2)),
                  trim=dict(cornice=dict(value=None, support=.9)),
                  balconies=dict(value=[], support=.9))
    original = copy.deepcopy(source)
    overlay(prior, source)
    assert prior['wall']['material']['value'] == 'stone'
    assert prior['trim']['cornice']['value'] == .4
    assert prior['balconies']['value'] == []
    assert source == original


def test_low_confidence_row_and_shapes_do_not_override_supported_other_row():
    row, form = fixture()
    form['floors'][0]['confidence']['bay_count'] = .1
    form['floors'][1]['confidence'].update(head=.1, surround=.1)
    wall = compile_structure(row, form, 2)
    assert wall['doors']['candidates'] == []
    assert len(wall['windows']['rectangles_m']) == 2
    assert wall['windows']['head_shapes'] == [None, None]
    assert wall['windows']['surround_shapes'] == [None, None]
    assert reading_fields(wall)['windows']['support'] == .8


@pytest.mark.parametrize('material', ['stone', 'granite', 'rusticated-stone', 'rusticated_stone'])
def test_stone_reading_compiles_to_stone_painter_module(material):
    row, form = fixture()
    form['material'] = material
    wall = compile_structure(row, form, 2)
    assert compile_wall(wall, 2)['module'] // 6 == 2


def test_material_colour_and_structure_stay_on_the_observed_wall(monkeypatch):
    from turku3d import facade_programs as fp
    row, form = fixture()
    row['normal'] = [0, 1]
    form['material'] = 'brick'
    observed = prepare(dict(building_id='test', storeys=dict(value=2),
                            walls=[compile_structure(row, form, 2)]))
    monkeypatch.setattr(fp, 'measured', lambda _: observed)
    monkeypatch.setattr(fp, 'read_description', lambda _: None)
    vertices = np.array([[0,2,0],[12,2,0],[12,10,0],[0,10,0]], float)
    b = SimpleNamespace(key='test',facade_key='test',base=2,top=10,
        footprint=shapely.box(0,0,12,5),attributes=dict(levels=2,construction_year=1887,material_class='stone'))
    program = resolve(b, np.concatenate([vertices, vertices+[0,0,5]]), {})
    own, other = program['walls']
    assert own['wall']['material']['value'] == 'brick'
    assert other['wall']['material']['value'] == 'stone'
    assert own['bays']['count'] == 2
    assert not other.get('measured_wall_id')


def test_target_descriptors_are_wall_local_and_hashed(tmp_path, monkeypatch):
    from turku3d.facade_inference import apply_prior, evidence
    from turku3d.facade_programs import program_hash
    from turkutex.facade_archetypes import sample
    record = evidence('103295896N')
    program = sample('test', dict(levels=3, construction_year=1887),
                     [dict(wall_id='test',width_m=40,height_m=15)])
    own, other = copy.deepcopy(program['walls'][0]), copy.deepcopy(program['walls'][0])
    apply_prior(own, 'neo_renaissance', {}, dict(wall_id='103295896N_770004661f',structure={}), record)
    apply_prior(other, 'neo_renaissance', {}, dict(wall_id='unrelated'), record)
    assert own['wall']['colour']['value'] == '#ac9686'
    assert other['wall']['colour']['value'] != '#ac9686'
    before = program_hash('103295896N')
    monkeypatch.setattr(C, 'REPO_ROOT', tmp_path)
    assert program_hash('103295896N') != before


def test_existing_uncalibrated_patch_is_a_wall_local_prior_not_discarded():
    from turku3d.facade_inference import apply_prior
    from turkutex.facade_archetypes import sample
    wall = sample('test', dict(levels=3, construction_year=1887),
                  [dict(wall_id='test', width_m=20, height_m=12)])['walls'][0]
    own = dict(wall_id='own', wall=dict(colour=dict(value='#ac958b', support=.65,
                                                  source='reviewed wall patch')))
    other = copy.deepcopy(wall)
    apply_prior(wall, 'neo_renaissance', {}, own, {})
    apply_prior(other, 'neo_renaissance', {}, dict(wall_id='other'), {})
    assert wall['wall']['colour']['value'] == '#ac958b'
    assert 'uncalibrated' in wall['wall']['colour']['source']
    assert other['wall']['colour']['value'] != '#ac958b'
    assert wall['appearance_details']['pilasters']['value']
