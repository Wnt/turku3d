"""Fill the input fingerprint index (tile_layer.fingerprints) for every raw/lidar/input file in
parallel, then merge under its lock, so build workers stop serialising on hashing."""
import fcntl, hashlib, json, os, sys
from multiprocessing import Pool
from pathlib import Path

HOME = "/home/ubuntu/turku3d-drafts"
index = Path(HOME + "/work/cache/inputs/hashes.json")
roots = [HOME + "/data/raw", HOME + "/work/cache/lidar", HOME + "/work/coarse/.inputs"]


def ident(path):
    st = os.stat(path)
    return {"path": path, "size": st.st_size, "mtime_ns": st.st_mtime_ns}


def digest(path):
    try:
        cur = ident(path)
        with open(path, "rb") as f:
            cur["sha256"] = hashlib.file_digest(f, "sha256").hexdigest()
    except FileNotFoundError:  # builds replace prepared inputs concurrently
        return path, None
    return path, cur


old = json.loads(index.read_text())
todo = []
for r in roots:
    for dirpath, _, files in os.walk(r):
        for name in files:
            if name.endswith(".lock") or ".tmp" in name:
                continue
            p = os.path.join(dirpath, name)
            o = old.get(p, {})
            c = ident(p)
            if not ("sha256" in o and all(o.get(k) == v for k, v in c.items())):
                todo.append(p)
print(len(todo), "files to hash", flush=True)
with Pool(48) as pool:
    fresh = {p: c for p, c in pool.imap_unordered(digest, todo, chunksize=4) if c}
with open(index.with_suffix(".lock"), "a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    memo = json.loads(index.read_text())
    memo.update(fresh)
    tmp = index.with_name(f"{index.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(memo, indent=2) + "\n")
    tmp.replace(index)
print("merged", len(fresh), "total", len(memo), flush=True)
