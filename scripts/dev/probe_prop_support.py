"""Audit real sign/stop support and collision on frozen deployed OCI source."""

import argparse
import shlex
import subprocess
import sys

from deploy_drafts import HOME
from facade_publish import HOST, SSH


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", help="Published full commit; defaults to this frozen checkout")
    args = parser.parse_args()
    commit = args.commit or subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        parser.error("--commit must be a full lowercase Git SHA")
    source = f"{HOME}/sources/{commit}"
    command = shlex.join(
        [
            "timeout",
            "--kill-after=3",
            "530",
            f"{source}/tools/remote-drafts/run.sh",
            "python",
            f"{source}/tools/remote-drafts/probe-prop-support.py",
            "--commit",
            commit,
        ]
    )
    # finish's ten-minute deadline includes admission waits. Bound the remote process
    # independently so losing the SSH client cannot leave a generator running forever.
    result = subprocess.run(
        [
            *SSH,
            "-o",
            "ConnectTimeout=10",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=2",
            HOST,
            f"TURKU3D_DRAFT_SOURCE={shlex.quote(source)} " + command,
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
