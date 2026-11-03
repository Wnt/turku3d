"""Product generations share one facade-kit render through the shared cache."""

import json

from turku3d import facade_kit
from turku3d.coarse import packaging


def test_shared_cache_renders_one_kit_for_every_output_root(tmp_path, monkeypatch):
    renders = []

    def ensure_kit(root):
        target = root / "facade-kit-k1"
        if not (target / "kit.json").exists():
            renders.append(root)
            target.mkdir(parents=True)
            for slot in ("albedo", "normal", "orm"):
                (target / f"{slot}.ktx2").write_bytes(slot.encode() * 3)
            (target / "kit.json").write_text(json.dumps(dict(
                key="k1", maps={s: str(target / f"{s}.ktx2") for s in ("albedo", "normal", "orm")}, version=3)))
        return json.loads((target / "kit.json").read_text())

    monkeypatch.setattr(packaging, "ensure_kit", ensure_kit)
    monkeypatch.setattr(facade_kit, "code_hash", lambda: "k1")
    monkeypatch.setenv("TURKU3D_SHARED_CACHE", str(tmp_path / "cache"))
    kits = [packaging.prepared_kit(tmp_path / generation / "shared") for generation in ("g1", "g2", "g1")]
    assert renders == [tmp_path / "cache" / "facade-kits"]
    for kit, generation in zip(kits, ("g1", "g2", "g1"), strict=True):
        target = tmp_path / generation / "shared" / "facade-kit-k1"
        assert kit == dict(key="k1", maps={s: str(target / f"{s}.ktx2") for s in ("albedo", "normal", "orm")},
                           version=3)
        assert (target / "normal.ktx2").read_bytes() == b"normalnormalnormal"

    monkeypatch.delenv("TURKU3D_SHARED_CACHE")
    packaging.prepared_kit(tmp_path / "local")
    assert renders[-1] == tmp_path / "local"
