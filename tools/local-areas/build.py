"""Full-pass builds of named local areas with N persistent coarse workers (resumable).

Run in the local-areas environment:
  tools/local-areas/run.sh python tools/local-areas/build.py AREAS.json OUT WORKERS AREA...

AREAS.json maps an area name to its L0 tile ids. Tiles go to OUT/<tile> (shared textures to
OUT/shared); one JSON line per finished tile is appended to OUT/progress.jsonl. A tile whose raw
inputs are missing is recorded as "pending" and never fetched here: fetch first (see README), so
the build never writes into raw directories linked from another root.
"""

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

PENDING = 3
areas = json.loads(Path(sys.argv[1]).read_text())
out = Path(sys.argv[2])
workers = int(sys.argv[3])
names = sys.argv[4:]
out.mkdir(parents=True, exist_ok=True)
progress = out / "progress.jsonl"
done = set()
if progress.exists():
    for line in progress.read_text().splitlines():
        row = json.loads(line)
        if row["outcome"] == "built":
            done.add(row["tile"])
queue = [(name, tile) for name in names for tile in areas[name] if tile not in done]
lock = threading.Lock()
log = progress.open("a")
print(f"{len(queue)} tiles to build, {len(done)} built before", flush=True)


def summary(tile):
    m = json.loads((out / tile / "manifest.json").read_text())
    return {
        "buildings": m.get("buildings"),
        "bridges": m.get("bridges"),
        "trees": m.get("trees"),
        "roof_heights": len(m.get("roof_heights") or []),
        "seconds_total": (m.get("seconds") or {}).get("total"),
    }


def work(index):
    proc = subprocess.Popen(
        [sys.executable, "-m", "turku3d.coarse", "worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=open(out / f"worker-{index}.log", "a"),
        text=True,
    )
    while True:
        with lock:
            if not queue:
                break
            area, tile = queue.pop(0)
        started = time.time()
        proc.stdin.write(json.dumps({"tile": tile, "out": str(out / tile), "first_pass": False}) + "\n")
        proc.stdin.flush()
        result = json.loads(proc.stdout.readline())
        code = result["code"]
        outcome = "built" if code == 0 else "pending" if code == PENDING else "failed"
        row = {"area": area, "tile": tile, "outcome": outcome, "seconds": round(time.time() - started, 1),
               "worker": index, "at": time.time()}
        if code == 0:
            row.update(summary(tile))
        elif code == PENDING:
            row["pending"] = json.loads(result["stdout"])["pending"]
        else:
            row["error"] = result.get("stderr", "")[-400:]
        with lock:
            log.write(json.dumps(row) + "\n")
            log.flush()
    proc.stdin.close()
    proc.wait()


threads = [threading.Thread(target=work, args=(i,)) for i in range(workers)]
for t in threads:
    t.start()
for t in threads:
    t.join()
print("ALL DONE", flush=True)
