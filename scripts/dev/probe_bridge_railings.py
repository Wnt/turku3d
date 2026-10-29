"""Audit crossing railings and build the reported overpass on frozen deployed OCI source."""

import shlex
import subprocess
import sys

from deploy_drafts import HOME
from facade_publish import remote

commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
source = f"{HOME}/sources/{commit}"
command = shlex.join(
    [
        "timeout",
        "--kill-after=3",
        "540",
        f"{source}/tools/remote-drafts/run.sh",
        "python",
        f"{source}/tools/remote-drafts/probe-bridge-railings.py",
    ]
)
try:
    print(remote(f"TURKU3D_DRAFT_SOURCE={shlex.quote(source)} " + command), flush=True)
except subprocess.CalledProcessError as error:
    print(error.stdout, flush=True)
    sys.exit(error.stderr[-8000:])
