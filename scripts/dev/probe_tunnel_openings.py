"""Audit cached opening obstructions, then build only Auransilta and its neighbours on OCI."""

import argparse
import shlex
import subprocess
import uuid

from deploy_drafts import HOME
from facade_publish import remote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    args = parser.parse_args()
    source = f'{HOME}/sources/{args.commit}'
    root = f'/srv/turku3d/work/tunnel-openings-{uuid.uuid4().hex[:12]}'
    command = shlex.join([f'{source}/tools/remote-drafts/run.sh', 'python',
                          f'{source}/tools/remote-drafts/probe-tunnel-openings.py',
                          '--root', root, '--assets', '/srv/turku3d/work/coarse'])
    try:
        print(remote(f'TURKU3D_DRAFT_SOURCE={shlex.quote(source)} ' + command), flush=True)
    except subprocess.CalledProcessError as error:
        print(error.stdout, flush=True)
        raise SystemExit(error.stderr[-6000:]) from error


if __name__ == '__main__':
    main()
