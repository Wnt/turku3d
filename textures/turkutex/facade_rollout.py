"""Generated-facade rollout: a ranked wall queue, its batches, accounting and publication.

queue    rank every street-facing wall of the approved inventory without a generated map
batch    take the next walls of the queue into a run directory and record the starting quota
summary  write the batch accounting JSON (calls, seconds, tokens, quota, verdicts) and the ledger
contact  reference / generated / verdict contact sheet for the manual look
publish  copy the finally accepted maps into the approval root under the export lock

Final acceptance needs the automated audit (`facade_generated_qa`), whose reviewer answer it
includes, and a manual verdict in `<run>/manual.json` ({facade_id: {"ok": bool, "note": str}}).
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

from turku3d import config

from .building_jobs import latest, reference_status
from .facade_batch import asset_path, report, write_json
from .jobs import _views
from .post.pipeline import sha
from .streetview.facades import load_facades

RING_M = 50.0
# Layout-guided walls beyond this aspect missed the 3% aspect check in 6 of 6 attempts
# (docs/reports/w1-09g-generated-office.md), so they get no image calls.
MAX_ASPECT = 3.5
CORE_MIN_WIDTH = 4.0


def generated_ids(assembly: Path) -> set[str]:
    """Walls whose published map is a generated, audited image."""
    found = set()
    for path in (assembly / "facades").glob("*/*.json"):
        if path.name == "style.json":
            continue
        meta = json.loads(path.read_text())
        if meta.get("source_mode") == "generate" or "generated_qa" in meta:
            found.add(f"{meta['building_id']}_{meta['facade_index']}")
    return found


def spawn_en(tiles_manifest: Path) -> tuple[float, float]:
    x, y, z = json.loads(tiles_manifest.read_text())["spawn"]["position"]
    e, n, _ = config.local_to_en(x, y, z)
    return e, n


def bbox_distance(point, bbox) -> float:
    dx = max(bbox.emin - point[0], 0.0, point[0] - bbox.emax)
    dy = max(bbox.nmin - point[1], 0.0, point[1] - bbox.nmax)
    return math.hypot(dx, dy)


def build_queue(inventory: Path, assembly: Path, spawn: tuple[float, float]) -> dict:
    core = config.OFFICE_BLOCK.core.to_en()
    done = generated_ids(assembly)
    walls = []
    for f in load_facades(inventory):
        if not f.props.get("street_facing") or f.id in done:
            continue
        outside = bbox_distance(f.centre, core)
        to_spawn = math.dist(f.centre, spawn)
        if outside == 0:
            group = "core_ge4" if f.length >= CORE_MIN_WIDTH else "core_rest"
            order = (0 if group == "core_ge4" else 1, -f.length / max(to_spawn, 1.0))
        else:
            ring = 1 + int(outside // RING_M)
            group = f"ring_{ring}"
            order = (1 + ring, -f.length / max(to_spawn, 1.0))
        aspect = f.length / f.height_m
        walls.append(
            (
                order,
                f.id,
                {
                    "facade_id": f.id,
                    "building_id": f.props["building_id"],
                    "facade_index": f.props["facade_index"],
                    "group": group,
                    "width_m": round(f.length, 3),
                    "height_m": round(f.height_m, 3),
                    "aspect": round(aspect, 3),
                    "distance_core_bbox_m": round(outside, 1),
                    "distance_spawn_m": round(to_spawn, 1),
                    "deferred": (
                        f"extreme aspect {aspect:.2f}:1 (layout guide misses the 3% aspect check)"
                        if max(aspect, 1 / aspect) > MAX_ASPECT
                        else None
                    ),
                },
            )
        )
    walls.sort(key=lambda item: item[:2])
    return {
        "inventory_sha256": sha(inventory),
        "core_bbox_en": core.as_tuple(),
        "spawn_en": [round(v, 2) for v in spawn],
        "ring_m": RING_M,
        "max_aspect": MAX_ASPECT,
        "excluded_generated": sorted(done),
        "walls": [{"rank": i + 1, **w} for i, (_, _, w) in enumerate(walls)],
        "batches": {},
    }


def take_batch(queue: dict, number: int, size: int, walls: list[str] | None = None) -> list[str]:
    """The batch's wall IDs: recorded ones, else the named walls, else the next undeferred walls
    of the queue."""
    key = f"{number:02d}"
    if key not in queue["batches"] and walls:
        known = {w["facade_id"] for w in queue["walls"]} | set(queue["excluded_generated"])
        unknown = [w for w in walls if w not in known]
        if unknown:
            raise ValueError(f"walls are not street-facing walls of the queue: {unknown}")
        queue["batches"][key] = list(walls)
    if key not in queue["batches"]:
        used = {fid for ids in queue["batches"].values() for fid in ids}
        queue["batches"][key] = [
            w["facade_id"]
            for w in queue["walls"]
            if not w["deferred"] and w["facade_id"] not in used
        ][:size]
    return queue["batches"][key]


def subset(inventory: Path, ids: list[str], output: Path):
    data = json.loads(inventory.read_text())
    wanted = {i: n for n, i in enumerate(ids)}
    features = [
        f
        for f in data["features"]
        if f"{f['properties']['building_id']}_{f['properties']['facade_index']}" in wanted
    ]
    features.sort(
        key=lambda f: wanted[f"{f['properties']['building_id']}_{f['properties']['facade_index']}"]
    )
    write_json(output, {**data, "features": features})


def quota() -> int | None:
    from .codex.usage import used_percent

    return used_percent()


def attempts(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def tokens(records: list[dict]) -> dict:
    total: dict[str, int] = {}
    for record in records:
        for name, value in (record.get("tokens") or {}).items():
            total[name] = total.get(name, 0) + value
    return total


def seconds(records: list[dict]) -> dict:
    values = [r["seconds"] for r in records]
    if not values:
        return {"mean": None, "min": None, "max": None, "all": []}
    return {
        "mean": round(sum(values) / len(values), 1),
        "min": min(values),
        "max": max(values),
        "all": values,
    }


def verdicts(run: Path) -> dict[str, dict]:
    """Final verdict per wall: automated audit plus manual look."""
    audit = (
        json.loads((run / "acceptance.json").read_text())
        if (run / "acceptance.json").exists()
        else {"rows": []}
    )
    manual = json.loads((run / "manual.json").read_text()) if (run / "manual.json").exists() else {}
    rows = {}
    for row in audit["rows"]:
        fid = row["facade_id"]
        look = manual.get(fid)
        problems = [f"automated QA: {p}" for p in row["problems"]]
        if look is None:
            problems.append("manual look missing")
        elif not look["ok"]:
            problems.append(f"manual look: {look['note']}")
        rows[fid] = {
            "accepted": not problems,
            "problems": problems,
            "manual": look,
        }
    return rows


def summarize(run: Path, queue: dict, number: int) -> dict:
    state = json.loads((run / "state.json").read_text())
    facades = load_facades(run / "facades.geojson")
    generate = attempts(run / "run_generate/attempts.jsonl")
    review = attempts(run / "run_visualqa/attempts.jsonl")
    grid_calls = attempts(run / "run_refgrid/attempts.jsonl")
    final = verdicts(run)
    answers = latest(run / "run_visualqa/out.jsonl")
    after = quota()
    refs_root = Path(state["refs"])
    grids = latest(run / "run_refgrid/out.jsonl")
    per_wall, skipped, rejected, sources, reasons = [], {}, {}, {}, {}
    fallback_walls = []
    for f in facades:
        views, grid, reason = reference_status(f, refs_root, grids)
        refs_summary = (
            json.loads((refs_root / f.id / "refs.json").read_text())
            if (refs_root / f.id / "refs.json").exists()
            else {}
        )
        if refs_summary.get("fallback", {}).get("found"):
            fallback_walls.append(f.id)
        calls = [r for r in generate if r["meta"]["facade_id"] == f.id]
        if reason:
            skipped[f.id] = reason
            key = "skipped: " + reason.split(":")[0]
            reasons[key] = reasons.get(key, 0) + 1
        else:
            sources[f.id] = views[0]["source"]
        verdict = final.get(f.id)
        if not reason and (verdict is None or not verdict["accepted"]):
            problems = verdict["problems"] if verdict else ["no generated image"]
            rejected[f.id] = "; ".join(problems)
            for problem in problems:
                key = problem if problem.startswith("automated") else problem.split(":")[0]
                reasons[key] = reasons.get(key, 0) + 1
        per_wall.append(
            {
                "facade_id": f.id,
                "width_m": round(f.length, 3),
                "height_m": round(f.height_m, 3),
                "reference": None
                if reason
                else {
                    "source": views[0]["source"],
                    "view_id": views[0]["view_id"],
                    "screen": views[0].get("screen"),
                },
                "skipped": reason,
                "reference_grid": grid,
                "accepted": bool(verdict and verdict["accepted"]),
                "generate_calls": len(calls),
                "image_calls": sum(len(r.get("imagegen_calls", [])) for r in calls),
                "automated_problems": verdict["problems"] if verdict else [],
                "reviewer_answer": answers.get(f"{f.id}.visualqa", {}).get("answer"),
                "my_review": verdict["manual"] if verdict else None,
            }
        )
    accepted = [w["facade_id"] for w in per_wall if w["accepted"]]
    before = state["quota_before_percent"]
    points = None if after is None or before is None else after - before
    readable = len(facades) - len(skipped)
    source_counts: dict[str, int] = {}
    for source in sources.values():
        source_counts[source] = source_counts.get(source, 0) + 1
    end = time.time()
    return {
        "batch": number,
        "model": state["model"],
        "effort": state["effort"],
        "reviewer": "gpt-6-astra/low (routing kind generated_visual_qa)",
        "start": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(state["start_epoch"])),
        "end": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(end)),
        "start_epoch": state["start_epoch"],
        "end_epoch": end,
        "wall_clock_seconds": round(end - state["start_epoch"], 1),
        "walls_attempted": len(facades),
        "walls_readable": readable,
        "walls_skipped": skipped,
        "walls_accepted": len(accepted),
        "acceptance_rate_readable": round(len(accepted) / readable, 3) if readable else None,
        "accepted": accepted,
        "rejected": rejected,
        "rejection_reasons": reasons,
        "reference_sources": source_counts,
        "reference_fallback_walls": fallback_walls,
        "generate_calls": len(generate),
        "image_calls": sum(len(r.get("imagegen_calls", [])) for r in generate),
        "images_returned": sum(len(r.get("images", [])) for r in generate),
        "qa_calls": len(review),
        "reference_grid_calls": len(grid_calls),
        "tokens": {
            "generate": tokens(generate),
            "visual_qa": tokens(review),
            "reference_grid": tokens(grid_calls),
        },
        "seconds_per_generate_call": seconds(generate),
        "seconds_per_qa_call": seconds(review),
        "quota_before_percent": before,
        "quota_after_percent": after,
        "quota_points": points,
        "quota_points_per_accepted_wall": (
            round(points / len(accepted), 3) if points is not None and accepted else None
        ),
        "quota_note": "Integer weekly usedPercent from codex app-server, read before and after the batch.",
        "per_wall": per_wall,
        "run_dir": str(run),
        "queue_ranks": [
            w["rank"] for w in queue["walls"] if w["facade_id"] in {f.id for f in facades}
        ],
    }


def ledger(logs: Path) -> str:
    rows = [json.loads(p.read_text()) for p in sorted(logs.glob("batch-*.json"))]
    lines = [
        "# Generated facade rollout ledger",
        "",
        "| Batch | Walls | Readable | Accepted | Image calls | Reviewer calls | Quota before → after | "
        "Points/accepted | s/image call | Rejection reasons |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | --- |",
    ]
    for r in rows:
        reasons = r["rejection_reasons"]
        lines.append(
            f"| {r['batch']} | {r['walls_attempted']} | {r['walls_readable']} | {r['walls_accepted']} "
            f"| {r['image_calls']} | {r['qa_calls']} | {r['quota_before_percent']} → "
            f"{r['quota_after_percent']} | {r['quota_points_per_accepted_wall']} | "
            f"{r['seconds_per_generate_call']['mean']} | "
            + ", ".join(f"{k} ×{v}" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]))
            + " |"
        )
    total = sum(r["walls_accepted"] for r in rows)
    if rows:
        lines += [
            "",
            f"Accepted walls: {total}. Quota {rows[0]['quota_before_percent']} → "
            f"{rows[-1]['quota_after_percent']}.",
        ]
    return "\n".join(lines) + "\n"


def contact_sheet(run: Path, output: Path, title: str):
    from PIL import Image, ImageDraw

    final = verdicts(run)
    refs_root = Path(json.loads((run / "state.json").read_text())["refs"])
    grids = latest(run / "run_refgrid/out.jsonl")
    tiles = []
    for f in load_facades(run / "facades.geojson"):
        views, _, reason = reference_status(f, refs_root, grids)
        refs = refs_root / f.id
        if views:
            ref = refs / views[0]["files"]["rectified"]
        elif (refs / "refs.json").exists() and _views(refs):
            ref = refs / _views(refs)[0]["files"]["rectified"]
        else:
            ref = None
        generated = run / "generated" / f"{f.id}.png"
        verdict = final.get(f.id)
        if reason:
            text, colour = f"SKIPPED: {reason}", (120, 90, 0)
        elif verdict and verdict["accepted"]:
            text, colour = "ACCEPTED", (0, 110, 40)
        else:
            text = "REJECTED: " + ("; ".join(verdict["problems"]) if verdict else "no image")
            colour = (160, 20, 20)
        tiles.append((f, ref, generated if generated.exists() else None, text[:170], colour))
    width, thumb = 900, 180
    sheet = Image.new("RGB", (width, 40 + len(tiles) * (thumb + 40)), (245, 244, 240))
    draw = ImageDraw.Draw(sheet)
    draw.text((10, 10), title, fill=(0, 0, 0))
    draw.text((10, 24), "REFERENCE (rectified street photo)", fill=(60, 60, 60))
    draw.text((width // 2 + 10, 24), "GENERATED TEXTURE", fill=(60, 60, 60))
    y = 40
    for f, ref, generated, text, colour in tiles:
        draw.text((10, y + 4), f"{f.id}  {f.length:.1f} x {f.height_m:.1f} m  {text}", fill=colour)
        for x0, path in ((0, ref), (width // 2, generated)):
            if path is None:
                continue
            with Image.open(path) as image:
                image = image.convert("RGB")
                image.thumbnail((width // 2 - 20, thumb))
                sheet.paste(image, (x0 + (width // 2 - image.width) // 2, y + 20))
        y += thumb + 40
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, quality=72)


def publish(run: Path, inventory: Path, assembly: Path) -> dict:
    """Bind the finally accepted maps into the approval root (additive, inventory unchanged)."""
    from types import SimpleNamespace

    from .export import export_assets, export_lock

    accepted = [fid for fid, v in verdicts(run).items() if v["accepted"]]
    subset(run / "facades.geojson", accepted, run / "accepted.geojson")
    if not accepted:
        return {"published": []}
    refs_root = Path(json.loads((run / "state.json").read_text())["refs"])
    summary = report(run / "accepted.geojson", refs_root, run, run / "staging")
    incomplete = [r["facade_id"] for r in summary["rows"] if not r["complete"]]
    if incomplete:
        raise ValueError(f"accepted walls fail the staging report: {incomplete}")
    with export_lock(assembly):
        before = json.loads((assembly / "MANIFEST.json").read_text())
        result = export_assets(
            SimpleNamespace(
                facades=inventory,
                source_inventory=run / "accepted.geojson",
                source=run / "staging",
                qa=run / "qa.json",
                output=assembly,
                merge=True,
            )
        )
    for f in load_facades(run / "accepted.geojson"):
        target = asset_path(f, assembly).with_suffix(".json")
        if json.loads(target.read_text()).get("generated_qa") is None:
            raise ValueError(f"{f.id}: published sidecar lacks the generated audit")
    return {
        "published": accepted,
        "inventory_sha256": result["inventory_sha256"],
        "bound_before": len(before["bound"]),
        "bound_after": len(result["bound"]),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    q = sub.add_parser("queue")
    q.add_argument("--inventory", type=Path, required=True)
    q.add_argument("--assembly", type=Path, required=True)
    q.add_argument("--tiles-manifest", type=Path, required=True)
    q.add_argument("--output", type=Path, required=True)
    for name in ("batch", "summary", "contact", "publish"):
        p = sub.add_parser(name)
        p.add_argument("number", type=int)
        p.add_argument("--queue", type=Path, required=True)
        p.add_argument("--run", type=Path, required=True, help="batch run directory")
        if name == "batch":
            p.add_argument("--inventory", type=Path, required=True)
            p.add_argument("--refs", type=Path, required=True)
            p.add_argument("--size", type=int, default=25)
            p.add_argument(
                "--walls", help="comma-separated facade IDs instead of the next queue walls"
            )
            p.add_argument("--model", default="gpt-6-luna")
            p.add_argument("--effort", default="low")
        if name == "summary":
            p.add_argument("--logs", type=Path, required=True)
        if name == "contact":
            p.add_argument("--output", type=Path, required=True)
        if name == "publish":
            p.add_argument("--inventory", type=Path, required=True)
            p.add_argument("--assembly", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.command == "queue":
        if a.output.exists():
            raise SystemExit(f"{a.output} exists; the queue is written once")
        result = build_queue(a.inventory, a.assembly, spawn_en(a.tiles_manifest))
        write_json(a.output, result)
        print(json.dumps({"walls": len(result["walls"])}))
        return 0
    queue = json.loads(a.queue.read_text())
    if a.command == "batch":
        if sha(a.inventory) != queue["inventory_sha256"]:
            raise SystemExit("inventory changed since the queue was written")
        ids = take_batch(queue, a.number, a.size, a.walls.split(",") if a.walls else None)
        write_json(a.queue, queue)
        subset(a.inventory, ids, a.run / "facades.geojson")
        state = a.run / "state.json"
        if not state.exists():
            write_json(
                state,
                {
                    "start_epoch": time.time(),
                    "refs": str(a.refs),
                    "quota_before_percent": quota(),
                    "model": a.model,
                    "effort": a.effort,
                },
            )
        print(json.dumps({"batch": a.number, "walls": ids}))
        return 0
    if a.command == "summary":
        result = summarize(a.run, queue, a.number)
        a.logs.mkdir(parents=True, exist_ok=True)
        write_json(a.logs / f"batch-{a.number:02d}.json", result)
        (a.logs / "ledger.md").write_text(ledger(a.logs))
        print(
            json.dumps(
                {
                    k: result[k]
                    for k in (
                        "walls_attempted",
                        "walls_readable",
                        "walls_accepted",
                        "image_calls",
                        "quota_before_percent",
                        "quota_after_percent",
                    )
                }
            )
        )
        return 0
    if a.command == "contact":
        contact_sheet(
            a.run,
            a.output,
            f"Batch {a.number}: final images after automated, reviewer and manual QA",
        )
        return 0
    print(json.dumps(publish(a.run, a.inventory, a.assembly)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
