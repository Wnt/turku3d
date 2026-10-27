"""Resolve native test tools exactly as pipeline/env/run.sh does."""

import os
from pathlib import Path

root = Path.home() / "turku3d"
os.environ["PATH"] = os.pathsep.join(
    [str(root / "envs/pipeline/bin"), str(root / "tools/bin"), os.environ.get("PATH", "")]
)
