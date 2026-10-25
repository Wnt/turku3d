"""Versioned job routing, with explicit per-call overrides."""

import os
import tomllib
from pathlib import Path


def route(kind, model=None, effort=None):
    path = Path(
        os.environ.get(
            "TURKU3D_CODEX_ROUTING", Path(__file__).resolve().parents[2] / "codex-routing.toml"
        )
    )
    with path.open("rb") as stream:
        config = tomllib.load(stream)
    selected = config.get(kind, config["default"])
    return model or selected["model"], effort or selected["effort"]
