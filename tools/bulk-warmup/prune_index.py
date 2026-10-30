"""Shrink the input fingerprint index under its lock: drop entries of finished tiles' per-tile
caches and stale /root paths. Raw and lidar entries stay. Smaller index = shorter lock holds."""
import fcntl, json, os, re
from pathlib import Path
index = Path("/home/ubuntu/turku3d-drafts/work/cache/inputs/hashes.json")
done = {json.loads(l)["tile"] for l in open("/root/turku3d-bulk-warmup/state/bulk-progress.jsonl")}
tile = re.compile(r"/work/coarse/(?:\.stages|\.inputs)?/?(t0_-?\d+_-?\d+)/")
with open(index.with_suffix(".lock"), "a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    memo = json.loads(index.read_text())
    keep = {}
    for k, v in memo.items():
        if k.startswith("/root/"):
            continue
        m = tile.search(k)
        if m and m.group(1) in done:
            continue
        keep[k] = v
    tmp = index.with_name(f"{index.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(keep, indent=2) + "\n")
    tmp.replace(index)
print(len(memo), "->", len(keep), "entries;", round(index.stat().st_size / 1e6, 1), "MB")
