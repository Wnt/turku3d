"""Run focused forest-corridor evidence on the exact deployed OCI source."""

import shlex
import subprocess
import sys
import uuid

from deploy_drafts import HOME
from facade_publish import remote

commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
source = f"{HOME}/sources/{commit}"
assert remote(f"readlink -f {HOME}/repo") == source, "source is not deployed"
root = f"/srv/turku3d/work/path-tree-clearance-{uuid.uuid4().hex[:12]}"
command = shlex.join(["timeout", "--kill-after=3", "540", f"{source}/tools/remote-drafts/run.sh",
                      "python", f"{source}/tools/remote-drafts/probe-path-trees.py", root,
                      "/srv/turku3d/work/coarse"])
try:
    print(remote(f"TURKU3D_DRAFT_SOURCE={shlex.quote(source)} " + command), flush=True)
except subprocess.CalledProcessError as error:
    print(error.stdout, flush=True)
    sys.exit(error.stderr[-8000:])
