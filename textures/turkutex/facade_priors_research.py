"""Reproduce the local-only priors, lidar and grouped distillation experiments."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image
from turku3d import config

from . import facade_cv as cv
from . import facade_priors as p
from .facade_batch import write_json
from .facade_derive import building_attributes
from .facade_priors_lidar import opening_grid


def dataset(labels, fixture, inventory, wfs):
    descriptions, geometry = {}, {}
    for path in sorted(labels.glob("*/descriptions.json")):
        descriptions.update(json.loads(path.read_text()))
        geometry.update(
            {
                r["facade_id"]: r
                for r in json.loads((path.parent / "plan.json").read_text())["walls"]
            }
        )
    benchmark = json.loads((fixture / "plan.json").read_text())["walls"]
    geometry.update({r["facade_id"]: r for r in benchmark})
    attributes = building_attributes(wfs, {k.rsplit("_", 1)[0] for k in geometry})
    truth_buildings = {p.building_group(r["facade_id"]) for r in benchmark}
    records = []
    for fid, g in geometry.items():
        source = descriptions.get(fid)
        records.append(
            dict(
                **g,
                attributes=attributes.get(fid.rsplit("_", 1)[0], {}),
                source=source["source"] if source else "benchmark",
                label=source["description"] if source else None,
                reference=source.get("reference") if source else None,
                group=p.building_group(fid),
                benchmark_building=p.building_group(fid) in truth_buildings,
            )
        )
    return records


def extract(args):
    records = dataset(args.labels, args.fixture, args.inventory, args.wfs)
    if args.lidar:
        from turku3d.lidar import walls as w

        features = json.loads(args.inventory.read_text())["features"]
        wanted = {r["facade_id"] for r in records}
        walls = [wall for wall in w.walls_from_features(features) if wall.id in wanted]
        points_path = args.output / "wall-points.npz"
        if points_path.exists():
            points = w.load(points_path)
        else:
            points = w.wall_points(walls)
            w.save(points_path, points)
        lidar = {}
        for wall in walls:
            stats, grids = opening_grid(points[wall.id], wall.length, wall.height)
            lidar[wall.id] = stats
            np.savez_compressed(args.output / f"lidar-{wall.id}.npz", **grids)
        write_json(args.output / "lidar.json", lidar)
        print(
            json.dumps(
                dict(lidar_walls=len(lidar), usable=sum(s["usable"] for s in lidar.values()))
            )
        )
        return
    embedding = p.Embeddings() if args.embeddings else None
    cache_path = args.output / "features.json"
    old = (
        {r["facade_id"]: r for r in json.loads(cache_path.read_text())}
        if cache_path.exists()
        else {}
    )
    for index, record in enumerate(records):
        fid = record["facade_id"]
        record["priors"] = p.geometry_features(
            record["width_m"], record["height_m"], record["attributes"]
        )
        record["geometry"] = p.prior(fid, record["width_m"], record["height_m"], {}, register=False)
        record["register"] = p.prior(
            fid, record["width_m"], record["height_m"], record["attributes"]
        )
        crop = args.fixture / "inputs" / f"{fid}.jpg" if record["source"] == "benchmark" else None
        if record["reference"]:
            ref = record["reference"]
            crop = args.refs / fid / f"{ref['view_id']}_rectified.jpg"
        if crop is None or not crop.exists():
            continue
        with Image.open(crop) as image:
            image = image.convert("RGB")
            scale = 768 / max(image.size)
            if scale < 1:
                image = image.resize(
                    (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                    Image.Resampling.LANCZOS,
                )
            rgb = np.asarray(image)
        start = time.monotonic()
        answer, sidecar = cv.measure(rgb, fid, record["width_m"], record["height_m"])
        record.update(classic=answer, appearance=p.image_features(rgb, answer, sidecar))
        record["image_seconds"] = time.monotonic() - start
        if embedding:
            start = time.monotonic()
            record["dino"] = (
                old[fid]["dino"] if fid in old and "dino" in old[fid] else embedding(rgb).tolist()
            )
            record["embedding_seconds"] = time.monotonic() - start
        elif "dino" in old.get(fid, {}):
            record["dino"] = old[fid]["dino"]
        if index % 40 == 0:
            print(f"features {index}/{len(records)}", flush=True)
    write_json(cache_path, records)
    print(
        json.dumps(
            dict(
                records=len(records),
                images=sum("classic" in r for r in records),
                embeddings=sum("dino" in r for r in records),
            )
        )
    )


def metrics(predictions, records):
    if not records:
        return {}
    pairs = [
        (predictions[r["facade_id"]], r["label"]) for r in records if r["facade_id"] in predictions
    ]
    return dict(
        n=len(pairs),
        storeys=sum(a["storeys"] == b["storeys"] for a, b in pairs),
        columns_n=sum(b["storeys"] > 1 for _, b in pairs),
        columns=sum(
            a["window_columns"] == b["window_columns"] for a, b in pairs if b["storeys"] > 1
        ),
        material=sum(a["material"] == b["material"] for a, b in pairs),
        colour_delta=round(
            float(
                np.median(
                    [
                        np.linalg.norm(
                            p.colour_lab(a["wall_colour"]) - p.colour_lab(b["wall_colour"])
                        )
                        for a, b in pairs
                    ]
                )
            ),
            3,
        ),
    )


VARIANTS = {
    "ridge_priors": ["priors"],
    "ridge_image": ["appearance"],
    "ridge_fusion": ["priors", "appearance"],
    "ridge_dino": ["dino"],
    "ridge_dino_priors": ["priors", "appearance", "dino"],
    "forest_priors": ["priors"],
    "forest_fusion": ["priors", "appearance"],
    "forest_dino_priors": ["priors", "appearance", "dino"],
}


def vector(record, features):
    return sum((record[k] for k in features), [])


def train_predict(train, targets, features, name="ridge"):
    fit = p.fit_forest if name.startswith("forest_") else p.fit_head
    head = fit([vector(r, features) for r in train], [r["label"] for r in train])
    return head, {r["facade_id"]: p.predict_head(head, vector(r, features)) for r in targets}


def experiment(args):
    start = time.monotonic()
    records = json.loads((args.output / "features.json").read_text())
    lidar = (
        json.loads((args.output / "lidar.json").read_text())
        if (args.output / "lidar.json").exists()
        else {}
    )
    for r in records:
        stats = lidar.get(r["facade_id"], {})
        r["lidar"] = [stats.get(k, 0) for k in ("points_per_m2", "coverage", "supported")]
        for kind in ("density", "depth"):
            r["lidar"] += [
                stats.get("candidates", {}).get(kind, {}).get(k, 0)
                for k in ("storeys", "window_columns")
            ]
    pseudo = [r for r in records if r["source"] == "reference" and "classic" in r]
    eligible = [r for r in pseudo if not r["benchmark_building"]]
    groups = sorted(
        {r["group"] for r in eligible},
        key=lambda s: hashlib.sha256(f"priors-2026:{s}".encode()).hexdigest(),
    )
    folds = {g: i % 5 for i, g in enumerate(groups)}
    # Fixed building split: 60% train, 20% select/calibrate, 20% untouched test.
    train = [r for r in eligible if folds[r["group"]] < 3]
    validation = [r for r in eligible if folds[r["group"]] == 3]
    test = [r for r in eligible if folds[r["group"]] == 4]
    usable = [r for r in records if "classic" in r]
    variants = {k: v for k, v in VARIANTS.items() if all(all(f in r for f in v) for r in usable)}
    if lidar:
        variants["ridge_lidar_fusion"] = ["priors", "appearance", "lidar"]
    predictions = {
        k: {r["facade_id"]: r[k] for r in records if k in r}
        for k in ("geometry", "register", "classic")
    }
    heads = {}
    for name, features in variants.items():
        heads[name], predictions[name] = train_predict(train, usable, features, name)
    for kind in ("density", "depth"):
        if lidar:
            predictions[f"lidar_{kind}"] = {
                r["facade_id"]: dict(
                    r["register"],
                    **{
                        k: lidar.get(r["facade_id"], {})
                        .get("candidates", {})
                        .get(kind, {})
                        .get(k, 0)
                        for k in ("storeys", "window_columns")
                    },
                )
                for r in records
            }
    scores = {
        name: dict(validation=metrics(pred, validation), test=metrics(pred, test))
        for name, pred in predictions.items()
    }
    # Lidar remains diagnostic unless adequately sampled; do not turn empty cells into evidence.
    choices = [k for k in predictions if not k.startswith("lidar_") and k != "ridge_lidar_fusion"]
    fusion, calibration = {}, {}
    for field in p.FIELDS:
        selected = [r for r in validation if field != "window_columns" or r["label"]["storeys"] > 1]

        def loss(name, field=field, selected=selected):
            pairs = [
                (predictions[name][r["facade_id"]][field], r["label"][field]) for r in selected
            ]
            return float(
                np.mean(
                    [
                        np.linalg.norm(p.colour_lab(a) - p.colour_lab(b))
                        if field == "wall_colour"
                        else a != b
                        for a, b in pairs
                    ]
                )
            )

        source = min(choices, key=loss)
        fusion[field] = source
        # Disjoint calibration fold: it never selects a source or changes its fitted head.
        calibrate = [r for r in test if field != "window_columns" or r["label"]["storeys"] > 1]
        success = sum(
            np.linalg.norm(
                p.colour_lab(predictions[source][r["facade_id"]][field])
                - p.colour_lab(r["label"][field])
            )
            <= 15
            if field == "wall_colour"
            else predictions[source][r["facade_id"]][field] == r["label"][field]
            for r in calibrate
        )
        calibration[field] = dict(
            probability=float(success / len(calibrate)),
            successes=int(success),
            n=len(calibrate),
            event="deltaE76 <= 15" if field == "wall_colour" else "exact match",
            target="Astra pseudo-label",
            scope="building-held-out test/calibration fold; global field reliability",
        )
    fused = {
        r["facade_id"]: {
            field: predictions[source][r["facade_id"]][field] for field, source in fusion.items()
        }
        for r in usable
    }
    predictions["fusion"] = fused
    scores["fusion"] = dict(validation=metrics(fused, validation), test=metrics(fused, test))
    # Learning curve uses nested building subsets and the same untouched test buildings.
    curve = []
    train_groups = [g for g in groups if folds[g] < 3]
    for fraction in (0.25, 0.5, 0.75, 1.0):
        chosen = set(train_groups[: max(2, round(len(train_groups) * fraction))])
        subset = [r for r in train if r["group"] in chosen]
        row = dict(buildings=len(chosen), labels=len(subset), fraction=fraction)
        for name in ("ridge_priors", "ridge_fusion", "ridge_dino_priors", "forest_priors"):
            if name in variants:
                _, pred = train_predict(subset, test, variants[name], name)
                row[name] = metrics(pred, test)
        curve.append(row)
    # Five-fold predictions score every eligible direct label without evaluating its own fit.
    out_of_fold = {name: {} for name in variants}
    for fold in range(5):
        fit = [r for r in eligible if folds[r["group"]] != fold]
        held = [r for r in eligible if folds[r["group"]] == fold]
        for name, features in variants.items():
            _, pred = train_predict(fit, held, features, name)
            out_of_fold[name].update(pred)
    benchmark_targets = [r for r in usable if r["benchmark_building"]]
    for name, features in variants.items():
        _, pred = train_predict(eligible, benchmark_targets, features, name)
        out_of_fold[name].update(pred)
    for name in ("geometry", "register", "classic", "lidar_density", "lidar_depth"):
        if name in predictions:
            out_of_fold[name] = predictions[name]
    scores_278 = {name: metrics(pred, pseudo) for name, pred in out_of_fold.items()}
    scores_wide = {
        name: metrics(pred, [r for r in pseudo if r["width_m"] >= 3])
        for name, pred in out_of_fold.items()
    }
    # Explicitly labelled fallback-inclusive comparison, not 425 independent vision truths.
    all_labels = [r for r in records if r["source"] != "benchmark"]
    scores_425 = {
        name: metrics(
            {r["facade_id"]: pred.get(r["facade_id"], r["register"]) for r in all_labels},
            all_labels,
        )
        for name, pred in out_of_fold.items()
    }
    # Keep shipped head identical to the split model whose test/calibration is reported.
    selected_heads = {
        name: dict(features=variants[name], head=heads[name])
        for name in set(fusion.values())
        if name in heads
    }
    for name, model in selected_heads.items():
        if model["head"].get("kind") == "forest":
            model["head"]["fields"] = {
                field: value
                for field, value in model["head"]["fields"].items()
                if fusion[field] == name
            }
    parameters = dict(
        version=p.VERSION,
        embedding=any("dino" in model["features"] for model in selected_heads.values()),
        heads=selected_heads,
        fusion=fusion,
        calibration=calibration,
        training=dict(
            labels=len(train),
            buildings=len({r["group"] for r in train}),
            groups=sorted({r["group"] for r in train}),
            excluded_reviewer_buildings=sorted(
                {r["group"] for r in records if r["benchmark_building"]}
            ),
        ),
    )
    write_json(args.output / "parameters.json", parameters)
    write_json(args.output / "predictions.json", predictions)
    write_json(args.output / "oof-predictions.json", out_of_fold)
    report = dict(
        inventory=len(all_labels),
        direct_labels=len(pseudo),
        eligible_labels=len(eligible),
        eligible_buildings=len(groups),
        splits={
            name: dict(labels=len(rows), buildings=len({r["group"] for r in rows}))
            for name, rows in (("train", train), ("validation", validation), ("test", test))
        },
        split_groups=folds,
        scores=scores,
        pseudo_oof=scores_278,
        pseudo_oof_width_at_least_3m=scores_wide,
        fallback_inclusive_425=scores_425,
        learning_curve=curve,
        fusion=fusion,
        calibration=calibration,
        training_seconds=round(time.monotonic() - start, 3),
    )
    write_json(args.output / "experiment.json", report)
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ("split_groups", "calibration")}, indent=2
        )
    )


def score_benchmark(args):
    """Score cached predictions with the original evaluator; its timings are not inference."""
    from .facade_cv_benchmark import benchmark

    predictions = json.loads((args.output / "predictions.json").read_text())
    records = {r["facade_id"]: r for r in json.loads((args.output / "features.json").read_text())}
    for name, pred in predictions.items():

        def measure(rgb, fid, width, height, pred=pred, name=name):
            answer = records[fid]["classic"].copy()
            answer.update({field: pred[fid][field] for field in p.FIELDS})
            return answer, dict(engine=name, cached_scores=True)

        benchmark(
            args.fixture,
            args.assembly,
            args.output / f"benchmark-{name}.json",
            "classic",
            measure_fn=measure,
            engine=name,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["extract", "experiment", "benchmark"])
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--refs", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument(
        "--inventory", type=Path, default=config.build_dir("layers/buildings/facades.geojson")
    )
    parser.add_argument("--wfs", type=Path, default=config.raw_dir("wfs/work-3x3"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--assembly", type=Path, default=config.data_root() / "facade-approvals/assembly"
    )
    parser.add_argument("--lidar", action="store_true")
    parser.add_argument("--embeddings", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    {"extract": extract, "experiment": experiment, "benchmark": score_benchmark}[args.command](args)


if __name__ == "__main__":
    main()
