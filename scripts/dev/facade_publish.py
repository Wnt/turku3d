"""Publish existing facade inputs and a sanitized runtime store to the OCI data volume."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

HOST = "ubuntu@82.70.50.61"
SSH = ["ssh", "-o", "IdentitiesOnly=yes", "-i", str(Path.home() / ".ssh/oci_cuttlefish")]


def remote(command):
    return subprocess.run(
        [*SSH, HOST, command], check=True, capture_output=True, text=True
    ).stdout.strip()


def publish():
    """Export on OCI from its existing mirror; this command never copies private input data."""
    started = time.monotonic()
    result = remote(
        "/home/ubuntu/turku3d-drafts/run.sh python -m turku3d.facade_assets "
        "--export /srv/turku3d/data/facade-assets"
    )
    return {
        **json.loads(result),
        "total_seconds": round(time.monotonic() - started, 3),
        "url": "https://turku.lab.madekivi.fi/facades/index.json",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    print(json.dumps(publish()))
    return 0
