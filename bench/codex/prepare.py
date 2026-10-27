#!/usr/bin/env python3
"""Freeze eight QA view pairs from existing Phase 0/1 photographs, no downloads."""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "qa"))
from judge.judge import build_prompt  # noqa: E402
from qalib import degrade  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refs", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    images = []
    truth = []
    provenance = []
    names = ["office_oblique_sw.jpg", "sv_04.jpg", "sv_05.jpg", "sv_06.jpg"]
    for i in range(8):
        src = args.refs / names[i // 2]
        with Image.open(src) as original:
            im = original.convert("RGB")
            im.thumbnail((768, 768))
        ref = args.out / f"{i:02d}_reference.png"
        render = args.out / f"{i:02d}_render.png"
        im.save(ref)
        if i % 2:
            result, planted = degrade.grey_mask(im, [0.2, 0.2, 0.8, 0.8])
        else:
            result, planted = im, []
        result.save(render)
        images.extend([str(ref), str(render)])
        truth.append(planted)
        provenance.append(
            {
                "source": str(src),
                "sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
                "operation": "grey_mask" if i % 2 else "identity",
            }
        )
    schema = json.loads((ROOT / "qa/judge/qa.schema.json").read_text())
    batch = {
        "type": "object",
        "additionalProperties": False,
        "required": ["judgements"],
        "properties": {
            "judgements": {"type": "array", "minItems": 8, "maxItems": 8, "items": schema}
        },
    }
    (ROOT / "bench/codex/qa-batch.schema.json").write_text(json.dumps(batch, indent=2) + "\n")
    case = {
        "id": "qa-eight-v1",
        "kind": "qa_batch",
        "images": images,
        "sha256": [hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in images],
        "truth": truth,
        "provenance": provenance,
        "schema": "bench/codex/qa-batch.schema.json",
        "prompt": "Judge eight independent pairs, numbered 00 through 07. For each pair XX_reference.png is image 1 and XX_render.png is image 2. Return judgements in exact numerical order, one per pair. Do not use external tools or inspect other files.\n"
        + build_prompt(),
    }
    # Store portable sandbox output paths; original image hashes remain provenance.
    work = os.environ["TURKU3D_WORK"]
    case["images"] = [p.replace(work, "${TURKU3D_WORK}") for p in images]
    args.manifest.write_text(
        json.dumps({"version": "qa-planted-v1", "cases": [case]}, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
