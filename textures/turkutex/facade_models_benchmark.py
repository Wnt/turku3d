"""Local model experiment runner: reviewed walls and explicitly separated pseudo labels."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image

from .facade_batch import write_json
from .facade_cv_benchmark import benchmark
from .facade_models import Engine
from .facade_style import lab


def samples(root: Path):
    """Yield all 425 labels; absent references remain explicit, never fabricated crops."""
    for path in sorted((root / "labels").glob("*/descriptions.json")):
        labels = json.loads(path.read_text())
        plan = {
            w["facade_id"]: w for w in json.loads((path.parent / "plan.json").read_text())["walls"]
        }
        for fid, record in sorted(labels.items()):
            view = record.get("reference", {}).get("view_id")
            crop = root / "refs" / fid / f"{view}_rectified.jpg" if view else None
            yield fid, record, plan[fid], crop if crop and crop.exists() else None


def crop_bytes(path: Path):
    """Reproduce facade_fast's JPEG input byte for byte before comparing pseudo labels."""
    with Image.open(path) as image:
        image = image.convert("RGB")
        scale = 768 / max(image.size)
        if scale < 1:
            image = image.resize(
                (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                Image.Resampling.LANCZOS,
            )
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=88)
        return buffer.getvalue()


def read_crop(path: Path):
    return np.asarray(Image.open(io.BytesIO(crop_bytes(path))).convert("RGB"))


def delta(a, b):
    def colour(s):
        return lab(np.array([int(s[i : i + 2], 16) for i in (1, 3, 5)]))

    return float(np.linalg.norm(colour(a) - colour(b)))


def pseudo_benchmark(root: Path, engine, output: Path):
    rows = []
    for fid, record, wall, crop in samples(root):
        row = dict(
            facade_id=fid,
            source=record["source"],
            astra=record["description"],
            has_crop=crop is not None,
        )
        if crop:
            row["reference_hash_matches"] = (
                hashlib.sha256(crop_bytes(crop)).hexdigest() == record["reference"]["crop_sha256"]
            )
            start = time.monotonic()
            answer, sidecar = engine.measure(
                read_crop(crop), fid, wall["width_m"], wall["height_m"]
            )
            row.update(description=answer, cv=sidecar, seconds=time.monotonic() - start)
        rows.append(row)
        if len(rows) % 25 == 0:
            print(f"pseudo {len(rows)}", flush=True)
            write_json(output.with_suffix(".partial.json"), dict(rows=rows))
    measured = [r for r in rows if r["has_crop"]]
    visible = [r for r in measured if r["astra"]["wall_visible"]]
    columns = [r for r in visible if r["astra"]["storeys"] > 1]
    summary = dict(
        walls=len(rows),
        measured=len(measured),
        matched_input_hashes=sum(r["reference_hash_matches"] for r in measured),
        missing_reference=sum(not r["has_crop"] for r in rows),
        visible_n=len(visible),
        columns_n=len(columns),
        storeys_exact=sum(r["description"]["storeys"] == r["astra"]["storeys"] for r in visible),
        columns_exact=sum(
            r["description"]["window_columns"] == r["astra"]["window_columns"] for r in columns
        ),
        material_exact=sum(r["description"]["material"] == r["astra"]["material"] for r in visible),
        visibility_exact=sum(
            r["description"]["wall_visible"] == r["astra"]["wall_visible"] for r in measured
        ),
        median_colour_delta=float(
            np.median(
                [delta(r["description"]["wall_colour"], r["astra"]["wall_colour"]) for r in visible]
            )
        ),
        seconds_per_wall=sum(r["seconds"] for r in measured) / max(1, len(measured)),
        rows=rows,
    )
    write_json(output, summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--assembly", type=Path)
    parser.add_argument("--pseudo-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--short", action="store_true")
    parser.add_argument("--size", type=int, default=512)
    args = parser.parse_args()
    start = time.monotonic()
    engine = Engine(args.engine, short=args.short, size=args.size)
    load = time.monotonic() - start
    if args.pseudo_root:
        pseudo_benchmark(args.pseudo_root, engine, args.output)
    else:
        benchmark(
            args.fixture,
            args.assembly,
            args.output,
            "classic",
            measure_fn=engine.measure,
            engine=args.engine,
        )
    result = json.loads(args.output.read_text())
    result["model_load_seconds"] = round(load, 3)
    write_json(args.output, result)


if __name__ == "__main__":
    main()
