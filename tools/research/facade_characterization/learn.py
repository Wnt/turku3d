"""Inventory accepted readings, fit distributions, and evaluate a frozen building holdout.

All products are private sandbox files. Characterization inputs are read-only.
Rejected forms are skipped without inspecting their content. No image/model calls.
"""

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from learning_context import enrich
from turku3d import config as C
from turkutex.facade_learning import NUMERIC, baseline, fit, predict, targets
from turkutex.facade_program import stable_seed
from turkutex.facade_sections import sections


def inventory(root, programs):
    accepted, revisions = {}, Counter()
    for path in sorted((root / "batches").glob("*/answers.json")):
        for form in json.loads(path.read_text()):
            if form.get("reject"):
                continue
            accepted.setdefault(form["wall_id"], []).append(form)
            revisions[form["wall_id"]] += 1
    distinct = defaultdict(Counter)

    def visit(value, key=""):
        if isinstance(value, dict):
            for k, v in value.items():
                if k not in ("wall_id", "revision_of", "confidence"):
                    visit(v, key + "." + k)
        elif isinstance(value, list):
            for v in value:
                visit(v, key + "[]")
        else:
            distinct[key][json.dumps(value, sort_keys=True)] += 1

    for forms in accepted.values():
        for form in forms:
            visit(form)
    counts = Counter()
    remaining = {}
    for program in programs:
        for wall in program["walls"]:
            form = wall.get("structure", {})
            if form.get("render_deferred"):
                parts, unsupported = sections(form, program["storeys"]["value"])
                counts[
                    "deferred_with_partial_support"
                    if parts
                    else "deferred_without_renderable_extent"
                ] += 1
                counts["supported_sections"] += len(parts)
                remaining[wall["wall_id"]] = unsupported
            elif form:
                counts["full_structured_walls"] += 1
            else:
                counts["earlier_pilot_walls"] += 1
    return dict(
        batch_unique_accepted=len(accepted),
        batch_answers=sum(revisions.values()),
        repeated_wall_answers=sum(n - 1 for n in revisions.values()),
        published_buildings=len(programs),
        published_walls=sum(len(p["walls"]) for p in programs),
        coverage=dict(counts),
        distinct_values={k: dict(sorted(v.items())) for k, v in sorted(distinct.items())},
        unsupported_by_wall=remaining,
    )


def evaluate(programs):
    ids = sorted(
        (p["building_id"] for p in programs), key=lambda key: stable_seed(key, "holdout-v1")
    )
    holdout = set(ids[: round(len(ids) * 0.2)])
    train = [p for p in programs if p["building_id"] not in holdout]
    model = fit(train)
    scores = defaultdict(lambda: defaultdict(list))
    per_building = defaultdict(lambda: defaultdict(list))
    for p in programs:
        if p["building_id"] not in holdout:
            continue
        key, a = p["building_id"], p["attributes"]
        predictions = dict(
            authored=baseline(key, a),
            conditional=predict(key, a, model, local=False),
            neighbour=predict(key, a, model),
            deployed_sample=predict(key, a, model, sampled=True),
        )
        for wall in p["walls"]:
            for field, truth in targets(wall).items():
                for method, prediction in predictions.items():
                    if field not in prediction:
                        continue
                    error = (
                        abs(prediction[field] - truth)
                        if field in NUMERIC
                        else float(prediction[field] == truth)
                    )
                    per_building[(key, field)][method].append(error)
    for (_, field), methods in per_building.items():
        for method, values in methods.items():
            scores[field][method].append(sum(values) / len(values))
    result = {}
    for field, methods in sorted(scores.items()):
        result[field] = dict(
            metric="MAE" if field in NUMERIC else "accuracy",
            buildings=len(methods["authored"]),
            **{k: round(sum(v) / len(v), 4) for k, v in methods.items()},
        )
    return dict(
        train_buildings=len(train),
        test_buildings=len(holdout),
        holdout_ids=sorted(holdout),
        split="20% deterministic SHA256 ranking by building ID; all walls of a building stay together",
        fields=result,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=C.data_root() / "facade-characterization")
    parser.add_argument("--out", type=Path, default=C.work_root() / "reports/facade-learning")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    source = args.root / "publications/ring42/programs.json"
    programs = json.loads(source.read_text())
    inv = inventory(args.root, programs)
    (args.out / "inventory.json").write_text(json.dumps(inv, indent=2) + "\n")
    context = args.out / "context.json"
    if context.exists():
        contexts = json.loads(context.read_text())
        for p in programs:
            p["attributes"].update(contexts[p["building_id"]])
    else:
        enrich(programs)
        context.write_text(
            json.dumps({p["building_id"]: p["attributes"] for p in programs}, indent=2) + "\n"
        )
    result = evaluate(programs)
    result["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    result["available_covariates"] = dict(
        Counter(
            k
            for p in programs
            for k, v in p["attributes"].items()
            if v is not None and v != "unknown"
        )
    )
    result["coverage"] = inv["coverage"]
    (args.out / "evaluation.json").write_text(json.dumps(result, indent=2) + "\n")
    model = fit(programs)
    model["source_sha256"] = result["source_sha256"]
    (args.out / "facade-rules.json").write_text(json.dumps(model, separators=(",", ":")) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "holdout_ids"}, indent=2))


if __name__ == "__main__":
    main()
