"""Run the resumable city landmark queue without per-building manual steps."""

import argparse
import json
import time
from pathlib import Path

from turku3d import config as C

from . import enrichment, measure, ranking, references


def main():
    import subprocess
    import sys

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=C.data_root() / "research/landmarks")
    p.add_argument("--cache-root", type=Path, action="append", default=[])
    p.add_argument("--limit", type=int, default=10000)
    p.add_argument("--descriptions", type=Path, default=C.data_root() / "landmark-descriptions")
    p.add_argument("--reference-retries", type=int, default=3)
    p.add_argument(
        "--references-only",
        action="store_true",
        help="Gather images without invoking the vision runner",
    )
    a = p.parse_args()
    a.root.mkdir(parents=True, exist_ok=True)
    ranked = a.root / "ranked.json"

    def run(module, args):
        subprocess.run([sys.executable, "-m", module.__name__, *map(str, args)], check=True)

    run(ranking, ["--out", ranked])
    run(enrichment, ["--ranked", ranked, "--pageviews"])
    if a.cache_root:
        run(
            measure,
            ["--ranked", ranked, *[v for root in a.cache_root for v in ["--cache-root", root]]],
        )
    reference_args = [
        "--ranked",
        ranked,
        "--root",
        a.root,
        "--limit",
        a.limit,
        "--descriptions",
        a.descriptions,
        *([] if a.references_only else ["--describe"]),
    ]
    for attempt in range(a.reference_retries + 1):
        run(references, reference_args)
        outcomes = json.loads((a.root / "pipeline-results.json").read_text())
        deferred = [r for r in outcomes if r.get("retry_after")]
        if not deferred or attempt == a.reference_retries:
            break
        from email.utils import parsedate_to_datetime

        value = deferred[0]["retry_after"]
        try:
            seconds = max(1, float(value))
        except ValueError:
            seconds = max(1, parsedate_to_datetime(value).timestamp() - time.time())
        print(
            json.dumps(
                dict(
                    state="reference-rate-limit", retry_in_seconds=seconds, remaining=len(deferred)
                )
            ),
            flush=True,
        )
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            time.sleep(min(30, max(0, deadline - time.monotonic())))
    print(
        json.dumps(
            dict(
                candidates=len(json.loads(ranked.read_text())["buildings"]),
                processed=len(outcomes),
                descriptions=sum(bool(r.get("description")) for r in outcomes),
                retryable_errors=[r["id"] for r in outcomes if r.get("error")],
            )
        )
    )


if __name__ == "__main__":
    main()
