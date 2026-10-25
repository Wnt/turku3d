#!/usr/bin/env python3
"""Sequential, resumable model/effort pilot with a persistent quota ceiling."""

import argparse
import fcntl
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "textures"), str(ROOT / "qa"), str(Path(__file__).parent)]
import metrics  # noqa: E402
from turkutex.codex import usage  # noqa: E402
from turkutex.codex.runner import Job, QuotaStop, Runner  # noqa: E402

MODELS = [
    "gpt-6.1-sol",
    "gpt-6-astra",
    "gpt-6-sol",
    "gpt-6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
]


def reading():
    limits = usage.rate_limits() or {}
    windows = [w for w in (limits.get("primary"), limits.get("secondary")) if w]
    if not windows:
        raise QuotaStop("quota unavailable")
    weekly = max(windows, key=lambda w: w.get("windowDurationMins", 0))
    if weekly.get("windowDurationMins", 0) < 10080:
        raise QuotaStop("weekly window unavailable")
    return weekly


def check_budget(state, current):
    pct = current.get("usedPercent")
    if pct is None or current.get("resetsAt") != state["reset"]:
        raise QuotaStop("quota unavailable or window reset; retain evidence and start a new run")
    # Reserve a full percentage point for integer rounding / one in-flight call.
    if pct >= min(state["cap"], state["start"] + state["budget"]) - 1:
        raise QuotaStop(f"budget stop: {pct}%, baseline {state['start']}%, reserve 1 point")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--cap", type=float, default=80)
    ap.add_argument("--budget", type=float, default=3)
    ap.add_argument("--models", nargs="+", default=MODELS, choices=MODELS)
    ap.add_argument("--efforts", nargs="+", default=["low", "medium"], choices=["low", "medium"])
    ap.add_argument("--limit", type=int)
    args = ap.parse_args(argv)
    if not 0 < args.budget <= 3 or not 0 < args.cap <= 80:
        ap.error("budget must be in (0,3], cap in (0,80]")
    manifest = json.loads(args.manifest.read_text())
    digest = hashlib.sha256(args.manifest.read_bytes()).hexdigest()
    args.out.mkdir(parents=True, exist_ok=True)
    lock = (args.out / "run.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        ap.error("this benchmark output directory is already running")
    state_path = args.out / "budget.json"
    try:
        current = reading()
        if state_path.exists():
            state = json.loads(state_path.read_text())
            if state["manifest"] != digest:
                raise ValueError("manifest changed; use a new output directory")
            state["cap"] = min(state["cap"], args.cap)
            state["budget"] = min(state["budget"], args.budget)
        else:
            state = {
                "start": current["usedPercent"],
                "reset": current["resetsAt"],
                "cap": args.cap,
                "budget": args.budget,
                "manifest": digest,
            }
        state_path.write_text(json.dumps(state, indent=2))
        runner = Runner(
            args.out,
            parallel=1,
            retries=0,
            cache_dir=args.out / "cache",
            max_pct=min(state["cap"], state["start"] + state["budget"]) - 1,
        )
        done = runner.done_records()
        scored = (
            {
                json.loads(line)["id"]
                for line in (args.out / "scores.jsonl").read_text().splitlines()
            }
            if (args.out / "scores.jsonl").exists()
            else set()
        )
        count = 0
        for effort in args.efforts:
            for model in args.models:
                for case in manifest["cases"]:
                    kind = case["kind"]
                    if kind == "label" and not case.get("reviewed"):
                        continue
                    if kind == "improve" and (
                        model not in MODELS[:3] or not case.get("pilot", True)
                    ):
                        continue
                    images = [Path(os.path.expandvars(p)).expanduser() for p in case["images"]]
                    for p, expected in zip(images, case["sha256"], strict=True):
                        if hashlib.sha256(p.read_bytes()).hexdigest() != expected:
                            raise ValueError(f"input changed: {p}")
                    job = Job(
                        id=f"{case['id']}.{model}.{effort}",
                        prompt=case["prompt"],
                        images=images,
                        model=model,
                        effort=effort,
                        expect_images=int(kind == "improve"),
                        timeout=120,
                        schema=ROOT / case["schema"] if case.get("schema") else None,
                        meta={
                            "kind": kind,
                            "facade_id": case["id"] if kind not in ("qa", "qa_batch") else None,
                            "views": list(range(len(case["truth"])))
                            if kind == "qa_batch"
                            else ([case["id"]] if kind == "qa" else []),
                        },
                        prompt_version=manifest["version"],
                    )
                    existing = done.get(job.id, {})
                    reusable = existing.get("key") == job.key() and existing.get("ok")
                    if reusable and job.id in scored:
                        continue
                    if reusable:
                        rec = existing
                    else:
                        check_budget(state, reading())
                        rec = runner.run_job(job)
                    score = {}
                    if rec["ok"] and kind == "qa_batch":
                        answers = (rec["answer"] or {}).get("judgements", [])
                        if len(answers) != len(case["truth"]):
                            rec["ok"] = False
                        else:
                            scores = [
                                metrics.qa(answer, truth)
                                for answer, truth in zip(answers, case["truth"], strict=True)
                            ]
                            tp = sum(s["true_positives"] for s in scores)
                            n = sum(s["planted"] for s in scores)
                            score = {
                                "recall": tp / n if n else None,
                                "true_positives": tp,
                                "planted": n,
                                "false_positives": sum(s["false_positives"] for s in scores),
                                "views": len(scores),
                            }
                    if rec["ok"] and kind == "qa":
                        score = metrics.qa(rec["answer"] or {}, case["truth"])
                    if rec["ok"] and kind == "label":
                        score = metrics.labels(rec["answer"] or {}, case["truth"])
                    if rec["ok"] and kind == "improve":
                        score = metrics.improve(rec["primary"], images[0], case["aspect"])
                        check_budget(state, reading())
                        assessment = runner.run_job(
                            Job(
                                id=job.id + ".judge",
                                model="gpt-6-astra",
                                effort="medium",
                                images=[images[0], Path(rec["primary"])],
                                prompt="Image 1 is the reference facade; image 2 is the cleaned facade texture. Score architectural fidelity (floors, bays, doors, wall material and colours) and cleanliness (removed occluders, perspective, baked lighting) from 0 to 10. Do not reward invented architectural changes. Return the scores and a short reason.",
                                schema=ROOT / "bench/codex/improve-score.schema.json",
                                meta={"kind": "improve_judge", "facade_id": case["id"]},
                            )
                        )
                        score["astra"] = assessment.get("answer")
                        score["judge_tokens"] = assessment.get("tokens")
                        score["judge_seconds"] = assessment.get("seconds")
                        rec["ok"] = rec["ok"] and assessment["ok"]
                    with (args.out / "scores.jsonl").open("a") as f:
                        f.write(
                            json.dumps(
                                {
                                    "id": job.id,
                                    "kind": kind,
                                    "model": model,
                                    "effort": effort,
                                    "ok": rec["ok"],
                                    "tokens": rec["tokens"],
                                    "seconds": rec["seconds"],
                                    "metrics": score,
                                }
                            )
                            + "\n"
                        )
                    current = reading()
                    state["last"] = current["usedPercent"]
                    state_path.write_text(json.dumps(state, indent=2))
                    count += 1
                    check_budget(state, current)
                    if args.limit and count >= args.limit:
                        return 0
    except QuotaStop as exc:
        (args.out / "stop.txt").write_text(str(exc) + "\n")
        print(exc)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
