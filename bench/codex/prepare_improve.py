#!/usr/bin/env python3
"""Freeze six production improve prompts; model labels are inputs, never truth."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "textures"))
from turkutex.codex.runner import load_jobs  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jobs", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    cases = []
    for job in load_jobs(args.jobs):
        aspect = job.meta.get("aspect") or job.meta.get("width_m", 0) / max(
            job.meta.get("height_m", 1), 0.001
        )
        if job.meta.get("tile", "none") != "none" or not 0.8 <= aspect <= 3:
            continue
        cases.append(
            {
                "id": job.id,
                "kind": "improve",
                "images": [str(p) for p in job.images],
                "sha256": [hashlib.sha256(p.read_bytes()).hexdigest() for p in job.images],
                "prompt": job.prompt,
                "aspect": aspect,
                "pilot": len(cases) < 4,
                "truth_note": "Reference fidelity is scored by Astra; production model labels are unreviewed inputs.",
            }
        )
        if len(cases) == 6:
            break
    if len(cases) != 6:
        raise ValueError("need six eligible whole-facade jobs")
    args.out.write_text(json.dumps({"version": "improve-v1", "cases": cases}, indent=2) + "\n")


if __name__ == "__main__":
    main()
