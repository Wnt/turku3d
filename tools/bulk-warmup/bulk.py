"""Bulk tile builder for a rented host: full-pass builds of the warm-up walk with N persistent
workers. Run with the remote-drafts run.sh environment. Writes $TURKU3D_WORK/coarse/<tile> and
appends one JSON line per finished tile to bulk-progress.jsonl (read by the OCI pull loop)."""

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from turku3d import config as C

PENDING = 3
root = C.work_root() / "coarse"
state = Path(sys.argv[1])  # directory with walk.json and skip.json
workers = int(sys.argv[2])
walk = json.loads((state / "walk.json").read_text())
skip = set(json.loads((state / "skip.json").read_text()))
progress = state / "bulk-progress.jsonl"
done = set()
if progress.exists():
    for line in progress.read_text().splitlines():
        r = json.loads(line)
        done.add(r["tile"])
queue = [t for t in walk if t not in skip and t not in done]
lock = threading.Lock()
log = open(progress, "a")
print(f"{len(queue)} tiles to build, {len(skip)} current on OCI, {len(done)} done before", flush=True)


def record(row):
    with lock:
        log.write(json.dumps(row) + "\n")
        log.flush()


def fetch(units):
    for unit in units:
        subprocess.run([sys.executable, "-m", "turku3d.coarse", "fetch", unit], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def work(index):
    proc = subprocess.Popen([sys.executable, "-m", "turku3d.coarse", "worker"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=open(state / f"worker-{index}.log", "a"), text=True)
    while True:
        with lock:
            if not queue:
                break
            tile = queue.pop(0)
        started = time.time()
        for attempt in range(3):
            proc.stdin.write(json.dumps({"tile": tile, "out": str(root / tile), "first_pass": False}) + "\n")
            proc.stdin.flush()
            result = json.loads(proc.stdout.readline())
            if result["code"] != PENDING:
                break
            fetch(json.loads(result["stdout"])["pending"])
        outcome = "built" if result["code"] == 0 else "pending" if result["code"] == PENDING else "failed"
        record({"tile": tile, "outcome": outcome, "seconds": round(time.time() - started, 1),
                "worker": index, "error": result.get("stderr", "")[-300:], "at": time.time()})
    proc.stdin.close()
    proc.wait()


threads = [threading.Thread(target=work, args=(i,)) for i in range(workers)]
for t in threads:
    t.start()
for t in threads:
    t.join()
print("ALL DONE", flush=True)
