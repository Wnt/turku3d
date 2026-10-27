#!/usr/bin/env python3
"""Judge exactly the same real pair three times, recording sample variance and all calls."""

import argparse
import json
import statistics
from datetime import UTC, datetime
from pathlib import Path

from judge.batch import judge_pairs
from qalib.paths import data_dir
from qalib.provenance import file_digest


def summarize(results):
    scores = [j["result"]["similarity"] for j in results if j.get("ok") and j.get("result")]
    if len(scores) != 3:
        raise ValueError(f"calibration needs 3 successful calls, got {len(scores)}")
    return {
        "n": 3,
        "scores": scores,
        "mean": statistics.mean(scores),
        "sample_variance": statistics.variance(scores),
        "sample_stddev": statistics.stdev(scores),
        "range": max(scores) - min(scores),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True, type=Path)
    ap.add_argument("--view", required=True)
    ap.add_argument("--out", type=Path, default=data_dir() / "calibration")
    ap.add_argument("--cap", type=float)
    a = ap.parse_args(argv)
    run = json.loads((a.run / "run.json").read_text())
    pair = next(p for p in run["pairs"] if p["view_id"] == a.view)
    pairs = [{**pair, "pair_id": f"{a.view}-repeat-{i}"} for i in range(1, 4)]
    call_dir = a.out / (
        run["run_id"] + "-" + a.view + "-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    )
    results = judge_pairs(pairs, call_dir, cap=a.cap, with_ids=run.get("with_id_image", True))
    try:
        stats = summarize(list(results.values()))
    except ValueError as e:
        print(e)
        return 1
    stats.update(
        {
            "calls_directory": str(call_dir),
            "run_id": run["run_id"],
            "view_id": a.view,
            "comparison_key": run.get("comparison_key"),
            "prompt_sha256": file_digest(Path(__file__).parent / "judge/prompt.md"),
            "usage_before_pct": [j["usage_before_pct"] for j in results.values()],
            "usage_after_pct": [j["usage_after_pct"] for j in results.values()],
        }
    )
    (a.out / "calibration.json").write_text(json.dumps(stats, indent=2))
    (call_dir / "calibration.json").write_text(json.dumps(stats, indent=2))
    (a.run / "calibration.json").write_text(json.dumps(stats, indent=2))
    from report.report import build_report

    build_report(a.run)
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
