#!/usr/bin/env python3
"""osgallery-dev: backstop for the bulk host while it serves as a build helper (helper-ttl.sh powers it off itself).

  helper-guard.py check     (helper-guard.timer, every 5 min) stop the server when it is on and its in-server deadline
                            (helper-ttl.sh status over ssh) passed more than GRACE minutes ago, or when it cannot be
                            reached and the last deadline seen passed GRACE minutes ago, or no deadline was ever seen
                            and it has been on for more than TTL + GRACE minutes
  helper-guard.py install | remove    the user timer

Acts only on server.json's server (upcloud.SERVER) and only stops it: no plan change, nothing created or deleted. A
stopped helper is started again by the next helper-build.sh. The credential (~/.config/turku3d/upcloud-ttl) is
never printed. State: ~/turku3d-local/loops/bulkgen3/helper-guard.json; log: helper-guard.log beside it.
"""
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import upcloud  # noqa: E402

D = Path.home() / 'turku3d-local/loops/bulkgen3'
STATE, LOG = D / 'helper-guard.json', D / 'helper-guard.log'
CREDENTIAL = Path.home() / '.config/turku3d/upcloud-ttl'
GRACE, TTL = 10 * 60, 55 * 60
UNIT = 'bulk-helper-guard'


def log(**row):
    with LOG.open('a') as out:
        out.write(json.dumps(dict(t=round(time.time()), **row)) + '\n')


def check():
    state = json.loads(STATE.read_text()) if STATE.is_file() else {}
    client = upcloud.Client(CREDENTIAL)
    server = client.server()
    now = time.time()
    if server['state'] != 'started':
        state.pop('on_since', None)
        STATE.write_text(json.dumps(state))
        return 0
    state.setdefault('on_since', now)
    out = subprocess.run(['ssh', '-i', str(Path.home() / '.ssh/bulk_gen3'), '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                          f"root@{upcloud.CONFIG['ip']}", '/home/wnt/turku3d-bulk-gen3/helper-ttl.sh status'],
                         capture_output=True, text=True, timeout=60)
    try:
        seen = json.loads(out.stdout)
        state['deadline'], state['busy'] = seen['deadline'], seen['busy']
    except ValueError:
        seen = None
    deadline = state.get('deadline') or state['on_since'] + TTL
    if now > deadline + GRACE and not (seen and seen['busy']):
        log(event='stop', reason='past deadline' if seen else 'unreachable past last deadline', deadline=deadline,
            plan=server.get('plan'))
        client.stop()
        state.pop('on_since', None)
    STATE.write_text(json.dumps(state))
    return 0


def install():
    here = Path(__file__).resolve()
    subprocess.run(['systemctl', '--user', 'stop', f'{UNIT}.timer'], capture_output=True)
    subprocess.run(['systemd-run', '--user', f'--unit={UNIT}', '--on-active=60', '--on-unit-active=300',
                    '--timer-property=AccuracySec=10s', sys.executable, str(here), 'check'], check=True)
    log(event='installed')
    return 0


def remove():
    for unit in (f'{UNIT}.timer', f'{UNIT}.service'):
        subprocess.run(['systemctl', '--user', 'stop', unit], capture_output=True)
    log(event='removed')
    return 0


if __name__ == '__main__':
    raise SystemExit(dict(check=check, install=install, remove=remove)[sys.argv[1] if len(sys.argv) > 1 else 'check']())
