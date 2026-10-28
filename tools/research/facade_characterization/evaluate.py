"""Private reference appearance comparisons, annotation scoring and throughput."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from PIL import Image
from turku3d import config
from turkutex import facade_program as fp
from turkutex.facade_style import lab


def rgb(colour):
    return np.array([int(colour[i : i + 2], 16) for i in (1, 3, 5)])


def delta(a, b):
    return float(np.linalg.norm(lab(np.array(a)) - lab(np.array(b))))


def reference_groups(root):
    registry = {
        r["feature_key"]: str(r["pysyvarakennustunnus"])
        for r in pq.read_table(
            config.raw_dir("wfs/work-3x3") / "bldg_Building_LOD2.parquet",
            columns=["feature_key", "pysyvarakennustunnus"],
        ).to_pylist()
    }
    groups = []
    round2 = Path.home() / "turku3d/private/gmaps-data/round2/coverage.json"
    states = json.loads(round2.read_text())["quarters"]
    for quarter, state in states.items():
        if state["status"] != "measured":
            continue
        report = json.loads(Path(state["report"]).read_text())
        for name, patches in report["facades"].get("colour_groups", {}).items():
            match = re.fullmatch(r"walls_(Building_\d+):normal-(-?\d+)", name)
            if not match:
                continue
            groups.append(
                dict(
                    round=2,
                    quarter=quarter,
                    group=name,
                    building_id=registry.get(match[1]),
                    yaw=int(match[2]),
                    reference_rgb=np.median(
                        [p["reference"] for p in patches.values()], axis=0
                    ).tolist(),
                    own_rgb=np.median([p["own"] for p in patches.values()], axis=0).tolist(),
                    report=state["report"],
                )
            )
    measurement = json.loads((root / "round3-measurements.json").read_text())
    aliases = {b["building_key"]: b["building_id"] for b in measurement["buildings"]}
    for g in measurement["wall_groups"]:
        match = re.fullmatch(r"walls_(Building_\d+):normal-(-?\d+)", g["group"])
        if match:
            groups.append(
                dict(g, round=3, building_id=aliases.get(g["building_key"]), yaw=int(match[2]))
            )
    return groups


def compare(root):
    programs = {b["building_id"]: b for b in json.loads((root / "programs.json").read_text())}
    results = []
    exclusions = Counter()
    for g in reference_groups(root):
        b = programs.get(g["building_id"])
        if not b:
            exclusions["building not photographed"] += 1
            continue
        normal = np.array([np.cos(np.radians(g["yaw"])), np.sin(np.radians(g["yaw"]))])
        candidates = [
            w
            for w in b["walls"]
            if abs(normal @ np.array(w["geometry"]["normal"])) >= np.cos(np.radians(15))
            and w["wall"]["colour"]["value"]
        ]
        if len(candidates) != 1:
            exclusions["no unique parallel photographed wall"] += 1
            continue
        w = candidates[0]
        rendered = np.median(np.array(fp.paint(w, b["storeys"]["value"])).reshape(-1, 3), axis=0)
        results.append(
            dict(
                round=g["round"],
                quarter=g["quarter"],
                group=g["group"],
                building_id=b["building_id"],
                wall_id=w["wall_id"],
                wall_colour_delta=delta(rgb(w["wall"]["colour"]["value"]), g["reference_rgb"]),
                rendered_delta=delta(rendered, g["reference_rgb"]),
                baseline_delta=delta(g["own_rgb"], g["reference_rgb"]),
            )
        )
    summary = {}
    for round_ in [2, 3]:
        selected = [r for r in results if r["round"] == round_]
        by_building = defaultdict(list)
        for r in selected:
            by_building[r["building_id"]].append(r)
        summary[str(round_)] = dict(
            groups=len(selected),
            buildings=len(by_building),
            median={
                k: float(np.median([r[k] for r in selected])) if selected else None
                for k in ["wall_colour_delta", "rendered_delta", "baseline_delta"]
            },
            building_balanced_median={
                k: float(
                    np.median([np.median([r[k] for r in rows]) for rows in by_building.values()])
                )
                if selected
                else None
                for k in ["wall_colour_delta", "rendered_delta", "baseline_delta"]
            },
        )
    out = dict(
        summary=summary,
        exclusions=dict(exclusions),
        rows=results,
        matching="Same register building, exactly one photographed wall within 15 degrees of unsigned group normal; trim groups excluded",
        limitations="Reference groups include glazing and baked lighting. These are appearance comparisons, not calibrated wall albedo truth. Parallel walls without an unambiguous match abstain.",
    )
    (root / "reference-comparison.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(summary))


def manual_prepare(root):
    buildings = json.loads((root / "programs.json").read_text())
    candidates = [
        (b, w)
        for b in buildings
        for w in b["walls"]
        if w["observation"]["visible_share"] >= 0.75 and w["width_m"] >= 10
    ]
    candidates.sort(key=lambda bw: fp.stable_seed(bw[1]["wall_id"], "manual"))
    selected = []
    ids = set()
    for b, w in candidates:
        if b["building_id"] in ids:
            continue
        ids.add(b["building_id"])
        selected.append((b, w))
        if len(selected) == 12:
            break
    records = []
    for i, (_b, w) in enumerate(selected):
        with Image.open(w["evidence"]["photo"]) as im:
            image = im.copy()
        image.save(root / f"manual-{i + 1:02d}.png")
        records.append(
            dict(
                index=i + 1,
                wall_id=w["wall_id"],
                photo=str(root / f"manual-{i + 1:02d}.png"),
                visible_windows=None,
                visible_doors=None,
                bays=None,
                notes="",
            )
        )
    (root / "manual-template.json").write_text(json.dumps(records, indent=1))
    print(
        json.dumps(
            dict(
                samples=len(records),
                selection="SHA256-order, one wall/building, model visible share >= .75, width >=10m; count predictions hidden",
            )
        )
    )


def manual_score(root):
    truth = json.loads((root / "manual-labels.json").read_text())
    programs = json.loads((root / "programs.json").read_text())
    walls = {w["wall_id"]: w for b in programs for w in b["walls"]}
    results = []
    for t in truth:
        w = walls[t["wall_id"]]
        predicted = dict(
            visible_windows=len(w["windows"]["rectangles_m"]),
            visible_doors=sum(d["source"] == "DINO door" for d in w["doors"]["candidates"]),
            bays=w["bays"]["count"],
        )
        results.append(dict(**t, predicted=predicted))
    metrics = {}
    for field in ["visible_windows", "visible_doors", "bays"]:
        subset = [r for r in results if r[field] is not None]
        metrics[field] = (
            dict(
                n=len(subset),
                exact=sum(r[field] == r["predicted"][field] for r in subset),
                mae=float(np.mean([abs(r[field] - r["predicted"][field]) for r in subset])),
            )
            if subset
            else dict(n=0)
        )
    (root / "manual-scores.json").write_text(
        json.dumps(
            dict(
                metrics=metrics,
                rows=results,
                interpretation="Single Codex visual review of a small local photo sample; count accuracy, not box IoU or a second independent human review",
            ),
            indent=1,
        )
    )
    print(json.dumps(metrics))


def timing(root):
    raw = [json.loads(p.read_text()) for p in (root / "raw").glob("*.json")]
    detection = [json.loads(p.read_text()) for p in (root / "doors").glob("*.json")]
    walls = len(json.loads((root / "selection.json").read_text()))
    totals = {
        k: sum(r["seconds"][k] for r in raw) for k in ["decode_share", "rectify", "inference"]
    }
    totals["detector"] = sum(r["seconds"] for r in detection)
    totals["fit"] = json.loads((root / "fit-timing.json").read_text())["seconds"]
    totals["opening_refinement"] = json.loads((root / "detector-run.json").read_text())[
        "refine_seconds"
    ]
    seconds = sum(totals.values())
    per_wall = seconds / walls
    cpu = totals["decode_share"] + totals["rectify"] + totals["fit"] + totals["opening_refinement"]
    gpu = totals["inference"] + totals["detector"]
    estimates = {}
    for n in [3079, 5664]:
        estimates[str(n)] = {
            str(wpp): dict(
                walls=n * wpp,
                mac_hours=n * wpp * per_wall / 3600,
                rented_gpu_hours_scenario={
                    str(speedup): n * wpp * (cpu + gpu / speedup) / walls / 3600
                    for speedup in [1, 2, 4]
                },
            )
            for wpp in [2, 4, 8]
        }
    out = dict(
        views=len(raw),
        walls=walls,
        stages_seconds=totals,
        seconds_per_wall=per_wall,
        walls_per_second=1 / per_wall,
        estimates=estimates,
        painter=json.loads((root / "painter-timing.json").read_text()),
        limitations="Serial shared-host timings exclude weight loading, network copy, host-slot waits and output compression. GPU scenarios are assumptions, not rented-hardware benchmarks; decode/rectify/fit unchanged. Panorama-to-unique-wall ratio is unknown.",
    )
    (root / "timing.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stage", choices=["compare", "manual_prepare", "manual_score", "timing"])
    p.add_argument("--root", type=Path, required=True)
    a = p.parse_args()
    globals()[a.stage](a.root)


if __name__ == "__main__":
    main()
