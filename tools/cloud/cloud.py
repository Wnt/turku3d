#!/usr/bin/env python3
"""W2 campaign cloud leases on UpCloud: servers that cannot be left on and cannot exceed the budget.

  cloud.py up --plan PLAN --owner AGENT --purpose TEXT [--lease 1h] [--zone Z] [--disk GB] [--template T] [--dry-run]
  cloud.py renew NAME [--lease 1h]     the lease then ends LEASE from now (never more than 4 h ahead)
  cloud.py down NAME                   delete the server with its storage
  cloud.py status [--json]             leases, the spend of the trailing 24 hours, the reaper's heartbeat
  cloud.py reap [--dry-run]            delete every campaign server whose lease expired, with its storage
  cloud.py ssh NAME [COMMAND...]       a shell or a command on a leased server
  cloud.py plans [--gpu] [--all]       plans with hourly prices and the hours per day the budget allows
  cloud.py inventory                   every server, storage and gateway of the account with its cost per day (read-only)
  cloud.py reaper install | status     the reaper on osgallery-dev: a systemd user timer that runs `reap` every 5 min

A lease is a server titled `w2c-<owner>-<id>` with labels: owner, purpose, expires (epoch), price_c_h. The default
lease is 1 hour and the longest 4 hours. Nobody renews it -> it is deleted with its disk:
  1. the reaper on osgallery-dev (independent of the Mac and of every Claude session) deletes it within 5 minutes
     of the expiry;
  2. a unit inside the server, installed at first boot and armed at every boot, powers it off at the expiry
     (`renew` moves that deadline over ssh);
  3. every `status`, `up`, `renew` and `reap` on the Mac reaps as well.
`up` refuses when the reaper has no fresh heartbeat, and deletes the server again when the in-server unit cannot
be verified.

Budget: 20 EUR in any 24 hours for the whole campaign. The ledger ($W2CLOUD_HOME/ledger.jsonl, on the Mac
~/turku3d/loops/w2/cloud/ledger.jsonl) records every create, renew, delete and refusal with the hourly price from
UpCloud's own price list (plan + disk beyond the plan + IPv4 address). Every started hour of a server counts in
full. `up` and `renew` refuse when the trailing 24 hours, plus what the running leases are still committed to,
plus the request would exceed the budget. The reaper keeps its own ledger from what it observes and deletes every
campaign server when that passes the budget.

Scope: the tool changes only servers it created, recognised by the title prefix AND the label, re-read from the API
before every mutation. `vm-control`, `turku3d-bulk*` and everything else can only be listed. The credential
(~/.config/up_token on the Mac, ~/.config/turku3d/upcloud-ttl on osgallery-dev) is never printed or logged.
"""
import argparse
import base64
import calendar
import contextlib
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request

API = 'https://api.upcloud.com/1.3'
BUDGET_EUR = 20.0
WINDOW_S = 24 * 3600
DEFAULT_LEASE_S, MIN_LEASE_S, MAX_LEASE_S = 3600, 600, 4 * 3600
PREFIX = 'w2c-'
MARK = ('w2cloud', 'lease')
# Never touched, whatever their labels say. Everything without the prefix and the mark is refused anyway.
PROTECTED_UUIDS = {'00d9ba4f-6916-4929-b8ef-7bc520c0e0be'}  # vm-control
PROTECTED_TITLES = ('vm-control', 'turku3d-bulk')
MAX_DISK_GB = 200        # 200 GB Standard left behind would cost about 0.57 EUR a day
FALLBACK_DISK_GB = 30    # while the reaper can only stop servers, not delete them
TEMPLATES = {
    'ubuntu': '01000000-0000-4000-8000-000030240200',   # Ubuntu Server 24.04 LTS, 10 GB
    'nvidia': '01000000-0000-4000-8000-000030700200',   # Ubuntu 24.04 with NVIDIA drivers and CUDA, 20 GB
    'debian': '01000000-0000-4000-8000-000020080100',   # Debian 13, 10 GB
}
REAPER_HOST = 'osgallery-dev'
REAPER_DIR = 'turku3d-w2-cloud'            # on the reaper host, relative to its home
REAPER_MAX_AGE_S = 12 * 60
HOME = Path(os.environ.get('W2CLOUD_HOME') or Path.home() / 'turku3d/loops/w2/cloud')
ROLE = os.environ.get('W2CLOUD_ROLE', 'client')
UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'

DEADMAN = r'''#!/bin/bash
# W2 cloud lease, layer 2: power this server off at the lease's expiry. `cloud.py renew` moves the deadline.
mkdir -p /etc/w2cloud
echo __EXPIRES__ > /etc/w2cloud/deadline
cat > /usr/local/sbin/w2cloud-deadman <<'EOF'
#!/bin/bash
# No readable deadline counts as expired: a lease that cannot be read is not a lease.
d=$(cat /etc/w2cloud/deadline 2>/dev/null)
case "$d" in ''|*[!0-9]*) d=0 ;; esac
if [ "$(date +%s)" -ge "$d" ]; then
  logger -t w2cloud "lease expired at $d: poweroff"
  systemctl poweroff
fi
EOF
chmod 755 /usr/local/sbin/w2cloud-deadman
cat > /etc/systemd/system/w2cloud-deadman.service <<'EOF'
[Unit]
Description=W2 cloud lease: power off at the lease's expiry
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/w2cloud-deadman
EOF
cat > /etc/systemd/system/w2cloud-deadman.timer <<'EOF'
[Unit]
Description=W2 cloud lease watch
[Timer]
OnBootSec=20s
OnUnitActiveSec=30s
AccuracySec=5s
[Install]
WantedBy=timers.target
EOF
mkdir -p /etc/systemd/system/poweroff.target.d
printf '[Unit]\nJobTimeoutSec=120\nJobTimeoutAction=poweroff-force\n' > /etc/systemd/system/poweroff.target.d/w2cloud.conf
systemctl daemon-reload
systemctl enable --now w2cloud-deadman.timer
'''


class ApiError(RuntimeError):
    def __init__(self, method, path, status, error):
        super().__init__(f'UpCloud {method} {path}: HTTP {status} {error}')
        self.status, self.error = status, error


def labels_of(item):
    """Labels as a dict; servers carry {'label': [...]}, storages a plain list."""
    raw = item.get('labels') or []
    if isinstance(raw, dict):
        raw = raw.get('label') or []
    return {row['key']: row['value'] for row in raw}


def is_campaign(item):
    """True only for a server or storage this tool created: prefix AND label, and never a protected one."""
    title = item.get('title') or ''
    if item.get('uuid') in PROTECTED_UUIDS or title.startswith(PROTECTED_TITLES):
        return False
    return title.startswith(PREFIX) and labels_of(item).get(MARK[0]) == MARK[1]


class Api:
    """UpCloud client whose mutations are confined to campaign servers and their storages."""

    def __init__(self, credential=None):
        self.credential = credential
        self._auth = None
        self._servers, self._storages = set(), set()

    # Transport (replaced in tests) -------------------------------------------------
    def transport(self, method, path, body=None):
        if self._auth is None:
            path_ = Path(self.credential or os.environ.get('W2CLOUD_CREDENTIAL') or next(
                (p for p in (Path.home() / '.config/up_token', Path.home() / '.config/turku3d/upcloud-ttl') if p.is_file()),
                Path.home() / '.config/up_token')).expanduser()
            secret = path_.read_text().strip()
            self._auth = ('Basic ' + base64.b64encode(secret.encode()).decode()) if ':' in secret else 'Bearer ' + secret
        request = urllib.request.Request(API + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                         headers={'Authorization': self._auth, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                status, text = response.status, response.read().decode()
        except urllib.error.HTTPError as error:
            status, text = error.code, error.read().decode()
        try:
            value = json.loads(text) if text.strip() else {}
        except ValueError:
            value = {}
        return status, value

    # Guard ---------------------------------------------------------------------------
    def call(self, method, path, body=None):
        if method != 'GET':
            self._permit(method, path, body)
        status, value = self.transport(method, path, body)
        if status >= 400:
            raise ApiError(method, path, status, value.get('error') if isinstance(value, dict) else None)
        return value

    def _permit(self, method, path, body):
        if method == 'POST' and path == '/server':
            server = body['server']
            if is_campaign(server) and all(d['title'].startswith(PREFIX) for d in server['storage_devices']['storage_device']):
                return
            raise PermissionError('refusing to create a server without the campaign title and label')
        found = re.fullmatch(rf'/server/({UUID})(/stop|\?storages=1&backups=delete)?', path)
        if found and found.group(1) in self._servers and (method, found.group(2)) in (
                ('POST', '/stop'), ('DELETE', '?storages=1&backups=delete'), ('PUT', None)):
            return
        found = re.fullmatch(rf'/storage/({UUID})', path)
        if found and found.group(1) in self._storages and method == 'DELETE':
            return
        raise PermissionError(f'refusing {method} {path}: not a server or storage this tool created')

    def own(self, uuid):
        """Re-read a server; only when it is a campaign server may it (and its campaign storages) be changed."""
        server = self.call('GET', f'/server/{uuid}')['server']
        if not is_campaign(server):
            raise PermissionError(f'refusing to act on {server.get("title")} ({uuid}): not created by this tool')
        self._servers.add(uuid)
        for device in server.get('storage_devices', {}).get('storage_device', []):
            if (device.get('storage_title') or '').startswith(PREFIX):
                self._storages.add(device['storage'])
        return server

    def own_storage(self, uuid):
        storage = self.call('GET', f'/storage/{uuid}')['storage']
        if not is_campaign(storage):
            raise PermissionError(f'refusing to act on storage {storage.get("title")} ({uuid}): not created by this tool')
        self._storages.add(uuid)
        return storage

    # Reads ---------------------------------------------------------------------------
    def servers(self):
        return self.call('GET', '/server')['servers']['server']

    def campaign_servers(self):
        return [s for s in self.servers() if is_campaign(s)]

    def storages(self):
        return self.call('GET', '/storage/private')['storages']['storage']


class Prices:
    """UpCloud's own price list (cents per hour) and plan catalogue."""

    def __init__(self, api):
        self.plans = {p['name']: p for p in api.call('GET', '/plan')['plans']['plan']}
        self.zones = {z['name']: z for z in api.call('GET', '/price')['prices']['zone']}

    def cents(self, zone, key):
        return self.zones[zone][key]['price'] / self.zones[zone][key].get('amount', 1)

    def hourly(self, plan, zone, disk_gb, tier):
        """Cents per hour of one server: plan, disk beyond what the plan includes, and one public IPv4 address
        (counted always; some plans include it)."""
        if plan not in self.plans or f'server_plan_{plan}' not in self.zones.get(zone, {}):
            raise SystemExit(f'unknown plan {plan} in zone {zone}: see `cloud.py plans`')
        spec = self.plans[plan]
        included = (spec.get('storage_size') or 0) if (spec.get('storage_tier') or tier) == tier else 0
        parts = dict(plan=self.cents(zone, f'server_plan_{plan}'),
                     disk=max(0, disk_gb - included) * self.cents(zone, f'storage_{tier}'),
                     ipv4=self.cents(zone, 'ipv4_address'))
        return dict(parts, total=round(sum(parts.values()), 4))


# Ledger and budget -------------------------------------------------------------------
def hours_started(start, end):
    return max(1, math.ceil((end - start) / 3600 - 1e-9))


class Ledger:
    def __init__(self, path):
        self.path = Path(path)

    @contextlib.contextmanager
    def lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.with_suffix('.lock'), 'w') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def rows(self):
        if not self.path.is_file():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def append(self, event, now=None, **row):
        now = time.time() if now is None else now
        row = dict(t=round(now, 1), utc=iso(now), event=event, **row)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open('a') as out:
            out.write(json.dumps(row) + '\n')
        return row

    def leases(self):
        """uuid -> created, price_c_h, expires, deleted (None while it exists), from create/renew/delete rows."""
        leases = {}
        for row in self.rows():
            uuid = row.get('uuid')
            if row['event'] == 'create':
                leases[uuid] = dict(uuid=uuid, name=row.get('name'), owner=row.get('owner'), created=row['created'],
                                    price_c_h=row['price_c_h'], expires=row['expires'], deleted=None)
            elif row['event'] == 'renew' and uuid in leases:
                leases[uuid]['expires'] = row['expires']
            elif row['event'] == 'delete' and uuid in leases and leases[uuid]['deleted'] is None:
                leases[uuid]['deleted'] = row['t']
        return leases

    def spend(self, now):
        """EUR in the window ending now: hours already started, and hours the running leases are committed to."""
        accrued = committed = 0.0
        for lease in self.leases().values():
            start = lease['created']
            ran_to = lease['deleted'] if lease['deleted'] is not None else now
            end = lease['deleted'] if lease['deleted'] is not None else max(lease['expires'], now)
            first = max(0, math.ceil((now - WINDOW_S - start) / 3600))   # hours started before the window are outside it
            ran, total = hours_started(start, ran_to), hours_started(start, end)
            accrued += max(0, ran - first) * lease['price_c_h'] / 100
            committed += max(0, total - max(ran, first)) * lease['price_c_h'] / 100
        return dict(accrued=round(accrued, 4), committed=round(committed, 4), total=round(accrued + committed, 4))


def iso(t):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(t))


def parse_lease(text):
    found = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(m|min|h)', text.strip())
    if not found:
        raise SystemExit(f'lease "{text}": give minutes or hours, for example 30m or 2h')
    seconds = int(float(found.group(1)) * (3600 if found.group(2) == 'h' else 60))
    if not MIN_LEASE_S <= seconds <= MAX_LEASE_S:
        raise SystemExit(f'lease "{text}": between {MIN_LEASE_S // 60} minutes and {MAX_LEASE_S // 3600} hours')
    return seconds


def lease_of(server):
    """The lease a campaign server carries in its labels. An unreadable expiry is an expired lease."""
    labels = labels_of(server)

    def number(key, default):
        try:
            return float(labels.get(key, ''))
        except ValueError:
            return default
    return dict(uuid=server['uuid'], name=server['title'], owner=labels.get('owner', '?'), purpose=labels.get('purpose', ''),
                created=number('created', 0.0), expires=number('expires', 0.0), price_c_h=number('price_c_h', 0.0),
                plan=server.get('plan'), zone=server.get('zone'), state=server.get('state'))


def lease_labels(owner, purpose, created, expires, price_c_h):
    rows = dict([MARK], owner=owner, purpose=purpose, created=str(int(created)), expires=str(int(expires)),
                expires_utc=iso(expires), price_c_h=f'{price_c_h:.4f}')
    return [dict(key=k, value=v) for k, v in rows.items()]


# Deleting ----------------------------------------------------------------------------
def destroy(api, uuid, ledger, by, now=None, wait=150, sleep=time.sleep):
    """Hard-stop a campaign server and delete it with its storage. Returns the ledger row: `delete`, or `stop` when
    the credential may not delete. None when the server is already gone or still stopping (the next reap finishes it)."""
    try:
        server = api.own(uuid)
    except ApiError as error:
        if error.status == 404:
            return None
        raise
    lease = lease_of(server)
    storages = [d['storage'] for d in server.get('storage_devices', {}).get('storage_device', []) if d['storage'] in api._storages]
    deadline, found = time.monotonic() + wait, server['state']
    while server['state'] != 'stopped':
        if server['state'] == 'started':
            with contextlib.suppress(ApiError):
                api.call('POST', f'/server/{uuid}/stop', dict(stop_server=dict(stop_type='hard')))
        if time.monotonic() > deadline:
            return None
        sleep(5)
        server = api.own(uuid)
    try:
        api.call('DELETE', f'/server/{uuid}?storages=1&backups=delete')
    except ApiError as error:
        if error.status != 403:
            raise
        if any(row['event'] == 'stop' and row.get('uuid') == uuid for row in ledger.rows()):
            return dict(event='stop', uuid=uuid)
        return ledger.append('stop', now, uuid=uuid, name=lease['name'], by=by, note='stopped only: the credential may not delete')
    left = []
    for storage in storages:   # deleted with the server; a leftover is deleted by its uuid
        for _ in range(6):
            try:
                api.call('GET', f'/storage/{storage}')
            except ApiError as error:
                if error.status == 404:
                    break
            sleep(5)
        else:
            try:
                api.call('DELETE', f'/storage/{storage}')
            except ApiError:
                left.append(storage)
    now = time.time() if now is None else now
    created = lease['created'] or now
    hours = hours_started(created, now)
    return ledger.append('delete', now, uuid=uuid, name=lease['name'], owner=lease['owner'], by=by, plan=lease['plan'],
                         price_c_h=lease['price_c_h'], created=created, expires=lease['expires'],
                         minutes=round((now - created) / 60, 1), hours_billed=hours,
                         cost_eur=round(hours * lease['price_c_h'] / 100, 4), state_found=found,
                         storages_deleted=storages, storages_left=left)


def reap(api, ledger, now=None, dry_run=False, sleep=time.sleep, can_delete=True, budget=BUDGET_EUR):
    """Delete campaign servers whose lease expired and campaign storages no server holds. In the reaper role the
    ledger is built from what is observed, and passing the budget deletes every campaign server."""
    fixed = now      # tests pass a clock; otherwise each ledger row gets the time it happened
    now = time.time() if now is None else now
    known = ledger.leases()
    servers = [lease_of(s) for s in api.campaign_servers()]
    for lease in servers:       # observed leases: the reaper's own ledger, and a row for a create that lost its own
        seen = known.get(lease['uuid'])
        if seen is None:
            ledger.append('create', now, by='observed', **{k: lease[k] for k in ('uuid', 'name', 'owner', 'purpose', 'plan', 'zone',
                                                                                  'created', 'expires', 'price_c_h')})
        elif abs(seen['expires'] - lease['expires']) > 1 and seen['deleted'] is None:
            ledger.append('renew', now, by='observed', uuid=lease['uuid'], name=lease['name'], expires=lease['expires'])
    over = ROLE == 'reaper' and ledger.spend(now)['accrued'] > budget
    done = []
    for lease in servers:
        # An expiry further ahead than the longest lease is not a lease this tool wrote.
        expired = now >= lease['expires'] or lease['expires'] - now > MAX_LEASE_S + 600
        if not (expired or over):
            continue
        reason = 'lease expired' if expired else 'over budget'
        if dry_run:
            done.append(dict(name=lease['name'], uuid=lease['uuid'], would='delete', reason=reason))
            continue
        row = destroy(api, lease['uuid'], ledger, by=f'{ROLE}: {reason}', now=fixed, sleep=sleep)
        if row:
            can_delete = row['event'] == 'delete'
        done.append(dict(name=lease['name'], uuid=lease['uuid'], reason=reason, result=row['event'] if row else 'stopping'))
    for storage in api.storages():      # a campaign disk without a server costs money for nothing
        if not is_campaign(storage) or dry_run:
            continue
        detail = api.own_storage(storage['uuid'])
        age = now - calendar.timegm(time.strptime(detail['created'], '%Y-%m-%dT%H:%M:%SZ'))
        if not detail.get('servers', {}).get('server') and detail.get('state') == 'online' and age > 900:
            api.call('DELETE', f'/storage/{storage["uuid"]}')
            ledger.append('delete-storage', now, uuid=storage['uuid'], name=storage['title'], by=ROLE)
            done.append(dict(name=storage['title'], uuid=storage['uuid'], result='orphan storage deleted'))
    for uuid, lease in known.items():   # gone without this ledger's delete row: deleted by the other side or by hand
        if lease['deleted'] is None and uuid not in {s['uuid'] for s in servers} and not dry_run:
            ledger.append('delete', now, uuid=uuid, name=lease['name'], by='gone', note='no longer on the account')
    return dict(t=round(now), utc=iso(now), ok=True, role=ROLE, servers=len(servers), actions=done, can_delete=can_delete,
                spend_24h_eur=ledger.spend(now)['accrued'], over_budget=over)


# The reaper host, seen from the Mac --------------------------------------------------
def ssh_run(host, command, timeout=40, extra=()):
    return subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', *extra, host, command],
                          capture_output=True, text=True, timeout=timeout)


def reaper_state():
    """Heartbeat, delete rows and public key of the reaper host; {} when it cannot be reached."""
    try:
        out = ssh_run(REAPER_HOST, f'cd {REAPER_DIR} 2>/dev/null && cat heartbeat.json && echo && echo ==== && '
                      f'(grep -h \'"event": "delete"\' ledger.jsonl | tail -200; true) && echo ==== && '
                      f'cat ~/.ssh/id_ed25519.pub && echo ==== && sha256sum cloud.py && '
                      f'systemctl --user is-active w2cloud-reaper.timer; true')
    except subprocess.TimeoutExpired:
        return {}
    parts = out.stdout.split('====\n')
    if out.returncode != 0 or len(parts) != 4:
        return {}
    try:
        beat = json.loads(parts[0])
    except ValueError:
        return {}
    tail = parts[3].split()
    return dict(heartbeat=beat, deletes=[json.loads(line) for line in parts[1].splitlines() if line.strip()],
                key=parts[2].strip(), sha=tail[0] if tail else '', timer=tail[-1] if tail else '')


def reaper_problem(state, now):
    if not state:
        return f'the reaper on {REAPER_HOST} cannot be read (ssh failed, or `cloud.py reaper install` never ran)'
    beat = state['heartbeat']
    if state['timer'] != 'active':
        return 'the reaper timer is not active'
    if not beat.get('ok'):
        return f'the reaper\'s last run failed: {beat.get("error")}'
    if now - beat['t'] > REAPER_MAX_AGE_S:
        return f'the reaper\'s last run is {round((now - beat["t"]) / 60)} minutes old'
    return None


def reconcile(api, ledger, state, now):
    """Bring the Mac's ledger up to date: the reaper's deletions, servers created without a row, and reap."""
    mine = ledger.leases()
    for row in state.get('deletes', []):
        lease = mine.get(row.get('uuid'))
        if lease and lease['deleted'] is None and row.get('by') != 'gone':
            ledger.append('delete', row['t'], **{k: v for k, v in row.items() if k not in ('t', 'utc', 'event')})
    return reap(api, ledger, now)


# Commands ----------------------------------------------------------------------------
def local_keys():
    return [p.read_text().strip() for p in (Path.home() / '.ssh/id_ed25519.pub', Path.home() / '.ssh/id_rsa.pub') if p.is_file()]


def server_ssh(ip, command=None, timeout=40):
    known = HOME / 'known_hosts'
    args = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', '-o', 'StrictHostKeyChecking=accept-new',
            '-o', f'UserKnownHostsFile={known}', f'root@{ip}']
    if command is None:
        return args
    return subprocess.run(args + [command], capture_output=True, text=True, timeout=timeout)


def public_ip(server):
    return next((i['address'] for i in server.get('ip_addresses', {}).get('ip_address', [])
                 if i.get('access') == 'public' and i.get('family') == 'IPv4'), None)


def find(api, name):
    matches = [s for s in api.campaign_servers() if name in (s['title'], s['uuid'])]
    if len(matches) != 1:
        raise SystemExit(f'no lease named {name}: see `cloud.py status`')
    return matches[0]


def cmd_up(a, api, ledger, now=None, state=None):
    now = time.time() if now is None else now
    lease_s = parse_lease(a.lease)
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,23}', a.owner):
        raise SystemExit('--owner: your agent name, for example w2-core')
    purpose = re.sub(r'[^ -~]', '?', a.purpose.strip())[:200]
    if len(purpose) < 8:
        raise SystemExit('--purpose: say what the server is for')
    prices = Prices(api)
    gpu = a.plan.startswith('GPU-')
    zone = a.zone or ('fi-hel2' if gpu else 'fi-hel1')
    template = a.template or ('nvidia' if gpu else 'ubuntu')
    spec = prices.plans.get(a.plan) or {}
    tier = a.tier or spec.get('storage_tier') or 'standard'
    disk = a.disk or spec.get('storage_size') or (60 if gpu else 30)
    price = prices.hourly(a.plan, zone, disk, tier)
    state = reaper_state() if state is None else state
    cap = MAX_DISK_GB if (state.get('heartbeat') or {}).get('can_delete', True) else FALLBACK_DISK_GB
    if disk > cap:
        raise SystemExit(f'refused: disk {disk} GB is above the {cap} GB cap (a disk left behind is billed while idle)')
    with ledger.lock():
        if state and not a.dry_run:
            reconcile(api, ledger, state, now)
        spend = ledger.spend(now)
        ask = hours_started(now, now + lease_s) * price['total'] / 100
        plan = dict(plan=a.plan, zone=zone, disk_gb=disk, tier=tier, lease_minutes=lease_s // 60, price_c_h=price['total'],
                    price_parts=price, lease_eur=round(ask, 4), spend_24h_eur=spend['total'], budget_eur=BUDGET_EUR,
                    dry_run=a.dry_run)
        if spend['total'] + ask > BUDGET_EUR + 1e-9:
            ledger.append('refuse', now, what='up', owner=a.owner, reason='budget', **plan)
            print(json.dumps(dict(plan, refused=f'budget: {spend["total"]:.2f} EUR of the trailing 24 h is spent or committed, '
                                  f'this lease adds {ask:.2f} EUR, the limit is {BUDGET_EUR:.0f} EUR'), indent=1))
            return 3
        problem = reaper_problem(state, now)
        if problem:
            print(json.dumps(dict(plan, refused=f'no dead man\'s switch, no server: {problem}'), indent=1))
            return 4
        if a.dry_run:
            print(json.dumps(dict(plan, would='create', reaper='ok'), indent=1))
            return 0
        name = f'{PREFIX}{a.owner}-{secrets.token_hex(3)}'
        expires = now + lease_s
        labels = lease_labels(a.owner, purpose, now, expires, price['total'])
        body = dict(server=dict(
            zone=zone, title=name, hostname=name, plan=a.plan, metadata='yes', firewall='off',
            labels=dict(label=labels),
            storage_devices=dict(storage_device=[dict(action='clone', storage=TEMPLATES.get(template, template),
                                                      title=f'{name}-disk', size=disk, tier=tier, labels=[dict(key=MARK[0], value=MARK[1])])]),
            networking=dict(interfaces=dict(interface=[dict(ip_addresses=dict(ip_address=[dict(family='IPv4')]), type='public')])),
            login_user=dict(username='root', create_password='no',
                            ssh_keys=dict(ssh_key=[k for k in local_keys() + [state.get('key')] if k])),
            user_data=DEADMAN.replace('__EXPIRES__', str(int(expires)))))
        server = api.call('POST', '/server', body)['server']
        uuid = server['uuid']
        ledger.append('create', now, uuid=uuid, name=name, owner=a.owner, purpose=purpose, plan=a.plan, zone=zone, disk_gb=disk,
                      tier=tier, created=round(now, 1), expires=round(expires, 1), lease_minutes=lease_s // 60,
                      price_c_h=price['total'], price_parts=price, lease_eur=round(ask, 4), by=a.owner)
    ip = public_ip(server)
    problem = armed(api, uuid, ip, expires)
    if problem:
        destroy(api, uuid, ledger, by=f'up: {problem}')
        print(json.dumps(dict(plan, name=name, refused=f'deleted again: {problem}'), indent=1))
        return 5
    print(json.dumps(dict(name=name, uuid=uuid, ip=ip, ssh=' '.join(server_ssh(ip)), expires_utc=iso(expires),
                          **plan), indent=1))
    return 0


def armed(api, uuid, ip, expires, wait=360):
    """None when the server runs and its in-server unit holds this deadline; else what is wrong."""
    deadline = time.monotonic() + wait
    with contextlib.suppress(Exception):
        subprocess.run(['ssh-keygen', '-R', ip, '-f', str(HOME / 'known_hosts')], capture_output=True)
    seen = 'never reached over ssh'
    while time.monotonic() < deadline:
        time.sleep(5)
        if api.call('GET', f'/server/{uuid}')['server']['state'] != 'started':
            continue
        with contextlib.suppress(subprocess.TimeoutExpired):
            out = server_ssh(ip, 'systemctl is-active w2cloud-deadman.timer; cat /etc/w2cloud/deadline')
            got = out.stdout.split()
            if got == ['active', str(int(expires))]:
                return None
            seen = f'in-server unit not armed (saw {" ".join(got) or out.stderr.strip()[-80:]})'
    return seen


def cmd_renew(a, api, ledger):
    now = time.time()
    lease_s = parse_lease(a.lease)
    server = api.own(find(api, a.name)['uuid'])
    lease = lease_of(server)
    if now >= lease['expires']:
        raise SystemExit(f'refused: the lease of {lease["name"]} expired at {iso(lease["expires"])}; create a new one')
    expires = now + lease_s
    if expires <= lease['expires']:
        print(f'{lease["name"]} already runs until {iso(lease["expires"])}')
        return 0
    with ledger.lock():
        reconcile(api, ledger, reaper_state(), now)
        spend = ledger.spend(now)
        ask = (hours_started(lease['created'], expires) - hours_started(lease['created'], lease['expires'])) * lease['price_c_h'] / 100
        if spend['total'] + ask > BUDGET_EUR + 1e-9:
            ledger.append('refuse', now, what='renew', uuid=lease['uuid'], name=lease['name'], reason='budget',
                          spend_24h_eur=spend['total'], lease_eur=round(ask, 4))
            print(f'refused: {spend["total"]:.2f} EUR of the trailing 24 h is spent or committed, the renewal adds '
                  f'{ask:.2f} EUR, the limit is {BUDGET_EUR:.0f} EUR')
            return 3
        ip = public_ip(server)
        out = server_ssh(ip, f'echo {int(expires)} > /etc/w2cloud/deadline.tmp && mv /etc/w2cloud/deadline.tmp '
                         f'/etc/w2cloud/deadline && cat /etc/w2cloud/deadline')
        if out.stdout.split() != [str(int(expires))]:
            raise SystemExit(f'refused: the in-server deadline could not be moved ({out.stderr.strip()[-120:]}); '
                             f'the lease still ends {iso(lease["expires"])}')
        api.call('PUT', f'/server/{lease["uuid"]}', dict(server=dict(labels=dict(label=lease_labels(
            lease['owner'], lease['purpose'], lease['created'], expires, lease['price_c_h'])))))
        ledger.append('renew', now, uuid=lease['uuid'], name=lease['name'], owner=lease['owner'], expires=round(expires, 1),
                      price_c_h=lease['price_c_h'], added_eur=round(ask, 4))
    print(f'{lease["name"]} runs until {iso(expires)} (+{ask:.2f} EUR)')
    return 0


def cmd_down(a, api, ledger):
    server = find(api, a.name)
    with ledger.lock():
        row = destroy(api, server['uuid'], ledger, by='down')
    print(json.dumps(row or dict(name=server['title'], result='still stopping; the reaper finishes it'), indent=1))
    return 0 if row else 1


def cmd_status(a, api, ledger):
    now = time.time()
    state = reaper_state() if ROLE == 'client' else {}
    with ledger.lock():
        reaped = reconcile(api, ledger, state, now)
    leases = []
    for server in api.campaign_servers():
        lease = lease_of(server)
        detail = api.call('GET', f'/server/{server["uuid"]}')['server']
        leases.append(dict(lease, ip=public_ip(detail), expires_utc=iso(lease['expires']),
                           minutes_left=round((lease['expires'] - now) / 60, 1)))
    spend = ledger.spend(now)
    beat = state.get('heartbeat') or {}
    result = dict(leases=leases, spend_24h_eur=spend, budget_eur=BUDGET_EUR, left_eur=round(BUDGET_EUR - spend['total'], 4),
                  reaper=dict(problem=reaper_problem(state, now), last_run_utc=beat.get('utc'),
                              age_s=round(now - beat['t']) if beat else None, can_delete=beat.get('can_delete'),
                              code_current=state.get('sha') == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()),
                  reaped_now=reaped['actions'])
    if a.json:
        print(json.dumps(result, indent=1))
        return 0
    print(f'Spend, trailing 24 h: {spend["accrued"]:.2f} EUR started + {spend["committed"]:.2f} EUR committed = '
          f'{spend["total"]:.2f} of {BUDGET_EUR:.0f} EUR ({result["left_eur"]:.2f} EUR left)')
    problem = result['reaper']['problem']
    print(f'Reaper on {REAPER_HOST}: ' + (f'NOT OK: {problem}' if problem else f'ok, last run {result["reaper"]["age_s"]} s ago')
          + ('' if result['reaper']['code_current'] or not state else '; its copy of cloud.py is older: run `cloud.py reaper install`'))
    for lease in leases:
        print(f'  {lease["name"]:28s} {lease["plan"]:28s} {lease["state"]:8s} {lease["ip"] or "-":15s} owner {lease["owner"]}, '
              f'ends {lease["expires_utc"]} ({lease["minutes_left"]:.0f} min), {lease["price_c_h"] / 100:.4f} EUR/h: {lease["purpose"]}')
    if not leases:
        print('  no leases')
    for action in reaped['actions']:
        print(f'  reaped now: {action}')
    return 0


def cmd_reap(a, api, ledger):
    """The timer's entry. Always leaves a heartbeat, so a failing reaper is visible to `up`."""
    beat_path = HOME / 'heartbeat.json'
    previous = json.loads(beat_path.read_text()) if beat_path.is_file() else {}
    try:
        with ledger.lock():
            beat = reap(api, ledger, dry_run=a.dry_run, can_delete=previous.get('can_delete', True))
    except Exception as error:      # noqa: BLE001 - the heartbeat must say what went wrong
        beat = dict(t=round(time.time()), utc=iso(time.time()), ok=False, role=ROLE, error=f'{type(error).__name__}: {error}'[:300],
                    can_delete=previous.get('can_delete', True))
    if not a.dry_run:
        HOME.mkdir(parents=True, exist_ok=True)
        beat_path.with_suffix('.tmp').write_text(json.dumps(beat))
        beat_path.with_suffix('.tmp').replace(beat_path)
        if beat.get('actions') or not beat['ok']:
            with (HOME / 'reaper.log').open('a') as out:
                out.write(json.dumps(beat) + '\n')
    print(json.dumps(beat))
    return 0 if beat['ok'] else 1


def cmd_ssh(a, api, ledger):
    ip = public_ip(api.call('GET', f'/server/{find(api, a.name)["uuid"]}')['server'])
    os.execvp('ssh', server_ssh(ip) + a.command)


def cmd_plans(a, api, ledger):
    prices = Prices(api)
    available = {}
    with contextlib.suppress(ApiError):
        for zone, kinds in api.call('GET', '/device/availability').items():
            for name, row in kinds.get('gpu_plans', {}).items():
                available.setdefault(name, {})[zone] = row['amount']
    rows = []
    for name, spec in prices.plans.items():
        gpu = name.startswith('GPU-')
        if a.gpu != gpu and not a.all:
            continue
        if not gpu and not a.all and not name.startswith(('CLOUDNATIVE-', 'STARTER-', 'DEV-')):
            continue
        zone = next(iter(available.get(name, {})), 'fi-hel2' if gpu else 'fi-hel1')
        if gpu and name not in available and not a.all:
            continue
        cents = prices.cents(zone, f'server_plan_{name}')
        rows.append((cents, name, spec, zone))
    print(f'{"plan":38s} {"zone":8s} {"cores":>5s} {"GB":>5s} {"GPU":28s} {"free":>4s} {"EUR/h":>8s} {"h/day in 20 EUR":>15s}  billed when off')
    for cents, name, spec, zone in sorted(rows):
        gpu = f'{spec.get("gpu_amount")} x {spec.get("gpu_model")}' if spec.get('gpu_amount') else ''
        off = 'no (disk and IP only)' if name.startswith(('CLOUDNATIVE-', 'GPU-')) else 'yes'
        free = str(available[name][zone]) if name in available else ''
        print(f'{name:38s} {zone:8s} {spec["core_number"]:5d} {spec["memory_amount"] // 1024:5d} {gpu:28s} {free:>4s} '
              f'{cents / 100:8.4f} {min(24.0, BUDGET_EUR / (cents / 100)):15.1f}  {off}')
    print('Prices are the plan alone; a lease adds the disk beyond the plan (Standard 0.0118 c/GB/h) and 0.48 c/h for IPv4.')
    return 0


def cmd_inventory(a, api, ledger):
    prices = Prices(api)
    total = 0.0
    print('Servers')
    for server in api.servers():
        cents = prices.cents(server['zone'], f'server_plan_{server["plan"]}') if f'server_plan_{server["plan"]}' in prices.zones[server['zone']] else 0
        total += cents * 24 / 100
        created = time.strftime('%Y-%m-%d', time.gmtime(server['created'])) if server.get('created') else '?'
        print(f'  {server["title"]:24s} {server["plan"]:24s} {server["state"]:8s} {server["zone"]} created {created} '
              f'{cents * 24 / 100:6.2f} EUR/day {"(campaign lease)" if is_campaign(server) else ""}')
    print('Storages (private, backups included)')
    for storage in api.storages():
        billed = storage.get('part_of_plan') != 'yes'
        key = 'storage_backup' if storage['type'] == 'backup' else f'storage_{storage.get("tier", "maxiops")}'
        cents = storage['size'] * prices.cents(storage['zone'], key) if billed else 0
        total += cents * 24 / 100
        print(f'  {storage["title"]:24s} {storage["size"]:5d} GB {storage.get("tier", ""):9s} {storage["type"]:7s} {storage["state"]:8s} '
              f'{storage["zone"]} created {storage.get("created", "?")[:10]} {cents * 24 / 100:6.2f} EUR/day '
              f'{"(part of the server plan)" if not billed else ""}')
    print('Network gateways')
    for gateway in api.call('GET', '/gateway'):
        cents = prices.cents(gateway['zone'], f'network_gateway_{gateway["plan"]}')
        total += cents * 24 / 100
        print(f'  {gateway["name"]:24s} {gateway["plan"]:24s} {gateway["operational_state"]:8s} {gateway["zone"]} '
              f'created {gateway["created_at"][:10]} {cents * 24 / 100:6.2f} EUR/day')
    print(f'Total about {total:.2f} EUR/day (list prices; traffic and extra IP addresses not included)')
    return 0


SERVICE = f'''[Unit]
Description=W2 cloud lease reaper: delete UpCloud campaign servers whose lease expired

[Service]
Type=oneshot
Environment=W2CLOUD_HOME=%h/{REAPER_DIR} W2CLOUD_ROLE=reaper W2CLOUD_CREDENTIAL=%h/.config/turku3d/upcloud-ttl
ExecStart=/usr/bin/python3 %h/{REAPER_DIR}/cloud.py reap
TimeoutStartSec=280
'''
TIMER = '''[Unit]
Description=W2 cloud lease reaper, every 5 minutes

[Timer]
OnCalendar=*:0/5
AccuracySec=10s
Persistent=true

[Install]
WantedBy=timers.target
'''


def cmd_reaper(a, api, ledger):
    if a.action == 'install':
        staging = HOME / 'reaper-install'
        staging.mkdir(parents=True, exist_ok=True)
        (staging / 'w2cloud-reaper.service').write_text(SERVICE)
        (staging / 'w2cloud-reaper.timer').write_text(TIMER)
        subprocess.run(['ssh', REAPER_HOST, f'mkdir -p {REAPER_DIR} .config/systemd/user'], check=True)
        subprocess.run(['scp', '-q', str(Path(__file__).resolve()), f'{REAPER_HOST}:{REAPER_DIR}/cloud.py'], check=True)
        subprocess.run(['scp', '-q', str(staging / 'w2cloud-reaper.service'), str(staging / 'w2cloud-reaper.timer'),
                        f'{REAPER_HOST}:.config/systemd/user/'], check=True)
        out = ssh_run(REAPER_HOST, 'systemctl --user daemon-reload && systemctl --user enable -q --now w2cloud-reaper.timer && '
                      'systemctl --user start w2cloud-reaper.service; loginctl show-user $USER -p Linger', timeout=320)
        print(out.stdout.strip(), out.stderr.strip())
    state = reaper_state()
    print(json.dumps(dict(problem=reaper_problem(state, time.time()), heartbeat=state.get('heartbeat'), timer=state.get('timer'),
                          code_current=state.get('sha') == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()), indent=1))
    return 0 if state and not reaper_problem(state, time.time()) else 1


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='verb', required=True)
    up = sub.add_parser('up')
    up.add_argument('--plan', required=True)
    up.add_argument('--owner', required=True, help='your agent name')
    up.add_argument('--purpose', required=True)
    up.add_argument('--lease', default='1h', help='10m to 4h (default 1h)')
    up.add_argument('--zone', help='default fi-hel1, GPU plans fi-hel2')
    up.add_argument('--disk', type=int, help='GB (default: what the plan includes, else 30, GPU 60)')
    up.add_argument('--tier', choices=('standard', 'maxiops', 'hdd'))
    up.add_argument('--template', help=f'{", ".join(TEMPLATES)} or a template uuid (default ubuntu, GPU plans nvidia)')
    up.add_argument('--dry-run', action='store_true', help='check the budget and the reaper, create nothing')
    renew = sub.add_parser('renew')
    renew.add_argument('name')
    renew.add_argument('--lease', default='1h')
    sub.add_parser('down').add_argument('name')
    sub.add_parser('status').add_argument('--json', action='store_true')
    sub.add_parser('reap').add_argument('--dry-run', action='store_true')
    ssh = sub.add_parser('ssh')
    ssh.add_argument('name')
    ssh.add_argument('command', nargs=argparse.REMAINDER)
    plans = sub.add_parser('plans')
    plans.add_argument('--gpu', action='store_true')
    plans.add_argument('--all', action='store_true')
    sub.add_parser('inventory')
    sub.add_parser('reaper').add_argument('action', choices=('install', 'status'))
    a = p.parse_args(argv)
    if ROLE == 'reaper' and a.verb not in ('reap', 'status', 'inventory', 'plans'):
        raise SystemExit(f'{a.verb} runs on the Mac; this host only reaps')
    handler = dict(up=cmd_up, renew=cmd_renew, down=cmd_down, status=cmd_status, reap=cmd_reap, ssh=cmd_ssh,
                   plans=cmd_plans, inventory=cmd_inventory, reaper=cmd_reaper)[a.verb]
    return handler(a, Api(), Ledger(HOME / 'ledger.jsonl'))


if __name__ == '__main__':
    raise SystemExit(main())
