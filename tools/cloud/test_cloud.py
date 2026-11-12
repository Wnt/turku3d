"""cloud.py against an in-memory UpCloud: the scope guard, the reaper's choices and the budget."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cloud  # noqa: E402

VM_CONTROL = '00d9ba4f-6916-4929-b8ef-7bc520c0e0be'
NOW = 1_800_000_000.0


def uuid(n):
    return f'00000000-0000-4000-8000-{n:012d}'


class FakeApi(cloud.Api):
    """Servers and storages in memory; every request that reaches the transport is recorded."""

    def __init__(self):
        super().__init__()
        self.servers_, self.storages_, self.requests, self.forbid_delete = {}, {}, [], False

    def add(self, n, title, labels=None, state='started', disk=None):
        disk_id = uuid(1000 + n)
        self.storages_[disk_id] = dict(uuid=disk_id, title=disk or f'{title}-disk', labels=[dict(key='w2cloud', value='lease')] if labels else [],
                                       size=10, tier='standard', type='normal', state='online', zone='fi-hel1',
                                       created='2026-11-12T20:00:00Z', servers=dict(server=[uuid(n)]))
        self.servers_[uuid(n)] = dict(
            uuid=uuid(n), title=title, state=state, plan='STARTER-1xCPU-1GB', zone='fi-hel1',
            labels=dict(label=[dict(key=k, value=v) for k, v in (labels or {}).items()]),
            storage_devices=dict(storage_device=[dict(storage=disk_id, storage_title=self.storages_[disk_id]['title'])]),
            ip_addresses=dict(ip_address=[dict(access='public', family='IPv4', address='192.0.2.1')]))
        return uuid(n)

    def lease(self, n, expires, owner='w2-test', price=0.9276, created=NOW - 600):
        return self.add(n, f'w2c-{owner}-{n:06d}', dict(w2cloud='lease', owner=owner, purpose='unit test lease',
                                                         created=str(int(created)), expires=str(int(expires)), price_c_h=str(price)))

    def transport(self, method, path, body=None):
        self.requests.append((method, path))
        base, _, _ = path.partition('?')
        parts = base.strip('/').split('/')
        if (method, path) == ('GET', '/server'):
            return 200, dict(servers=dict(server=list(self.servers_.values())))
        if (method, path) == ('GET', '/storage/private'):
            return 200, dict(storages=dict(storage=list(self.storages_.values())))
        if (method, path) == ('GET', '/plan'):
            return 200, dict(plans=dict(plan=[dict(name='STARTER-1xCPU-1GB', core_number=1, memory_amount=1024, storage_size=10, storage_tier='standard'),
                                              dict(name='GPU-8xCPU-64GB-1xL40S', core_number=8, memory_amount=65536, storage_size=0, storage_tier=None)]))
        if (method, path) == ('GET', '/price'):
            zone = {'name': 'fi-hel2', 'server_plan_STARTER-1xCPU-1GB': dict(amount=1, price=0.4464),
                    'server_plan_GPU-8xCPU-64GB-1xL40S': dict(amount=1, price=111.1607), 'storage_standard': dict(amount=1, price=0.0118),
                    'ipv4_address': dict(amount=1, price=0.4812)}
            return 200, dict(prices=dict(zone=[zone, dict(zone, name='fi-hel1')]))
        store = self.servers_ if parts[0] == 'server' else self.storages_
        if len(parts) < 2 or parts[1] not in store:
            return 404, dict(error=dict(error_code='NOT_FOUND'))
        item = store[parts[1]]
        if method == 'GET':
            return 200, {parts[0]: item}
        if method == 'POST' and parts[2:] == ['stop']:
            item['state'] = 'stopped'
            return 200, dict(server=item)
        if method == 'DELETE':
            if self.forbid_delete:
                return 403, dict(error=dict(error_code='ACTION_FORBIDDEN'))
            if parts[0] == 'server':
                for device in item['storage_devices']['storage_device']:
                    self.storages_.pop(device['storage'], None)
            del store[parts[1]]
            return 204, {}
        return 400, dict(error=dict(error_code='UNEXPECTED'))


class CloudTest(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        self.api.add(1, 'vm-control')
        self.api.servers_[VM_CONTROL] = dict(self.api.servers_.pop(uuid(1)), uuid=VM_CONTROL)
        self.bulk = self.api.add(2, 'turku3d-bulk3', state='stopped')
        self.ledger = cloud.Ledger(Path(tempfile.mkdtemp()) / 'ledger.jsonl')

    def mutations(self):
        return [r for r in self.api.requests if r[0] != 'GET']

    def test_reap_deletes_only_expired_campaign_servers_with_their_disk(self):
        expired = self.api.lease(3, NOW - 60)
        running = self.api.lease(4, NOW + 1800)
        beat = cloud.reap(self.api, self.ledger, now=NOW, sleep=lambda s: None)
        self.assertEqual(set(self.api.servers_), {VM_CONTROL, self.bulk, running})
        self.assertNotIn(uuid(1003), self.api.storages_)
        self.assertEqual(self.mutations(), [('POST', f'/server/{expired}/stop'), ('DELETE', f'/server/{expired}?storages=1&backups=delete')])
        self.assertEqual([a['result'] for a in beat['actions']], ['delete'])
        row = [r for r in self.ledger.rows() if r['event'] == 'delete'][0]
        self.assertEqual((row['uuid'], row['hours_billed'], row['cost_eur']), (expired, 1, 0.0093))

    def test_everything_but_campaign_servers_is_refused(self):
        # The right prefix alone, or the label alone, is not enough; protected names never pass.
        impostor = self.api.add(5, 'w2c-no-label')
        labelled = self.api.add(6, 'turku3d-bulk4', dict(w2cloud='lease', expires='0'))
        self.api.servers_[VM_CONTROL]['labels'] = dict(label=[dict(key='w2cloud', value='lease')])
        for target in (VM_CONTROL, self.bulk, impostor, labelled):
            with self.assertRaises(PermissionError):
                cloud.destroy(self.api, target, self.ledger, by='test', sleep=lambda s: None)
            for method, path in (('POST', f'/server/{target}/stop'), ('DELETE', f'/server/{target}?storages=1&backups=delete'),
                                 ('PUT', f'/server/{target}'), ('DELETE', f'/storage/{uuid(1002)}')):
                with self.assertRaises(PermissionError):
                    self.api.call(method, path, {})
        cloud.reap(self.api, self.ledger, now=NOW, sleep=lambda s: None)
        self.assertEqual(self.mutations(), [])
        self.assertEqual(len(self.api.servers_), 4)

    def test_up_refuses_a_lease_that_would_break_the_budget(self):
        # 16 hours of an L40S server in the last day: 17.79 EUR. Four more hours would make 22.23 EUR.
        self.ledger.append('create', NOW - 20 * 3600, uuid=uuid(7), name='w2c-x-1', created=NOW - 20 * 3600, expires=NOW - 4 * 3600,
                           price_c_h=111.1607 + 0.4812 + 60 * 0.0118)
        self.ledger.append('delete', NOW - 4 * 3600 - 1, uuid=uuid(7), name='w2c-x-1')
        self.assertAlmostEqual(self.ledger.spend(NOW)['total'], 16 * 1.1235, places=2)
        ok = dict(heartbeat=dict(t=NOW - 60, ok=True, can_delete=True), timer='active', key='ssh-ed25519 AAAA test', deletes=[])
        args = argparse.Namespace(plan='GPU-8xCPU-64GB-1xL40S', owner='w2-test', purpose='budget refusal test', lease='4h', zone=None,
                                  disk=None, tier=None, template=None, dry_run=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cloud.cmd_up(args, self.api, self.ledger, now=NOW, state=ok)
        self.assertEqual(code, 3)
        self.assertIn('budget', json.loads(out.getvalue())['refused'])
        args.lease = '1h'      # one more hour still fits
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cloud.cmd_up(args, self.api, self.ledger, now=NOW, state=ok), 0)
        stale = dict(ok, heartbeat=dict(t=NOW - 3600, ok=True))
        with contextlib.redirect_stdout(out):
            self.assertEqual(cloud.cmd_up(args, self.api, self.ledger, now=NOW, state=stale), 4)
        self.assertEqual(self.mutations(), [])

    def test_spend_counts_started_hours_inside_the_window_and_commitments(self):
        self.ledger.append('create', NOW, uuid=uuid(8), name='w2c-x-2', created=NOW - 30 * 3600, expires=NOW - 26 * 3600, price_c_h=100)
        self.ledger.append('delete', NOW - 22.5 * 3600, uuid=uuid(8), name='w2c-x-2')       # ran 7.5 h: hours 0..7, two in the window
        self.ledger.append('create', NOW, uuid=uuid(9), name='w2c-x-3', created=NOW - 1800, expires=NOW + 2.5 * 3600, price_c_h=50)
        self.assertEqual(self.ledger.spend(NOW), dict(accrued=2.5, committed=1.0, total=3.5))

    def test_a_credential_that_cannot_delete_stops_the_server(self):
        expired = self.api.lease(3, NOW - 60)
        self.api.forbid_delete = True
        beat = cloud.reap(self.api, self.ledger, now=NOW, sleep=lambda s: None)
        self.assertEqual((self.api.servers_[expired]['state'], beat['can_delete']), ('stopped', False))
        cloud.reap(self.api, self.ledger, now=NOW + 300, sleep=lambda s: None)
        self.assertEqual([r['event'] for r in self.ledger.rows()], ['create', 'stop'])


if __name__ == '__main__':
    unittest.main()
