"""Repo and data locations for qa/.

Images (references, renders, judge outputs) are data and live outside git:
  $QA_DATA if set, else <repo>/data/qa   (the root /data/ is git-ignored).
Registry rows store image paths relative to the repo root when the file is inside the repo,
else absolute paths.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

QA_DIR = Path(__file__).resolve().parents[1]
REPO = QA_DIR.parent
PIPELINE = REPO / "pipeline"


def data_dir() -> Path:
    return Path(os.environ.get("QA_DATA", REPO / "data" / "qa")).expanduser().resolve()


def rel(p: str | Path) -> str:
    """Path as stored in registries: repo-relative if inside the repo, else absolute."""
    p = Path(p).resolve()
    try:
        return str(p.relative_to(REPO))
    except ValueError:
        return str(p)


def resolve(p: str | Path, base: Path | None = None) -> Path:
    """Inverse of rel(): absolute stays, relative is taken against the repo root (or base)."""
    p = Path(p)
    if p.is_absolute():
        return p
    return ((base or REPO) / p).resolve()


def ensure_pipeline_on_path() -> None:
    if str(PIPELINE) not in sys.path:
        sys.path.insert(0, str(PIPELINE))
