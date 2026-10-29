"""Audits cannot hold a tile's public pointer or lose its immutable input."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from turku3d.coarse import audit


def test_failed_audit_keeps_published_tile_and_pins_input(tmp_path, monkeypatch):
    out = tmp_path / 't0_0_0'
    out.mkdir()
    data = b'published geometry'
    identity = hashlib.sha256(data).hexdigest()
    visual = dict(sha256=identity, url=f'vis.{identity}.glb')
    payload = out / visual['url']
    payload.write_bytes(data)
    manifest = out / 'manifest.json'
    manifest.write_text(json.dumps(visual))
    audit.submit(out, visual, launch=False)
    payload.unlink()  # Normal retention of a superseded tile cannot race the auditor.
    observed = []

    def run(command, **kwargs):
        observed.append(Path(command[2]).read_bytes())
        return SimpleNamespace(returncode=1, stderr='bad geometry')

    monkeypatch.setattr(audit.subprocess, 'run', run)
    monkeypatch.setattr(audit.os, 'nice', lambda n: None)
    audit.drain(tmp_path / '.visual-audits')
    assert observed == [data]
    assert json.loads(manifest.read_text()) == visual
    assert audit.health(tmp_path) == dict(pending=0, passed=0, failed=1, failed_tiles=['t0_0_0'])


def test_enqueue_failure_is_advisory(tmp_path, monkeypatch):
    out = tmp_path / 't0_0_0'
    out.mkdir()
    monkeypatch.setattr(audit.os, 'link', lambda *args: (_ for _ in ()).throw(OSError('disk full')))
    audit.submit(out, dict(sha256='a' * 64, url='vis.glb'))


def test_real_decoded_audit_records_stage_and_total_times(tmp_path, monkeypatch):
    import os
    import numpy as np
    from turku3d.package.gltf import GlbMesh, build_glb

    out = tmp_path / 't0_0_0'
    out.mkdir()
    data = build_glb([GlbMesh(
        'wall', np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0]], float),
        np.array([[0, 1, 2]], np.uint32),
        extras=dict(layer='buildings', object_id='wall', material_key='facade'),
    )])
    identity = hashlib.sha256(data).hexdigest()
    (out / 'vis.glb').write_bytes(data)
    audit.submit(out, dict(sha256=identity, url='vis.glb'), launch=False)
    monkeypatch.setattr(audit.os, 'nice', lambda n: None)
    monkeypatch.setenv('TURKU3D_GLTF_TOOLS', os.environ.get(
        'TURKU3D_GLTF_TOOLS', str(Path.home() / 'turku3d/tools/npm')))
    audit.drain(tmp_path / '.visual-audits')
    result = json.loads((tmp_path / '.visual-audits' / f'{identity}.result.json').read_text())
    assert result['state'] == 'passed'
    assert result['wall_surfaces']['triangles'] == 1
    assert result['seconds'] >= result['stage_seconds']['total'] >= 0
    assert set(result['stage_seconds']) == {'decode', 'walls', 'snapshot', 'total'}
    assert audit.health(tmp_path) == dict(pending=0, passed=1, failed=0, failed_tiles=[])
