"""Temporary registered A/B experiments. Use feature_flag('literal_name') only."""

import os


def feature_flag(name: str) -> bool:
    return os.environ.get(f"TURKU3D_AB_{name.upper()}") == "1"
