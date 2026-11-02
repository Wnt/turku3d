"""Run the resumable city landmark queue without per-building manual steps."""

import argparse
import json
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
    run(
        references,
        [
            "--ranked",
            ranked,
            "--root",
            a.root,
            "--limit",
            a.limit,
            *([] if a.references_only else ["--describe"]),
        ],
    )
    outcomes = json.loads((a.root / "pipeline-results.json").read_text())
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
