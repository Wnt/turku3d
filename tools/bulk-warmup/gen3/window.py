#!/usr/bin/env python3
"""osgallery-dev: open and close the 64-core window of the bulk host in server.json, with a 55-minute backstop.

  window.py up   [--dry-run]   preflight, stop, plan 64 cores, start, arm the backstop, verify
                               the in-server TTL (/run/bulk-ttl) over ssh
  window.py down [--dry-run]   stop, plan 2 cores, start (the final pull continues), disarm
  window.py status | disarm

Acts only on that server (upcloud.SERVER): stop, modify plan, start. Never creates, clones or
deletes anything. `up` refuses to resize unless both TTLs can be armed: the in-server unit is
enabled on the host and user timers survive logout (lingering). If the in-server TTL is not
armed within 5 minutes of the start, it closes the window at once. The backstop timer runs
`down` once at start + TTL and is a transient unit, so it disarms itself after firing.
The credential (~/.config/turku3d/upcloud-ttl, a restricted sub-account) is never printed.
"""
import argparse
import getpass
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import upcloud  # noqa: E402

HOST = f"root@{upcloud.CONFIG['ip']}"
UNIT = 'bulk-window-down'
LOG = Path.home() / 'turku3d-local/loops/bulkgen3/window.log'
API_LOG = Path.home() / 'turku3d-local/loops/bulkgen3/window-api.jsonl'


def log(**row):
    row = dict(t=round(time.time(), 3), **row)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open('a') as out:
        out.write(json.dumps(row) + '\n')
    print(json.dumps(row), flush=True)


def ssh(command, timeout=30):
    return subprocess.run(['ssh', '-i', str(Path.home() / '.ssh/bulk_gen3'), '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', HOST, command],
                          capture_output=True, text=True, timeout=timeout)


def user_timers():
    out = subprocess.run(['systemctl', '--user', 'list-timers', '--all', '--no-legend', f'{UNIT}*'],
                         capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if UNIT in line]


def disarm():
    for unit in (f'{UNIT}.timer', f'{UNIT}.service', f'{UNIT}-dryrun.timer', f'{UNIT}-dryrun.service'):
        subprocess.run(['systemctl', '--user', 'stop', unit], capture_output=True)
    subprocess.run(['systemctl', '--user', 'reset-failed'], capture_output=True)
    left = user_timers()
    log(event='disarmed', timers_left=left)
    return not left


def preflight(dry_run, credential):
    problems = []
    if not dry_run and not Path(credential).expanduser().is_file():
        problems.append(f'no credential at {credential}')
    linger = subprocess.run(['loginctl', 'show-user', getpass.getuser(), '-p', 'Linger', '--value'],
                            capture_output=True, text=True).stdout.strip()
    if linger != 'yes':
        problems.append('lingering is off for this user: user timers would not survive logout')
    if subprocess.run(['systemd-run', '--user', '--version'], capture_output=True).returncode:
        problems.append('systemd-run --user is unavailable')
    enabled = ssh('systemctl is-enabled bulk-ttl.service bulk-ttl.timer && test -x /usr/local/sbin/bulk-ttl')
    if enabled.returncode:
        problems.append('in-server TTL is not installed and enabled on the bulk host: ' + enabled.stdout.strip().replace('\n', ' '))
    if user_timers():
        problems.append('a window-down timer is already armed: run `window.py status` / `disarm` first')
    return problems


def arm_backstop(at, dry_run, credential):
    """A transient user timer: it runs `down` once and disappears; never re-arms itself."""
    unit = UNIT + ('-dryrun' if dry_run else '')
    delay = max(1, int(at - time.time()))
    command = [sys.executable, str(Path(__file__).resolve()), 'down', '--reason', 'backstop',
               '--credential', str(credential)] + (['--dry-run'] if dry_run else [])
    done = subprocess.run(['systemd-run', '--user', f'--unit={unit}', f'--on-active={delay}s',
                           '--timer-property=AccuracySec=1s', *command], capture_output=True, text=True)
    log(event='backstop-armed' if not done.returncode else 'backstop-failed', unit=unit, fires_in_s=delay,
        fires_at=round(at), stderr=done.stderr.strip()[-300:])
    return done.returncode == 0


def verify_in_server(timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        out = ssh('cat /run/bulk-ttl 2>/dev/null; grep -c ^processor /proc/cpuinfo')
        lines = out.stdout.strip().splitlines()
        if out.returncode == 0 and len(lines) == 2:
            ttl, cpus = json.loads(lines[0]), int(lines[1])
            log(event='in-server-ttl', cpus=cpus, ttl=ttl)
            return ttl
        time.sleep(10)
    return None


def up(a):
    problems = preflight(a.dry_run, a.credential)
    if problems:
        log(event='refused', problems=problems)
        return 2
    client = upcloud.Client(a.credential, log=API_LOG, dry_run=a.dry_run)
    log(event='window-up', dry_run=a.dry_run, before=_state(client))
    # Arm before resizing: if anything below fails, the window still closes 55 min from now.
    if not arm_backstop(time.time() + a.ttl_min * 60, a.dry_run, a.credential):
        log(event='refused', problems=['backstop timer could not be armed'])
        return 2
    try:
        client.stop()
        client.set_plan(upcloud.LARGE)
        started = client.start()
    except Exception as error:
        log(event='closing', reason=f'resize failed: {error}')
        return down(a, reason='resize-failed')
    start = time.time()
    log(event='started', plan=started.get('plan'), cores=started.get('core_number'), start=round(start))
    disarm()
    if not arm_backstop(start + a.ttl_min * 60, a.dry_run, a.credential):
        log(event='closing', reason='backstop could not be re-armed')
        return down(a, reason='backstop-failed')
    if a.dry_run:
        log(event='dry-run', note='in-server TTL check skipped: the small plan never arms it')
        return 0
    ttl = verify_in_server()
    if not ttl or ttl.get('cpus', 0) < 32:
        log(event='closing', reason='in-server TTL not armed within 5 min')
        return down(a, reason='in-server-ttl-missing')
    log(event='window-open', poweroff_at=ttl['poweroff_at'], backstop_at=round(start + a.ttl_min * 60))
    return 0


def down(a, reason=None):
    client = upcloud.Client(a.credential, log=API_LOG, dry_run=a.dry_run)
    log(event='window-down', reason=reason or a.reason, dry_run=a.dry_run, before=_state(client))
    client.stop()
    client.set_plan(upcloud.SMALL)
    started = client.start()
    log(event='closed', plan=started.get('plan'), cores=started.get('core_number'), state=started.get('state'))
    if reason != 'backstop' and a.reason != 'backstop':
        disarm()  # A manual close removes the pending backstop; a fired backstop is already gone.
    return 0


def _state(client):
    server = client.server()
    return dict(state=server.get('state'), plan=server.get('plan'))


def status(a):
    client = upcloud.Client(a.credential, log=API_LOG, dry_run=a.dry_run)
    out = ssh('cat /run/bulk-ttl 2>/dev/null || echo none; grep -c ^processor /proc/cpuinfo; systemctl is-enabled bulk-ttl.service bulk-ttl.timer')
    print(json.dumps(dict(server=_state(client), timers=user_timers(), host=out.stdout.strip().splitlines())))
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=('up', 'down', 'status', 'disarm'))
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--credential', type=Path, default=Path('~/.config/turku3d/upcloud-ttl'))
    p.add_argument('--ttl-min', type=float, default=55)
    p.add_argument('--reason', default='manual')
    a = p.parse_args()
    if a.command == 'disarm':
        return 0 if disarm() else 1
    return dict(up=up, down=lambda x: down(x), status=status)[a.command](a)


if __name__ == '__main__':
    raise SystemExit(main())
