"""Run owned material-range acceptance on frozen deployed OCI source."""

import argparse
import json
import os
import shlex
import subprocess
from pathlib import Path

from deploy_drafts import HOME
from facade_publish import remote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    source = f"{HOME}/sources/{args.commit}"
    out = Path(os.environ["TURKU3D_WORK"]) / "reports/facade-materials"
    out.mkdir(parents=True, exist_ok=True)
    command = shlex.join(
        [
            "timeout",
            "--kill-after=3",
            "520",
            f"{source}/tools/remote-drafts/run.sh",
            "python",
            f"{source}/tools/remote-drafts/probe-facade-materials.py",
            "--root",
            "/srv/turku3d/work/coarse",
        ]
    )
    try:
        output = remote(f"TURKU3D_DRAFT_SOURCE={shlex.quote(source)} " + command)
    except subprocess.CalledProcessError as error:
        print(error.stdout, flush=True)
        raise SystemExit(error.stderr[-6000:]) from error
    print(output, flush=True)
    result = json.loads(output.splitlines()[-1])
    (out / "oci.json").write_text(json.dumps(dict(commit=args.commit, **result), indent=2) + "\n")


if __name__ == "__main__":
    main()
