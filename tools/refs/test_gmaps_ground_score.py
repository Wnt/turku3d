"""Ground measurement exports use supported matching cells, not mismatch candidates."""

import sys
from types import SimpleNamespace

import numpy as np

from gmaps_ground_score import export_ground, summary


def test_exports_matching_material_medians_and_rejects_height_conflicts(monkeypatch):
    monkeypatch.setitem(sys.modules, 'compare', SimpleNamespace(
        delta_e=lambda a, b: np.linalg.norm(a - b)))
    h = np.zeros((10, 10))
    truth = SimpleNamespace(h=h, rgb=np.full((10, 10, 3), 80.))
    ours = SimpleNamespace(h=h.copy(), rgb=np.full((10, 10, 3), 90.),
                           material_keys=['asphalt_worn', 'grass'],
                           material_index=np.zeros((10, 10), int))
    ours.material_index[:4] = 1
    ours.h[:4] = 3
    masks = dict(valid=np.ones((10, 10), bool), calibration=np.ones((10, 10), bool), ground=h)
    result = export_ground(truth, ours, masks, np.ones((10, 10), bool))
    assert list(result) == ['asphalt_worn']
    assert result['asphalt_worn']['cells'] == 60
    assert result['asphalt_worn']['own_rgb'] == [90., 90., 90.]
    assert result['asphalt_worn']['reference_rgb'] == [80., 80., 80.]


def test_abstention_is_not_a_zero_score():
    assert summary({'a': dict(status='abstained'), 'b': dict(status='incomplete')}) == dict(
        scored=0, abstained=1, metrics={})


def test_materializes_oci_sibling_resources_and_checks_source_hash(tmp_path, monkeypatch):
    import hashlib
    import json
    import struct
    import pytest
    from gmaps_ground_score import materialize_own

    source = tmp_path / 'durable/oci/t0_0_0'
    source.mkdir(parents=True)
    shared = source.parent / 'shared'
    shared.mkdir()
    (shared / 'texture.ktx2').write_bytes(b'own OCI texture')
    (source / 'manifest.json').write_text('{}')
    raw = json.dumps(dict(images=[dict(uri='../shared/texture.ktx2')])).encode()
    payload = b'glTF' + struct.pack('<II', 2, len(raw) + 20) + struct.pack('<I', len(raw)) + b'JSON' + raw
    glb = source / 'visual.glb'
    glb.write_bytes(payload)
    own = dict(path=str(glb), sha256=hashlib.sha256(payload).hexdigest())
    monkeypatch.setenv('TURKU3D_WORK', str(tmp_path / 'work'))
    result = materialize_own(own, 't0_0_0')
    assert result.read_bytes() == payload
    assert (result.parent / '../shared/texture.ktx2').read_bytes() == b'own OCI texture'
    glb.write_bytes(b'corrupted')
    with pytest.raises(ValueError, match='source changed'):
        materialize_own(own, 't0_0_0')


def test_checkpoint_ignores_cli_edits_but_tracks_measurement_algebra():
    from gmaps_ground_score import measurement_source

    original = 'def export_ground(x):\n    return x + 1\ndef main():\n    print("old CLI")\n'
    changed_cli = original.replace('old CLI', 'new CLI')
    assert measurement_source(original) == measurement_source(changed_cli)
    assert measurement_source(original) != measurement_source(original.replace('x + 1', 'x + 2'))
