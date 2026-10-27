"""Emergency bounded label/QA pilot: nine low-effort calls, no image generation."""

import fcntl
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "textures"), str(ROOT / "qa"), str(Path(__file__).parent)]
import metrics  # noqa: E402
from run import check_budget, reading  # noqa: E402
from turkutex.codex.runner import QuotaStop, Runner, load_jobs  # noqa: E402


def main():
    work = Path(os.environ["TURKU3D_WORK"]) / "reports/costbench/v2"
    out = work / "pilot"
    out.mkdir(parents=True, exist_ok=True)
    lock = (out / "run.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    templates = [json.loads(line) for line in (work / "jobs.jsonl").read_text().splitlines()]
    label = next(j for j in templates if j["meta"]["kind"] == "building_label")
    qa = next(j for j in templates if j["meta"]["kind"] == "qa_batch")
    jobs = []
    for template in (label, qa):
        for model in ["gpt-6-luna", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-6-sol"]:
            jobs.append(
                {
                    **template,
                    "model": model,
                    "effort": "low",
                    "id": f"{template['meta']['kind']}.{model}.low",
                    "timeout": 90,
                }
            )
    jobs.append(
        {
            **qa,
            "model": "gpt-6-astra",
            "effort": "low",
            "id": "qa_batch.gpt-6-astra.low",
            "timeout": 90,
        }
    )
    manifest = out / "jobs.jsonl"
    manifest.write_text("".join(json.dumps(j) + "\n" for j in jobs))
    statepath = out / "budget.json"
    current = reading()
    state = (
        json.loads(statepath.read_text())
        if statepath.exists()
        else {"start": current["usedPercent"], "reset": current["resetsAt"], "cap": 95, "budget": 3}
    )
    statepath.write_text(json.dumps(state, indent=2))
    fingerprint = hashlib.sha256(manifest.read_bytes()).hexdigest()
    if state.get("manifest", fingerprint) != fingerprint:
        raise ValueError("Benchmark inputs changed; preserve the original output directory")
    content_digest = hashlib.sha256(
        b"".join(
            (ROOT / template["meta"]["truth_file"]).read_bytes()
            + Path(template["schema"]).read_bytes()
            for template in (label, qa)
        )
    ).hexdigest()
    if state.get("content", content_digest) != content_digest:
        raise ValueError("Benchmark truth or schema changed; keep frozen evidence")
    state["content"] = content_digest
    state["manifest"] = fingerprint
    statepath.write_text(json.dumps(state, indent=2))
    for template in (label, qa):
        cases = json.loads((ROOT / template["meta"]["truth_file"]).read_text())["cases"]
        expected = (
            [c["sha256"] for c in cases]
            if template is label
            else [h for c in cases for h in c["input_sha256"]]
        )
        for path, digest in zip(template["images"], expected, strict=True):
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
                raise ValueError(f"Frozen image changed: {path}")
    runner = Runner(
        out, parallel=1, retries=0, cache_dir=out / "cache", max_pct=min(94, state["start"] + 2)
    )
    done = runner.done_records()
    scores = []
    try:
        for job in load_jobs(manifest):
            rec = done.get(job.id)
            fresh = not rec or rec.get("key") != job.key()
            if fresh:
                check_budget(state, reading())
                rec = runner.run_job(job)
            truth = json.loads((ROOT / job.meta["truth_file"]).read_text())["cases"]
            result = {}
            if rec["ok"]:
                answer = rec.get("answer") or {}
                if job.meta["kind"] == "building_label":
                    per = [
                        metrics.labels(a, c["truth"])
                        for a, c in zip(answer["facades"], truth, strict=True)
                    ]
                    correct = sum(p["correct"] for p in per)
                    fields = sum(p["fields"] for p in per)
                    result = {
                        "accuracy": correct / fields,
                        "correct": correct,
                        "fields": fields,
                        "facades": 12,
                        "per_field": {
                            k: metrics.labels(
                                {str(i): a.get(k) for i, a in enumerate(answer["facades"])},
                                {
                                    str(i): c["truth"][k]
                                    for i, c in enumerate(truth)
                                    if k in c["truth"]
                                },
                            )
                            for k in ["floors", "wall_material", "roof_shape", "roof_colour"]
                        },
                    }
                else:
                    per = [
                        metrics.qa(a, c["truth"])
                        for a, c in zip(answer["judgements"], truth, strict=True)
                    ]
                    result = {
                        "recall": sum(p["true_positives"] for p in per) / 8,
                        "false_positives": sum(p["false_positives"] for p in per),
                        "views": 8,
                        "per_view": per,
                    }
            scores.append(
                {
                    "id": job.id,
                    "model": job.model,
                    "effort": job.effort,
                    "kind": job.meta["kind"],
                    "ok": rec["ok"],
                    "tokens": rec.get("tokens"),
                    "seconds": rec.get("seconds"),
                    "error": rec.get("error"),
                    "metrics": result,
                }
            )
            (out / "scores.json").write_text(json.dumps(scores, indent=2) + "\n")
            if fresh:
                current = reading()
                state["last"] = current["usedPercent"]
                statepath.write_text(json.dumps(state, indent=2))
            print(job.id, rec["ok"], rec.get("tokens"), result, flush=True)
    except QuotaStop as exc:
        (out / "stop.txt").write_text(str(exc) + "\n")
        print(exc, flush=True)
        return 3
    (out / "stop.txt").unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
