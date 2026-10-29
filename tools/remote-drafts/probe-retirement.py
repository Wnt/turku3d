"""Send systemd's parent-only termination to an isolated real full-pass producer."""
import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def get(url):
    with urllib.request.urlopen(url, timeout=2) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--assets', type=Path, default=Path('/srv/turku3d/work/coarse'))
    parser.add_argument('--churn-demand', action='store_true',
                        help='Replace/expire the full-pass lease instead of terminating its parent')
    args = parser.parse_args()
    work = args.root
    root = work / 'coarse'
    root.mkdir(parents=True, exist_ok=False)
    assets = args.assets
    tile = 't0_0_0'
    for name in ('.inputs', '.stages'):
        source = assets / name / tile
        if source.exists():
            shutil.copytree(source, root / name / tile)
    # Keep compilation cached, but require the selected full pass to repackage.
    shutil.rmtree(root / '.stages' / tile / 'package', ignore_errors=True)
    for pattern in ('shared', '.surface-library*', '.compiled-textures'):
        for source in assets.glob(pattern):
            if source.is_dir():
                shutil.copytree(source, root / source.name)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, TURKU3D_WORK=str(work), TURKU3D_DRAFT_PORT=str(port), TURKU3D_DRAFT_STANDBY='0')
    base = f'http://127.0.0.1:{port}/coarse/'
    started = time.monotonic()
    with (work / 'service.log').open('w') as log:
        proc = subprocess.Popen([sys.executable, str(Path(__file__).with_name('public.py'))],
                                env=env, stdout=log, stderr=log, start_new_session=True)
        try:
            while time.monotonic() - started < 40:
                try:
                    get(base + 'health')
                    break
                except (OSError, urllib.error.URLError):
                    if proc.poll() is not None:
                        raise RuntimeError('isolated producer exited during startup')
                    time.sleep(.1)
            else:
                raise TimeoutError('isolated producer startup')
            while time.monotonic() - started < 120:
                get(base + f'demand?client=retirement-test&tiles={tile}&visible={tile}')
                get(base + f'{tile}/manifest.json?client=retirement-test')
                status = root / '.scheduler.json'
                if status.exists() and tile in json.loads(status.read_text()).get('upgrading', []):
                    break
                time.sleep(.1)
            else:
                raise TimeoutError('full pass did not start')
            signalled = time.monotonic()
            changed_at = time.time()
            if args.churn_demand:
                # Remove a real running upgrade from the want-list, retain a
                # different urgent crossing, then stop heartbeats past the TTL.
                # None of these HTTP updates may kill the packaging children.
                for tiles in ['', 't0_9_9', '', 't0_8_9']:
                    get(base + f'demand?client=retirement-test&tiles={tiles}&urgent={tiles}')
                while time.monotonic() - signalled < 300:
                    manifest = json.loads((root / tile / 'manifest.json').read_text())
                    if manifest['pass_kind'] == 'full' and time.monotonic() - signalled > 13:
                        break
                    if proc.poll() is not None:
                        raise RuntimeError('producer exited during demand churn')
                    time.sleep(.25)
                else:
                    raise TimeoutError('full pass did not finish after its lease expired')
            proc.send_signal(signal.SIGTERM)  # KillMode=mixed: children receive no signal.
            assert proc.wait(timeout=300 if not args.churn_demand else 15) == 0
            manifest = json.loads((root / tile / 'manifest.json').read_text())
            assert manifest['pass_kind'] == 'full'
            assert len(manifest['collision']) == 16
            text = (work / 'service.log').read_text()
            jobs = [json.loads(line) for line in text.splitlines() if line.startswith('{"job":')]
            assert any(row['pass'] == 'first' and row['outcome'] == 'built' for row in jobs), jobs
            assert any(row['pass'] == 'full' and row['outcome'] == 'built' for row in jobs), jobs
            assert not any(row['outcome'] in ('FAILED', 'cancelled') for row in jobs), jobs
            if args.churn_demand:
                assert any(row['pass'] == 'full' and row['at'] > changed_at for row in jobs), jobs
            result = dict(root=str(work), seconds=round(time.monotonic()-started, 3),
                          mode='demand-churn' if args.churn_demand else 'retirement',
                          drain_seconds=round(time.monotonic()-signalled, 3), jobs=jobs,
                          buildings=manifest['buildings'], collision=len(manifest['collision']))
            (work / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
            print(json.dumps(result))
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()


if __name__ == '__main__':
    main()
