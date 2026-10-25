"""Shared streetlevel cache locking, atomic publication, size report and partial-file GC."""

import argparse
import fcntl
import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

from turku3d import config as C

_THREAD_LOCK = threading.RLock()
_DEPTH = threading.local()


def synchronized(method):
    """Serialize cache readers/writers across processes; allow nested pano operations."""

    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with _THREAD_LOCK:
            depth = getattr(_DEPTH, "value", 0)
            if depth:
                return method(self, *args, **kwargs)
            with cache_lock(self.root):
                _DEPTH.value = depth + 1
                try:
                    return method(self, *args, **kwargs)
                finally:
                    _DEPTH.value = depth

    return wrapped


def cache_root():
    return C.data_root() / "cache/streetlevel"


@contextmanager
def cache_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".cache.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


@contextmanager
def atomic_target(path: Path):
    """Publish a complete download, keeping the final image suffix for encoders."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.stem}.{os.getpid()}.part{path.suffix}")
    try:
        yield temp
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def collect(root: Path, apply=False, age_seconds=86400):
    """Remove only stale interrupted writes. Completed images are never evicted implicitly."""
    with cache_lock(root):
        files = [
            p
            for p in root.rglob("*")
            if p.is_file() and not p.is_symlink() and p.name != ".cache.lock"
        ]
        stale = [
            p for p in files if ".part" in p.name and time.time() - p.stat().st_mtime > age_seconds
        ]
        inodes = {}
        for path in files:
            stat = path.stat()
            inodes[(stat.st_dev, stat.st_ino)] = stat.st_size
        report = {
            "root": str(root),
            "files": len(files),
            "logical_bytes": sum(p.stat().st_size for p in files),
            "unique_inode_bytes": sum(inodes.values()),
            "stale_partials": len(stale),
            "reclaimable_bytes": sum(p.stat().st_size for p in stale),
            "applied": apply,
        }
        if apply:
            for path in stale:
                path.unlink()
        return report


def import_cache(source: Path, root: Path):
    """Import existing immutable downloads, deduplicating bytes by SHA256 hardlinks.

    Metadata/layout is retained. Never overwrite a different existing key or delete sources.
    """
    import shutil

    if root.resolve().is_relative_to(source.resolve()):
        raise ValueError("import source must not contain the destination cache")
    with cache_lock(root):
        blobs = root / ".objects"
        blobs.mkdir(exist_ok=True)
        for path in source.rglob("*"):
            if (
                not path.is_file()
                or path.is_symlink()
                or ".part" in path.name
                or path.name.startswith(".")
            ):
                continue
            dest = root / path.relative_to(source)
            if dest.exists():
                continue
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            blob = blobs / digest
            if not blob.exists():
                with atomic_target(blob) as temp:
                    shutil.copyfile(path, temp)
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.link(blob, dest)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--import-cache", type=Path)
    args = ap.parse_args()
    if args.import_cache:
        import_cache(args.import_cache, cache_root())
    print(json.dumps(collect(cache_root(), args.apply), indent=2))


if __name__ == "__main__":
    main()
