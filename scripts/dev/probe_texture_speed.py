"""Owned ten-minute-budget evidence for shared texture compilation on deployed OCI."""
import argparse
import shlex
import uuid

from deploy_drafts import HOME
from facade_publish import remote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    parser.add_argument("--assembly-baseline")
    args = parser.parse_args()
    source = remote(f'readlink -f {HOME}/repo')
    if source != f'{HOME}/sources/{args.commit}':
        raise SystemExit(f'Deployed source differs: {source}')
    root = f'/srv/turku3d/work/pipeline-speed-max-{uuid.uuid4().hex[:12]}'
    arguments = ['timeout', '--kill-after=3', '540', f'{source}/tools/remote-drafts/run.sh',
                         'python', f'{source}/tools/remote-drafts/profile-textures.py', '--root', root,
                         '--assets', '/srv/turku3d/work/coarse', '--tiles', 't0_0_0', 't0_0_-2']
    if args.assembly_baseline:
        arguments += ['--assembly-baseline', f'{HOME}/sources/{args.assembly_baseline}/pipeline/turku3d/wall_assembly.py']
    command = shlex.join(arguments)
    print(remote(f'TURKU3D_DRAFT_SOURCE={shlex.quote(source)} ' + command), flush=True)


if __name__ == '__main__':
    main()
