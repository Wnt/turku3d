"""Where texture data lives. Images never go into git.

Data root comes from turku3d.config.data_root(): $TURKU3D_DATA, else ~/turku3d/data on the Mac
(~/turku3d/data on Linux).
- raw/streetview/google/<panoid>/    pano_z<zoom>.jpg, depth.npy (as returned, NOT flipped), meta.json
- raw/streetview/lookaround/<id>/    face<k>_z<zoom>.heic, meta.json
- raw/streetview/coverage/           cached coverage-tile queries
- textures/runs/<run>/               codex runner output (out.jsonl, failed.json, logs/, images/)
- textures/refs/<facade_id>/         reference crops + pose JSON
- textures/post/<asset>/             post-processed maps + KTX2
"""
from __future__ import annotations

import os
from pathlib import Path

from turku3d import config

TEXTURES_DIR = Path(__file__).resolve().parents[1]  # repo/textures
REPO_ROOT = TEXTURES_DIR.parent
PROMPTS_DIR = TEXTURES_DIR / "prompts"
SCHEMAS_DIR = TEXTURES_DIR / "schemas"
STYLE_DIR = TEXTURES_DIR / "style"
NODE_DIR = TEXTURES_DIR / "node"


def data_root() -> Path:
    return config.data_root()


def streetview_dir() -> Path:
    return Path(os.environ.get("TURKUTEX_STREETVIEW", config.raw_dir("streetview")))


def textures_data_dir(sub: str = "") -> Path:
    return data_root() / "textures" / sub


def tools_dir() -> Path:
    """Third-party tools fetched at setup time (DeepBump clone)."""
    return data_root() / "tools"
