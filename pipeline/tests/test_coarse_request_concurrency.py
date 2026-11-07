"""Slow source discovery must not serialize demand and unrelated tile requests."""
import threading

from turku3d.coarse.service import Builder


def test_source_discovery_releases_scheduler_condition(tmp_path):
    entered, release = threading.Event(), threading.Event()

    def inputs(tile):
        entered.set()
        assert release.wait(2)
        return ["missing-input"]

    builder = Builder(tmp_path, code="test", workers=0, inputs=inputs)
    request = threading.Thread(target=builder.request, args=("t0_0_0", 0))
    request.start()
    try:
        assert entered.wait(1)
        assert builder.lock.acquire(timeout=.5), "HTTP input I/O retained the scheduler condition"
        builder.lock.release()
    finally:
        release.set()
        request.join(2)
        builder.stop()


def test_current_verdict_is_reused_until_the_manifest_changes(tmp_path, monkeypatch):
    import os

    from turku3d.coarse import service

    calls = []
    monkeypatch.setattr(service, "tile_current", lambda builder, tile, code=None: calls.append(tile) or True)
    builder = Builder(tmp_path, code="test", workers=0)
    try:
        manifest = builder.manifest("t0_0_0")
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("{}")
        assert builder.built("t0_0_0") and builder.built("t0_0_0")
        assert calls == ["t0_0_0"], "an unchanged manifest re-ran the full freshness check"
        manifest.write_text('{"code": "x"}')
        os.utime(manifest, ns=(1, 1))
        assert builder.built("t0_0_0") and calls == ["t0_0_0", "t0_0_0"]
        manifest.unlink()
        assert not builder.built("t0_0_0")
    finally:
        builder.stop()
