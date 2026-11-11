"""Check city.lab's published sign support with the registered farm source snapshot."""

import argparse
import shlex
import subprocess
import sys

BOOTSTRAP = r"""
import json, os, pathlib, sys, tempfile
farm, revision, commit, public = sys.argv[1:]
farm = pathlib.Path(farm)
aliases = json.loads((farm / 'aliases.json').read_text())
revision = aliases.get(revision, revision)
meta = json.loads((farm / 'revisions' / revision / 'revision.json').read_text())
assert meta.get('kind') == 'migrated', 'Expected a migration publication'
assert meta['code_revision'] == commit, 'Registered migration uses another source commit'
repo = farm / 'revisions' / commit / 'repo'
work = pathlib.Path(tempfile.mkdtemp(prefix='prop-support-', dir=farm / 'work'))
os.environ.update(REPO=str(repo), TURKU3D_WORK=str(work), TURKU3D_THREADS_PER_WORKER='2')
args = [str(repo / 'scripts/dev/shared-tiles-run.sh'), 'python',
        str(repo / 'tools/remote-drafts/probe-prop-support.py'), '--commit', commit,
        '--farm-root', str(farm), '--revision', revision, '--public-url', public]
os.execv(args[0], args)
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True, help="Published migration alias or revision")
    parser.add_argument(
        "--commit", help="Registered source commit; defaults to frozen checkout HEAD"
    )
    parser.add_argument("--host", default="osgallery-dev")
    parser.add_argument("--farm-root", default="/home/wnt/turku3d-local/loops/tiles/farm")
    parser.add_argument("--public-url", default="https://city.lab.madekivi.fi/coarse/")
    args = parser.parse_args()
    commit = args.commit or subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        parser.error("--commit must be a full lowercase Git SHA")
    command = shlex.join(
        [
            "timeout",
            "--kill-after=3",
            "530",
            "python3",
            "-c",
            BOOTSTRAP,
            args.farm_root,
            args.revision,
            commit,
            args.public_url,
        ]
    )
    # The caller's ten-minute deadline includes admission; remote audit is bounded
    # independently if its SSH client disappears. This script queues no builds.
    result = subprocess.run(
        [
            "ssh",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=2",
            args.host,
            command,
        ],
        capture_output=True,
        text=True,
        timeout=550,
    )
    print(result.stdout, end="", flush=True)
    if result.returncode:
        print(result.stderr[-8000:], file=sys.stderr, flush=True)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
