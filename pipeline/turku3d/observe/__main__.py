"""Observe buildings: views per wall, rectified crops and the vision reading into stored descriptions.

    python -m turku3d.observe views    (--building KEY... | --at E,N [--radius R] | --view ID...)
    python -m turku3d.observe describe (--building KEY... | --at E,N [--radius R] | --view ID...) [--effort low]
    python -m turku3d.observe assemble                descriptions again from the saved readings, no model call

`views` writes the crops and views.json of each building under $TURKU3D_WORK/observe/buildings/<key>/ and reports
the walls no panorama sees. `describe` also reads them and writes the description to
$TURKU3D_WORK/observe/descriptions/building/<key>.json (the layout of a private descriptions root), and stores it
through turku3d.world.store (docs/design/world-pipeline.md, section 4.2). `assemble` rebuilds every description of
the work directory from its views.json and reading.json, for when only the assembly changed.
`--view` takes reference views of qa/views/views.jsonl and selects every building visible in them.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

from turku3d import config

from . import check, lod2, reader
from .panoramas import REPO, Panoramas
from .prepare import prepare

VISIBLE_SHARE = 0.004  # of a view's pixels, for a building to count as visible in it


def store(doc: dict) -> str | None:
    """Store a description in the shared descriptions root; None when the store refuses."""
    from turku3d.world import store as world_store

    try:
        return str(world_store.write_description(doc, by="w2-observe", note=doc["provenance"][0].get("note", "")))
    except ValueError as error:  # a higher tier is stored already
        print(f"observe: {doc['id']} not stored: {error}", file=sys.stderr)
        return None


def finish(record: dict, reading: dict, meta: dict, packet: Path, root: Path) -> dict:
    """Assemble a reading into its description, check it, write and store it; the facts for the run's report."""
    from turku3d.world.kinds.building import description as fmt

    from turkutex.streetview.height import ground_height  # the survey lidar; on sys.path through .panoramas

    doc = reader.assemble(record, reading, meta, ground=ground_height)
    problems = fmt.errors(doc)
    target = root / "descriptions/building" / f"{record['building']}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    (target if not problems else packet / "invalid-description.json").write_text(
        json.dumps(doc, indent=1, ensure_ascii=False))
    line = {"building": record["building"], "read_s": meta["seconds"], "cost_usd": meta["cost_usd"],
            "invalid": problems[:5] or None, "description": str(target) if not problems else None,
            "stored": store(doc) if not problems else None}
    if not problems:
        try:
            report = check.build_report(doc)
        except Exception as error:  # noqa: BLE001 - the generator is another track's code: its failure is a finding
            line["build_error"] = f"{type(error).__name__}: {error}"[:200]
        else:
            (packet / "build-report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
            line.update(dropped=check.count(report), dropped_reader=check.count(report, reader_only=True),
                        unmatched_walls=report["unmatched"] or None)
    return line


def second_look(record: dict, packet: Path, root: Path, reading: dict, meta: dict, first: dict, effort: str):
    """Ask the reader about the parts the generator dropped; keep whichever reading loses fewer parts."""
    from turkutex.streetview.height import ground_height

    report = json.loads((packet / "build-report.json").read_text())
    asked = {wall: [i for i in items if i["reader"]] for wall, items in report["walls"].items()}
    text = reader.feedback(record, reading, {w: i for w, i in asked.items() if i}, ground_height)
    try:
        again, meta_again = reader.read(record, packet, effort=effort, feedback_text=text)
    except Exception as error:  # noqa: BLE001 - the first reading stands
        return reading, meta, {**first, "second_look": f"failed: {error}"[:120]}
    (packet / "reading-first.json").write_text(json.dumps({"reading": reading, "meta": meta}, indent=1,
                                                          ensure_ascii=False))
    total = {"seconds": round(meta["seconds"] + meta_again["seconds"], 1),
             "cost_usd": (meta["cost_usd"] or 0) + (meta_again["cost_usd"] or 0)}
    second = finish(record, again, {**meta_again, **total}, packet, root)
    note = f"{first['dropped_reader']} -> {second.get('dropped_reader')} parts the generator drops"
    if second.get("invalid") or second.get("dropped_reader", 1e9) >= first["dropped_reader"]:
        kept = finish(record, reading, {**meta, **total}, packet, root)  # the first reading stays stored
        return reading, {**meta, **total}, {**kept, "second_look": note + " (first kept)"}
    return again, {**meta_again, **total}, {**second, "second_look": note}


def visible_in_view(view: dict, scene: lod2.Scene, grid=(96, 72), reach: float = 250.0) -> dict:
    """{building key: share of the view's pixels} from rays against the LoD2 scene."""
    from qalib.camera import pixel_rays

    pose = view["pose"]
    rays = pixel_rays(pose, *grid).reshape(-1, 3)
    cam = np.array([pose["E"], pose["N"], pose["H"]])
    hit, who = scene.cast(cam, cam + rays * reach, stop_short=0.0)
    share = {}
    for k in who[np.isfinite(hit)]:
        share[scene.ids[k]] = share.get(scene.ids[k], 0) + 1 / len(rays)
    return {k: round(v, 4) for k, v in sorted(share.items(), key=lambda kv: -kv[1]) if v >= VISIBLE_SHARE}


def select(args) -> tuple[lod2.Scene, list, dict]:
    """The scene, the buildings asked for and why each was selected."""
    why: dict = {}
    if args.view:
        rows = [json.loads(line) for line in (REPO / "qa/views/views.jsonl").read_text().splitlines() if line.strip()]
        views = [r for r in rows if r["id"] in set(args.view)]
        missing = set(args.view) - {r["id"] for r in views}
        if missing:
            sys.exit(f"unknown views: {', '.join(sorted(missing))}")
        e = [v["pose"]["E"] for v in views]
        n = [v["pose"]["N"] for v in views]
        scene = lod2.Scene((min(e) - 400, min(n) - 400, max(e) + 400, max(n) + 400))
        for v in views:
            for key, share in visible_in_view(v, scene).items():
                why.setdefault(key, {})[v["id"]] = share
        return scene, [scene.buildings[k] for k in why], why
    if args.at:
        e, n = (float(x) for x in args.at.split(","))
        scene = lod2.scene_around(e, n, args.radius + 250)
        found = scene.within(e, n, args.radius) if args.radius > 0 else [scene.nearest(e, n)]
        return scene, [b for b in found if b is not None], why
    if not args.building:
        sys.exit("give --building, --at or --view")
    # A key alone does not say where the building is: find it in the footprint index of the whole register.
    scene = lod2.Scene((-1e12, -1e12, 1e12, 1e12)) if args.everywhere else None
    if scene is None:
        sys.exit("--building needs --near E,N (or --everywhere) until the world store has a building index")
    return scene, [scene.buildings[k] for k in args.building if k in scene.buildings], why


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m turku3d.observe", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["views", "describe", "assemble"])
    ap.add_argument("--building", nargs="*", help="entity keys (with --near)")
    ap.add_argument("--near", help="E,N near the buildings named by --building")
    ap.add_argument("--everywhere", action="store_true", help="load the whole register to find --building keys")
    ap.add_argument("--at", help="E,N (EPSG:3877): the nearest building, or all within --radius")
    ap.add_argument("--radius", type=float, default=0.0)
    ap.add_argument("--view", nargs="*", help="reference view ids: every building visible in them")
    ap.add_argument("--effort", default=reader.EFFORT)
    ap.add_argument("--out", type=Path, default=None, help="root (default $TURKU3D_WORK/observe)")
    ap.add_argument("--jobs", type=int, default=4, help="readings in parallel")
    ap.add_argument("--force", action="store_true", help="read again even when a description exists")
    ap.add_argument("--no-second-look", action="store_true",
                    help="keep the first reading even when the generator drops parts of it")
    a = ap.parse_args(argv)
    root = a.out or config.work_root() / "observe"
    if a.command == "assemble":
        for packet in sorted((root / "buildings").iterdir()):
            if (packet / "reading.json").exists() and (packet / "views.json").exists():
                saved = json.loads((packet / "reading.json").read_text())
                print(json.dumps(finish(json.loads((packet / "views.json").read_text()), saved["reading"],
                                        saved["meta"], packet, root), ensure_ascii=False), flush=True)
        return 0
    if a.building and a.near:
        e, n = (float(x) for x in a.near.split(","))
        scene = lod2.scene_around(e, n, 400)
        buildings, why = [scene.buildings[k] for k in a.building if k in scene.buildings], {}
    else:
        scene, buildings, why = select(a)
    panos = Panoramas(root / "panoramas")
    lines, jobs = {}, []
    for b in buildings:
        started = time.time()
        packet = root / "buildings" / b.id
        record = prepare(scene, b, panos, packet)
        record["selected_by"] = why.get(b.id, {})
        (packet / "views.json").write_text(json.dumps(record, indent=1, ensure_ascii=False))
        line = {"building": b.id, "address": b.register.get("address"), "walls": len(b.walls),
                "seen": sum(w["seen"] for w in record["walls"]), "crops": len(record["crops"]),
                "unseen": [f"{u['wall'].split(':')[-1]} ({u['reason']})" for u in record["unseen"]],
                "views_s": round(time.time() - started, 1)}
        lines[b.id] = line
        target = root / "descriptions/building" / f"{b.id}.json"
        if a.command != "describe":
            print(json.dumps(line, ensure_ascii=False), flush=True)
        elif not record["crops"]:
            line["skipped"] = "no panorama sees any wall"
            print(json.dumps(line, ensure_ascii=False), flush=True)
        elif target.exists() and not a.force:
            line["description"] = str(target)
            line["skipped"] = "already described (--force reads again)"
            print(json.dumps(line, ensure_ascii=False), flush=True)
        else:
            jobs.append((b.id, record, packet))

    def describe(job):
        key, record, packet = job
        line = lines[key]
        try:
            reading, meta = reader.read(record, packet, effort=a.effort)
        except Exception as error:  # noqa: BLE001 - one failed building must not stop the others
            line["error"] = str(error)[:300]
        else:
            first = finish(record, reading, meta, packet, root)
            if first.get("dropped_reader") and not a.no_second_look:
                reading, meta, first = second_look(record, packet, root, reading, meta, first, a.effort)
            (packet / "reading.json").write_text(json.dumps({"reading": reading, "meta": meta}, indent=1,
                                                            ensure_ascii=False))
            line.update(first)
        print(json.dumps(line, ensure_ascii=False), flush=True)

    if jobs:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as pool:
            list(pool.map(describe, jobs))
    summary = list(lines.values())
    read = [x for x in summary if x.get("cost_usd") is not None]
    if read:
        print(json.dumps({"described": len(read), "failed": sum("error" in x for x in summary),
                          "cost_usd": round(sum(x["cost_usd"] for x in read), 2),
                          "mean_read_s": round(sum(x["read_s"] for x in read) / len(read), 1)}), flush=True)
    (root / "last-run.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
