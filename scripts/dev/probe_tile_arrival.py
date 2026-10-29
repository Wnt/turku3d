"""One bounded arrival investigation: real OCI builds and real client rendering in parallel."""
import argparse
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit')
    parser.add_argument('--reuse-caches')
    parser.add_argument('--client-only', action='store_true', help='Serving/client changes do not compile new geometry')
    parser.add_argument('--browser-slot-held', action='store_true')
    parser.add_argument('--label-prefix', default='')
    parser.add_argument('--rounds', type=int, choices=(1, 2), default=1)
    parser.add_argument('--fast-fly', action='store_true')
    parser.add_argument('--tiles', nargs='+', default=['t0_0_0', 't0_0_-2', 't0_1_0'])
    parser.add_argument('--passes', nargs='+')
    args = parser.parse_args()
    if not args.client_only and not (args.commit and args.reuse_caches):
        parser.error('--commit and --reuse-caches are required for generator measurements')
    generator = [sys.executable, 'scripts/dev/probe_texture_speed.py', '--commit', args.commit,
                 '--reuse-caches', args.reuse_caches, '--tiles', *args.tiles, '--audit']
    if args.passes:
        generator += ['--passes', *args.passes]

    def render():
        routes = [('drive-50', '120', '50'), ('fly-200', '60', '200')]
        if args.fast_fly:
            routes.append(('fast-fly', '30', '500'))
        for round_index in range(args.rounds):
            for label, seconds, speed in routes:
                label = f'{args.label_prefix}{label}' + (f'-{round_index + 1}' if args.rounds > 1 else '')
                command = ['node', 'game/scripts/tile-arrival-profile.mjs', label, seconds, speed]
                if not args.browser_slot_held:
                    command = [sys.executable, 'scripts/dev/host_slots.py', 'browser', '--', *command]
                run = subprocess.run(command)
                if run.returncode:
                    return run.returncode
        return 0

    if args.client_only:
        return render()

    # The outer post-deploy runner owns the hard 600s deadline and one build slot;
    # the browser also takes its GPU slot. OCI and the local renderer are independent.
    with ThreadPoolExecutor(max_workers=2) as pool:
        builds = pool.submit(subprocess.run, generator)
        client = pool.submit(render)
        return int(builds.result().returncode != 0 or client.result() != 0)


if __name__ == '__main__':
    raise SystemExit(main())
