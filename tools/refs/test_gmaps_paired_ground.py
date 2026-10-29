"""Rendered calibration claims require identical reference and sample support."""

import json

import pytest

from gmaps_paired_ground import paired


def fixture(tmp_path, reference='same', samples=5):
    states = {}
    for label, value in [('before', 12), ('after', 8)]:
        folder = tmp_path / label
        folder.mkdir()
        quarters = {}
        for index in range(60):
            key = f'q{index}'
            if index >= 57:
                quarters[key] = dict(status='abstained')
                continue
            report = folder / f'{key}.json'
            report.write_text(json.dumps(dict(reference_sha256=reference,
                scorer_sha256='same-scorer', scores={'ground_colour_dE76':
                dict(value=value, samples=samples)}, ground_colour_by_material={
                    'draft_asphalt_worn': dict(value=value, cells=50)})))
            quarters[key] = dict(status='scored', report=str(report))
        path = folder / 'coverage.json'
        path.write_text(json.dumps(dict(quarters=quarters)))
        states[label] = path
    return states


def test_complete_paired_ground_and_holdout(tmp_path):
    paths = fixture(tmp_path)
    result = paired(paths['before'], paths['after'], ['q0', 'q1'])
    assert result['ground']['before_median'] == 12
    assert result['ground']['after_median'] == 8
    assert result['ground']['improved'] == 57
    assert result['holdout']['quarters'] == 2
    assert result['surfaces']['asphalt']['quarters'] == 57


def test_changed_ground_support_is_rejected(tmp_path):
    paths = fixture(tmp_path)
    target = tmp_path / 'after/q0.json'
    changed = json.loads(target.read_text())
    changed['scores']['ground_colour_dE76']['samples'] = 4
    target.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match='sample support changed'):
        paired(paths['before'], paths['after'])


def test_unrelated_geometry_support_does_not_spoil_ground_pair(tmp_path):
    paths = fixture(tmp_path)
    before = tmp_path / 'before/q0.json'
    after = tmp_path / 'after/q0.json'
    for path, samples in [(before, 3), (after, 4)]:
        report = json.loads(path.read_text())
        report['scores']['roof_colour_dE76'] = dict(value=10, samples=samples)
        path.write_text(json.dumps(report))
    result = paired(paths['before'], paths['after'])
    assert result['ground']['quarters'] == 57
    assert result['changed_other_support']['roof_colour_dE76'] == 1
