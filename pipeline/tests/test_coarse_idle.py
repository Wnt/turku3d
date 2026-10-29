"""Idle admission, ring barriers, cache hits and pre-start cancellation races."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

from turku3d.coarse.idle import IDLE_SECONDS, MIN_FREE, IdlePrebuild, route_walk, spawn_tiles
from turku3d.coarse.service import Builder


def idle_builder(tmp_path, walk):
    now = [0.0]
    b = Builder(tmp_path, run=Mock(), code='test', workers=0)
    b.workers = 4
    b.run.cancel = Mock(return_value=True)
    idle = IdlePrebuild(b, lambda: (walk, {}), lambda: False,
                       free=lambda: MIN_FREE + 1, clock=lambda: now[0])
    return b, idle, now


def test_idle_delay_full_pool_ring_barrier_and_preemption(tmp_path):
    walk = [(0, f't0_{i}_0') for i in range(5)] + [(1, 't0_9_0')]
    b, idle, now = idle_builder(tmp_path, walk)
    idle.tick()
    assert not b.priority
    now[0] = IDLE_SECONDS
    idle.tick()
    assert len(b.priority) == 4
    assert 't0_9_0' not in b.priority
    # A connection arrives in the gap between selecting a job and registering its PID.
    tile = next(iter(b.priority))
    b.building[tile] = 0
    with b.lock:
        idle.activity()
    assert not b.priority
    assert tile in b.cancelled
    b._worker_started(tile)
    b.run.cancel.assert_called_with(tile)
    assert not idle.tiles
    assert not idle.allowed()


def test_current_full_tiles_are_skipped_first_pass_is_upgraded(tmp_path):
    b, idle, now = idle_builder(tmp_path, [(0, 't0_0_0'), (0, 't0_1_0')])
    b.built = lambda tile, code=None: True
    for i, kind in enumerate(('full', 'first')):
        path = tmp_path / f't0_{i}_0'
        path.mkdir()
        (path / 'manifest.json').write_text(json.dumps({'pass_kind': kind}))
    b.two_pass = True
    now[0] = IDLE_SECONDS
    idle.tick()
    assert idle.done == {'t0_0_0'}
    assert b.upgrade_queue == {'t0_1_0'}
    assert not b.priority


def test_presence_failure_and_disk_floor_fail_closed(tmp_path):
    b, idle, now = idle_builder(tmp_path, [(0, 't0_0_0')])
    now[0] = IDLE_SECONDS
    idle.presence = Mock(side_effect=OSError('offline'))
    idle.tick()
    assert not b.priority
    idle.presence = lambda: False
    now[0] += IDLE_SECONDS
    idle.free = lambda: MIN_FREE - 1
    idle.tick()
    assert idle.state == 'low-disk'
    assert not b.priority


def test_route_walk_exact_grid_crossings_neighbours_and_spawn_ring():
    nav = dict(roads=dict(nodes=[77.1, 187.6, 1077.1, 187.6], edges=[[0, 1, 3, 1000]]),
               addresses=dict(origin=[77.1, 187.6], addresses=[
                   dict(id='end', street=[1077.1, 187.6], landmark=True)]))
    walk, summary = route_walk(nav)
    rings = {t: r for r, t in walk}
    assert all(rings[t] == 0 for t in spawn_tiles())
    assert {'t0_4_-1', 't0_4_0', 't0_4_1', 't0_5_0'} <= rings.keys()
    assert rings['t0_5_0'] > rings['t0_1_0']
    assert walk == sorted(walk)
    assert summary['landmarks'] == 1
