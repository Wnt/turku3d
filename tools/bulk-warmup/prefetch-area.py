"""Fetch every raw input unit the warm-up walk still lacks, one unit at a time, before any bulk
build. A tile's input key covers neighbouring spot-height files, so building before the whole
area's inputs exist leaves keys that go stale when later downloads arrive.

Run with the deployment environment: ./run.sh python tools/bulk-warmup/prefetch-area.py WALK.json
Writes one JSON line per unit to ~/prefetch-area.jsonl; resumable (fetched units are skipped)."""

import json
import subprocess
import sys
import time
from pathlib import Path

from turku3d import config as C
from turku3d.coarse.sources import NoCoverage, Sources

walk = json.loads(Path(sys.argv[1]).read_text())
log = Path.home() / "prefetch-area.jsonl"
done = {json.loads(line)["unit"] for line in log.read_text().splitlines()} if log.exists() else set()
sources = Sources()
units = {}
for tile in walk:
    try:
        for unit in sources.missing_for_service(C.VisualTile.parse(tile)):
            units.setdefault(unit.key, tile)
    except NoCoverage:
        continue
todo = [u for u in units if u not in done]
print(f"{len(units)} missing units for {len(walk)} tiles, {len(todo)} to fetch", flush=True)
with log.open("a") as out:
    for unit in todo:
        started = time.time()
        r = subprocess.run([sys.executable, "-m", "turku3d.coarse", "fetch", unit],
                           capture_output=True, text=True)
        out.write(json.dumps({"unit": unit, "code": r.returncode, "seconds": round(time.time() - started, 1),
                              "error": r.stderr.strip().splitlines()[-1][:200] if r.returncode else ""}) + "\n")
        out.flush()
print("prefetch complete", flush=True)
