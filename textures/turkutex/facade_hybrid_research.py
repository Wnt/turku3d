"""Extract, cross-fit and measure the hybrid without exposing labels to their own predictor."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
from turku3d import config

from . import facade_fast as ff
from . import facade_hybrid as hybrid
from . import facade_priors as p
from .facade_derive import building_attributes
from .facade_hybrid_truth import read_labels


def fold(fid):
    return int(hashlib.sha256(("hybrid-v1:" + p.building_group(fid)).encode()).hexdigest(), 16) % 5


def old_rows(labels: Path, refs: Path, root: Path):
    result = []
    for path in sorted(labels.glob("*/descriptions.json")):
        geometry = {
            r["facade_id"]: r for r in json.loads((path.parent / "plan.json").read_text())["walls"]
        }
        for fid, record in json.loads(path.read_text()).items():
            row = geometry[fid]
            if record["source"] != "reference" or row["width_m"] < 6:
                continue
            ref = record["reference"]
            crop = ff.crop_for_call(
                SimpleNamespace(id=fid),
                {"files": {"rectified": ref["view_id"] + "_rectified.jpg"}},
                refs,
                root / "old-inputs",
            )
            if ff.sha(crop) != ref["crop_sha256"]:
                raise ValueError(f"original crop hash mismatch: {fid}")
            result.append(dict(**row, crop=str(crop), label=record["description"], old=True))
    return result


def extract(args):
    args.output.mkdir(parents=True, exist_ok=True)
    rows = old_rows(args.labels, args.refs, args.output)
    if (args.truth / "agreed.json").exists():
        rows += [
            dict(r, old=False)
            for r in json.loads((args.truth / "agreed.json").read_text()).values()
        ]
    attrs = building_attributes(config.raw_dir("wfs/work-3x3"), {r["building_id"] for r in rows})
    path = args.output / "features.json"
    old = json.loads(path.read_text()) if path.exists() else {}
    model = hybrid.FeatureExtractor()
    extractor_sha = hashlib.sha256(Path(hybrid.__file__).read_bytes()).hexdigest()
    for index, row in enumerate(rows):
        fid = row["facade_id"]
        digest = ff.sha(Path(row["crop"]))
        cached = old.get(fid)
        if (
            cached
            and cached["crop_sha256"] == digest
            and cached.get("extractor_sha") == extractor_sha
        ):
            cached.update(row, attributes=attrs.get(row["building_id"], {}))
            continue
        with Image.open(row["crop"]) as image:
            rgb = np.asarray(image.convert("RGB"))
        start = time.perf_counter()
        features = model.extract(rgb, fid, row["width_m"], row["height_m"])
        old[fid] = dict(
            row,
            crop_sha256=digest,
            extractor_sha=extractor_sha,
            attributes=attrs.get(row["building_id"], {}),
            features=features,
            seconds=time.perf_counter() - start,
        )
        ff.write_json(path, old)
        if index % 20 == 0:
            print(json.dumps(dict(extracted=index + 1, total=len(rows))), flush=True)
    ff.write_json(path, old)
    print(json.dumps(dict(extracted=len(old))))


def clean(row):
    e = row["features"]["evidence"]
    return (
        not e["screen_problem"]
        and e["occlusion_share"] <= 0.2
        and e["facade_share"] >= 0.5
        and not e.get("multiple_surfaces", False)
    )


def fit(rows, material_source="embedding"):
    if not rows:
        raise ValueError("no clean training rows")
    return dict(
        version=hybrid.VERSION,
        material_source=material_source,
        head=p.fit_head(
            [r["features"]["embedding"] for r in rows], [r["label"] for r in rows], 100
        ),
        training=dict(
            walls=len(rows),
            buildings=len({p.building_group(r["facade_id"]) for r in rows}),
            alpha=100,
            input_hash=hashlib.sha256(
                json.dumps(
                    [(r["facade_id"], r["crop_sha256"], r["label"]) for r in rows], sort_keys=True
                ).encode()
            ).hexdigest(),
        ),
    )


def select_material(rows):
    """Material source selection uses only training buildings, never the scored fold."""
    scores = dict(embedding=0, classic=0, register=0)
    for i in range(5):
        train = [r for r in rows if fold(r["facade_id"]) != i]
        test = [r for r in rows if fold(r["facade_id"]) == i]
        if not train or not test:
            continue
        head = fit(train)["head"]
        for r in test:
            fid, w, h = r["facade_id"], r["width_m"], r["height_m"]
            candidates = dict(
                embedding=p.predict_head(head, r["features"]["embedding"])["material"],
                classic=r["features"]["classic"]["material"],
                register=p.prior(fid, w, h, r["attributes"])["material"],
            )
            for source, value in candidates.items():
                scores[source] += value == r["label"]["material"]
    return max(scores, key=scores.get), scores


def summary(rows):
    result = dict(walls=len(rows))
    for source in ("register", "hybrid", "astra_low"):
        available = [r for r in rows if r.get(source)]
        columns = [r for r in rows if r["label"]["storeys"] > 1]
        result[source] = dict(
            storeys=dict(
                correct=sum(r[source]["storeys"] == r["label"]["storeys"] for r in available),
                total=len(rows),
            ),
            columns=dict(
                correct=sum(
                    r[source]["window_columns"] == r["label"]["window_columns"]
                    for r in columns
                    if r.get(source)
                ),
                total=len(columns),
            ),
            material=dict(
                correct=sum(r[source]["material"] == r["label"]["material"] for r in available),
                total=len(rows),
            ),
            colour_median_de76=float(
                np.median(
                    [
                        np.linalg.norm(
                            p.colour_lab(r[source]["wall_colour"])
                            - p.colour_lab(r["label"]["wall_colour"])
                        )
                        for r in available
                    ]
                )
            )
            if available
            else None,
            colour_coverage=len(available),
        )
    return result


def winning_fields(metrics):
    """Field-wise deployment evidence; ties keep the register and small pilots cannot qualify."""
    if metrics["walls"] < 100:
        return []
    baseline, candidate = metrics["register"], metrics["hybrid"]
    fields = [
        field
        for field, score in (
            ("storeys", "storeys"),
            ("window_columns", "columns"),
            ("material", "material"),
        )
        if candidate[score]["total"] == baseline[score]["total"]
        and candidate[score]["correct"] > baseline[score]["correct"]
    ]
    if (
        candidate["colour_coverage"] == baseline["colour_coverage"] == metrics["walls"]
        and candidate["colour_median_de76"] < baseline["colour_median_de76"]
    ):
        fields.append("wall_colour")
    return fields


def evaluate(args):
    records = list(json.loads((args.output / "features.json").read_text()).values())
    old = [r for r in records if r["old"] and clean(r)]
    expected = json.loads((args.truth / "agreed.json").read_text())
    truth = [r for r in records if not r["old"] and r["facade_id"] in expected]
    if len(truth) != len(expected) or not truth:
        raise ValueError("extract every agreed truth wall before evaluation")
    if any(
        r["crop_sha256"] != expected[r["facade_id"]]["crop_sha256"]
        or r["label"] != expected[r["facade_id"]]["label"]
        for r in truth
    ):
        raise ValueError("truth inputs changed; extract again before evaluation")
    low = read_labels(args.truth, "low")
    results, sources = [], []
    # Each held-out building is absent from both fitting and material-source selection.
    for i in range(5):
        train = old + [r for r in truth if fold(r["facade_id"]) != i]
        source, scores = select_material(train)
        parameters = fit(train, source)
        sources.append(dict(fold=i, source=source, validation_scores=scores))
        model = hybrid.Fusion(parameters)
        for r in truth:
            fid = r["facade_id"]
            if fold(fid) != i:
                continue
            w, h, attrs = r["width_m"], r["height_m"], r["attributes"]
            prediction, sidecar = model.predict(r["features"], fid, w, h, attrs)
            record = low.get(fid, {})
            results.append(
                dict(
                    facade_id=fid,
                    label=r["label"],
                    register=p.prior(fid, w, h, attrs),
                    hybrid=prediction,
                    fields=sidecar["fields"],
                    astra_low=record or None,
                )
            )
    metrics = summary(results)
    metrics.update(
        protocol="five-fold building-held-out; material selection nested in each training fold",
        old_clean_walls=len(old),
        material_selection=sources,
        seconds_per_wall=float(np.mean([r["seconds"] for r in truth])) if truth else None,
        abstentions={
            f: sum(r["fields"][f]["state"] == "abstain" for r in results) for f in hybrid.FIELDS
        },
    )
    metrics["winning_fields"] = winning_fields(metrics)
    metrics["eligible_for_queue"] = bool(metrics["winning_fields"])
    ff.write_json(args.output / "predictions.json", results)
    ff.write_json(args.output / "metrics.json", metrics)
    source, scores = select_material(old + truth)
    parameters = fit(old + truth, source)
    parameters["material_validation"] = scores
    parameters["evaluation"] = metrics
    ff.write_json(args.output / "parameters.json", parameters)
    print(json.dumps(metrics, indent=2))


def smoke(args):
    """Exercise the real dispatcher/resolver with networking forbidden, without publishing."""
    import socket

    from .building_jobs import latest

    inventory = args.truth / "candidates.geojson"
    selected = list(json.loads((args.truth / "agreed.json").read_text()))[:5]
    run = args.output / "offline-smoke"
    ff.write_json(
        run / "plan.json", ff.plan_walls(inventory, run / "unused-assembly", [], selected)
    )
    original = socket.socket.connect

    def forbidden(*args, **kwargs):
        raise RuntimeError("offline smoke forbids socket connections")

    socket.socket.connect = forbidden
    try:
        result = ff.describe(run, inventory, args.truth / "refs", 1, 80, engine="hybrid")
    finally:
        socket.socket.connect = original
    _, facades = ff.planned_facades(run, inventory)
    records = latest(run / "run_describe/out.jsonl")
    descriptions = ff.descriptions(run, facades, {})
    if result["failed"] or result["calls"] or result["jobs"] != len(selected):
        raise ValueError(result)
    if any(
        row["source"] != "hybrid" or row["description"] != records[fid + ".fast"]["answer"]
        for fid, row in descriptions.items()
    ):
        raise ValueError("resolver changed hybrid field fallbacks")
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["extract", "evaluate", "smoke"])
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--refs", type=Path)
    args = parser.parse_args()
    globals()[args.command](args)


if __name__ == "__main__":
    main()
