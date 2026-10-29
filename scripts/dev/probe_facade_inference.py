"""Run the owned two-tile facade acceptance on frozen, published OCI source."""
import argparse
import shlex
import subprocess
from deploy_drafts import HOME
from facade_publish import remote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    args = parser.parse_args()
    source = f'{HOME}/sources/{args.commit}'
    remote('test -d '+shlex.quote(source))
    command = shlex.join(['timeout', '--kill-after=3', '540', f'{source}/tools/remote-drafts/run.sh',
        'python', f'{source}/tools/remote-drafts/probe-facade-inference.py',
        '--root', '/srv/turku3d/work/coarse'])
    try:
        print(remote(f'TURKU3D_DRAFT_SOURCE={shlex.quote(source)} '+command), flush=True)
    except subprocess.CalledProcessError as error:
        print(error.stdout, flush=True)
        raise SystemExit(error.stderr[-6000:]) from error


if __name__ == '__main__':
    main()
