"""Accept generated facade maps only after metric, reference and visual-grid checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

from .building_jobs import latest, reference_matches
from .facade_batch import asset_path, write_json
from .post.pipeline import sha
from .streetview.facades import load_facades


def audit(
    facades: Path,
    refs: Path,
    generated: Path,
    staging: Path,
    visual_qa: Path,
    min_ppm: int = 0,
) -> list[dict]:
    if min_ppm and not 4 <= min_ppm <= 72:
        raise ValueError("Minimum facade density must be 4–72 pixels per metre")
    answers = latest(visual_qa)
    rows = []
    for facade in load_facades(facades):
        fid = facade.id
        source = generated / f"{fid}.png"
        sidecar = asset_path(facade, staging).with_suffix(".json")
        problems = []
        if not reference_matches(facade, refs):
            problems.append("stale reference geometry")
        if not source.is_file() or not sidecar.is_file():
            problems.append("missing image or staged map")
        if not problems:
            meta = json.loads(sidecar.read_text())
            with Image.open(source) as image:
                aspect = image.width / image.height
            wall_aspect = facade.length / facade.height_m
            aspect_error = max(aspect / wall_aspect, wall_aspect / aspect) - 1
            if aspect_error > 0.03 or meta["qa"]["content_aspect_error"] > 0.03:
                problems.append("image aspect differs from measured wall by more than 3%")
            if meta["qa"]["scale_relative_error"] > 0.03:
                problems.append("unequal horizontal and vertical texel density")
            if not meta["qa"]["gltf_basisu_valid"]:
                problems.append("KTX2 validation failed")
            for name, digest in meta["hashes"].items():
                if sha(sidecar.parent / name) != digest:
                    problems.append(f"staged map hash changed: {name}")
            if len(meta["references"]) < 1:
                problems.append("no logged reference")
            record = answers.get(f"{fid}.visualqa", {})
            answer = record.get("answer") or {}
            inputs = {i["name"]: i["sha256"] for i in record.get("inputs", [])}
            if inputs.get(source.name) != sha(source):
                problems.append("visual QA did not inspect this generated image")
            if answer.get("facade_id") != fid or not answer.get("grid_matches"):
                problems.append("window or ground-opening grid mismatch")
            if answer.get("upper_openings") != answer.get("expected_upper_openings"):
                problems.append("upper opening count mismatch")
            if not answer.get("clean") or answer.get("foreign_objects"):
                problems.append("large foreground or foreign object detected")
            if not problems:
                meta["source_mode"] = "generate"
                if min_ppm:
                    meta["min_pixels_per_metre"] = min_ppm
                meta["generated_qa"] = {
                    "source_sha256": sha(source),
                    "aspect_error": aspect_error,
                    "visual_qa_thread": record.get("thread_id"),
                    "visual_qa_answer": answer,
                }
                write_json(sidecar, meta)
        rows.append({"facade_id": fid, "accepted": not problems, "problems": problems})
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("facades", "refs", "generated", "staging", "visual-qa", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--min-ppm", type=int, default=0)
    args = parser.parse_args(argv)
    rows = audit(
        args.facades, args.refs, args.generated, args.staging, args.visual_qa, args.min_ppm
    )
    write_json(args.output, {"rows": rows, "accepted": sum(r["accepted"] for r in rows)})
    print(json.dumps(rows))
    return 0 if all(r["accepted"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
