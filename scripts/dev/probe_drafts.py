"""Run an agent-selected build probe against its exact published OCI source."""

import argparse
import json
import shlex
import subprocess

from deploy_drafts import HOME
from facade_publish import remote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tile", required=True)
    parser.add_argument("--first-budget", type=float, required=True)
    parser.add_argument("--full-budget", type=float, required=True)
    args = parser.parse_args()
    source = remote(f"readlink -f {HOME}/repo")
    frozen = f"{HOME}/sources/{args.commit}"
    remote("test -d " + shlex.quote(frozen))
    if source != frozen:
        print(
            f"Active source moved; measuring frozen published {args.commit}",
            file=__import__("sys").stderr,
        )
    source = frozen
    command = shlex.join(
        [
            f"{source}/tools/remote-drafts/run.sh",
            "python",
            f"{source}/tools/remote-drafts/probe-builds.py",
            "--root",
            "/srv/turku3d/work/coarse",
            "--tile",
            args.tile,
            "--first-budget",
            str(args.first_budget),
            "--full-budget",
            str(args.full_budget),
        ]
    )
    try:
        result = json.loads(remote(f"TURKU3D_DRAFT_SOURCE={shlex.quote(source)} " + command))
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.stderr[-4000:]) from error
    print(json.dumps(dict(commit=args.commit, **result)))


if __name__ == "__main__":
    main()
