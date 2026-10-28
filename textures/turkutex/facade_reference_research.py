"""Reproducible before/after reference acquisition on fixed local wall inventories.

All images and per-wall records stay in the sandbox. Automatic support counts
are evidence screens, not independent truth labels or facade recognition accuracy.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image

from . import facade_fast as fast
from .building_jobs import screened_full_views
from .streetview.evidence import VERSION, measure, problem
from .streetview.facades import load_facades, save_facades


def inventory(args):
    ids = {
        fid
        for path in args.labels.glob("*/descriptions.json")
        for fid in json.loads(path.read_text())
    }
    features = []
    for fid in sorted(ids):
        path = args.before / fid / "refs.json"
        if not path.exists():
            raise ValueError(f"missing original geometry for {fid}")
        features.append(json.loads(path.read_text())["facade"])
    fast.write_json(args.inventory, dict(type="FeatureCollection", features=features))
    print(json.dumps(dict(walls=len(features))))


def screen(facades, refs, output):
    records = json.loads(output.read_text()) if output.exists() else {}
    for f in facades:
        views, reason = screened_full_views(f, refs)
        row = dict(facade_id=f.id, pixel_screen=bool(views), usable=False, reason=reason)
        if views:
            view = views[0]
            path = refs / f.id / view["files"]["rectified"]
            digest = fast.sha(path)
            cached = records.get(f.id, {})
            if (
                cached.get("sha256") == digest
                and cached.get("semantic", {}).get("version") == VERSION
            ):
                continue
            with Image.open(path) as image:
                evidence = measure(np.asarray(image.convert("RGB")))
            row.update(
                sha256=digest,
                view_id=view["view_id"],
                source=view["source"],
                semantic=evidence,
                usable=problem(evidence) is None,
                reason=problem(evidence),
            )
        records[f.id] = row
        fast.write_json(output, records)
    return {f.id: records[f.id] for f in facades}


def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    facades = load_facades(args.inventory)
    if args.limit:
        facades = facades[: args.limit]
    save_facades(facades, args.output / "candidates.geojson")
    before = screen(facades, args.before, args.output / "before.json")
    print(
        json.dumps(
            dict(
                stage="before", walls=len(before), usable=sum(r["usable"] for r in before.values())
            )
        ),
        flush=True,
    )
    start = time.monotonic()
    errors = {}
    for index in range(0, len(facades), 10):
        errors.update(
            fast.make_refs(
                facades[index : index + 10],
                args.output / "refs",
                tuple(args.sources.split(",")),
                3,
                args.max_views,
            )
        )
        fast.write_json(args.output / "errors.json", errors)
        print(
            json.dumps(
                dict(
                    stage="refs",
                    processed=min(index + 10, len(facades)),
                    total=len(facades),
                    seconds=round(time.monotonic() - start, 1),
                    errors=len(errors),
                )
            ),
            flush=True,
        )
    after = screen(facades, args.output / "refs", args.output / "after.json")
    metrics = dict(
        walls=len(facades),
        inventory_sha256=fast.sha(args.inventory),
        before_pixel_screen=sum(r["pixel_screen"] for r in before.values()),
        before_automatic_support=sum(r["usable"] for r in before.values()),
        after_automatic_support=sum(r["usable"] for r in after.values()),
        acquisition_seconds=time.monotonic() - start,
        errors=len(errors),
        note="Automatic geometry/semantic support is not independently reviewed usability.",
    )
    fast.write_json(args.output / "metrics.json", metrics)
    from types import SimpleNamespace

    from .facade_hybrid_truth import screen as truth_screen

    truth_screen(SimpleNamespace(root=args.output), errors)
    print(json.dumps(metrics), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["inventory", "run"])
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--sources", default="lookaround,google,mapillary")
    parser.add_argument("--max-views", type=int, default=2)
    args = parser.parse_args()
    for name in ("inventory", "before", "output", "labels"):
        if getattr(args, name) is not None:
            setattr(args, name, getattr(args, name).resolve())
    globals()[args.command](args)


if __name__ == "__main__":
    main()
