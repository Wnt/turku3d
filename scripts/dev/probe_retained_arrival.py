"""Shared restart/steady arrival yardstick alongside ten real OCI drivers, within 600s."""
import json
import os
import shlex
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from deploy_drafts import HOME
from facade_publish import remote


def drivers():
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    source = f'{HOME}/sources/{commit}'
    output = f'/srv/turku3d/work/arrival-serve-last-good/ten-{uuid.uuid4().hex[:12]}.json'
    command = ['timeout', '--kill-after=3', '540', f'{source}/tools/remote-drafts/run.sh',
               'python', f'{source}/tools/remote-drafts/ten-drivers.py', '--output', output,
               '--seconds', '120', '--speed-kmh', '50', '--drivers', '10', '--start-office']
    print(remote('TURKU3D_DRAFT_SOURCE=' + shlex.quote(source) + ' ' + shlex.join(command)), flush=True)
    result = json.loads(remote('cat ' + shlex.quote(output)))
    path = Path(os.environ['TURKU3D_WORK']) / 'reports/ten-drivers.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + '\n')
    return result['summary']['passed']


def main():
    command = [sys.executable, 'scripts/dev/host_slots.py', 'browser', '--', sys.executable,
               'scripts/dev/probe_tile_arrival.py', '--client-only', '--browser-slot-held',
               '--rounds', '2', '--fast-fly', '--label-prefix', 'after-']
    with ThreadPoolExecutor(max_workers=2) as pool:
        clients = pool.submit(subprocess.run, command)
        ten = pool.submit(drivers)
        browser_ok = clients.result().returncode == 0
        drivers_ok = ten.result()
    return int(not (browser_ok and drivers_ok))


if __name__ == '__main__':
    raise SystemExit(main())
