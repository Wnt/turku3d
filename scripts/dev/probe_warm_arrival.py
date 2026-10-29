"""Bounded post-deploy warm-cache evidence: online real OCI builds and shared arrival routes."""
import argparse
import os
from concurrent.futures import ThreadPoolExecutor
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--arrival-only', action='store_true')
    parser.add_argument('--browser-admitted', action='store_true')
    parser.add_argument('--phase', nargs='+', choices=['after-deploy', 'steady'],
                        default=['after-deploy', 'steady'])
    args = parser.parse_args()
    if args.browser_admitted:
        from host_slots import snapshot
        assert any(e['pid'] == os.getppid() and e['kind'] == 'browser' and e['state'] == 'running'
                   for e in snapshot()), 'Expected the parent to own browser admission'

    def builds():
        return subprocess.call([sys.executable, 'scripts/dev/probe_warm_cache.py', '--commit', args.commit])

    def arrivals():
        for phase in args.phase:
            for label, seconds, speed in [('50', '120', '50'), ('200', '60', '200'), ('fast-fly', '30', '500')]:
                command = ['node', 'game/scripts/tile-arrival-profile.mjs',
                           'warm-' + phase + '-' + label, seconds, speed]
                if not args.browser_admitted:
                    command = [sys.executable, 'scripts/dev/host_slots.py', 'browser', '--', *command]
                result = subprocess.call(command)
                if result:
                    return result
        return 0

    if args.arrival_only:
        return arrivals()

    # codex-task's outer selective run owns the ten-minute deadline, including slots.
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(builds)
        second = pool.submit(arrivals)
        return int(first.result() != 0 or second.result() != 0)


if __name__ == '__main__':
    raise SystemExit(main())
