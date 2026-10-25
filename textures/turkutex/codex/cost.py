"""Backfill durable evidence and report token costs (cached/reasoning are subsets)."""

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from . import accounting as a


def backfill(roots):
    added = 0
    for root in roots:
        root = Path(root).expanduser()
        # Structured outputs contain model/job identity absent from event streams.
        outputs = sorted(set(root.rglob("out.jsonl")) | set(root.rglob("attempts.jsonl")))
        for path in outputs:
            for index, r in enumerate(a.rows(path)):
                if r.get("cached") or not r.get("model"):
                    continue
                tid = r.get("thread_id")
                meta = r.get("meta") or {}
                ev = path.parent / "logs" / f"{r.get('id')}.events.jsonl"
                events = ev.read_text() if ev.exists() else ""
                # A log can belong to a later retry; never attach it to an earlier one.
                if not tid or tid not in events:
                    events = ""
                added += a.record_call(
                    source=path,
                    model=r["model"],
                    effort=r.get("effort", "unknown"),
                    job_type=meta.get("kind", "texture"),
                    task=_task(path),
                    job_id=r.get("id"),
                    events=events,
                    tokens=r.get("tokens"),
                    call_id=None
                    if events
                    else (
                        f"{tid}:0" if tid else r.get("accounting_id") or a.stable_id(path, index)
                    ),
                    fallback_id=r.get("accounting_id") or a.stable_id(path, index),
                    seconds=r.get("seconds"),
                    started=_timestamp(r.get("finished_at"), path),
                    ok=r.get("ok"),
                    units={
                        "facade": len(meta.get("facade_ids", []))
                        or int(bool(meta.get("facade_id"))),
                        "view": len(meta.get("views", meta.get("views_used", []))),
                    },
                )
        for path in sorted(root.rglob("events*.jsonl")):
            meta_path = path.parent / "meta.json"
            if not meta_path.exists():
                continue
            meta = json.loads(meta_path.read_text())
            thread, index = None, 0
            for event in a.rows(path):
                if event.get("type") == "thread.started":
                    thread = event.get("thread_id")
                elif event.get("type") in ("turn.completed", "turn.failed"):
                    added += a.record_call(
                        source=path,
                        model=meta.get("model", "gpt-6-astra"),
                        effort=meta.get("effort", "unknown"),
                        job_type="task",
                        task=meta.get("name", path.parent.name),
                        tokens=event.get("usage"),
                        call_id=a.stable_id(path, index),
                        events=json.dumps({"type": "thread.started", "thread_id": thread}),
                        started=meta.get("created", path.stat().st_mtime),
                        ok=event["type"] == "turn.completed",
                    )
                    index += 1
        # QA stores all attempts in each judgment JSON, including unsuccessful ones.
        for path in root.rglob("*.json"):
            if "qa" not in path.parts:
                continue
            try:
                r = json.loads(path.read_text())
            except (ValueError, OSError):
                continue
            if not isinstance(r, dict) or not r.get("model") or "attempts" not in r:
                continue
            for index, attempt in enumerate(r["attempts"]):
                tid = attempt.get("thread_id")
                added += a.record_call(
                    source=path,
                    model=r["model"],
                    effort=r.get("effort", "unknown"),
                    job_type="qa",
                    task=_task(path),
                    job_id=r.get("ref"),
                    tokens=attempt.get("tokens"),
                    call_id=f"{tid}:0"
                    if tid
                    else attempt.get("accounting_id") or a.stable_id(path, index),
                    seconds=attempt.get("secs"),
                    started=path.stat().st_mtime,
                    units={"view": 1},
                    ok=not bool(attempt.get("error")),
                )
    return added


def _task(path):
    parts = path.parts
    return parts[parts.index("wt") + 1] if "wt" in parts else "historical"


def _timestamp(stamp, path):
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except (ValueError, TypeError):
        return path.stat().st_mtime


def summary(records, group):
    buckets = defaultdict(list)
    records = list(records)
    voided = {r["voids"] for r in records if r.get("record_type") == "void"}
    for r in records:
        if r.get("record_type") == "void" or r["call_id"] in voided:
            continue
        key = r["started_at"][:10] if group == "day" else r.get(group, "unknown")
        buckets[str(key)].append(r)
    result = []
    for key, rs in sorted(buckets.items()):
        tokens = {k: sum((r["tokens"].get(k) or 0) for r in rs) for k in a.FIELDS}
        total = tokens["input_tokens"] + tokens["output_tokens"]
        per_unit = {}
        for unit in ("facade", "view"):
            attributed = [r for r in rs if r.get("units", {}).get(unit, 0)]
            distinct = {}
            for r in attributed:
                identity = (r.get("task"), r.get("job_id") or r["call_id"])
                distinct[identity] = max(distinct.get(identity, 0), r["units"][unit])
            count = sum(distinct.values())
            cost = sum(
                (r["tokens"].get("input_tokens") or 0) + (r["tokens"].get("output_tokens") or 0)
                for r in attributed
            )
            per_unit[unit] = round(cost / count, 1) if count else None
        result.append(
            {
                "group": key,
                "calls": len(rs),
                "unknown_usage": sum(not r["usage_known"] for r in rs),
                **tokens,
                "total_tokens": total,
                "wall_seconds": round(sum(r.get("wall_seconds") or 0 for r in rs), 2),
                "unknown_wall": sum(r.get("wall_seconds") is None for r in rs),
                "tokens_per_facade": per_unit["facade"],
                "tokens_per_view": per_unit["view"],
            }
        )
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--backfill", nargs="+", metavar="ROOT")
    p.add_argument("--by", choices=["job_type", "model", "task", "day"], default="job_type")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    if args.backfill:
        print(f"Backfilled {backfill(args.backfill)} calls")
    report = summary(a.rows(a.ledger_path()), args.by)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            "group | calls | input | cached | output | reasoning | seconds | tokens/facade | tokens/view | unknown usage"
        )
        for r in report:
            print(
                " | ".join(
                    str(r[k])
                    for k in [
                        "group",
                        "calls",
                        *a.FIELDS,
                        "wall_seconds",
                        "tokens_per_facade",
                        "tokens_per_view",
                        "unknown_usage",
                    ]
                )
            )
    return 0
