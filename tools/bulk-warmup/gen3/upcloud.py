"""Minimal UpCloud API client for the bulk window, locked to one server.

Mutations are refused unless they target the bulk host in server.json (SERVER): stop, modify plan
and start. It never creates, clones or deletes anything (stage.sh creates and deletes the host).
vm-control and its storage are never touched. The credential is read from a file and never printed or
logged: an API token (Bearer) or a sub-account `username:password` (Basic).
"""
import base64
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

API = 'https://api.upcloud.com/1.3'
# The one server this tool may act on: the bulk host stage.sh created, recorded in server.json (copied to
# osgallery-dev by stage.sh bootstrap). SMALL is the plan it was created with; LARGE is the 64-core window plan.
CONFIG = json.loads(Path(os.environ.get('BULK_SERVER_JSON',
                                        Path.home() / 'turku3d-local/loops/bulkgen3/server.json')).read_text())
SERVER = CONFIG['uuid']
SMALL, LARGE = CONFIG['plan'], 'CLOUDNATIVE-64xCPU-256GB'
PLANS = (SMALL, LARGE)
VM_CONTROL = '00d9ba4f-6916-4929-b8ef-7bc520c0e0be'


def target(uuid):
    if uuid != SERVER:
        raise PermissionError(f'refusing to act on server {uuid}: only the bulk host {SERVER}')
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

    def call(self, method, path, body=None):
        if method != "GET" and not path.startswith(f'/server/{SERVER}'):
            raise PermissionError(f'refusing {method} {path}: mutations only on the bulk host')
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
