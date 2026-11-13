"""Observe buildings: views per wall, rectified crops and the vision reading into stored descriptions.

    python -m turku3d.observe views    (--building KEY... | --at E,N [--radius R] | --view ID...)
    python -m turku3d.observe describe (--building KEY... | --at E,N [--radius R] | --view ID...) [--effort low]
    python -m turku3d.observe assemble                descriptions again from the saved readings, no model call
    python -m turku3d.observe review [--building KEY...]   compare each wall with a drawing of what gets built
    python -m turku3d.observe review --render --building KEY...   compare the game's render with the photograph
    python -m turku3d.observe roof --building KEY...       ask again about the dormers and chimneys of read buildings
    python -m turku3d.observe unblock --building KEY... --near E,N   other views for walls no crop showed
    python -m turku3d.observe against [--out DIR]     the automatic tier against the stored readings, per attribute
    python -m turku3d.observe light --location NAME --view ID...   what stands in the judged photographs, by kind,
                                                      and the area profile (light.py); one cheap call per photograph

`describe` does what `unblock` does by itself: when the reader reports that no crop of a wall showed it (a quay
wall, a fence or trees in the way, which LoD2 does not hold), the wall gets crops from panoramas on other paths
and is read again from them, once.

Views can be prepared on another machine and read here: run `views` there (scripts/dev/on-host HOST --data
raw/laz/maanpinta --pull observe/buildings -- python -m turku3d.observe views --view ID... --min-share 0.01), then
`describe --prepared` with the same selection: a packet with its crops is taken as it is, and the panoramas stay
on that machine.

`views` writes the crops and views.json of each building under $TURKU3D_WORK/observe/buildings/<key>/ and reports
the walls no panorama sees. `describe` also reads them and writes the description to
$TURKU3D_WORK/observe/descriptions/building/<key>.json (the layout of a private descriptions root), and stores it
through turku3d.world.store (docs/design/world-pipeline.md, section 4.2). `assemble` rebuilds every description of
the work directory from its views.json and reading.json, for when only the assembly changed. `review` draws what
the generator builds on each read wall on the rulers of its crop (elevation.py), shows the reader photograph and
drawing side by side and stores the corrected description at tier `reviewed`.
`--view` takes reference views of qa/views/views.jsonl and selects every building visible in them.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

from turku3d import config

from . import check, elevation, lod2, reader
from .panoramas import REPO, Panoramas
from .prepare import blocked_walls, other_views, prepare, street_presence

VISIBLE_SHARE = 0.004  # of a view's pixels, for a building to count as visible in it
# Tiers by rule, from what the street sees of a building (prepare.street_presence):
READ_AREA_M2 = 40.0  # less facade than this in sight: not read, the building stays at the automatic tier
VIEW_SHARE = 0.02  # a building filling this much of an asked view is read however small its street front
# A second look is a whole second reading (0.5 to 1.3 USD notional): one or two lost openings do not pay for it.
SECOND_LOOK_FROM = 3
STORE = True  # --no-store: descriptions stay in the run's own root (comparisons against what is stored)
REVIEW_FRONTAGE_M = 8.0  # a street frontage at least this long is reviewed against its drawing after the reading


def store(doc: dict) -> str | None:
    """Store a description in the shared descriptions root; None when the store refuses or when somebody else
    stored a reviewed (hand-written) description. A reading replaces an automatic or converted one."""
    from turku3d.world import store as world_store

    log = world_store.description_path(doc["id"]).with_suffix(".log.jsonl")
    last = json.loads(log.read_text().splitlines()[-1]) if log.exists() else None
    if last and last["by"] != "w2-observe" and last["tier"] == "reviewed":
        print(f"observe: {doc['id']} not stored: {last['by']} stored it at tier {last['tier']}", file=sys.stderr)
        return None
    try:
        return str(world_store.write_description(doc, by="w2-observe", note=doc["provenance"][0].get("note", "")))
    except ValueError as error:  # a higher tier is stored already
        print(f"observe: {doc['id']} not stored: {error}", file=sys.stderr)
        return None


def finish(record: dict, reading: dict, meta: dict, packet: Path, root: Path) -> dict:
    """Assemble a reading into its description, check it, write and store it; the facts for the run's report."""
    from turkutex.streetview.height import (
        ground_height,  # the survey lidar; on sys.path through .panoramas
    )

    from turku3d.world.kinds.building import description as fmt

    doc = reader.assemble(record, reading, meta, ground=ground_height, tier=meta.get("tier", "observed"))
    problems = fmt.errors(doc)
    target = root / "descriptions/building" / f"{record['building']}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    (target if not problems else packet / "invalid-description.json").write_text(
        json.dumps(doc, indent=1, ensure_ascii=False))
    line = {"building": record["building"], "read_s": meta["seconds"], "cost_usd": meta["cost_usd"],
            "invalid": problems[:5] or None, "description": str(target) if not problems else None,
            "stored": store(doc) if not problems and STORE else None}
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
    """Ask the reader about the walls with parts the generator dropped; keep whichever reading loses fewer."""
    from turkutex.streetview.height import ground_height

    report = json.loads((packet / "build-report.json").read_text())
    asked = {wall: [i for i in items if i["reader"]] for wall, items in report["walls"].items()}
    text = reader.feedback(record, reading, {w: i for w, i in asked.items() if i}, ground_height)
    try:
        again, meta_again = reader.read(record, packet, effort=effort, feedback_text=text, first=reading)
    except Exception as error:  # noqa: BLE001 - the first reading stands
        return reading, meta, {**first, "second_look": f"failed: {error}"[:120]}
    (packet / "reading-first.json").write_text(json.dumps({"reading": reading, "meta": meta}, indent=1,
                                                          ensure_ascii=False))
    total = {"seconds": round(meta["seconds"] + meta_again["seconds"], 1),
             "cost_usd": (meta["cost_usd"] or 0) + (meta_again["cost_usd"] or 0),
             **({"tier": meta["tier"]} if "tier" in meta else {})}  # a reviewed reading stays reviewed
    second = finish(record, again, {**meta_again, **total}, packet, root)
    note = f"{first['dropped_reader']} -> {second.get('dropped_reader')} parts the generator drops"
    if second.get("invalid") or second.get("dropped_reader", 1e9) >= first["dropped_reader"]:
        kept = finish(record, reading, {**meta, **total}, packet, root)  # the first reading stays stored
        return reading, {**meta, **total}, {**kept, "second_look": note + " (first kept)"}
    return again, {**meta_again, **total}, {**second, "second_look": note}


def unblock(scene, building, record: dict, reading: dict, meta: dict, packet: Path, effort: str, panos,
            again: bool = False):
    """Walls none of whose crops showed them (the reader's report: a quay wall, a fence, trees in the way) get
    crops from panoramas on other paths, once (`again`: once more), and the reader reads those walls from them.
    Returns (reading, meta, note); the record gains the new crops."""
    blocked = {wall: views for wall, views in blocked_walls(record, reading, REVIEW_HIDDEN).items()
               if again or wall not in record.get("blocked", {})}
    if not blocked:
        return reading, meta, None
    walls = ", ".join(sorted(w.split(":")[-1] for w in blocked))
    new = other_views(scene, building, panos, packet, record, blocked)
    (packet / "views.json").write_text(json.dumps(record, indent=1, ensure_ascii=False))
    if not new:
        return reading, meta, f"{walls}: hidden in every crop, and no panorama on another path sees them"
    hidden = {name for names in record["blocked"].values() for name in names}
    report = {v["file"]: v for v in reading.get("views", [])}
    shown = {**record, "crops": [c for c in record["crops"] if Path(c["file"]).name not in hidden],
             "contexts": [c for c in record["contexts"] if report.get(Path(c["file"]).name, {}).get("usable", True)]}
    try:
        again, meta_again = reader.read(shown, packet, effort=effort, feedback_text=reader.blocked_text(blocked, new),
                                        first=reading)
    except Exception as error:  # noqa: BLE001 - the first reading stands
        return reading, meta, f"{walls}: new crops, but the reading failed: {error}"[:200]
    (packet / "reading-blocked.json").write_text(json.dumps({"reading": reading, "meta": meta}, indent=1,
                                                            ensure_ascii=False))
    total = {**meta_again, "seconds": round(meta["seconds"] + meta_again["seconds"], 1),
             "cost_usd": (meta["cost_usd"] or 0) + (meta_again["cost_usd"] or 0),
             **({"tier": meta["tier"]} if "tier" in meta else {})}
    return again, total, (f"{walls}: hidden in every crop; read again from {len(new)} crops on other paths "
                          f"(+{meta_again['seconds']} s, +${meta_again['cost_usd'] or 0:.2f})")


REVIEW_MAY_DROP = 2  # parts a review may lose more than the first reading: the photographs outweigh the fit
REVIEW_HIDDEN = 0.7  # a crop hidden this much by trees or vehicles cannot confirm a wall


def review(packet: Path, root: Path, effort: str, force: bool = False) -> dict:
    """Render and compare for one read building: drawings of what gets built beside the crops, the reader's
    corrections, the result stored at tier reviewed when it is valid and loses hardly more parts than before.
    A review already made (reading-reviewed.json) is taken again without a model call unless forced."""
    from turkutex.streetview.height import ground_height

    from turku3d.world.kinds.building import description as fmt

    record = json.loads((packet / "views.json").read_text())
    first = packet / ("reading-observed.json" if (packet / "reading-observed.json").exists() else "reading.json")
    saved = json.loads(first.read_text())
    reading, meta = saved["reading"], saved["meta"]
    doc = reader.assemble(record, reading, meta, ground=ground_height)
    before = check.count(check.build_report(doc), reader_only=True)
    pairs = {}
    for wall in reading["description"].get("walls", []):
        crop = next((c for c in record["crops"] if Path(c["file"]).name == wall.get("measured_on")), None)
        if crop is None:
            continue
        drawing = packet / f"{Path(crop['file']).stem}-built.jpg"
        registration = reader.wall_registration(record, reading, wall["id"], ground_height)
        if elevation.draw_wall(doc, wall["id"], crop, registration, drawing):
            pairs[wall["id"]] = (Path(crop["file"]).name, drawing.name)
    line = {"building": record["building"], "compared_walls": len(pairs)}
    if not pairs:
        return {**line, "skipped": "no read wall lies on a shell wall of the generator"}
    (packet / "first-description.json").write_text(json.dumps(reading["description"], indent=1, ensure_ascii=False))
    made = packet / "reading-reviewed.json"
    if made.exists() and not force:
        again, meta_again = (json.loads(made.read_text())[k] for k in ("reading", "meta"))
    else:
        try:
            again, meta_again = reader.read(record, packet, effort=effort, feedback_text=reader.review_text(pairs))
        except Exception as error:  # noqa: BLE001 - the observed description stands
            return {**line, "error": str(error)[:300]}
        meta_again["tier"] = "reviewed"
        made.write_text(json.dumps({"reading": again, "meta": meta_again}, indent=1, ensure_ascii=False))
    candidate = reader.assemble(record, again, meta_again, ground=ground_height, tier="reviewed")
    problems = fmt.errors(candidate)
    after = None if problems else check.count(check.build_report(candidate), reader_only=True)
    line.update(read_s=meta_again["seconds"], cost_usd=meta_again["cost_usd"], invalid=problems[:5] or None,
                dropped_reader=f"{before} -> {after}", changed=str(again.get("notes", ""))[:400])
    if problems or after > before + REVIEW_MAY_DROP:
        return {**line, "kept": "the observed description (the review was invalid or lost more parts)"}
    seen = {v["file"]: v for v in again.get("views", [])}
    if not any(seen.get(crop, {}).get("usable", True) and seen.get(crop, {}).get("hidden_share", 0.0) < REVIEW_HIDDEN
               for crop, _ in pairs.values()):
        return {**line, "kept": "the observed description (no compared crop shows enough of its wall)"}
    (packet / "reading-observed.json").write_text(json.dumps(saved, indent=1, ensure_ascii=False))
    (packet / "reading.json").write_text(json.dumps({"reading": again, "meta": meta_again}, indent=1,
                                                    ensure_ascii=False))
    done = finish(record, again, meta_again, packet, root)
    return {**done, **line}


def ask(packet: Path, root: Path, effort: str, text: str, about: str) -> dict:
    """A question to the reader about a building already read (a reader.QUESTION text, answered as a correction).
    The corrected reading replaces the saved one, at the tier it had, when it is valid and loses hardly more
    parts; the reading before it is kept as reading-before-<about>.json."""
    from turkutex.streetview.height import ground_height

    from turku3d.world.kinds.building import description as fmt

    record = json.loads((packet / "views.json").read_text())
    saved = json.loads((packet / "reading.json").read_text())
    reading, meta = saved["reading"], saved["meta"]
    tier = meta.get("tier", "observed")
    before = check.count(check.build_report(reader.assemble(record, reading, meta, ground=ground_height, tier=tier)),
                         reader_only=True)
    again, meta_again = reader.read(record, packet, effort=effort, feedback_text=text, first=reading)
    total = {**meta_again, "seconds": round(meta["seconds"] + meta_again["seconds"], 1),
             "cost_usd": (meta["cost_usd"] or 0) + (meta_again["cost_usd"] or 0),
             **({"tier": meta["tier"]} if "tier" in meta else {})}
    problems = fmt.errors(reader.assemble(record, again, total, ground=ground_height, tier=tier))
    after = None if problems else check.count(check.build_report(
        reader.assemble(record, again, total, ground=ground_height, tier=tier)), reader_only=True)
    line = {"building": record["building"], "asked": about, "call_s": meta_again["seconds"],
            "call_cost_usd": meta_again["cost_usd"], "invalid": problems[:5] or None,
            "dropped_reader": f"{before} -> {after}", "changed": str(again.get("notes", ""))[:400]}
    if problems or after > before + REVIEW_MAY_DROP:
        return {**line, "kept": "the reading as it was (the answer was invalid or lost more parts)"}
    (packet / f"reading-before-{about}.json").write_text(json.dumps(saved, indent=1, ensure_ascii=False))
    (packet / "reading.json").write_text(json.dumps({"reading": again, "meta": total}, indent=1, ensure_ascii=False))
    done = finish(record, again, total, packet, root)
    roof = again["description"].get("roof", {})
    return {**done, **line, "dormers": [[d.get("type"), d.get("anchor", {}).get("wall"), d.get("anchor", {}).get("u_m"),
                                         d.get("w_m")] for d in roof.get("dormers", [])],
            "chimneys": len(roof.get("chimneys", []))}


def render(packet: Path, root: Path) -> Path:
    """The game's own render of a described building from the camera of its first context photograph, as
    render-1.jpg in the packet: w2-buildings' one-building workbench builds the description of this root and
    shoots it (the shot takes a Mac browser lease by itself and gives it back)."""
    import subprocess

    from PIL import Image

    from turku3d.world.kinds.building import workbench

    record = json.loads((packet / "views.json").read_text())
    key, context = record["building"], record["contexts"][0]
    view = packet / "context-1.view.json"
    view.write_text(json.dumps({"image": str(packet / Path(context["file"]).name), "width": context["size"][0],
                                "height": context["size"][1], "pose": context["pose"]}))
    workbench.build(root / "descriptions/building" / f"{key}.json", None, key, view)
    pair = packet / "render-pair.jpg"  # photograph | render
    done = subprocess.run(["node", str(REPO / "scripts/dev/building-workbench.mjs"), "shot", key, "--weather",
                           "overcast", "--out", str(pair)], capture_output=True, text=True, timeout=600,
                          stdin=subprocess.DEVNULL, check=False)
    if done.returncode or not pair.exists():
        raise RuntimeError(f"workbench shot: {(done.stderr or done.stdout)[-300:]}")
    image = Image.open(pair)
    image.crop((image.width // 2, 0, image.width, image.height)).save(packet / "render-1.jpg", quality=90)
    return packet / "render-1.jpg"


PREPARE_TIMEOUT_S = 900  # the views of one building; panorama downloads have no timeout of their own


def within(seconds: float, work):
    """work() in a thread of its own; TimeoutError when it does not come back in time. The thread is a daemon, so
    a download that never returns cannot hold the run (seen on the Air: one building stalled a set for 16 min)."""
    import threading

    box: dict = {}

    def run():
        try:
            box["value"] = work()
        except BaseException as error:  # noqa: BLE001 - raised again in the caller
            box["error"] = error

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise TimeoutError(f"no result within {seconds} s")
    if "error" in box:
        raise box["error"]
    return box["value"]


def visible_in_view(view: dict, scene: lod2.Scene, grid=(96, 72), reach: float = 250.0,
                    least: float = VISIBLE_SHARE) -> dict:
    """{building key: share of the view's pixels} from rays against the LoD2 scene."""
    from qalib.camera import pixel_rays

    pose = view["pose"]
    rays = pixel_rays(pose, *grid).reshape(-1, 3)
    cam = np.array([pose["E"], pose["N"], pose["H"]])
    hit, who = scene.cast(cam, cam + rays * reach, stop_short=0.0)
    share = {}
    for k in who[np.isfinite(hit)]:
        share[scene.ids[k]] = share.get(scene.ids[k], 0) + 1 / len(rays)
    return {k: round(v, 4) for k, v in sorted(share.items(), key=lambda kv: -kv[1]) if v >= least}


def select(args) -> tuple[lod2.Scene, list, dict]:
    """The scene, the buildings asked for and why each was selected."""
    why: dict = {}
    if args.view:
        rows = [json.loads(line) for line in args.views_file.read_text().splitlines() if line.strip()]
        views = [r for r in rows if r["id"] in set(args.view)]
        missing = set(args.view) - {r["id"] for r in views}
        if missing:
            sys.exit(f"unknown views: {', '.join(sorted(missing))}")
        e = [v["pose"]["E"] for v in views]
        n = [v["pose"]["N"] for v in views]
        scene = lod2.Scene((min(e) - 400, min(n) - 400, max(e) + 400, max(n) + 400))
        if not scene.buildings:
            sys.exit(f"no LoD2 buildings around {', '.join(args.view)}")
        for v in views:
            for key, share in visible_in_view(v, scene, least=args.min_share).items():
                why.setdefault(key, {})[v["id"]] = share
        return scene, [scene.buildings[k] for k in why], why
    if args.at:
        e, n = (float(x) for x in args.at.split(","))
        scene = lod2.scene_around(e, n, args.radius + 250)
        found = scene.within(e, n, args.radius) if args.radius > 0 else [scene.nearest(e, n)]
        return scene, [b for b in found if b is not None], why
    sys.exit("give --at E,N, --view ID or --building KEY with --near E,N (a key alone does not say where it is)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m turku3d.observe", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["views", "describe", "assemble", "review", "roof", "unblock", "against", "light", "colour"])
    ap.add_argument("--building", nargs="*", help="entity keys (with --near)")
    ap.add_argument("--near", help="E,N near the buildings named by --building")
    ap.add_argument("--at", help="E,N (EPSG:3877): the nearest building, or all within --radius")
    ap.add_argument("--radius", type=float, default=0.0)
    ap.add_argument("--check", action="store_true", help="colour: the sampler against the read colours")
    ap.add_argument("--apply", type=Path, help="colour: store the lines of a run made elsewhere with --no-store")
    ap.add_argument("--sheet", type=Path, help="colour: write a contact sheet of the named buildings' packets "
                                               "(house, sampled crops, swatches) and measure nothing new")
    ap.add_argument("--title", default="", help="colour --sheet: the sheet's heading")
    ap.add_argument("--light", action="store_true",
                    help="colour: measure the light (overcast 0..1) of every panorama the packets use")
    ap.add_argument("--scatter", action="store_true",
                    help="colour: how far each part's colour scatters between panoramas of one wall, overcast against "
                         "sunny (packets under --out prepared with --layers; needs --parts TAG and --light done)")
    ap.add_argument("--layers", type=int, default=0, metavar="N",
                    help="views: every time layer of the panorama spots is a candidate; a wall gets its best view of "
                         "each visit, up to N (for ranking by weather and for scatter between visits)")
    ap.add_argument("--per-visit", type=int, default=1, metavar="K",
                    help="views --layers: K views from different panoramas of each visit (pairs inside one visit)")
    ap.add_argument("--parts", metavar="TAG", help="colour --sheet: parts from the label maps of colour_segment.py "
                                                   "written with this tag (wall zones, trim, frames, plinth, roof, door)")
    ap.add_argument("--batch", action="store_true",
                    help="colour: measure wall colours unattended and resumably in 250 m cells under --out (needs "
                         "colour_segment.py --watch on the same root); area by --read-buildings, --building with "
                         "--near, --at E,N --radius R or --bounds; cells nearest to --first points first")
    ap.add_argument("--bounds", help="colour --batch: E0,N0,E1,N1")
    ap.add_argument("--first", nargs="*", default=[], metavar="E,N",
                    help="colour --batch: points whose cells are done first (built locations, the centre)")
    ap.add_argument("--read-buildings", action="store_true",
                    help="colour --batch: the buildings a vision reading described (to check against)")
    ap.add_argument("--cells", type=int, default=None, help="colour --batch: stop after this many cells")
    ap.add_argument("--progress", action="store_true", help="colour: counts and throughput of the batch under --out")
    ap.add_argument("--apply-batch", action="store_true",
                    help="colour: the batch results under --out as measured.wall lines (--lines FILE); stored for "
                         "buildings nobody read unless --no-store")
    ap.add_argument("--lines", type=Path, help="colour --apply-batch: where the measured lines go")
    ap.add_argument("--paint", default="white", choices=["white", "pixel"],
                    help="colour --apply-batch: paint colour by the visit's white point, or the pixel colour")
    ap.add_argument("--white-l", type=float, default=None, help="colour --apply-batch: lightness white paint is brought to")
    ap.add_argument("--measured", type=Path,
                    help="against: measured lines (colour --apply-batch --lines) given to the automatic tier")
    ap.add_argument("--location", help="light: the name the views are stored under (location/NAME)")
    ap.add_argument("--view", nargs="*", help="reference view ids: every building visible in them")
    ap.add_argument("--views-file", type=Path, default=REPO / "qa/views/views.jsonl",
                    help="the view set --view names come from (one JSON view per line)")
    ap.add_argument("--min-share", type=float, default=VISIBLE_SHARE,
                    help="with --view: the share of a view's pixels a building must fill to be selected")
    ap.add_argument("--prepared", action="store_true",
                    help="take a building's views from its packet when views.json and its crops are there "
                         "(prepared by `views`, possibly on another machine)")
    ap.add_argument("--effort", default=reader.EFFORT)
    ap.add_argument("--out", type=Path, default=None, help="root (default $TURKU3D_WORK/observe)")
    ap.add_argument("--jobs", type=int, default=4, help="readings in parallel")
    ap.add_argument("--prepare-jobs", type=int, default=int(os.environ.get("TURKU3D_JOBS", "3")),
                    help="buildings whose views are prepared in parallel")
    ap.add_argument("--force", action="store_true", help="read again even when a description exists")
    ap.add_argument("--no-review", action="store_true", help="stop at tier observed")
    ap.add_argument("--render", action="store_true",
                    help="review: compare the game's own render with the context photograph (roof, ground storey, "
                         "relief) instead of the wall drawings")
    ap.add_argument("--no-store", action="store_true", help="do not write to the shared description store")
    ap.add_argument("--budget", type=float, default=None,
                    help="notional USD: no reading starts once this much is spent (most visible buildings first)")
    ap.add_argument("--no-second-look", action="store_true",
                    help="keep the first reading even when the generator drops parts of it")
    a = ap.parse_args(argv)
    global STORE
    STORE = not a.no_store
    root = a.out or config.work_root() / "observe"
    if a.command == "against":  # no model call: the automatic rules measured against every stored reading
        from . import against

        measured = None
        if a.measured:
            measured = {line["building"]: line["measured"] for line in map(json.loads, a.measured.read_text().splitlines())
                        if line.get("measured")}
        print(json.dumps(against.run(a.out, measured=measured), ensure_ascii=False), flush=True)
        return 0
    if a.command == "colour" and (a.batch or a.progress or a.apply_batch):
        from . import colour_batch, colour_parts

        if a.progress:
            print(json.dumps(colour_batch.progress(root)), flush=True)
            return 0
        if a.apply_batch:
            lines = colour_batch.measured_lines(root, mode=a.paint, white_l=a.white_l or colour_parts.WHITE_L)
            out = a.lines or root / "batch/measured.jsonl"
            out.write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines))
            report = {"lines": len(lines), "with_wall": sum(bool(line["measured"]) for line in lines), "file": str(out)}
            if STORE:
                report.update(colour_batch.store(lines))
            print(json.dumps(report), flush=True)
            return 0
        firsts = [tuple(float(v) for v in p.split(",")) for p in a.first]
        if a.read_buildings or a.building:
            from turku3d.world.store import data_root

            if a.read_buildings:
                points = colour_batch.read_positions(data_root() / "descriptions")
            else:
                e, n = (float(v) for v in a.near.split(","))
                scene = lod2.scene_around(e, n, 400)
                points = {k: tuple(scene.buildings[k].centre[:2]) for k in a.building if k in scene.buildings}
            by_cell = colour_batch.cells_of(points)
            order = sorted(by_cell, key=lambda c: min(((c[0] - f[0]) ** 2 + (c[1] - f[1]) ** 2 for f in firsts), default=0.0))
            work = {cell: by_cell[cell] for cell in order}
        else:
            if a.bounds:
                bounds = tuple(float(v) for v in a.bounds.split(","))
            elif a.at:
                e, n = (float(v) for v in a.at.split(","))
                bounds = (e - a.radius, n - a.radius, e + a.radius, n + a.radius)
            else:
                sys.exit("colour --batch: give --read-buildings, --building with --near, --at E,N --radius R or --bounds")
            work = {cell: None for cell in colour_batch.cells_first(bounds, firsts or ([tuple(float(v) for v in a.at.split(","))] if a.at else []))}
        print(json.dumps(colour_batch.run(root, work, tag=a.parts or colour_parts.TAG, jobs=a.prepare_jobs, limit=a.cells)), flush=True)
        return 0
    if a.command == "colour" and a.light:
        from . import colour_light

        light = colour_light.of_root(root)
        print(json.dumps({"panoramas": len(light), "overcast_0.7_or_more": sum(v["overcast"] >= 0.7 for v in light.values()),
                          "file": str(root / "panorama-light.json")}), flush=True)
        return 0
    if a.command == "colour" and a.scatter:
        from . import colour_parts

        light = json.loads((root / "panorama-light.json").read_text())
        results = colour_parts.measured_packets(root, a.parts or colour_parts.TAG, {k: v["overcast"] for k, v in light.items()})
        report = [colour_parts.scatter(results, part) for part in ("wall", "plinth", "roof")]
        (root / "scatter.json").write_text(json.dumps(report, indent=1))
        for line in report:
            print(json.dumps(line), flush=True)
        return 0
    if a.command == "colour" and (a.check or a.apply):
        from turku3d.world.store import data_root

        from . import against, colour

        if a.apply:
            print(json.dumps(colour.apply([json.loads(line) for line in a.apply.read_text().splitlines()
                                           if line.startswith("{")])), flush=True)
            return 0
        read = against.read_buildings(data_root() / "descriptions")
        result = colour.check(root, lambda key: against.read_wall_colour(read[key]) if key in read else None)
        (root / "against").mkdir(parents=True, exist_ok=True)
        (root / "against/colour-check.json").write_text(json.dumps(result, indent=1))
        print(json.dumps({k: v for k, v in result.items() if k != "rows"}), flush=True)
        return 0
    if a.command == "light":
        from . import light

        rows = [json.loads(line) for line in a.views_file.read_text().splitlines() if line.strip()]
        views = [r for r in rows if r["id"] in set(a.view or [])]
        if not a.location or len(views) != len(set(a.view or [])) or not views:
            sys.exit("light: give --location NAME and --view IDs that exist in the views file")
        print(json.dumps(light.run(a.location, views, root, effort=a.effort, write=STORE), ensure_ascii=False), flush=True)
        return 0
    if a.command == "review":
        from concurrent.futures import ThreadPoolExecutor

        packets = [p for p in sorted((root / "buildings").iterdir()) if (p / "reading.json").exists()
                   and (not a.building or p.name in a.building)]
        shots = {}
        if a.render:  # one browser: the renders one after another, then the questions in parallel
            if not a.building:
                sys.exit("review --render: name the buildings with --building")
            for packet in packets:
                try:
                    shots[packet.name] = render(packet, root).name
                except Exception as error:  # noqa: BLE001 - reported on the building's line
                    shots[packet.name] = error

        def one(packet):
            try:
                if a.render and isinstance(shots[packet.name], Exception):
                    raise shots[packet.name]
                if a.render:
                    saved = json.loads((packet / "reading.json").read_text())
                    line = ask(packet, root, a.effort, reader.render_text(saved["reading"], shots[packet.name],
                                                                          "context-1.jpg"), "render")
                    line["cost_usd"] = line.get("call_cost_usd")
                else:
                    line = review(packet, root, a.effort, a.force)
            except Exception as error:  # noqa: BLE001 - one failed building must not stop the others
                line = {"building": packet.name, "error": f"{type(error).__name__}: {error}"[:300]}
            print(json.dumps(line, ensure_ascii=False), flush=True)
            return line

        with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as pool:
            done = list(pool.map(one, packets))
        print(json.dumps({"reviewed": sum(bool(x.get("stored")) for x in done), "of": len(done),
                          "cost_usd": round(sum(x.get("cost_usd") or 0 for x in done), 2)}), flush=True)
        return 0
    if a.command == "roof":
        cost = 0.0
        for key in a.building or sys.exit("roof: name the buildings with --building"):
            packet = root / "buildings" / key
            try:
                saved = json.loads((packet / "reading.json").read_text())
                line = ask(packet, root, a.effort, reader.roof_text(saved["reading"]), "roof")
            except Exception as error:  # noqa: BLE001 - one failed building must not stop the others
                line = {"building": key, "error": f"{type(error).__name__}: {error}"[:300]}
            cost += line.get("call_cost_usd") or 0
            print(json.dumps({k: v for k, v in line.items() if k != "description"}, ensure_ascii=False), flush=True)
        print(json.dumps({"asked": len(a.building), "cost_usd": round(cost, 2)}), flush=True)
        return 0
    if a.command == "assemble":
        for packet in sorted((root / "buildings").iterdir()):
            if a.building and packet.name not in a.building:
                continue
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
    import threading
    from concurrent.futures import ThreadPoolExecutor

    local = threading.local()

    def panoramas():  # this thread's own panorama reader
        if not hasattr(local, "panos"):
            local.panos = Panoramas(root / "panoramas")
        return local.panos

    if a.command == "colour" and a.sheet:
        from . import colour_sheet

        packets = [root / "buildings" / b.id for b in buildings]
        light = root / "panorama-light.json"
        overcast = {k: v["overcast"] for k, v in json.loads(light.read_text()).items()} if light.exists() else None
        print(json.dumps(colour_sheet.parts_sheet(packets, a.sheet, a.title, a.parts, overcast) if a.parts
                         else colour_sheet.sheet(packets, a.sheet, a.title)), flush=True)
        return 0
    if a.command == "colour":  # wall colours from strips, no reader
        from . import colour

        lines = []
        for b in buildings:
            try:
                line = within(PREPARE_TIMEOUT_S, lambda b=b: colour.measure(scene, b, panoramas(), root))
            except Exception as error:  # noqa: BLE001 - one building must not stop the others
                line = {"building": b.id, "error": f"{type(error).__name__}: {error}"[:200]}
            lines.append(line)
            print(json.dumps(line, ensure_ascii=False), flush=True)
        if STORE:
            print(json.dumps(colour.apply(lines)), flush=True)
        return 0
    if a.command == "unblock":  # read buildings: other views for walls whose crops did not show them
        for b in buildings:
            packet = root / "buildings" / b.id
            record = json.loads((packet / "views.json").read_text())
            saved = json.loads((packet / "reading.json").read_text())
            reading, meta, note = unblock(scene, b, record, saved["reading"], saved["meta"], packet, a.effort,
                                          panoramas(), again=a.force)
            line = {"building": b.id, "other_views": note}
            if reading is not saved["reading"]:
                (packet / "reading.json").write_text(json.dumps({"reading": reading, "meta": meta}, indent=1,
                                                                ensure_ascii=False))
                line.update(finish(record, reading, meta, packet, root))
            print(json.dumps({k: v for k, v in line.items() if k != "description"}, ensure_ascii=False), flush=True)
        return 0

    def prepared(b):  # views and crops of one building, with this thread's own panorama reader
        started = time.time()
        packet = root / "buildings" / b.id
        described = (a.command == "describe" and not a.force and (packet / "views.json").exists()
                     and (root / "descriptions/building" / f"{b.id}.json").exists())
        if described:  # it will not be read again: its views are not prepared again either
            record = json.loads((packet / "views.json").read_text())
            record.setdefault("street", street_presence(b, record["crops"]))  # packets older than that measure
            return record, 0.0
        if a.prepared and (packet / "views.json").exists():
            record = json.loads((packet / "views.json").read_text())
            if all((packet / Path(c["file"]).name).exists() for c in record["crops"] + record["contexts"]):
                record["selected_by"] = why.get(b.id, record.get("selected_by", {}))
                return record, 0.0
        panos = panoramas()
        try:
            more = {"per_wall": a.layers, "layers": True, "limit": 5 * a.layers * a.per_visit,
                    "per_visit": a.per_visit} if a.layers else {}
            record = within(PREPARE_TIMEOUT_S, lambda: prepare(scene, b, panos, packet, **more))
        except TimeoutError:  # the stalled thread may hold this reader's locks: the next building gets a new one
            del local.panos
            print(f"observe: {b.id}: view preparation stalled for {PREPARE_TIMEOUT_S} s, building left out",
                  file=sys.stderr, flush=True)
            return None, time.time() - started
        record["selected_by"] = why.get(b.id, {})
        packet.mkdir(parents=True, exist_ok=True)  # a building no panorama sees has no crops, but keeps its record
        (packet / "views.json").write_text(json.dumps(record, indent=1, ensure_ascii=False))
        return record, time.time() - started

    lines, jobs = {}, []
    with ThreadPoolExecutor(max_workers=max(1, a.prepare_jobs)) as pool:
        records = list(pool.map(prepared, buildings))
    for b, (record, seconds) in zip(buildings, records, strict=True):
        if record is None:
            lines[b.id] = {"building": b.id, "address": b.register.get("address"),
                           "error": "view preparation stalled (a download that never came back)"}
            print(json.dumps(lines[b.id], ensure_ascii=False), flush=True)
            continue
        packet = root / "buildings" / b.id
        line = {"building": b.id, "address": b.register.get("address"), "walls": len(b.walls),
                "seen": sum(w["seen"] for w in record["walls"]), "crops": len(record["crops"]),
                "unseen": [f"{u['wall'].split(':')[-1]} ({u['reason']})" for u in record["unseen"]],
                "street": record["street"], "views_s": round(seconds, 1)}
        lines[b.id] = line
        target = root / "descriptions/building" / f"{b.id}.json"
        if a.command != "describe":
            print(json.dumps(line, ensure_ascii=False), flush=True)
        elif record["street"]["area_m2"] < READ_AREA_M2 and max(why.get(b.id, {}).values(), default=0) < VIEW_SHARE:
            line["skipped"] = "the street sees too little of it: it stays at the automatic tier"
            print(json.dumps(line, ensure_ascii=False), flush=True)
        elif target.exists() and not a.force:
            line["description"] = str(target)
            line["skipped"] = "already described (--force reads again)"
            print(json.dumps(line, ensure_ascii=False), flush=True)
        else:
            jobs.append((b.id, record, packet))
    # The buildings that fill most of the asked views, then those the street sees most of, come first, so a
    # budget ends on the least salient.
    jobs.sort(key=lambda job: (-sum(why.get(job[0], {}).values()), -job[1]["street"]["area_m2"]))
    spent = [0.0]

    def describe(job):
        key, record, packet = job
        line = lines[key]
        if a.budget is not None and spent[0] >= a.budget:
            line["skipped"] = "the budget was spent on buildings the street sees more of"
            print(json.dumps(line, ensure_ascii=False), flush=True)
            return
        try:
            reading, meta = reader.read(record, packet, effort=a.effort)
        except Exception as error:  # noqa: BLE001 - one failed building must not stop the others
            line["error"] = str(error)[:300]
        else:
            if not a.no_second_look:
                try:
                    reading, meta, note = unblock(scene, scene.buildings[key], record, reading, meta, packet,
                                                  a.effort, panoramas())
                except Exception as error:  # noqa: BLE001 - the first reading stands
                    note = f"other views failed: {type(error).__name__}: {error}"[:200]
                if note:
                    line["other_views"] = note
            first = finish(record, reading, meta, packet, root)
            if (first.get("dropped_reader") or 0) >= SECOND_LOOK_FROM and not a.no_second_look:
                reading, meta, first = second_look(record, packet, root, reading, meta, first, a.effort)
            (packet / "reading.json").write_text(json.dumps({"reading": reading, "meta": meta}, indent=1,
                                                            ensure_ascii=False))
            for stale in ("reading-observed.json", "reading-reviewed.json"):  # of an earlier reading's review
                (packet / stale).unlink(missing_ok=True)
            line.update(first)
            if record["street"]["frontage_m"] >= REVIEW_FRONTAGE_M and not a.no_review and not first.get("invalid"):
                try:
                    reviewed = review(packet, root, a.effort, force=True)
                except Exception as error:  # noqa: BLE001 - the observed description stands
                    reviewed = {"error": f"{type(error).__name__}: {error}"[:200]}
                line["review"] = {k: reviewed.get(k) for k in ("compared_walls", "dropped_reader", "kept", "error",
                                                              "changed") if reviewed.get(k) is not None}
                line["cost_usd"] = (line.get("cost_usd") or 0) + (reviewed.get("cost_usd") or 0)
                line["read_s"] = round((line.get("read_s") or 0) + (reviewed.get("read_s") or 0), 1)
                line["tier"] = "reviewed" if reviewed.get("stored") else "observed"
            spent[0] += line.get("cost_usd") or 0
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
