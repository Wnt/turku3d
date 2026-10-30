"""Audit and build affected river tiles on exact published OCI source; no browser."""

import argparse
import shlex

from deploy_drafts import HOME
from facade_publish import remote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    args = parser.parse_args()
    source = f'{HOME}/sources/{args.commit}'
    command = shlex.join([
        'timeout', '570', f'{source}/tools/remote-drafts/run.sh', 'python', '-u',
        f'{source}/tools/remote-drafts/probe-river-surface.py',
    ])
    print(remote(f'TURKU3D_DRAFT_SOURCE={shlex.quote(source)} ' + command), flush=True)


if __name__ == '__main__':
    main()
