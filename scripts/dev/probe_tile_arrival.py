"""One bounded arrival investigation: real OCI builds and real client rendering in parallel."""
import argparse
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--reuse-caches', required=True)
    parser.add_argument('--tiles', nargs='+', default=['t0_0_0', 't0_0_-2', 't0_1_0'])
    parser.add_argument('--passes', nargs='+')
    parser.add_argument('--label-prefix', default='')
    args = parser.parse_args()
    generator = [sys.executable, 'scripts/dev/probe_texture_speed.py', '--commit', args.commit,
                 '--reuse-caches', args.reuse_caches, '--tiles', *args.tiles, '--audit']
    if args.passes:
        generator += ['--passes', *args.passes]

    def render():
        for label, seconds, speed in [('drive-50', '120', '50'), ('fly-200', '60', '200')]:
            command = [sys.executable, 'scripts/dev/host_slots.py', 'browser', '--', 'node',
                       'game/scripts/tile-arrival-profile.mjs', args.label_prefix + label, seconds, speed]
            run = subprocess.run(command)
            if run.returncode:
                return run.returncode
        return 0

    # The outer post-deploy runner owns the hard 600s deadline and one build slot;
    # the browser also takes its GPU slot. OCI and the local renderer are independent.
    with ThreadPoolExecutor(max_workers=2) as pool:
        builds = pool.submit(subprocess.run, generator)
        client = pool.submit(render)
        return int(builds.result().returncode != 0 or client.result() != 0)


if __name__ == '__main__':
    raise SystemExit(main())
