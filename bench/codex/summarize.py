#!/usr/bin/env python3
"""Recompute QA pilot results from immutable outputs; does not call a model."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "qa"), str(ROOT / "textures")]
from metrics import qa  # noqa: E402
from turkutex.codex.accounting import rows  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    truth = json.loads(args.manifest.read_text())["cases"][0]["truth"]
    results = []
    for r in rows(args.run / "out.jsonl"):
        answers = (r.get("answer") or {}).get("judgements", [])
        if len(answers) != len(truth):
            results.append({"id": r["id"], "ok": False})
            continue
        strict = [qa(a, t) for a, t in zip(answers, truth, strict=True)]
        # Diagnostic only: preserve strict metric while showing category disagreements.
        relaxed = [
            qa({"issues": [{**i, "category": "texture"} for i in a["issues"]]}, t)
            for a, t in zip(answers, truth, strict=True)
        ]
        tokens = {}
        for event in rows(args.run / "logs" / f"{r['id']}.events.jsonl"):
            if event.get("type") == "turn.completed":
                for key, value in event.get("usage", {}).items():
                    tokens[key] = tokens.get(key, 0) + value
        n = sum(s["planted"] for s in strict)
        results.append(
            {
                "model": r["model"],
                "effort": r["effort"],
                "ok": r["ok"],
                "views": len(truth),
                "strict_recall": sum(s["true_positives"] for s in strict) / n,
                "strict_false_positives": sum(s["false_positives"] for s in strict),
                "localization_recall": sum(s["true_positives"] for s in relaxed) / n,
                "localization_false_positives": sum(s["false_positives"] for s in relaxed),
                "tokens": tokens,
                "seconds": r["seconds"],
            }
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "budget": json.loads((args.run / "budget.json").read_text()),
                "stop": (args.run / "stop.txt").read_text().strip()
                if (args.run / "stop.txt").exists()
                else None,
                "results": results,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
