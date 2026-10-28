"""Anonymous demand lifecycle: turn/exit, concurrent drivers, deadlines and fixed worker slots."""
import json
import time
from types import SimpleNamespace

from turku3d.coarse.demand import Demand, TTL
from turku3d.coarse.service import Builder
from turku3d.coarse.upgrade_queue import next_job


def test_turn_drops_queued_pending_upgrades_and_journal_but_keeps_other_driver(tmp_path):
    b = Builder(tmp_path, code='test', workers=0)
    old, shared, ahead = 't0_0_0', 't0_1_0', 't0_2_0'
    for tile in (old, shared, ahead):
        b.request(tile, 0)
    b.pending[old] = (['raw'], 0)
    b.upgrade_queue.add(old)
    b.demand.update('driver-a', [old, shared], [shared], [old])
    b.demand.update('driver-b', [shared], [shared], [shared])
    b.demand.update('driver-a', [ahead], [ahead], [])
    b.demand.prune(b)
    assert old not in b.priority and old not in b.pending and old not in b.upgrade_queue
    assert old not in b.journal.rows
    assert b.order() == [shared, ahead]
    assert not b.demand.wants('driver-a', old)
    at, tiles, urgent, visible = b.demand.clients['driver-b']
    b.demand.clients['driver-b'] = (at - TTL, tiles, urgent, visible)
    b.demand.prune(b)
    assert shared not in b.priority
    b.stop()


def test_ahead_first_then_visible_upgrade_before_background_first(tmp_path):
    b = Builder(tmp_path, code='test', workers=0)
    ahead, visible, background = 't0_2_0', 't0_1_0', 't0_1_1'
    b.workers = 4
    b.demand.update('driver-a', [ahead, visible, background], [ahead], [visible])
    b.request(ahead, 100)
    b.request(background, 0)
    b.upgrade_queue.add(visible)
    assert next_job(b) == ((ahead, 100), False)
    assert next_job(b) == ((visible, 0), True)
    assert next_job(b) == ((background, 0), False)
    b.stop()


def test_running_stale_work_cancelled_unless_almost_done(tmp_path):
    b = Builder(tmp_path, code='test', workers=0)
    cancelled = []
    b.run = SimpleNamespace(cancel=lambda t: cancelled.append(t) or True)
    for tile in ('t0_0_0', 't0_1_0'):
        b.building[tile] = time.time() - (2 if tile == 't0_0_0' else 7)
    b.demand.update('driver-a', list(b.building), [], [])
    b.demand.update('driver-a', [], [], [])
    b.demand.prune(b)
    assert cancelled == ['t0_0_0']
    assert b.cancelled == {'t0_0_0'}
    b.stop()


def test_expired_replay_does_not_resurrect_abandoned_journal(tmp_path):
    (tmp_path / '.requests.json').write_text(json.dumps({'t0_0_0': {'at': time.time() - 120, 'distance': 0}}))
    b = Builder(tmp_path, code='test', workers=0)
    assert not b.priority and not b.upgrade_queue
    b.stop()


def test_background_prebuild_yields_running_and_queued_work_to_driver(tmp_path):
    b = Builder(tmp_path, code='test', workers=0)
    cancelled = []
    b.run = SimpleNamespace(cancel=lambda tile: cancelled.append(tile) or True)
    b.request('t0_0_0', 1e6)
    b.request('t0_0_1', 1e6)
    b.priority.pop('t0_0_0')
    b.building['t0_0_0'] = time.time()
    b.demand.update('driver-a', ['t0_2_0'], ['t0_2_0'], [])
    b.request('t0_2_0', 0)
    b.demand.prune(b)
    assert b.order() == ['t0_2_0']
    assert cancelled == ['t0_0_0']
    b.stop()


def test_scheduler_edits_do_not_invalidate_generator_or_prepared_inputs(monkeypatch):
    from pathlib import Path
    from turku3d.coarse.packaging import code_hash
    from turku3d.coarse.inputs import _key

    before = code_hash(), _key([], 'ground'), _key([], 'wfs')
    read = Path.read_bytes
    monkeypatch.setattr(Path, 'read_bytes', lambda p: read(p) + (b'\n# scheduling edit' if p.name in {'service.py', 'demand.py', 'upgrade_queue.py'} else b''))
    assert (code_hash(), _key([], 'ground'), _key([], 'wfs')) == before


def test_overlapping_producer_snapshots_do_not_collide(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    from threading import Barrier
    from turku3d.coarse.demand import write_snapshot

    barrier = Barrier(2)
    replace = Path.replace
    def synchronized_replace(path, destination):
        barrier.wait(timeout=2)
        return replace(path, destination)
    monkeypatch.setattr(Path, 'replace', synchronized_replace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(write_snapshot, tmp_path, {'slot': slot}) for slot in (7765, 7766)]
        for job in jobs:
            job.result()
    assert json.loads((tmp_path / '.scheduler.json').read_text())['slot'] in (7765, 7766)
    assert not list(tmp_path.glob('*.tmp'))


def test_new_crossings_preempt_visual_upgrades_and_keep_their_demand(tmp_path):
    b = Builder(tmp_path, code="test", workers=0)
    cancelled = []
    b.run = SimpleNamespace(cancel=lambda t: cancelled.append(t) or True)
    b.workers = 4
    upgrades = [f"t0_{i}_0" for i in range(3)]
    urgent = ["t0_4_0", "t0_5_0", "t0_6_0"]
    b.demand.update("driver-a", urgent + upgrades, urgent, upgrades)
    for tile in upgrades:
        b.building[tile] = time.time()
        b.upgrading.add(tile)
    for tile in urgent:
        b.priority[tile] = 0
    b.demand.prune(b)
    assert len(cancelled) == 2  # one free worker plus two reclaimed slots
    assert b.upgrade_queue == set(cancelled)
    assert set(b.priority) == set(urgent)
    b.demand.prune(b)
    assert len(cancelled) == 2  # pending cancellation already releases its slot
    b.stop()


def test_request_hash_cache_observes_producer_file_edits(tmp_path, monkeypatch):
    from turku3d.coarse import packaging

    path = tmp_path / "producer.py"
    path.write_text("first")
    calls = []
    monkeypatch.setattr(packaging, "producer_paths", lambda: [path])
    monkeypatch.setattr(packaging, "code_hash", lambda: calls.append(1) or path.read_text())
    assert packaging.current_code() == packaging.current_code() == "first"
    assert len(calls) == 1
    path.write_text("second")
    assert packaging.current_code() == "second"
    assert len(calls) == 2


def test_queued_polling_does_not_repeat_source_admission(tmp_path):
    calls = []
    builder = Builder(tmp_path, code="test", workers=0,
                      inputs=lambda tile: calls.append(tile) or [])
    assert builder.request("t0_0_0", 10)["state"] == "queued"
    assert builder.request("t0_0_0", 5)["state"] == "queued"
    assert calls == ["t0_0_0"]
    assert builder.priority["t0_0_0"] == 5
    builder.stop()


def test_full_admission_evicts_lateral_work_before_rejecting_crossing(tmp_path):
    builder = Builder(tmp_path, code="test", workers=0)
    near, far, lateral = "t0_1_0", "t0_2_0", "t0_0_1"
    builder.demand.update("driver-a", [near, far, lateral], [near, far], [lateral])
    builder.request(lateral, 2)
    assert builder.demand.admit(builder, near)
    assert lateral not in builder.priority and lateral not in builder.journal.rows
    builder.request(near, 0)
    assert not builder.demand.admit(builder, far)
    assert not builder.demand.admit(builder, "t0_9_9")
    builder.stop()


def test_visual_data_wait_does_not_requeue_structural_job(tmp_path):
    from turku3d import config as C
    from turku3d.coarse.collision import fragment

    tile = 't0_0_0'
    directory = tmp_path / tile
    directory.mkdir()
    (directory / 'collision.json').write_text(json.dumps(dict(
        coarse=True, tile=tile, code='test', inputs='structural', pass_kind='first',
        collision={c.id: {} for c in C.VisualTile.parse(tile).collision_tiles()},
    )))
    missing = ['ortho/source']
    builder = Builder(tmp_path, code='test', workers=0, input_key=lambda _: 'full',
                      structural_input_key=lambda _: 'structural',
                      enrichment_inputs=lambda _: missing)
    builder.pending[tile] = (missing, 0)
    assert builder.request(tile, 0)['state'] == 'pending-data'
    assert fragment(builder, tile) and not builder.priority and not builder.upgrade_queue
    missing.clear()
    assert builder.request(tile, 0)['state'] == 'queued'
    assert builder.upgrade_queue == {tile} and not builder.priority
    builder.stop()
