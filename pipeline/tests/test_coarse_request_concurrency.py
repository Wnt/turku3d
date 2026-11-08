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


def test_an_expired_verdict_answers_at_once_while_it_is_checked_again(tmp_path, monkeypatch):
    """city.lab Espoo 2026-11-07: the 30 s re-check of a tile's raw input key took 10-30 s on the loaded host, under
    the scheduler lock, and every tile request waited on it. The kept verdict answers; the check runs behind it."""
    from turku3d.coarse import service

    entered, release = threading.Event(), threading.Event()
    calls = []

    def current(builder, tile, code=None):
        calls.append(tile)
        if len(calls) > 1:
            entered.set()
            assert release.wait(2)
            return False
        return True

    monkeypatch.setattr(service, "tile_current", current)
    monkeypatch.setattr(service, "CURRENT_TTL", 0.0)
    builder = Builder(tmp_path, code="test", workers=0)
    try:
        manifest = builder.manifest("t0_0_0")
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("{}")
        assert builder.built("t0_0_0")
        assert builder.built("t0_0_0"), "the expired verdict answers while it is checked again"
        assert entered.wait(1)
        assert builder.lock.acquire(timeout=.5), "the re-check held the scheduler condition"
        builder.lock.release()
        assert builder.built("t0_0_0") and len(calls) == 2, "one re-check per tile at a time"
        release.set()
        for _ in range(100):
            if not builder.rechecking:
                break
            threading.Event().wait(.01)
        assert not builder.built("t0_0_0"), "the re-check's verdict replaces the kept one"
    finally:
        release.set()
        builder.stop()


def test_a_first_freshness_check_runs_before_the_scheduler_lock(tmp_path, monkeypatch):
    from turku3d.coarse import service

    entered, release = threading.Event(), threading.Event()

    def current(builder, tile, code=None):
        entered.set()
        assert release.wait(2)
        return True

    monkeypatch.setattr(service, "tile_current", current)
    builder = Builder(tmp_path, code="test", workers=0)
    manifest = builder.manifest("t0_0_0")
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("{}")
    request = threading.Thread(target=builder.request, args=("t0_0_0", 0))
    request.start()
    try:
        assert entered.wait(1)
        assert builder.lock.acquire(timeout=.5), "a tile's first freshness check held the scheduler condition"
        builder.lock.release()
    finally:
        release.set()
        request.join(2)
        builder.stop()


def wait_for(condition, timeout=5.0):
    import time

    end = time.monotonic() + timeout
    while not condition() and time.monotonic() < end:
        time.sleep(0.01)
    return condition()


def test_a_served_fragment_answers_at_once_after_a_build_and_rereads_when_its_files_change(tmp_path, monkeypatch):
    """Each poll re-read the tile's manifests and stat'ed every payload: 2-3 s on a loaded host. Re-reading every
    tile in the foreground behind each finished build cost 4-16 s per tile (city.lab Amsterdam 2026-11-08)."""
    from turku3d.coarse import serving

    reads = []
    monkeypatch.setattr(serving, "_retained_fragment", lambda builder, tile: reads.append(tile) or {"tile": tile})
    builder = Builder(tmp_path, code="test", workers=0)
    try:
        manifest = builder.manifest("t0_0_0")
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("{}")
        first = serving.retained_fragment(builder, "t0_0_0")
        first["failure"] = {"state": "failed"}  # callers may decorate their copy
        assert serving.retained_fragment(builder, "t0_0_0") == {"tile": "t0_0_0"} and reads == ["t0_0_0"]
        builder.version += 1  # a build finished elsewhere
        assert serving.retained_fragment(builder, "t0_0_0") == {"tile": "t0_0_0"}, "the kept fragment answers"
        assert wait_for(lambda: len(reads) == 2 and not builder.fragment_refreshing), "one re-read runs behind it"
        serving.retained_fragment(builder, "t0_0_0")
        assert len(reads) == 2, "the re-read is kept for the new build count"
        manifest.write_text('{"changed": true}')  # the tile's own files changed
        serving.retained_fragment(builder, "t0_0_0")
        assert len(reads) == 3
    finally:
        builder.stop()


def test_warming_goes_nearest_the_active_demand_first_and_picks_up_installed_tiles(tmp_path, monkeypatch):
    """After a restart, an install of a prebuilt tile or a finished build, a tile's first poll paid its cold
    freshness check and fragment read (4-16 s per tile on a loaded host, 2026-11-08); the warm thread pays them
    behind the requests, nearest the players first, and finds manifests that change under it."""
    from turku3d.coarse import service, service_warm, serving

    monkeypatch.setattr(service_warm, "WARM_PAUSE_S", 0)
    checked = []
    monkeypatch.setattr(service, "tile_current", lambda builder, tile, code=None: checked.append(tile) or True)
    monkeypatch.setattr(serving, "_retained_fragment", lambda builder, tile: {"tile": tile})
    for tile in ("t0_3_0", "t0_0_0", "t0_-1_1", "t0_5_5"):
        (tmp_path / tile).mkdir()
        (tmp_path / tile / "manifest.json").write_text("{}")
    builder = Builder(tmp_path, code="test", workers=0)
    try:
        builder.requested["t0_4_4"] = __import__("time").time()  # a player is polling around t0_4_4
        assert service_warm.warm_pass(builder) == ["t0_5_5", "t0_3_0", "t0_0_0", "t0_-1_1"]
        assert checked == ["t0_5_5", "t0_3_0", "t0_0_0", "t0_-1_1"]
        assert {"t0_5_5", "t0_0_0", "t0_-1_1", "t0_3_0"} <= builder.servable
        assert builder.built("t0_0_0") and len(checked) == 4, "the request finds the warmed verdict"
        assert service_warm.warm_pass(builder) == [], "nothing is cold until a manifest changes"
        (tmp_path / "t0_2_2").mkdir()
        (tmp_path / "t0_2_2" / "manifest.json").write_text("{}")  # a prebuilt tile installed into the store
        (tmp_path / "t0_0_0" / "manifest.json").write_text('{"rebuilt": true}')
        assert service_warm.warm_pass(builder) == ["t0_2_2", "t0_0_0"]
        assert "t0_2_2" in builder.servable and checked[-2:] == ["t0_2_2", "t0_0_0"]
        builder.requested.clear()
        builder.journal.record("t0_-1_1", 0)
        assert service_warm.demand_anchors(builder) == [(-1, 1)], "without recent polls the request journal"
        builder.journal.discard("t0_-1_1")
        assert service_warm.demand_anchors(builder) == [(0, 0)], "and without either, the spawn tile"
    finally:
        builder.stop()


def test_a_poll_writes_the_request_journal_outside_the_scheduler_lock(tmp_path, monkeypatch):
    from turku3d.coarse import request_journal

    entered, release = threading.Event(), threading.Event()
    original = request_journal.Path.replace

    def slow_replace(self, target):
        if self.name.startswith(".requests.json"):
            entered.set()
            assert release.wait(2)
        return original(self, target)

    monkeypatch.setattr(request_journal.Path, "replace", slow_replace)
    builder = Builder(tmp_path, code="test", workers=0, inputs=lambda tile: ["missing-input"])
    request = threading.Thread(target=builder.request, args=("t0_0_0", 0))
    request.start()
    try:
        assert entered.wait(1)
        assert builder.lock.acquire(timeout=.5), "the journal write held the scheduler condition"
        builder.lock.release()
    finally:
        release.set()
        request.join(2)
        builder.stop()
    assert "t0_0_0" in request_journal.RequestJournal(tmp_path).rows


def test_a_stale_tiles_collision_pair_is_checked_before_the_scheduler_lock(tmp_path, monkeypatch):
    """After a producer change every tile is stale, and each poll read its collision pair and raw input key under
    the scheduler lock."""
    from turku3d.coarse import service

    entered, release = threading.Event(), threading.Event()

    def collision(builder, tile):
        entered.set()
        assert release.wait(2)
        return {"collision": {}}

    monkeypatch.setattr(service, "collision_current", collision)
    builder = Builder(tmp_path, code="test", workers=0)
    builder.two_pass = True
    request = threading.Thread(target=builder._request, args=("t0_0_0", 0))
    request.start()
    try:
        assert entered.wait(1)
        assert builder.lock.acquire(timeout=.5), "the collision pair check held the scheduler condition"
        builder.lock.release()
    finally:
        release.set()
        request.join(2)
        builder.stop()
    assert "t0_0_0" in builder.upgrade_queue
