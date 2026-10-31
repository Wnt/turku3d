"""Run the selective new-tile imagery check on the exact deployed OCI source."""

import shlex
import subprocess
from pathlib import Path

from facade_publish import remote


def main():
    repo = Path(__file__).resolve().parents[2]
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    source = "/home/ubuntu/turku3d-drafts/sources/" + commit
    command = [
        "timeout",
        "540",
        "python",
        source + "/tools/remote-drafts/probe-new-imagery.py",
        "--plan",
        "/srv/turku3d/work/outer-warmup-20261031/plan.json",
        "--out",
        "/srv/turku3d/work/new-imagery-probe-" + commit,
    ]
    try:
        result = remote(
            "TURKU3D_DRAFT_SOURCE="
            + shlex.quote(source)
            + " /home/ubuntu/turku3d-drafts/run.sh "
            + shlex.join(command)
        )
    except subprocess.CalledProcessError as error:
        print(error.stdout or "", flush=True)
        print(error.stderr or "", flush=True)
        raise
    print(result, flush=True)


if __name__ == "__main__":
    main()
