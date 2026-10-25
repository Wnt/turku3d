"""Workers never put newly downloaded or generated texture data in shared raw/."""

from turkutex import paths


def test_sandbox_outputs(tmp_path, monkeypatch):
    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "worker"))
    monkeypatch.setenv("TURKU3D_DATA", str(tmp_path / "shared"))
    monkeypatch.delenv("TURKUTEX_STREETVIEW", raising=False)
    assert paths.textures_data_dir() == tmp_path / "worker/textures"
    assert paths.streetview_dir() == tmp_path / "shared/cache/streetlevel"
    assert paths.tools_dir() == tmp_path / "worker/tools"


def test_shared_panorama_reuses_complete_atomic_download(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    from turkutex.streetview.panos import PanoCache

    calls = []

    class Image:
        def save(self, path, **kwargs):
            calls.append(path)
            path.write_bytes(b"complete image")

    streetview = SimpleNamespace(
        find_panorama_by_id=lambda *a, **k: object(), get_panorama=lambda *a, **k: Image()
    )
    monkeypatch.setitem(
        sys.modules, "turkutex.streetview.sl", SimpleNamespace(streetview=streetview)
    )
    first = PanoCache(tmp_path, sleep=0).google_image("test", zoom=1)
    second = PanoCache(tmp_path, sleep=0).google_image("test", zoom=1)
    assert first == second
    assert first.read_bytes() == b"complete image"
    assert len(calls) == 1
    assert not list(tmp_path.rglob("*.part*"))


def test_legacy_panorama_is_reused_without_migration(tmp_path, monkeypatch):
    from turkutex.streetview.panos import PanoCache

    monkeypatch.setenv("TURKU3D_WORK", str(tmp_path / "worker"))
    old = tmp_path / "worker/cache/streetview/google/test/pano_z1.jpg"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"existing")
    shared = tmp_path / "shared"
    assert PanoCache(shared).google_image("test", zoom=1) == old
    assert not (shared / "google/test/pano_z1.jpg").exists()
