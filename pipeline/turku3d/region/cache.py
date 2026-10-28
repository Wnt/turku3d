"""Shared streetlevel cache locking, atomic publication, size report and partial-file GC."""

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import threading
import time
from contextlib import ExitStack, contextmanager
from functools import wraps
from pathlib import Path

from turku3d import config as C

_DEPTH = threading.local()
# The size limit walks the whole cache tree (tens of thousands of files, seconds under load)
# so a process enforces it at most this often, not per call.
ENFORCE_INTERVAL_S = 300.0
_LAST_ENFORCED: dict[Path, float] = {}


def synchronized(method):
    """Share maintenance access and retain per-key pins for the whole outer cache call."""

    @wraps(method)
    def wrapped(self, *args, **kwargs):
        if getattr(_DEPTH, "value", 0):
            return method(self, *args, **kwargs)
        with cache_lock(self.root, shared=True), ExitStack() as pins:
            if _enforce_due(self.root):
                _try_enforce(self.root)
            _DEPTH.value = 1
            _DEPTH.pins = pins
            _DEPTH.keys = set()
            try:
                result = method(self, *args, **kwargs)
            finally:
                _DEPTH.value = 0
                _DEPTH.pins = None
        return result

    return wrapped


def _enforce_due(root: Path) -> bool:
    now = time.monotonic()
    if now - _LAST_ENFORCED.get(root, -ENFORCE_INTERVAL_S) < ENFORCE_INTERVAL_S:
        return False
    _LAST_ENFORCED[root] = now
    return True


def _try_enforce(root: Path):
    """One evictor at a time; busy keys never prevent eviction of other keys."""
    with (
        cache_lock(root, shared=True),
        (root / f".lock-{hashlib.sha256(b'eviction').hexdigest()}").open("a") as lock,
    ):
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        _enforce_limit(root, cache_limit(root))


def cache_root():
    return C.data_root() / "cache/streetlevel"


@contextmanager
def cache_lock(root: Path, key: str | None = None, shared: bool = False):
    root.mkdir(parents=True, exist_ok=True)
    name = ".cache.lock" if key is None else f".lock-{hashlib.sha256(key.encode()).hexdigest()}"
    with (root / name).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        yield


def entry_key(root: Path, path: Path):
    relative = path.relative_to(root)
    if relative.parts[0] in {"google", "lookaround", "mapillary"}:
        return Path(*relative.parts[:2])
    return relative


def entry_lock(root: Path, path: Path):
    """Pin an entry across reads/builds; nested shared locks retain independent lifetimes."""
    return cache_lock(root, f"entry:{entry_key(root, path)}", shared=True)


def pin_entry(root: Path, path: Path):
    """Keep paths returned by nested helpers alive until their outer cache call finishes."""
    pins = getattr(_DEPTH, "pins", None)
    identity = (root, entry_key(root, path))
    if pins is not None and identity not in _DEPTH.keys:
        pins.enter_context(entry_lock(root, path))
        _DEPTH.keys.add(identity)


@contextmanager
def build_lock(root: Path, path: Path):
    """Exclusive per-key lock for building one cache entry (distinct from atomic_target's
    publication lock on the same key, which is taken inside it)."""
    with entry_lock(root, path), cache_lock(root, f"build:{path.relative_to(root)}"):
        yield


@contextmanager
def atomic_target(path: Path, root: Path | None = None):
    """Publish a complete download, keeping the final image suffix for encoders."""
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = f"{os.getpid()}.{threading.get_ident()}"
    temp = path.with_name(f".{path.stem}.{writer}.part{path.suffix}")
    with ExitStack() as locks:
        if root is not None:
            locks.enter_context(entry_lock(root, path))
        try:
            yield temp
            _publish_target(path, root, temp, writer)
        finally:
            temp.unlink(missing_ok=True)


def _publish_target(path, root, temp, writer):
    if root is None:
        os.replace(temp, path)
    else:
        with cache_lock(root, str(path.relative_to(root))), cache_lock(root, "objects"):
            with temp.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            blob = root / ".objects" / digest
            blob.parent.mkdir(exist_ok=True)
            if not blob.exists():
                os.link(temp, blob)
            temp.unlink()
            link = path.with_name(f".{path.name}.{writer}.link")
            try:
                os.link(blob, link)
                os.replace(link, path)
            finally:
                link.unlink(missing_ok=True)


def _payloads(root: Path):
    return [
        p
        for p in root.rglob("*")
        if p.is_file()
        and not p.is_symlink()
        and not p.name.startswith(".")
        and ".objects" not in p.parts
    ]


def cache_limit(root: Path | None = None):
    work, data = C.work_root().resolve(), C.data_root().resolve()
    sandbox_cache = root is not None and work != data and root.resolve().is_relative_to(work)
    default = "5" if sandbox_cache else "60"
    return int(float(os.environ.get("TURKU3D_STREETLEVEL_CACHE_GB", default)) * 1024**3)


def enforce_limit(root: Path, limit: int | None = None):
    """Evict least recently used keys; retain provenance metadata alongside live keys."""
    limit = cache_limit(root) if limit is None else limit
    with cache_lock(root, shared=True), cache_lock(root, "eviction"):
        _enforce_limit(root, limit)


def _enforce_limit(root: Path, limit: int):
    files = {}
    for path in _payloads(root):
        try:
            files[path] = path.stat()
        except FileNotFoundError:
            continue
    size = sum(stat.st_size for stat in files.values())
    groups = {}
    for path in files:
        groups.setdefault(entry_key(root, path), []).append(path)
    # Derived decoded arrays are cheap to rebuild and far larger than compressed sources.
    ordered = sorted(
        groups.items(),
        key=lambda item: (item[0].parts[0] != "decoded", max(files[p].st_mtime for p in item[1])),
    )
    for key, group in ordered:
        if size <= limit:
            break
        with (root / f".lock-{hashlib.sha256(f'entry:{key}'.encode()).hexdigest()}").open(
            "a"
        ) as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            for path in group:
                try:
                    size -= path.stat().st_size
                    path.unlink()
                except FileNotFoundError:
                    pass
    blobs = root / ".objects"
    if blobs.exists():
        for blob in blobs.iterdir():
            # Publishers link the blob under the same lock as this sweep.
            with cache_lock(root, "objects"):
                if blob.is_file() and blob.stat().st_nlink == 1:
                    blob.unlink()


def collect(root: Path, apply=False, age_seconds=86400):
    """Remove only stale interrupted writes. Completed images are never evicted implicitly."""
    with cache_lock(root):
        files = [
            p
            for p in root.rglob("*")
            if p.is_file()
            and not p.is_symlink()
            and not p.name.endswith(".lock")
            and not p.name.startswith(".lock-")
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
            "limit_bytes": cache_limit(root),
        }
        if apply:
            for path in stale:
                path.unlink()
        return report


def import_cache(source: Path, root: Path):
    """Import existing immutable downloads, deduplicating bytes by SHA256 hardlinks.

    Metadata/layout is retained. Never overwrite a different existing key or delete sources.
    """
    if not source.is_dir():
        return
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
                try:
                    os.link(path, blob)
                except OSError:
                    with atomic_target(blob) as temp:
                        shutil.copyfile(path, temp)
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.link(blob, dest)
        _enforce_limit(root, cache_limit(root))


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
