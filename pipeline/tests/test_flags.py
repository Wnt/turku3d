from turku3d.flags import feature_flag


def test_flags_require_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("TURKU3D_AB_EXAMPLE", raising=False)
    assert not feature_flag("example")
    monkeypatch.setenv("TURKU3D_AB_EXAMPLE", "true")
    assert not feature_flag("example")
    monkeypatch.setenv("TURKU3D_AB_EXAMPLE", "1")
    assert feature_flag("example")
