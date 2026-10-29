"""Run the selected producer-retirement integration case on the deployed OCI source."""
import argparse
import shlex
import uuid

from deploy_drafts import HOME
from facade_publish import remote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    args = parser.parse_args()
    source = remote(f'readlink -f {HOME}/repo')
    if source != f'{HOME}/sources/{args.commit}':
        raise SystemExit(f'Deployed source differs: {source}')
    root = f'/srv/turku3d/work/pipeline-speed-retirement-{uuid.uuid4().hex[:12]}'
    command = shlex.join(['timeout', '--kill-after=3', '480', f'{source}/tools/remote-drafts/run.sh',
                         'python', f'{source}/tools/remote-drafts/probe-retirement.py', '--root', root])
    print(remote(f'TURKU3D_DRAFT_SOURCE={shlex.quote(source)} ' + command), flush=True)


if __name__ == '__main__':
    main()
