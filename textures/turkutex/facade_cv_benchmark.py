"""Compare offline CV to the original fast-pass crops and accepted reviewer counts."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image

from .facade_batch import write_json
from .facade_cv import Segmenter, measure
from .facade_fast_render import adopt, register_description, validate
from .facade_style import lab as srgb_to_lab


def benchmark(
    fixture: Path,
    assembly: Path,
    output: Path,
    segmentation: str,
    *,
    measure_fn=None,
    engine="cv",
):
    plan = json.loads((fixture / "plan.json").read_text())
    colours = {r[0]: r[3] for r in json.loads((fixture / "colour.json").read_text())}
    astra = {
        r["id"].removesuffix(".fast"): r["answer"]
        for r in map(json.loads, (fixture / "run_describe/out.jsonl").read_text().splitlines())
        if r.get("ok")
    }
    start = time.monotonic()
    segmenter = Segmenter() if segmentation == "segformer" else None
    load_seconds = time.monotonic() - start
    rows = []
    for wall in plan["walls"]:
        fid = wall["facade_id"]
        building, index = fid.rsplit("_", 1)
        meta = json.loads((assembly / "facades" / building / f"{index}.json").read_text())
        truth = meta.get("generated_qa", {}).get("visual_qa_answer", {})
        start = time.monotonic()
        with Image.open(fixture / "inputs" / f"{fid}.jpg") as image:
            rgb = np.asarray(image.convert("RGB"))
            if measure_fn is None:
                answer, sidecar = measure(rgb, fid, wall["width_m"], wall["height_m"], segmenter)
            else:
                answer, sidecar = measure_fn(rgb, fid, wall["width_m"], wall["height_m"])
        seconds = time.monotonic() - start
        if validate(answer, fid):
            raise ValueError(validate(answer, fid))

        def lab(value):
            return srgb_to_lab(np.array([int(value[i : i + 2], 16) for i in (1, 3, 5)]))

        row = dict(
            facade_id=fid,
            description=answer,
            cv=sidecar,
            seconds=seconds,
            expected_storeys=truth.get("expected_storeys"),
            expected_columns=truth.get("expected_upper_openings"),
            colour_delta=float(np.linalg.norm(lab(answer["wall_colour"]) - lab(colours[fid]))),
            astra=astra.get(fid),
        )
        rows.append(row)
    # Use the same building/register fallback as facade_fast when comparing rendered colour.
    geometry = {r["facade_id"]: r for r in plan["walls"]}
    original = json.loads((fixture / "descriptions.json").read_text())
    for row in rows:
        fid = row["facade_id"]
        building = fid.rsplit("_", 1)[0]
        d = row["description"]
        if not d["wall_visible"]:
            donors = [
                r
                for r in rows
                if r["facade_id"].rsplit("_", 1)[0] == building and r["description"]["wall_visible"]
            ]
            if donors:
                donor = max(
                    donors,
                    key=lambda r: (
                        geometry[r["facade_id"]]["width_m"] * geometry[r["facade_id"]]["height_m"]
                    ),
                )
                d = adopt(
                    donor["description"],
                    fid,
                    geometry[fid]["width_m"],
                    geometry[donor["facade_id"]]["width_m"],
                )
            else:
                d = register_description(
                    fid,
                    building,
                    original[fid].get("attributes", {}),
                    geometry[fid]["width_m"],
                    geometry[fid]["height_m"],
                )
        row["evaluated_description"] = d
        row["colour_delta"] = float(np.linalg.norm(lab(d["wall_colour"]) - lab(colours[fid])))
        row["astra_colour_delta"] = float(
            np.linalg.norm(lab(original[fid]["description"]["wall_colour"]) - lab(colours[fid]))
        )
    counts = {}
    for label, field, expected in (
        ("storeys", "storeys", "expected_storeys"),
        ("columns", "window_columns", "expected_columns"),
    ):
        valid = [
            r
            for r in rows
            if r[expected] is not None
            and r["astra"]["wall_visible"]
            and (label != "columns" or (r["expected_storeys"] or 0) > 1)
        ]
        counts[label] = dict(
            n=len(valid),
            cv=sum(r["description"][field] == r[expected] for r in valid),
            astra=sum(r["astra"][field] == r[expected] for r in valid if r["astra"]),
        )
    summary = dict(
        engine=engine,
        segmentation=segmentation,
        walls=len(rows),
        model_load_seconds=round(load_seconds, 3),
        seconds=round(sum(r["seconds"] for r in rows), 3),
        seconds_per_wall=round(sum(r["seconds"] for r in rows) / len(rows), 3),
        counts=counts,
        median_colour_delta=round(float(np.median([r["colour_delta"] for r in rows])), 2),
        astra_median_colour_delta=round(
            float(np.median([r["astra_colour_delta"] for r in rows])), 2
        ),
        visible=sum(r["description"]["wall_visible"] for r in rows),
        rows=rows,
    )
    write_json(output, summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--assembly", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--segmentation", choices=("classic", "segformer"), default="classic")
    parser.add_argument("--engine", choices=("cv", "cv-priors"), default="cv")
    args = parser.parse_args()
    measure_fn = None
    if args.engine == "cv-priors":
        from turku3d import config

        from .facade_derive import building_attributes
        from .facade_priors import Engine

        walls = json.loads((args.fixture / "plan.json").read_text())["walls"]
        attributes = building_attributes(
            config.raw_dir("wfs/work-3x3"), {r["building_id"] for r in walls}
        )
        model = Engine()

        def measure_fn(rgb, fid, width, height):
            return model.measure(rgb, fid, width, height, attributes.get(fid.rsplit("_", 1)[0], {}))

    benchmark(
        args.fixture,
        args.assembly,
        args.output,
        args.segmentation,
        measure_fn=measure_fn,
        engine=args.engine,
    )


if __name__ == "__main__":
    main()
