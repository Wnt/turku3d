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
