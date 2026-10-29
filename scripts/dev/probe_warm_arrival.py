"""Bounded post-deploy warm-cache evidence: online real OCI builds and shared arrival routes."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    args = parser.parse_args()

    def builds():
        return subprocess.call([sys.executable, 'scripts/dev/probe_warm_cache.py', '--commit', args.commit])

    def arrivals():
        for phase in ('after-deploy', 'steady'):
            for label, seconds, speed in [('50', '120', '50'), ('200', '60', '200'), ('fast-fly', '30', '500')]:
                result = subprocess.call([sys.executable, 'scripts/dev/host_slots.py', 'browser', '--',
                                          'node', 'game/scripts/tile-arrival-profile.mjs',
                                          'warm-' + phase + '-' + label, seconds, speed])
                if result:
                    return result
        return 0

    # codex-task's outer selective run owns the ten-minute deadline, including slots.
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(builds)
        second = pool.submit(arrivals)
        return int(first.result() != 0 or second.result() != 0)


if __name__ == '__main__':
    raise SystemExit(main())
