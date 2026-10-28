"""Independent local review of usable before/after facade references.

Only aggregate measurements may enter the repo. Crops and reviewer output stay
under TURKU3D_WORK. A usable reference shows the physical target wall's full
extent, with no mixed wall and at most 20% foreground coverage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from . import facade_fast as fast
from .building_jobs import latest, screened_full_views
from .codex.runner import Runner, load_jobs
from .streetview.facades import load_facades


def prepare(args):
    args.output.mkdir(parents=True, exist_ok=True)
    walls = load_facades(args.inventory)
    rows = []
    for wall in walls:
        views, _ = screened_full_views(wall, args.refs)
        if not views:
            continue
        view = views[0]
        crop = fast.crop_for_call(wall, view, args.refs, args.output / "inputs")
        context = args.output / "inputs" / f"{wall.id}_context.jpg"
        shutil.copyfile(args.refs / wall.id / view["files"]["outline"], context)
        rows.append(
            dict(
                facade_id=wall.id,
                crop=str(crop),
                context=str(context),
                crop_sha256=fast.sha(crop),
                context_sha256=fast.sha(context),
            )
        )
    fast.write_json(args.output / "inputs.json", rows)
    fast.write_json(
        args.output / "inventory.json",
        dict(
            walls=len(walls),
            inventory_sha256=fast.sha(args.inventory),
            refs=str(args.refs),
            presented=len(rows),
        ),
    )
    print(json.dumps(dict(walls=len(walls), presented=len(rows))), flush=True)


def schema():
    item = dict(
        type="object",
        properties=dict(
            facade_id=dict(type="string"),
            usable=dict(type="boolean"),
            wrong_wall=dict(type="boolean"),
            multiple_surfaces=dict(type="boolean"),
            occlusion_share=dict(type="number", minimum=0, maximum=1),
            reason=dict(type="string"),
        ),
        required=[
            "facade_id",
            "usable",
            "wrong_wall",
            "multiple_surfaces",
            "occlusion_share",
            "reason",
        ],
        additionalProperties=False,
    )
    return dict(
        type="object",
        properties=dict(walls=dict(type="array", items=item)),
        required=["walls"],
        additionalProperties=False,
    )


def read(args, reviewer):
    rows = {r["facade_id"]: r for r in json.loads((args.output / "inputs.json").read_text())}
    result = {}
    for record in latest(args.output / reviewer / "out.jsonl").values():
        if not record.get("ok"):
            continue
        walls = record["answer"]["walls"]
        ids = [w["facade_id"] for w in walls]
        if len(set(ids)) != len(ids) or set(ids) != set(record["meta"]["ids"]):
            continue
        for wall in walls:
            fid = wall["facade_id"]
            if fid in rows and all(
                record["meta"][key].get(fid) == rows[fid][name]
                for key, name in (
                    ("crop_hashes", "crop_sha256"),
                    ("context_hashes", "context_sha256"),
                )
            ):
                result[fid] = wall
    return result


def label(args):
    rows = json.loads((args.output / "inputs.json").read_text())
    done = read(args, args.reviewer)
    rows = [r for r in rows if r["facade_id"] not in done]
    spec = args.output / "schema.json"
    fast.write_json(spec, schema())
    jobs = []
    for start in range(0, len(rows), args.batch_size):
        batch = rows[start : start + args.batch_size]
        ids = [r["facade_id"] for r in batch]
        jobs.append(
            dict(
                id=args.reviewer + "-" + hashlib.sha256(json.dumps(ids).encode()).hexdigest()[:16],
                prompt=(
                    "Independently inspect each target facade. Each JPG is the wall-plane crop; "
                    "its _context.jpg marks the intended wall in red. Judge whether the reference "
                    "shows the physical target wall from end to end and base to eaves. Reject a "
                    "wrong wall, two different facade planes, cropped window bays or floors, "
                    "pavement/interior images, or foreground objects hiding over 20% of the wall. "
                    "A blank but correctly framed wall is usable; no architectural label is needed. "
                    "For each ID report usable, wrong_wall, multiple_surfaces, occlusion_share, "
                    "and a concrete reason in English. Ignore other images and prior answers. Return each ID once. "
                    + json.dumps(ids)
                ),
                images=[r[k] for r in batch for k in ("crop", "context")],
                schema=str(spec),
                timeout=360,
                model="gpt-6-astra",
                effort="high",
                prompt_version="reference-usability-v1-" + args.reviewer,
                meta=dict(
                    kind="reference_audit",
                    ids=ids,
                    crop_hashes={r["facade_id"]: r["crop_sha256"] for r in batch},
                    context_hashes={r["facade_id"]: r["context_sha256"] for r in batch},
                ),
            )
        )
    path = args.output / f"jobs-{args.reviewer}.jsonl"
    path.write_text("".join(json.dumps(job) + "\n" for job in jobs))
    raise SystemExit(
        Runner(
            args.output / args.reviewer,
            parallel=args.parallel,
            max_pct=100,
            retries=0,
            cache_dir=args.output / "call-cache",
        ).run(load_jobs(path))
    )


def collect(args):
    meta = json.loads((args.output / "inventory.json").read_text())
    rows = json.loads((args.output / "inputs.json").read_text())
    a, b = read(args, "a"), read(args, "b")
    missing = [r["facade_id"] for r in rows if r["facade_id"] not in a or r["facade_id"] not in b]
    if missing:
        raise ValueError(f"{len(missing)} presented references lack paired readings")
    usable = sum(
        all(
            x[fid]["usable"]
            and not x[fid]["wrong_wall"]
            and not x[fid]["multiple_surfaces"]
            and x[fid]["occlusion_share"] <= 0.2
            for x in (a, b)
        )
        for fid in (r["facade_id"] for r in rows)
    )
    result = dict(**meta, usable=usable, usable_share=usable / meta["walls"])
    fast.write_json(args.output / "aggregate.json", result)
    print(json.dumps(result), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "label", "collect"])
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--refs", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--reviewer", choices=["a", "b"])
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--parallel", type=int, default=1)
    args = parser.parse_args()
    args.output = args.output.resolve()
    if args.refs:
        args.refs = args.refs.resolve()
    globals()[args.command](args)


if __name__ == "__main__":
    main()
