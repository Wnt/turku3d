"""Owned ten-minute-budget evidence for shared texture compilation on deployed OCI."""
import argparse
import shlex
import subprocess
import time
import uuid

from deploy_drafts import HOME
from facade_publish import remote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    parser.add_argument("--assembly-baseline")
    parser.add_argument("--atlas-baseline")
    parser.add_argument("--demand-churn", action='store_true')
    parser.add_argument('--reuse-caches')
    parser.add_argument('--visual-baseline')
    parser.add_argument('--audit', action='store_true')
    parser.add_argument('--tiles', nargs='+', default=['t0_0_0', 't0_0_-2'])
    args = parser.parse_args()
    source = remote(f'readlink -f {HOME}/repo')
    frozen = f'{HOME}/sources/{args.commit}'
    remote(f'test -d {shlex.quote(frozen)}')
    if source != frozen:
        print(f'Active source moved to {source}; checking frozen published {args.commit}', flush=True)
    source = frozen
    root = f'/srv/turku3d/work/tile-arrival-parity-{uuid.uuid4().hex[:12]}'
    arguments = ['timeout', '--kill-after=3', '540', f'{source}/tools/remote-drafts/run.sh',
                         'python', f'{source}/tools/remote-drafts/profile-textures.py', '--root', root,
                         '--assets', '/srv/turku3d/work/coarse', '--tiles', *args.tiles]
    if args.assembly_baseline:
        arguments += ['--assembly-baseline', f'{HOME}/sources/{args.assembly_baseline}/pipeline/turku3d/wall_assembly.py']
    if args.atlas_baseline:
        arguments += ['--atlas-baseline', args.atlas_baseline]
    if args.reuse_caches:
        arguments += ['--reuse-caches', args.reuse_caches]
    if args.visual_baseline:
        arguments += ['--visual-baseline', args.visual_baseline]
    if args.audit:
        arguments += ['--audit']
    command = shlex.join(arguments)
    started = time.monotonic()
    try:
        print(remote(f'TURKU3D_DRAFT_SOURCE={shlex.quote(source)} ' + command), flush=True)
        if args.demand_churn:
            remaining = int(540 - (time.monotonic() - started))
            if remaining < 20:
                raise SystemExit('No remaining time for the real demand-churn case')
            command = shlex.join(['timeout', '--kill-after=3', str(remaining),
                                 f'{source}/tools/remote-drafts/run.sh', 'python',
                                 f'{source}/tools/remote-drafts/probe-retirement.py',
                                 '--root', root + '-churn', '--assets', root, '--churn-demand'])
            print(remote(f'TURKU3D_DRAFT_SOURCE={shlex.quote(source)} ' + command), flush=True)
    except subprocess.CalledProcessError as error:
        print(error.stdout, flush=True)
        raise SystemExit(error.stderr[-8000:]) from error


if __name__ == '__main__':
    main()
