"""Run the underpass road-top evidence on the exact deployed OCI source within nine minutes."""

import argparse
import shlex
import subprocess
import uuid

from deploy_drafts import HOME
from facade_publish import remote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    source = f"{HOME}/sources/{args.commit}"
    # Delivery already recorded this source as deployed. A later agent may move
    # the live symlink while this frozen job waits for its host slot.
    remote(f"test -f {shlex.quote(source + '/pipeline/turku3d/coarse/road_top.py')}")
    print(
        f"Frozen deployed source: {source}; live source: " + remote(f"readlink -f {HOME}/repo"),
        flush=True,
    )
    root = f"/srv/turku3d/work/underpass-road-top-{uuid.uuid4().hex[:12]}"
    command = shlex.join(
        [
            "timeout",
            "--kill-after=3",
            "540",
            f"{source}/tools/remote-drafts/run.sh",
            "python",
            f"{source}/tools/remote-drafts/probe-underpass-roads.py",
            "--root",
            root,
            "--assets",
            "/srv/turku3d/work/coarse",
        ]
    )
    print(f"Owned OCI evidence: {root}", flush=True)
    try:
        print(remote(f"TURKU3D_DRAFT_SOURCE={shlex.quote(source)} " + command), flush=True)
    except subprocess.CalledProcessError as error:
        print(error.stdout, flush=True)
        raise SystemExit(error.stderr[-8000:]) from error


if __name__ == "__main__":
    main()
