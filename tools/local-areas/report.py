"""Summarise OUT/progress.jsonl of build.py: per-area outcomes, error kinds and building counts.

  python3 tools/local-areas/report.py OUT [TILE...]
"""

import collections
import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
latest = {}
for line in (out / "progress.jsonl").read_text().splitlines():
    row = json.loads(line)
    latest[row["tile"]] = row
by_area = collections.defaultdict(list)
for row in latest.values():
    by_area[row["area"]].append(row)
for area, rows in sorted(by_area.items()):
    outcomes = collections.Counter(r["outcome"] for r in rows)
    kinds = collections.Counter(
        r["error"].split(":", 1)[0] for r in rows if r["outcome"] == "failed"
    )
    built = [r for r in rows if r["outcome"] == "built"]
    seconds = sorted(r["seconds"] for r in built)
    print(
        f"{area}: {dict(outcomes)} buildings={sum(r['buildings'] or 0 for r in built)}"
        f" bridges={sum(r['bridges'] or 0 for r in built)}"
        f" p50={seconds[len(seconds) // 2] if seconds else '-'}s"
        + (f" errors={dict(kinds)}" if kinds else "")
    )
    top = sorted(built, key=lambda r: -(r["buildings"] or 0))[:3]
    print("  most buildings: " + ", ".join(f"{r['tile']}={r['buildings']}" for r in top))
for tile in sys.argv[2:]:
    print(tile, json.dumps(latest.get(tile)))
