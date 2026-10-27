"""Reproducible local-only transform ablations on reviewer and pseudo-labelled walls."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
from turku3d import config

from . import facade_cv as cv
from . import facade_cv_transforms as transforms
from .facade_batch import write_json
from .facade_fast_render import adopt, register_description, validate
from .facade_style import lab
from .jobs import _views
from .streetview import camera, rectify
from .streetview.rectify import rectify_record
from .streetview.refs import equirect_model, screen_pixels, screen_problem, source_rank

VARIANTS = (*transforms.VARIANTS, "native16", "native32", "native64")
RECTIFIER_CODE = hashlib.sha256(
    Path(rectify.__file__).read_bytes()
    + Path(camera.__file__).read_bytes()
    + inspect.getsource(equirect_model).encode()
).hexdigest()


def read_rgb(path: Path, side=768):
    with Image.open(path) as image:
        image = image.convert("RGB")
        if side:
            image.thumbnail((side, side), Image.Resampling.LANCZOS)
        return np.asarray(image)


def views_for(refs: Path, fid: str, first: str | None = None):
    records = []
    for view in _views(refs / fid):
        if view.get("part") != "full":
            continue
        path = refs / fid / view["files"]["rectified"]
        if path.exists() and screen_problem(screen_pixels(read_rgb(path))) is None:
            records.append(view)
    records.sort(
        key=lambda v: (v["view_id"] != first, source_rank(v["source"]), -v.get("score", 0))
    )
    if first and (not records or records[0]["view_id"] != first):
        raise ValueError(f"missing original screened view: {fid}/{first}")
    return records


def largest_local_panorama(view):
    """Existing resolutions of this exact panorama only; never downloads or mutates inputs."""
    source = Path(view["pano_file"])
    candidates = {source} if source.is_file() else set()
    roots = [source.parent]
    for cache in ("cache/streetlevel", "cache/streetview", "raw/streetview"):
        roots.append(config.data_root() / cache / view["source"] / view["pano_id"])
    pattern = {"google": "pano_z*.jpg", "lookaround": "equi_z*.jpg"}.get(view["source"], "pano.jpg")
    for root in roots:
        candidates.update(root.glob(pattern))
    if not candidates:
        raise FileNotFoundError(f"no local panorama: {view['pano_id']}")

    def area(path):
        with Image.open(path) as image:
            return image.width * image.height

    return max(sorted(candidates), key=area)


def prepare_native(view, output: Path, ppm: int):
    source = largest_local_panorama(view)
    stat = source.stat()
    key = hashlib.sha256(
        json.dumps(
            [RECTIFIER_CODE, view, str(source), stat.st_size, stat.st_mtime_ns, ppm]
        ).encode()
    ).hexdigest()
    path = output / "rectified" / f"{key}.png"
    if not path.exists():
        start = time.monotonic()
        rgb, _ = rectify_record(view, source, pixels_per_metre=ppm, max_side=1536)
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(rgb).save(path)
        with Image.open(source) as image:
            size = image.size
        write_json(
            path.with_suffix(".json"),
            dict(
                source=str(source),
                source_size=size,
                output_size=list(rgb.shape[1::-1]),
                ppm=ppm,
                seconds=time.monotonic() - start,
            ),
        )
    return path


def predictor(refs, views, variant, segmenter, output, image_measure=None):
    native = {}
    if variant.startswith("native"):
        for fid, records in views.items():
            if records:
                native[fid] = prepare_native(records[0], output, int(variant[6:]))

    def measure(rgb, fid, width, height):
        others = []
        if variant in ("median", "vote"):
            others = [read_rgb(refs / fid / v["files"]["rectified"]) for v in views[fid][1:]]
        if fid in native:
            rgb = read_rgb(native[fid], side=None)
        selected = "baseline" if variant.startswith("native") else variant
        if image_measure is None:
            return transforms.measure_variant(
                rgb, fid, width, height, segmenter, variant=selected, views=others
            )
        if selected == "vote":
            return transforms.vote_answers(
                [image_measure(im, fid, width, height) for im in [rgb, *others]]
            )
        if selected == "median":
            rgb = transforms.fuse_images([rgb, *others])
        elif selected.startswith("metric"):
            rgb = transforms.metric_resize(rgb, width, height, int(selected[6:]))
        elif selected in ("clahe", "shade", "whitebalance"):
            rgb = transforms.normalize_light(rgb, selected)
        elif selected != "baseline":
            raise ValueError("periodic/contrast variants implement classic opening masks only")
        return image_measure(rgb, fid, width, height)

    return measure


def colour_delta(a, b):
    def rgb(value):
        return np.array([int(value[i : i + 2], 16) for i in (1, 3, 5)])

    return float(np.linalg.norm(lab(rgb(a)) - lab(rgb(b))))


def pseudo_benchmark(labels, refs, output, variants, segmenter, engine, image_measure=None):
    from .facade_fast import crop_for_call, sha

    geometry, original = {}, {}
    for path in sorted(labels.glob("*/descriptions.json")):
        original.update(json.loads(path.read_text()))
        geometry.update(
            {
                w["facade_id"]: dict(w, tile=path.parent.name)
                for w in json.loads((path.parent / "plan.json").read_text())["walls"]
            }
        )
    views = {
        fid: views_for(refs, fid, entry.get("reference", {}).get("view_id"))
        for fid, entry in original.items()
    }
    crops, matched = {}, 0
    for fid, records in views.items():
        if not records:
            continue
        crops[fid] = crop_for_call(SimpleNamespace(id=fid), records[0], refs, output / "inputs")
        expected = original[fid].get("reference", {}).get("crop_sha256")
        if expected:
            if sha(crops[fid]) != expected:
                raise ValueError(f"recreated crop does not match recorded Astra input: {fid}")
            matched += 1
    for variant in variants:
        target = output / f"pseudo-{engine}-{variant}.json"
        measure = predictor(refs, views, variant, segmenter, output, image_measure)
        rows = []
        for fid, entry in original.items():
            wall = geometry[fid]
            start = time.monotonic()
            answer = sidecar = None
            if views[fid]:
                rgb = read_rgb(crops[fid], side=None)
                answer, sidecar = measure(rgb, fid, wall["width_m"], wall["height_m"])
                if validate(answer, fid):
                    raise ValueError(validate(answer, fid))
            row = dict(
                facade_id=fid,
                width_m=wall["width_m"],
                height_m=wall["height_m"],
                label_source=entry["source"],
                description=answer,
                cv=sidecar,
                seconds=time.monotonic() - start,
                truth=entry["description"],
            )
            rows.append(row)
        # Same tile-local donor/register policy as facade_fast; no copied label answers.
        for row in rows:
            fid = row["facade_id"]
            wall = geometry[fid]
            own = row["description"]
            if own and own["wall_visible"]:
                row["evaluated_description"] = own
                row["source"] = "reference"
                continue
            donors = [
                r
                for r in rows
                if geometry[r["facade_id"]]["building_id"] == wall["building_id"]
                and geometry[r["facade_id"]]["tile"] == wall["tile"]
                and r["description"]
                and r["description"]["wall_visible"]
            ]
            if donors:
                donor = max(donors, key=lambda r: r["width_m"] * r["height_m"])
                row["evaluated_description"] = adopt(
                    donor["description"], fid, wall["width_m"], donor["width_m"]
                )
                row["source"] = "building"
            else:
                row["evaluated_description"] = register_description(
                    fid,
                    wall["building_id"],
                    original[fid].get("attributes", {}),
                    wall["width_m"],
                    wall["height_m"],
                )
                row["source"] = "register"

        def score(selected, field):
            measured = [r for r in selected if r[field] is not None]
            columns = [r for r in measured if r["truth"]["storeys"] > 1]
            return dict(
                n=len(selected),
                measured=len(measured),
                storeys_exact=sum(r[field]["storeys"] == r["truth"]["storeys"] for r in measured),
                columns_n=len(columns),
                columns_exact=sum(
                    r[field]["window_columns"] == r["truth"]["window_columns"] for r in columns
                ),
                median_colour_delta=round(
                    float(
                        np.median(
                            [
                                colour_delta(r[field]["wall_colour"], r["truth"]["wall_colour"])
                                for r in measured
                            ]
                        )
                    ),
                    2,
                )
                if measured
                else None,
            )

        direct = [r for r in rows if r["label_source"] == "reference"]
        result = dict(
            engine=engine,
            variant=variant,
            walls=len(rows),
            original_crop_hashes_matched=matched,
            direct=score(direct, "description"),
            direct_width_at_least_3m=score([r for r in direct if r["width_m"] >= 3], "description"),
            fallback_inclusive=score(rows, "evaluated_description"),
            seconds_per_wall=round(sum(r["seconds"] for r in rows) / len(rows), 3),
            seconds_per_measured_wall=round(
                sum(r["seconds"] for r in rows if r["description"] is not None)
                / max(1, sum(r["description"] is not None for r in rows)),
                3,
            ),
            rows=rows,
        )
        write_json(target, result)
        print(json.dumps({k: v for k, v in result.items() if k != "rows"}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--refs", type=Path, required=True)
    parser.add_argument(
        "--assembly", type=Path, default=config.data_root() / "facade-approvals/assembly"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--variants", nargs="+", choices=VARIANTS, default=list(VARIANTS))
    parser.add_argument("--segmentation", choices=("classic", "segformer"), default="classic")
    parser.add_argument("--engine", default="cv", help="cv or a landed facade_models.Engine name")
    args = parser.parse_args()
    if bool(args.fixture) == bool(args.labels):
        parser.error("choose --fixture or --labels")
    start = time.monotonic()
    image_measure = None
    segmenter = None
    engine = args.segmentation
    if args.engine != "cv":
        from .facade_models import Engine

        image_measure = Engine(args.engine).measure
        engine = args.engine
    elif args.segmentation == "segformer":
        segmenter = cv.Segmenter()
    load_seconds = time.monotonic() - start
    if args.labels:
        pseudo_benchmark(
            args.labels, args.refs, args.output, args.variants, segmenter, engine, image_measure
        )
        return
    from .facade_cv_benchmark import benchmark

    jobs = [
        json.loads(line) for line in (args.fixture / "describe_jobs.jsonl").read_text().splitlines()
    ]
    views = {
        j["meta"]["facade_id"]: views_for(
            args.refs, j["meta"]["facade_id"], j["meta"]["reference"]["view_id"]
        )
        for j in jobs
    }
    for variant in args.variants:
        target = args.output / f"reviewer-{engine}-{variant}.json"
        measure = predictor(args.refs, views, variant, segmenter, args.output, image_measure)
        benchmark(
            args.fixture,
            args.assembly,
            target,
            "classic",
            measure_fn=measure,
            engine=f"{engine}-{variant}",
        )
        result = json.loads(target.read_text())
        result.update(model_load_seconds=round(load_seconds, 3), segmentation=args.segmentation)
        write_json(target, result)


if __name__ == "__main__":
    main()
