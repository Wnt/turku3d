"""Audit representative first/full tiles on the exact published OCI source."""

import argparse
import shlex

from deploy_drafts import HOME
from facade_publish import remote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tile", action="append", required=True)
    args = parser.parse_args()
    source = f"{HOME}/sources/{args.commit}"
    remote("test -d " + shlex.quote(source))
    command = [
        f"{source}/tools/remote-drafts/run.sh",
        "python",
        f"{source}/tools/remote-drafts/probe-collision-visual.py",
        "--root",
        "/srv/turku3d/work/coarse",
    ]
    for tile in args.tile:
        command.extend(["--tile", tile])
    print(remote(f"TURKU3D_DRAFT_SOURCE={shlex.quote(source)} " + shlex.join(command)))


if __name__ == "__main__":
    main()
