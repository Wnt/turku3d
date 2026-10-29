"""The installed private reader follows selective dataset identities without inputs.CODE."""

import sys
from types import SimpleNamespace

from gmaps_inputs import identity, install


def test_private_reader_uses_current_content_keys(tmp_path, monkeypatch):
    raw = tmp_path / "ground.laz"
    raw.write_bytes(b"first ground")
    reader = SimpleNamespace(paths_for=lambda tile: {"ground": [raw]})
    monkeypatch.setitem(sys.modules, "own_inputs", reader)
    install()
    before = reader.identity(None)
    assert before == identity({"ground": [raw]})
    raw.write_bytes(b"changed ground")
    assert reader.identity(None) != before
    assert identity({"ground": None}) != identity({"ground": [None]})


def test_orthophoto_chunk_state_invalidates_scorer(tmp_path):
    ortho = tmp_path / "photo.vrt"
    ortho.write_text("<VRTDataset />")
    chunks = tmp_path / "photo.chunks.json"
    before = identity({"ortho": [ortho]})
    chunks.write_text('{"status": "ok"}')
    assert identity({"ortho": [ortho]}) != before
