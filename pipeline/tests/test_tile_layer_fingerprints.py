"""Content fingerprints of shared build inputs."""

import os

from turku3d import tile_layer


def test_a_hard_link_made_while_hashing_does_not_count_as_a_change(tmp_path, monkeypatch):
    """train18 t0_1_-4: another build hard-linked the shared surface library's orm.png while this one
    hashed it. The link moved the inode's ctime and the build failed with "source changed"."""
    monkeypatch.setenv("TURKU3D_SHARED_CACHE", str(tmp_path / "shared"))
    source = tmp_path / "orm.png"
    source.write_bytes(b"pixels")
    real = tile_layer.hashlib.file_digest

    def digest_and_link(stream, name):
        os.link(source, tmp_path / "linked-by-another-build.png")
        return real(stream, name)

    monkeypatch.setattr(tile_layer.hashlib, "file_digest", digest_and_link)
    found = tile_layer.fingerprints({"orm": source})
    assert found["orm"]["sha256"] == tile_layer.hashlib.sha256(b"pixels").hexdigest()
