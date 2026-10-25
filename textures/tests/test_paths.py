"""Workers never put newly downloaded or generated texture data in shared raw/."""

from turkutex import paths


def test_sandbox_outputs(tmp_path, monkeypatch):
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "worker"))
    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "shared"))
    monkeypatch.delenv("TURKUTEX_STREETVIEW", raising=False)
    assert paths.textures_data_dir() == tmp_path / "worker/textures"
    assert paths.streetview_dir() == tmp_path / "worker/cache/streetview"
    assert paths.tools_dir() == tmp_path / "worker/tools"
