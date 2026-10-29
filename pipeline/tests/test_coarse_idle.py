"""Idle admission, ring barriers, cache hits and pre-start cancellation races."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

from turku3d.coarse.idle import IDLE_SECONDS, MIN_FREE, MIN_BOOT_FREE, IdlePrebuild, route_walk, spawn_tiles
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
    assert len(b.priority) == 1  # Immediate post-deploy spare warming.
    now[0] = IDLE_SECONDS
    idle.tick()
    assert len(b.priority) == 4
    assert 't0_9_0' not in b.priority
    # A connection arrives in the gap between selecting a job and registering its PID.
    tile = next(iter(b.priority))
    b.building[tile] = 0
    with b.lock:
        idle.activity()
    # An urgent crossing with every worker occupied reclaims its warm slot.
    b.building.update({f"t0_{i}_0": 0 for i in range(4)})
    b.demand.update("driver", ["t0_8_0"], ["t0_8_0"], [])
    b.priority["t0_8_0"] = 0
    with b.lock:
        idle.preempt()
    assert tile in b.cancelled
    b._worker_started(tile)
    b.run.cancel.assert_called_with(tile)
    assert not idle.tiles
    assert idle.allowed()


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


def test_only_explicit_idle_upgrades_can_fill_the_reserved_crossing_slot(tmp_path):
    from turku3d.coarse.upgrade_queue import next_job

    tiles = [f't0_{i}_0' for i in range(4)]
    b, idle, now = idle_builder(tmp_path, [(0, tile) for tile in tiles])
    b.two_pass = True
    idle.tiles.update(tiles)
    b.upgrade_queue.update(tiles)
    for tile in tiles:
        assert next_job(b) == ((tile, 0), True)
        b.building[tile] = 0
    with b.lock:
        idle.activity()
    assert len(b.cancelled) == 3
    assert b.run.cancel.call_count == 3
    assert len(idle.tiles) == 1


def test_presence_failure_and_disk_floor_fail_closed(tmp_path):
    b, idle, now = idle_builder(tmp_path, [(0, 't0_0_0')])
    now[0] = IDLE_SECONDS
    idle.presence = Mock(side_effect=OSError('offline'))
    idle.tick()
    assert not b.priority
    assert idle.state == 'presence-unavailable'
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


def test_retirement_does_not_cancel_an_admitted_idle_build(tmp_path):
    b, idle, now = idle_builder(tmp_path, [(0, 't0_0_0')])
    now[0] = IDLE_SECONDS + 1
    idle.tick()
    b.building['t0_0_0'] = now[0]
    b.drain()
    idle.tick()
    b.run.cancel.assert_not_called()
    b.stop()


def test_online_warming_keeps_one_worker_and_fair_predicted_routes(tmp_path):
    b, idle, now = idle_builder(tmp_path, [(0, 't0_0_0')])
    idle.presence = lambda: True
    b.demand.update('a', ['t0_8_0', 't0_9_0'], [], [])
    b.demand.update('b', ['t0_0_8', 't0_0_9'], [], [])
    idle.tick()
    assert list(idle.candidates())[:4] == [(-2, t) for t in ('t0_8_0', 't0_0_8', 't0_9_0', 't0_0_9')]
    assert idle.tiles == {'t0_8_0'}
    b.priority.clear()
    b.building['t0_8_0'] = 0
    for _ in range(5):
        with b.lock:
            idle.activity()
        idle.tick()
    b.run.cancel.assert_not_called()
    assert idle.tiles == {'t0_8_0'}


def test_visible_warm_job_is_promoted_and_never_cancelled(tmp_path):
    b, idle, now = idle_builder(tmp_path, [(0, 't0_0_0')])
    idle.tick()
    b.building['t0_0_0'] = 0
    b.demand.update('driver', ['t0_0_0'], ['t0_0_0'], ['t0_0_0'])
    with b.lock:
        b.demand.prune(b)
    assert not idle.tiles
    b.run.cancel.assert_not_called()
    assert 't0_0_0' not in b.cancelled


def test_boot_floor_recovers_and_does_not_reclaim_at_twelve_gib(tmp_path):
    b, idle, now = idle_builder(tmp_path, [(0, 't0_0_0')])
    idle.free = lambda: {'data': MIN_FREE + 1, 'boot': 12 * 1024**3}
    idle.reclaim_outer_stages = Mock()
    idle.tick()
    assert b.priority
    idle.reclaim_outer_stages.assert_not_called()
    idle.free = lambda: {'data': MIN_FREE + 1, 'boot': MIN_BOOT_FREE - 1}
    idle.tick()
    assert idle.state == 'low-disk'
    assert not b.priority
    idle.free = lambda: {'data': MIN_FREE + 1, 'boot': MIN_BOOT_FREE + 1}
    idle.tick()
    assert b.priority and not idle.disk_stopped


def test_recent_visible_tiles_survive_restart_without_player_positions(tmp_path):
    b, idle, now = idle_builder(tmp_path, [(0, 't0_0_0')])
    b.demand.update('driver', ['t0_4_2'], ['t0_4_2'], [])
    idle.tick()
    b2, idle2, _ = idle_builder(tmp_path, [(0, 't0_0_0')])
    idle2.tick()
    assert 't0_4_2' in idle2.recent
    assert next(idle2.candidates()) == (-1, 't0_4_2')


def test_finished_warm_slot_is_reused_when_route_order_changes(tmp_path):
    b, idle, now = idle_builder(tmp_path, [(0, 't0_0_0'), (0, 't0_1_0')])
    idle.tick()
    path = tmp_path / 't0_0_0'
    path.mkdir()
    (path / 'manifest.json').write_text('{"pass_kind":"full"}')
    b.built = lambda tile, code=None: tile == 't0_0_0'
    b.priority.clear()
    b.demand.update('driver', ['t0_1_0'], [], [])
    idle.tick()
    assert idle.completed == 1
    assert idle.tiles == {'t0_1_0'}


def test_raw_download_wait_does_not_consume_spare_build_capacity(tmp_path):
    b, idle, now = idle_builder(tmp_path, [(0, 't0_0_0')])
    idle.presence = lambda: True
    b.pending['t0_7_7'] = (['missing-raw'], 0)
    idle.tick()
    assert idle.tiles == {'t0_0_0'}
    assert idle.capacity() == 1
