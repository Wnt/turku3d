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
