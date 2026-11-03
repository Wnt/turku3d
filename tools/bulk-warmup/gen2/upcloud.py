"""Minimal UpCloud API client for the bulk window, locked to one server.

Mutations are refused unless they target turku3d-bulk (SERVER): stop, modify plan, start, and
attaching the one approved results storage. It never creates, clones or deletes anything
except through `teardown.py` (explicit OK only) and the one-time results storage. vm-control
and its storage are never touched. The credential is read from a file and never printed or
logged: an API token (Bearer) or a sub-account `username:password` (Basic).
"""
import base64
import json
from pathlib import Path
import time
import urllib.error
import urllib.request

API = 'https://api.upcloud.com/1.3'
SERVER = '0023e4fd-6cee-4af8-ab1f-f31541b9360d'  # turku3d-bulk, fi-hel1 (~/turku3d/bulk-gen2/server.json)
SMALL, LARGE = 'CLOUDNATIVE-2xCPU-4GB', 'CLOUDNATIVE-64xCPU-256GB'
PLANS = (SMALL, LARGE)
RESULTS_TITLE, RESULTS_GB = 'turku3d-bulk-results', 100
VM_CONTROL = '00d9ba4f-6916-4929-b8ef-7bc520c0e0be'


def target(uuid):
    if uuid != SERVER:
        raise PermissionError(f'refusing to act on server {uuid}: only turku3d-bulk {SERVER}')
    return uuid


class Client:
    def __init__(self, credential=None, log=None, dry_run=False):
        self.dry_run, self.log = dry_run, Path(log) if log else None
        self.auth = None
        if credential is not None and not dry_run:
            secret = Path(credential).expanduser().read_text().strip()
            self.auth = ('Basic ' + base64.b64encode(secret.encode()).decode()) if ':' in secret else 'Bearer ' + secret
        self.simulated = {}

    def record(self, **row):
        row = dict(t=round(time.time(), 3), dry_run=self.dry_run, **row)
        if self.log:
            self.log.parent.mkdir(parents=True, exist_ok=True)
            with self.log.open('a') as out:
                out.write(json.dumps(row) + '\n')
        return row

    def call(self, method, path, body=None, allow=False):
        if method != 'GET' and not allow and not path.startswith(f'/server/{SERVER}'):
            raise PermissionError(f'refusing {method} {path}: mutations only on turku3d-bulk')
        if VM_CONTROL in path:
            raise PermissionError('vm-control is never touched')
        if self.dry_run:
            self.record(method=method, path=path, body=body, status='dry-run')
            return self._simulate(method, path, body)
        if self.auth is None:
            raise RuntimeError('no UpCloud credential')
        request = urllib.request.Request(API + path, method=method,
                                         data=None if body is None else json.dumps(body).encode(),
                                         headers={'Authorization': self.auth, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                status, text = response.status, response.read().decode()
        except urllib.error.HTTPError as error:
            status, text = error.code, error.read().decode()
        value = json.loads(text) if text.strip().startswith('{') else {}
        self.record(method=method, path=path, body=body, status=status, response=_summary(value))
        if status >= 400:
            raise RuntimeError(f'UpCloud {method} {path}: HTTP {status} {_summary(value)}')
        return value

    def _simulate(self, method, path, body):
        server = self.simulated.setdefault(SERVER, dict(state='started', plan=SMALL))
        if path.endswith('/stop'):
            server['state'] = 'stopped'
        elif path.endswith('/start'):
            server['state'] = 'started'
        elif method == 'PUT' and body and 'server' in body:
            server.update(body['server'])
        return dict(server=dict(uuid=SERVER, **server))

    # Server helpers -------------------------------------------------------------
    def server(self, uuid=SERVER):
        return self.call('GET', f'/server/{target(uuid)}')['server']

    def wait(self, state, uuid=SERVER, timeout=600):
        deadline = time.monotonic() + timeout
        while True:
            current = self.server(uuid)
            if current['state'] == state:
                return current
            if time.monotonic() > deadline:
                raise TimeoutError(f'server is {current["state"]}, not {state}, after {timeout} s')
            time.sleep(5)

    def stop(self, uuid=SERVER, hard_after=120):
        target(uuid)
        if self.server(uuid)['state'] == 'stopped':
            return
        self.call('POST', f'/server/{uuid}/stop', dict(stop_server=dict(stop_type='soft', timeout=str(hard_after))))
        # A soft stop that does not finish turns hard after `hard_after` seconds on UpCloud's side.
        self.wait('stopped', uuid, timeout=hard_after + 300)

    def start(self, uuid=SERVER):
        self.call('POST', f'/server/{target(uuid)}/start')
        return self.wait('started', uuid)

    def set_plan(self, plan, uuid=SERVER):
        if plan not in PLANS:
            raise PermissionError(f'refusing plan {plan}')
        return self.call('PUT', f'/server/{target(uuid)}', dict(server=dict(plan=plan)))

    def inventory(self):
        """Read-only: every server and storage of the account, one line each."""
        lines = []
        for item in self.call('GET', '/server')['servers']['server']:
            lines.append(f"server {item['uuid']} {item.get('hostname')} {item.get('plan')} {item.get('state')} {item.get('zone')} {item.get('title')}")
        for item in self.call('GET', '/storage/private')['storages']['storage']:
            servers = ','.join(item.get('servers', {}).get('server', [])) or '-'
            lines.append(f"storage {item['uuid']} {item.get('title')} {item.get('size')} GB {item.get('tier')} {item.get('state')} {item.get('zone')} attached: {servers}")
        return lines

    def results_storage(self):
        """The one approved results storage, created once (title turku3d-bulk-results, 100 GB)."""
        found = [s for s in self.call('GET', '/storage/private')['storages']['storage'] if s.get('title') == RESULTS_TITLE]
        if len(found) > 1:
            raise RuntimeError('more than one results storage: fix by hand, never create another')
        if found:
            return found[0]['uuid']
        made = self.call('POST', '/storage', dict(storage=dict(size=str(RESULTS_GB), tier='maxiops', title=RESULTS_TITLE, zone='fi-hel1')), allow=True)
        return made['storage']['uuid']

    def attach(self, storage):
        server = self.server()
        if storage in [d['storage'] for d in server['storage_devices']['storage_device']]:
            return
        self.call('POST', f'/server/{SERVER}/storage/attach', dict(storage_device=dict(type='disk', address='virtio', storage=storage)))


def _summary(value):
    """State fields only: never echo credentials or full documents."""
    if not isinstance(value, dict):
        return None
    if 'error' in value:
        return value['error']
    for key in ('server', 'storage'):
        if key in value and isinstance(value[key], dict):
            item = value[key]
            return {k: item.get(k) for k in ('uuid', 'state', 'plan', 'core_number', 'memory_amount', 'size', 'tier', 'zone', 'title') if k in item}
    return {k: v for k, v in value.items() if not isinstance(v, (dict, list))}
